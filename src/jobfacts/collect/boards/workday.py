"""Workday career sites, read the way search engines read them.

No Workday API is used. Each site publishes `/<site>/siteMap.xml` (all posting URLs) and every
posting page carries a schema.org `JobPosting` JSON-LD block. We read only those, and only for
employers opted in by hand in `config/employers.toml`. See `notes/workday_terms.md`.

Rules this module enforces:
- robots.txt of the tenant host is fetched first; disallowed URLs are never requested.
- at most one request per second per host, shared by all threads (`RateLimiter`).
- incremental: a posting page is fetched only if it is new or its sitemap `lastmod` changed.
- only pages whose URL location segment looks Swiss or remote are fetched (`url_is_candidate`).
- a capped sitemap cannot show what closed, so stored open postings missing from it get their
  own page requested (same robots check and rate limit, at most `MAX_GONE_CHECKS` per run) and
  are closed only on a clear "gone" answer (see `page_status`).
"""

import json
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from dataclasses import replace
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from jobfacts.collect.http import USER_AGENT, _get
from jobfacts.collect.posting import BoardRead, Posting, parse_time
from jobfacts.locations import mentions_switzerland
from jobfacts.text import html_to_text, unescape_field

_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"
_REMOTE = re.compile(r"remote|home[\s-]?office|telecommut|anywhere|worldwide|virtual", re.I)
_PAGE_ACCEPT = "text/html,application/xhtml+xml"
# Workday sitemaps of big tenants list only 100 postings (seen on Novartis: 100 of ~850 jobs).
SITEMAP_CAP = 100
_REMOTE_SUFFIX = " / Remote"
MAX_GONE_CHECKS = 200  # own-page checks per employer per run


class RateLimiter:
    """At most one request per `interval` seconds per host, across threads.

    `clock` and `sleep` are injectable so tests never really wait.
    """

    def __init__(
        self,
        interval: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.interval, self.clock, self.sleep = interval, clock, sleep
        self._next: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, host: str) -> None:
        with self._lock:  # reserve a slot, then sleep outside the lock
            now = self.clock()
            slot = max(now, self._next.get(host, now))
            self._next[host] = slot + self.interval
        if slot > now:
            self.sleep(slot - now)


DEFAULT_LIMITER = RateLimiter()


class LimitedClient:
    """Wraps an httpx client so every request (retries included) waits for the limiter."""

    def __init__(self, client: httpx.Client, limiter: RateLimiter):
        self.client, self.limiter = client, limiter

    def get(self, url: str, headers: dict | None = None):
        self.limiter.wait(urlparse(url).netloc)
        return self.client.get(url, headers=headers)


def load_robots(client, base: str) -> RobotFileParser:
    """Fetch and parse robots.txt. 404 = no rules; 401/403 = everything disallowed.

    Any other failure raises, so the board counts as not read (we never guess permission).
    """
    parser = RobotFileParser()
    response = client.get(f"{base}/robots.txt", headers={"Accept": "text/plain"})
    if response.status_code in (404, 410):
        parser.parse([])
    elif response.status_code in (401, 403):
        parser.parse(["User-agent: *", "Disallow: /"])
    else:
        response.raise_for_status()
        parser.parse(response.text.splitlines())
    return parser


def parse_sitemap(xml_text: str) -> list[tuple[str, str | None]]:
    """(posting url, lastmod or None) for every `/job/` URL in a Workday sitemap."""
    root = ET.fromstring(xml_text.strip())
    if root.tag != f"{_NS}urlset":
        raise ValueError(f"not a sitemap urlset (root element {root.tag!r})")
    out = []
    for node in root.findall(f"{_NS}url"):
        loc = (node.findtext(f"{_NS}loc") or "").strip()
        if "/job/" in loc:  # Roche lists a bare site URL too
            out.append((loc, (node.findtext(f"{_NS}lastmod") or "").strip() or None))
    return out


def job_id_from_url(url: str) -> str:
    """Trailing requisition id of a posting URL: `.../Title_R-0006447-1` -> `R-0006447-1`."""
    last = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return last.rsplit("_", 1)[-1] if "_" in last else last


_SUFFIX = re.compile(r"^(.+\d)-\d+$")


def requisition_key(job_id: str) -> str:
    """Requisition id without the trailing site suffix: `r-17167-2` and `r-17167-4` -> `r-17167`.

    The same requisition has a different `-N` on each Workday site of an employer. Ids without
    a suffix (`r-19892`, `R-0006447`) are returned unchanged.
    """
    match = _SUFFIX.match(job_id)
    return match.group(1) if match else job_id


def url_location_text(url: str) -> str:
    """Location segment after `/job/`, de-slugged: `USA---NY---New-York` -> `USA NY New York`."""
    path = urlparse(url).path
    segment = path.split("/job/", 1)[1].split("/", 1)[0] if "/job/" in path else ""
    return re.sub(r"[-_]+", " ", segment).strip()


def url_is_candidate(url: str) -> bool:
    """True if the URL's location segment names a Swiss place or says remote."""
    text = url_location_text(url)
    return bool(text) and (
        mentions_switzerland(text, location_field=True) or bool(_REMOTE.search(text))
    )


def _json_ld_job(html_text: str) -> dict | None:
    for block in re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.S | re.I
    ):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data])
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return item
    return None


def _location(job: dict, url: str) -> str:
    places = job.get("jobLocation") or []
    places = places if isinstance(places, list) else [places]
    parts = []
    for place in places:
        address = (place or {}).get("address") or {}
        if isinstance(address, str):
            parts.append(unescape_field(address))
            continue
        country = address.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        locality = unescape_field(address.get("addressLocality"))
        if not locality or any(ch.isdigit() for ch in locality):  # Workday codes like "Wor-Ati6"
            locality = url_location_text(url)
        region = unescape_field(address.get("addressRegion"))
        parts.append(
            ", ".join(dict.fromkeys(filter(None, [locality, region, unescape_field(country)])))
        )
    if not parts and url_location_text(url):
        parts.append(url_location_text(url))
    if job.get("jobLocationType") == "TELECOMMUTE":
        parts.append("Remote")
    return " / ".join(dict.fromkeys(filter(None, parts)))


def parse_posting(html_text: str, url: str, version: str | None = None) -> Posting:
    """Map the page's JobPosting JSON-LD to a Posting. Raises ValueError if there is none."""
    job = _json_ld_job(html_text)
    if job is None:
        raise ValueError("no JobPosting JSON-LD on the page")
    identifier = job.get("identifier")
    identifier = identifier.get("value") if isinstance(identifier, dict) else identifier
    return Posting(
        source_job_id=job_id_from_url(url) or str(identifier or ""),
        url=url,
        title=unescape_field(job.get("title")),
        location_raw=_location(job, url),
        description_text=html_to_text(job.get("description")),
        posted_at=parse_time(job.get("datePosted")),
        source_version=version,
    )


_EMPTY_OG_TITLE = re.compile(r'<meta[^>]*property="og:title"(?![^>]*content="[^"]+")[^>]*>')


def page_status(client, robots, url: str) -> str:
    """Is the posting page still there? One of `open`, `gone`, `gone_shell`, `unknown`, `stop`.

    `gone` only on a clear signal: HTTP 404/410, or a redirect away from the posting (Workday
    sends removed jobs to the site's search/home page, so the final URL no longer has the
    requisition id). A 200 page on the same URL counts as `open` if it has the JobPosting
    JSON-LD; without it we cannot tell (JavaScript shell, maintenance page): `unknown`. Errors,
    timeouts, 5xx and robots-disallowed URLs are `unknown`: never close on them. 403/429 means
    the site asks us to stop (`stop`).
    """
    if not robots.can_fetch(USER_AGENT, url):
        return "unknown"
    try:
        response = client.get(url, headers={"Accept": _PAGE_ACCEPT})
    except httpx.HTTPError:
        return "unknown"
    code = response.status_code
    if code in (404, 410):
        return "gone"
    if code in (403, 429):
        return "stop"
    wanted = job_id_from_url(url).lower()
    if code in (301, 302, 303, 307, 308):  # client that does not follow redirects
        target = response.headers.get("location", "").lower()
        return "gone" if wanted and target and wanted not in target else "unknown"
    if code != 200:
        return "unknown"
    if wanted and wanted not in str(response.url).lower():
        return "gone"  # redirected to a generic page
    if _json_ld_job(response.text) is not None:
        return "open"
    # Workday answers a removed (or unknown) job with 200 and the page shell: no JSON-LD and an
    # EMPTY og:title (checked live on 3 Nov 2026; a live job has its title there).
    if _EMPTY_OG_TITLE.search(response.text):
        return "gone_shell"  # weaker signal than 404: see the outage guard in fetch()
    return "unknown"


def _drop_universal_remote(postings: list[Posting]) -> list[Posting]:
    """Some tenants mark every posting TELECOMMUTE (Disney, Logitech, Thomson Reuters ...).

    A flag on (nearly) all postings says nothing, so the "Remote" label is removed then.
    """
    flagged = [p for p in postings if p.location_raw.endswith(_REMOTE_SUFFIX)]
    if len(postings) >= 5 and len(flagged) >= 0.9 * len(postings):
        return [
            replace(p, location_raw=p.location_raw.removesuffix(_REMOTE_SUFFIX))
            if p in flagged
            else p
            for p in postings
        ]
    return postings


def fetch(
    client,
    board_id: str,
    extra: dict,
    known: dict[str, str | None] | None = None,
    limiter: RateLimiter | None = None,
    known_urls: dict[str, str] | None = None,
) -> BoardRead:
    """Read one Workday site. `known` maps stored open job ids to their stored sitemap lastmod.

    `known_urls` (stored open job id -> URL, least recently seen first) lets a capped read check
    the postings its sitemaps do not list, one page each, instead of closing nothing."""
    known = known or {}
    client = LimitedClient(client, limiter or DEFAULT_LIMITER)
    base = f"https://{board_id}.{extra['wd']}.myworkdayjobs.com"
    robots = load_robots(client, base)
    prefilter = extra.get("prefilter", True)  # false for employers whose jobs are all Swiss
    sites = [extra["site"], *extra.get("extra_sites", [])]
    merge = len(sites) > 1 or "extra_sites" in extra  # one posting per requisition, not per URL
    key_of = (lambda url: requisition_key(job_id_from_url(url))) if merge else job_id_from_url

    stats: Counter = Counter()
    failures: Counter = Counter()
    entries: dict[str, tuple[str, str | None]] = {}  # key -> (url, lastmod); main site wins
    capped_sites = []
    site_notes = []
    for index, site in enumerate(sites):
        sitemap_url = f"{base}/{site}/siteMap.xml"
        try:
            if not robots.can_fetch(USER_AGENT, sitemap_url):
                raise PermissionError(f"robots.txt disallows {sitemap_url}")
            listed = parse_sitemap(
                _get(client, sitemap_url, 2, accept="application/xml, text/xml").text
            )
        except (PermissionError, httpx.HTTPError, ValueError) as error:
            if index == 0:
                raise  # the main site must be readable, otherwise the board is not read
            # An unreadable extra site means we cannot tell what it holds: read the rest, close
            # nothing.
            capped_sites.append(site)
            site_notes.append(f"extra site {site} not read ({type(error).__name__}: {error})")
            continue
        stats["sitemap"] += len(listed)
        site_notes.append(f"{site} {len(listed)}")
        if len(listed) == SITEMAP_CAP:
            capped_sites.append(site)
        for url, lastmod in listed:
            key = key_of(url)
            # Main site wins, but a Swiss/remote URL beats a non-candidate one: the same
            # requisition may sit under a non-Swiss city on External and Zurich on Technology.
            if key not in entries or (
                prefilter and not url_is_candidate(entries[key][0]) and url_is_candidate(url)
            ):
                entries[key] = (url, lastmod)

    seen: set[str] = set()
    to_fetch = []
    for job_id, (url, lastmod) in entries.items():
        seen.add(job_id)
        if prefilter and not url_is_candidate(url):
            stats["skipped_by_prefilter"] += 1
            continue
        stats["kept_by_prefilter"] += 1
        if job_id in known and (lastmod is None or lastmod == known[job_id]):
            stats["unchanged"] += 1
        elif not robots.can_fetch(USER_AGENT, url):
            stats["robots_disallowed"] += 1
        else:
            to_fetch.append((job_id, url, lastmod))

    postings = []
    for index, (job_id, url, lastmod) in enumerate(to_fetch):
        try:
            page = _get(client, url, 2, accept=_PAGE_ACCEPT).text
            postings.append(replace(parse_posting(page, url, lastmod), source_job_id=job_id))
        except httpx.HTTPStatusError as error:
            code = error.response.status_code
            failures[f"HTTP {code}"] += 1
            if code in (403, 429):  # the site asks us to stop: leave the rest for the next run
                failures["stopped early (rest deferred)"] += len(to_fetch) - index - 1
                break
        except (httpx.TransportError, ValueError) as error:
            failures[f"{type(error).__name__}: {error}"] += 1
    postings = _drop_universal_remote(postings)
    stats["fetched"] = len(to_fetch) - sum(
        n for reason, n in failures.items() if reason.startswith("stopped")
    )
    close_ids: set[str] = set()
    gone_note = ""
    if capped_sites and known_urls:
        absent = [(i, known_urls[i]) for i in known_urls if i not in seen][:MAX_GONE_CHECKS]
        checked = 0
        shell_ids: set[str] = set()
        for job_id, url in absent:
            status = page_status(client, robots, url)
            if status == "stop":
                break
            checked += 1
            if status == "gone":
                close_ids.add(job_id)
            elif status == "gone_shell":
                shell_ids.add(job_id)
            elif status == "open":
                seen.add(job_id)  # still online, only outside the capped list
        if checked >= 5 and len(shell_ids) == checked:
            # Every page is an empty shell: more likely an outage or a changed page than a mass
            # removal. 404/410 and redirects are trusted; only the shell signal is held back.
            gone_note = f"; checked {checked}, all empty shells: closed none (suspicious)"
        else:
            close_ids |= shell_ids
            gone_note = (
                f"; checked {checked} postings missing from the capped list, "
                f"closed {len(close_ids)}"
            )
    notes = (
        f"sitemap {stats['sitemap']}, prefilter kept {stats['kept_by_prefilter']} / skipped "
        f"{stats['skipped_by_prefilter']}, unchanged {stats['unchanged']}, "
        f"fetched {stats['fetched']}, stored {len(postings)}, "
        f"robots disallowed {stats['robots_disallowed']}, failures {sum(failures.values())}"
    )
    if merge:
        notes += f"; sites: {', '.join(site_notes)}; merged {len(entries)} requisitions"
    if capped_sites:
        notes += (
            f"; capped or unread sitemap ({', '.join(capped_sites)}; cap is {SITEMAP_CAP}), "
            "so the list alone closes nothing"
        )
        notes += gone_note
    if failures:
        notes += " (" + "; ".join(f"{r} x{n}" for r, n in failures.items()) + ")"
    return BoardRead(
        postings=postings,
        seen_ids=seen,
        notes=notes,
        close_missing=not capped_sites,
        close_ids=close_ids,
    )


fetch.incremental = True
fetch.checks_gone = True  # run.py passes `known_urls`
