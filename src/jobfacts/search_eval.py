"""Recall check of the search, replayed on the past (`jobfacts search-eval`).

The owner's job applications are the jobs they wanted. For each application this module
decides whether `jobfacts search` would have shown it and, if not, at which step it was lost.
Matching an application to a board posting and classifying the result are pure functions;
a loader reads the database and a writer produces the Markdown report. See docs/search.md.
"""

import difflib
import json
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

import psycopg

from jobfacts.applications import split_link
from jobfacts.collect.boards.workday import job_id_from_url, requisition_key
from jobfacts.extract import jev
from jobfacts.extract.rules import level_words
from jobfacts.extract.store import load_postings
from jobfacts.grouping import title_key
from jobfacts.locations import OUTSIDE
from jobfacts.stats import wilson
from jobfacts.testset import PRIVATE_DIR

# Two titles that are not equal after `title_key` still match when they name the same level words
# (`rules.level_words`: senior, staff, intern, ...) and difflib's ratio is at least this. 0.9
# accepts a small addition such as " NLP" and rejects most different roles; the level words stop
# "Senior X" matching "Staff X" or "X".
# A posting first seen more than this many days after the application is a repost of a role
# (or another opening), not the one applied to; applied only when the employer had been read
# successfully on or before the application date.
REPOST_DAYS = 30
TITLE_RATIO = 0.9

# Outcomes in the order of the search pipeline; an application gets the first step it fails.
OUTCOMES = [
    "not_covered",
    "unknown_before_collection",
    "not_collected",
    "no_relevance",
    "not_relevant",
    "not_eligible",
    "not_extracted",
    "filtered",
    "found",
]
# Outcomes that count as misses (or hits) of the search itself. not_covered is a source gap and
# unknown_before_collection may predate our first read, so neither is in the recall base.
# not_eligible (a relevant posting outside Switzerland and not remote, which extraction skips by
# design, decision 7a) is only in the wider base of `recall(..., all_wanted=True)`.
RECALL_BASE = [
    "found",
    "not_collected",
    "no_relevance",
    "not_relevant",
    "not_extracted",
    "filtered",
]
# How far a posting got; among equal-title postings (one job in several places) the furthest
# one is reported, since search shows a group when any of its postings passes.
_PROGRESS = ["no_relevance", "not_relevant", "not_eligible", "not_extracted", "filtered", "found"]

ZURICH = ZoneInfo("Europe/Zurich")


def local_date(moment: datetime) -> date:
    """The calendar date in Zurich: application dates are the owner's local dates."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZURICH).date()


# ---------- names ----------

_LEGAL_SUFFIXES = {
    "ag",
    "sa",
    "gmbh",
    "inc",
    "ltd",
    "se",
    "sarl",
    "llc",
    "plc",
    "corp",
    "co",
    "bv",
    "nv",
    "ab",
    "oy",
    "srl",
    "spa",
    "kg",
    "limited",
    "corporation",
}


def normalise_company(name: str) -> str:
    """Casefold, strip accents and punctuation, drop legal suffixes ("Acme, Inc." -> "acme")."""
    text = unicodedata.normalize("NFKD", name)
    text = "".join(c for c in text if not unicodedata.combining(c)).casefold()
    tokens = re.sub(r"[^a-z0-9]+", " ", text).split()
    kept = [t for t in tokens if t not in _LEGAL_SUFFIXES]
    return " ".join(kept or tokens)


def company_matches(a: str, b: str) -> str | None:
    """ "exact" for equal normalised names, "contained" when the shorter one is a run of whole
    words of the longer ("nova quark" contains "quark"), else None. The shorter name needs two
    words or five characters to count."""
    ta, tb = a.split(), b.split()
    if not ta or not tb:
        return None
    if ta == tb:
        return "exact"
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    if len(short) < 2 and len(short[0]) < 5:  # "a1" or "blue" inside a longer name: too weak
        return None
    n = len(short)
    if any(long[i : i + n] == short for i in range(len(long) - n + 1)):
        return "contained"
    return None


# ---------- job ids and boards in links ----------

_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE)
_ATS_DOMAINS = {
    "ashby": "ashbyhq.com",
    "greenhouse": "greenhouse.io",
    "lever": "lever.co",
    "smartrecruiters": "smartrecruiters.com",
    "workable": "workable.com",
    "recruitee": "recruitee.com",
    "personio": "personio.de",
    "teamtailor": "teamtailor.com",
    "workday": "myworkdayjobs.com",
}
# Boards whose collector stores the id used in links as `source_job_id`.
_ID_IS_SOURCE_JOB_ID = {"ashby", "greenhouse", "lever", "smartrecruiters", "workable", "personio"}
_WORKDAY_REQUISITION = re.compile(r"[a-z]+-?\d+-\d+")
_WORKDAY_DIGITS_SUFFIX = re.compile(r"(\d+-\d+)-\d+")


def _on_domain(host: str, domain: str) -> bool:
    return host == domain or host.endswith("." + domain)


def _is(ats: str, host: str, hint: str | None) -> bool:
    domains = [_ATS_DOMAINS[ats]] + (["personio.com"] if ats == "personio" else [])
    return hint == ats or any(_on_domain(host, d) for d in domains)


def _parts(url: str):
    parsed = urlparse(url.strip())
    segments = [s for s in parsed.path.split("/") if s]
    return (parsed.hostname or "").lower(), segments, parse_qs(parsed.query)


def _first(pattern: str, text: str) -> str | None:
    m = re.search(pattern, text)
    return m.group(1) if m else None


def _workday_key(host: str, path: str) -> tuple[str, str] | None:
    path = re.sub(r"/apply/?$", "", path.rstrip("/"))
    if "/job/" not in path:
        return None
    job_id = job_id_from_url(path).casefold()
    if _WORKDAY_REQUISITION.fullmatch(job_id):
        job_id = requisition_key(job_id)
    elif m := _WORKDAY_DIGITS_SUFFIX.fullmatch(job_id):  # 202610-125078-1: site suffix
        job_id = m.group(1)
    return "workday", f"{host.split('.')[0]}/{job_id}"


def job_keys(
    url: str, hint: str | None = None, board_id: str | None = None
) -> set[tuple[str, str]]:
    """Stable (ats, job id) pairs found in a link, lower case. `hint` names the board type
    for custom domains and `board_id` the board of a stored posting (the collector knows
    both). Recruitee slugs are only unique within a board, so their key carries the board:
    the subdomain on recruitee.com, else the host, plus the stored board id when given."""
    if not url:
        return set()
    host, segments, query = _parts(url)
    path = "/" + "/".join(segments)
    keys: set[tuple[str, str]] = set()
    if (jid := (query.get("ashby_jid") or [""])[0]) and _UUID.fullmatch(jid):
        keys.add(("ashby", jid.lower()))
    if (jid := (query.get("gh_jid") or [""])[0]).isdigit():
        keys.add(("greenhouse", jid))
    if _is("ashby", host, hint) and (m := _UUID.search(path)):
        keys.add(("ashby", m.group(0).lower()))
    if _is("greenhouse", host, hint):
        token = (query.get("token") or [""])[0]
        job = _first(r"/jobs/(\d+)", path) or (token if token.isdigit() else None)
        if job:
            keys.add(("greenhouse", job))
    if _is("lever", host, hint) and (m := _UUID.search(path)):
        keys.add(("lever", m.group(0).lower()))
    if _is("smartrecruiters", host, hint) and len(segments) >= 2:
        if job := _first(r"^(\d+)", segments[1]):
            keys.add(("smartrecruiters", job))
    if _is("workable", host, hint) and (job := _first(r"/j/([A-Za-z0-9]+)", path)):
        keys.add(("workable", job.lower()))
    if _is("recruitee", host, hint) and (slug := _first(r"/o/([^/]+)", path)):
        own = host.split(".")[0] if _on_domain(host, "recruitee.com") else host
        for board in filter(None, [own, (board_id or "").casefold()]):
            keys.add(("recruitee", f"{board}/{slug.lower()}"))
    if _is("personio", host, hint) and (job := _first(r"/job/(\d+)", path)):
        keys.add(("personio", job))
    if _is("teamtailor", host, hint) and (job := _first(r"/jobs/(\d+)", path)):
        keys.add(("teamtailor", job))
    if _is("workday", host, hint) and (key := _workday_key(host, path)):
        keys.add(key)
    return keys


def board_key(url: str) -> tuple[str, str] | None:
    """(ats, board id) of a link on a known job board, lower case; matches `employers`."""
    if not url:
        return None
    host, segments, query = _parts(url)
    first_label = host.split(".")[0]
    if _on_domain(host, "ashbyhq.com") and segments:
        return "ashby", segments[0].lower()
    if _on_domain(host, "greenhouse.io"):
        if segments[:1] == ["embed"]:
            board = (query.get("for") or [""])[0]
        else:
            board = segments[0] if segments else ""
        return ("greenhouse", board.lower()) if board else None
    if _on_domain(host, "lever.co") and segments:
        return "lever", segments[0].lower()
    if _on_domain(host, "smartrecruiters.com") and segments:
        return "smartrecruiters", segments[0].lower()
    if _on_domain(host, "workable.com") and segments and segments[0] != "j":
        return "workable", segments[0].lower()
    for ats, domain in (
        ("recruitee", "recruitee.com"),
        ("personio", "personio.de"),
        ("personio", "personio.com"),
        ("teamtailor", "teamtailor.com"),
        ("workday", "myworkdayjobs.com"),
    ):
        if _on_domain(host, domain) and host != domain:
            return ats, first_label
    return None


def link_source(url: str) -> str:
    """linkedin, hiring.cafe, ats-direct, other or none, from the host of the link."""
    if not url:
        return "none"
    host = _parts(url)[0]
    if _on_domain(host, "linkedin.com"):
        return "linkedin"
    if _on_domain(host, "hiring.cafe"):
        return "hiring.cafe"
    domains = [*_ATS_DOMAINS.values(), "personio.com"]
    return "ats-direct" if any(_on_domain(host, d) for d in domains) else "other"


# ---------- data ----------


@dataclass(frozen=True)
class Application:
    key: str  # folder path under the applications folder, POSIX
    company: str
    title: str
    date: date | None
    url: str = ""


@dataclass(frozen=True)
class Employer:
    id: int
    name: str
    ats: str
    board_id: str
    first_read: date | None  # date of the first successful board read


@dataclass(frozen=True)
class BoardPosting:
    id: int
    employer_id: int
    title: str
    url: str
    source_job_id: str
    first_seen: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class PostingState:
    relevance: tuple[bool, float] | None  # (relevant, probability) of the Jev relevance row
    extraction: Mapping | None  # facts of the Jev search extraction
    stale: bool = False  # the extraction was made for an older text of the posting
    eligible: bool = True  # PostingText.is_extraction_eligible(): Swiss or remote location
    location: str = ""  # the posting's location_raw


@dataclass
class BoardData:
    employers: list[Employer]
    postings: list[BoardPosting]
    by_key: dict[tuple[str, str], list[BoardPosting]] = field(default_factory=dict)
    by_board: dict[tuple[str, str], Employer] = field(default_factory=dict)
    by_employer: dict[int, list[BoardPosting]] = field(default_factory=dict)
    names: list[tuple[str, Employer]] = field(default_factory=list)

    @classmethod
    def build(cls, employers: Iterable[Employer], postings: Iterable[BoardPosting]) -> "BoardData":
        data = cls(list(employers), list(postings))
        by_id = {e.id: e for e in data.employers}
        for e in data.employers:
            data.by_board.setdefault((e.ats, e.board_id.casefold()), e)
            data.names.append((normalise_company(e.name), e))
        for p in data.postings:
            emp = by_id.get(p.employer_id)
            if emp is None:
                continue
            data.by_employer.setdefault(emp.id, []).append(p)
            keys = job_keys(p.url, emp.ats, emp.board_id)
            if emp.ats in _ID_IS_SOURCE_JOB_ID and p.source_job_id:
                keys.add((emp.ats, p.source_job_id.casefold()))
            for key in keys:
                data.by_key.setdefault(key, []).append(p)
        return data


@dataclass
class Match:
    employers: list[Employer]
    employer_method: str  # url, url_board, name_exact, name_contained or ""
    postings: list[BoardPosting]  # equally good title matches, closest in time first
    posting_method: str  # url, title_exact, title_fuzzy or ""

    @property
    def method(self) -> str:
        if self.posting_method == "url":
            return "url"
        return " + ".join(m for m in (self.employer_method, self.posting_method) if m)


def _distance(posting: BoardPosting, when: date | None) -> int:
    return abs((local_date(posting.first_seen) - when).days) if when else 0


def best_title_matches(
    title: str, postings: Iterable[BoardPosting], when: date | None
) -> tuple[list[BoardPosting], str]:
    """The postings whose title is equal (after `title_key`) or, failing that, most similar
    to `title`: ratio >= TITLE_RATIO and the same level words. Closest first_seen_at to `when`
    first."""
    wanted = title.replace("_", " ")
    wanted_key = title_key(wanted)
    scored = [
        (difflib.SequenceMatcher(None, wanted_key, title_key(p.title)).ratio(), p) for p in postings
    ]
    exact = [p for s, p in scored if s == 1.0]
    if exact:
        best, method = exact, "title_exact"
    else:
        levels = level_words(wanted)
        similar = [(s, p) for s, p in scored if s >= TITLE_RATIO and level_words(p.title) == levels]
        if not similar:
            return [], ""
        top = max(s for s, _ in similar)
        best, method = [p for s, p in similar if s == top], "title_fuzzy"
    return sorted(best, key=lambda p: (_distance(p, when), p.id)), method


def _employers_of(app: Application, data: BoardData) -> tuple[list[Employer], str]:
    if board := board_key(app.url):
        if emp := data.by_board.get(board):
            return [emp], "url_board"
    norm = normalise_company(app.company)
    matches = [(company_matches(norm, n), e) for n, e in data.names]
    for kind in ("exact", "contained"):
        found = [e for m, e in matches if m == kind]
        if found:
            return found, f"name_{kind}"
    return [], ""


def _in_window(posting: BoardPosting, when: date | None, first_read: date | None) -> bool:
    """Could the posting be the opening applied to on `when`? Not closed before that day, and
    (when we were already reading the board then) not first seen more than REPOST_DAYS later."""
    if when is None:
        return True
    if posting.closed_at and local_date(posting.closed_at) < when:
        return False
    seen = local_date(posting.first_seen)
    return not (first_read and first_read <= when and (seen - when).days > REPOST_DAYS)


def _group_of(keys: set[str], pool: Iterable[BoardPosting], when: date | None) -> list:
    """The postings of `pool` whose title_key is in `keys`, closest first_seen_at to `when`
    first: the postings search puts in one group (same employer, same normalised title)."""
    group = [p for p in pool if title_key(p.title) in keys]
    return sorted(group, key=lambda p: (_distance(p, when), p.id))


def match_application(app: Application, data: BoardData) -> Match:
    """Link id first, then company name and title (see docs/search.md). The postings of a
    match are the whole group (same employer and title_key) of what was matched, because
    search shows a group through one posting."""
    hits = {p.id: p for key in job_keys(app.url) for p in data.by_key.get(key, [])}
    if hits:
        post = min(hits.values(), key=lambda p: (_distance(p, app.date), p.id))
        emp = next(e for e in data.employers if e.id == post.employer_id)
        pool = [
            p
            for p in data.by_employer.get(emp.id, [])
            if p.id != post.id and _in_window(p, app.date, emp.first_read)
        ]
        mates = _group_of({title_key(post.title)}, pool, app.date)
        return Match([emp], "url", [post, *mates], "url")
    employers, method = _employers_of(app, data)
    candidates = [
        p
        for e in employers
        for p in data.by_employer.get(e.id, [])
        if _in_window(p, app.date, e.first_read)
    ]
    best, posting_method = best_title_matches(app.title, candidates, app.date)
    postings = _group_of({title_key(p.title) for p in best}, candidates, app.date)
    return Match(employers, method, postings, posting_method)


# ---------- the owner-view filters ----------


def filter_reason(x: Mapping) -> str | None:
    """Why the owner-view filters drop a posting with these extracted facts, else None.

    These mirror the filters a downstream tool applies to `jobfacts search ""` results:
    German required at fluent level, fixed-term contracts, and postings without a way to work
    from Switzerland (no Swiss canton among the offices; remote only counts when the
    posting does not say remote work from Switzerland is not allowed). The first failing
    rule is returned, in this order.
    """
    if x.get("german") == "required_fluent":
        return "german required_fluent"
    if x.get("contract_type") == "fixed_term":
        return "fixed-term contract"
    swiss_office = any(c != OUTSIDE for c in x.get("office_cantons") or [])
    if x.get("work_mode") == "remote":
        if x.get("remote_from_switzerland") == "not_allowed" and not swiss_office:
            return "remote, not allowed from Switzerland"
        return None
    return None if swiss_office else "no Swiss office"


# ---------- classification ----------


@dataclass
class Outcome:
    app: Application
    outcome: str
    detail: str = ""
    employer: Employer | None = None
    posting: BoardPosting | None = None
    method: str = ""
    # found only: the posting was first seen before / same day / after the application, or
    # "collection started later" when the application predates the employer's first read
    timing: str = ""

    @property
    def timing_text(self) -> str:
        if not self.timing:
            return ""
        if self.timing == "collection started later":
            first = self.employer.first_read if self.employer else ""
            return f"collected from {first} (after the application; collection started then)"
        return f"posting first seen {self.timing} the application"


def _timing(posting: BoardPosting, when: date) -> str:
    seen = local_date(posting.first_seen)
    return "before" if seen < when else "same day" if seen == when else "after"


def _stage(posting: BoardPosting, state: PostingState) -> tuple[str, str]:
    """How far one posting gets: the outcome name and detail (a filter reason for `filtered`)."""
    if state.relevance is None:
        return "no_relevance", "no relevance row"
    relevant, probability = state.relevance
    if not relevant:
        return "not_relevant", f"relevance score {probability:.2f}"
    if state.extraction is None:
        if not state.eligible:
            return "not_eligible", state.location or "location not stated"
        return "not_extracted", "no successful Jev search extraction"
    if state.stale and posting.closed_at is None:  # search needs the current text's extraction
        return "not_extracted", "extraction is for older text"
    if reason := filter_reason(state.extraction):
        return "filtered", reason
    note = "extraction is from an older text of the posting (text changed)" if state.stale else ""
    return "found", note


def _search_order(posting: BoardPosting) -> tuple[float, int]:
    """`jobfacts search ""` order: first_seen_at DESC, id."""
    return -posting.first_seen.timestamp(), posting.id


def _classify_group(app: Application, group: list[BoardPosting], state_of) -> tuple:
    """(outcome, detail, posting) of one group of postings (same employer and title_key).

    Search shows a group through its first posting in search order among those that reach the
    owner-view filters, and the filters look at that posting's facts only. Without such a
    posting the group's furthest failure is reported (closest to the application date first).
    """
    stages = {p.id: _stage(p, state_of(p.id)) for p in group}
    for p in sorted(group, key=_search_order):
        if stages[p.id][0] in ("filtered", "found"):
            return stages[p.id][0], stages[p.id][1], p
    best = group[0]
    for p in group:
        if _PROGRESS.index(stages[p.id][0]) > _PROGRESS.index(stages[best.id][0]):
            best = p
    return stages[best.id][0], stages[best.id][1], best


def classify(
    app: Application, match: Match, state_of: Callable[[int], PostingState | None]
) -> Outcome:
    """The first failing step of the pipeline for one application (OUTCOMES order)."""
    if not match.employers:
        return Outcome(app, "not_covered")
    if not match.postings:
        first = min((e.first_read for e in match.employers if e.first_read), default=None)
        if first is not None and first <= app.date:
            return Outcome(
                app,
                "not_collected",
                f"employer read since {first}",
                match.employers[0],
                None,
                match.method,
            )
        detail = f"first read {first}" if first else "never read successfully"
        return Outcome(
            app, "unknown_before_collection", detail, match.employers[0], None, match.method
        )
    groups: dict[tuple, list[BoardPosting]] = {}
    for posting in match.postings:
        groups.setdefault((posting.employer_id, title_key(posting.title)), []).append(posting)
    best = None
    for group in groups.values():  # groups come closest to the application date first
        result = _classify_group(app, group, state_of)
        if best is None or _PROGRESS.index(result[0]) > _PROGRESS.index(best[0]):
            best = result
    outcome, detail, posting = best
    employer = next((e for e in match.employers if e.id == posting.employer_id), None)
    timing = ""
    if outcome == "found":
        if employer and employer.first_read and app.date < employer.first_read:
            timing = "collection started later"
        else:
            timing = _timing(posting, app.date)
    return Outcome(app, outcome, detail, employer, posting, match.method, timing)


def recall(counts: Mapping[str, int], all_wanted: bool = False) -> tuple[int, int, float, float]:
    """(found, recall base, Wilson 95% low, high). `all_wanted` adds not_eligible to the base."""
    k = counts.get("found", 0)
    base = [*RECALL_BASE, "not_eligible"] if all_wanted else RECALL_BASE
    n = sum(counts.get(o, 0) for o in base)
    return (k, n, *wilson(k, n))


# ---------- database ----------


def load_board_data(conn: psycopg.Connection) -> BoardData:
    """Board employers (with the date of their first successful read) and board postings."""
    employers = [
        Employer(r[0], r[1], r[2], r[3], local_date(r[4]) if r[4] else None)
        for r in conn.execute(
            "SELECT e.id, e.name, e.ats, e.board_id, "
            "(SELECT min(c.started_at) FROM board_reads b "
            " JOIN collection_runs c ON c.id = b.run_id "
            " WHERE b.employer_id = e.id AND b.ok) "
            "FROM employers e WHERE e.ats <> 'application'"
        )
    ]
    postings = [
        BoardPosting(*r)
        for r in conn.execute(
            "SELECT id, employer_id, title, url, source_job_id, first_seen_at, closed_at "
            "FROM postings WHERE origin = 'board'"
        )
    ]
    return BoardData.build(employers, postings)


def posting_state(conn: psycopg.Connection, posting_id: int) -> PostingState:
    """Relevance and the latest successful Jev search extraction of a posting.

    Like `search._BASE` but without the open-posting condition. An extraction made for the
    current text (content_hash) is preferred; otherwise the latest one is used and marked stale.
    """
    rel = conn.execute(
        "SELECT relevant, probability FROM relevance WHERE posting_id = %s AND model = %s",
        (posting_id, jev.MODEL),
    ).fetchone()
    row = conn.execute(
        "SELECT x.work_mode, x.remote_from_switzerland, x.german, x.contract_type, "
        "x.office_cantons, COALESCE(x.content_hash = p.content_hash, false) "
        "FROM extractions x JOIN extraction_runs r ON r.id = x.run_id "
        "JOIN postings p ON p.id = x.posting_id "
        "WHERE x.posting_id = %s AND r.extractor = 'jev' AND r.purpose = 'search' "
        "AND x.error IS NULL "
        "ORDER BY COALESCE(x.content_hash = p.content_hash, false) DESC, x.run_id DESC LIMIT 1",
        (posting_id,),
    ).fetchone()
    extraction = (
        dict(
            zip(
                [
                    "work_mode",
                    "remote_from_switzerland",
                    "german",
                    "contract_type",
                    "office_cantons",
                ],
                row[:5],
                strict=True,
            )
        )
        if row
        else None
    )
    text = load_postings(conn, [posting_id])[0]  # the rule itself lives in PostingText
    return PostingState(
        (rel[0], float(rel[1])) if rel else None,
        extraction,
        bool(row) and not row[5],
        text.is_extraction_eligible(),
        text.location_raw,
    )


def evaluate(conn: psycopg.Connection, apps: Iterable[Application]) -> list[Outcome]:
    data = load_board_data(conn)
    cache: dict[int, PostingState] = {}

    def state_of(posting_id: int) -> PostingState:
        if posting_id not in cache:
            cache[posting_id] = posting_state(conn, posting_id)
        return cache[posting_id]

    return [classify(app, match_application(app, data), state_of) for app in apps]


# ---------- applications folder ----------


def _parse_date(value) -> date | None:
    try:
        return date.fromisoformat(str(value)) if value else None
    except ValueError:
        return None


def load_applications(root: Path, dates: Mapping[str, str]) -> list[Application]:
    """One Application per `<root>/<Company>/<Role>/job_description.md`, date None if unknown."""
    apps = []
    for path in sorted(root.glob("**/job_description.md")):
        folder = path.parent.relative_to(root)
        if not folder.parts:
            print(f"warning: skipping {path} (not inside a company folder)", file=sys.stderr)
            continue
        key = folder.as_posix()
        url, _ = split_link(path.read_text(encoding="utf-8"))
        apps.append(
            Application(key, folder.parts[0], folder.parts[-1], _parse_date(dates.get(key)), url)
        )
    return apps


# ---------- report ----------


@dataclass
class EvalRun:
    outcomes: list[Outcome]
    counts: dict[str, int]
    no_date: int
    before_since: int
    dates_without_folder: int
    path: Path | None
    summary: str
    report: str


def default_output(today: date) -> Path:
    return PRIVATE_DIR / "reports" / f"search_eval_{today.isoformat()}.md"


def _cell(value) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _table(headers: list[str], rows: list[list]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(_cell(c) for c in row) + " |" for row in rows]
    return [*lines, ""]


RECALL_WITHIN_SCOPE = (
    "Recall within scope (Swiss or remote postings): found / (found + not_collected + "
    "no_relevance + not_relevant + not_extracted + filtered), the postings the search is "
    "built to cover."
)
RECALL_ALL_WANTED = (
    "Recall of all wanted jobs at covered employers: the same base plus not_eligible, relevant "
    "postings outside Switzerland that are not remote, which extraction skips by design."
)


def _recall_line(label: str, counts: Mapping[str, int], all_wanted: bool) -> str:
    k, n, lo, hi = recall(counts, all_wanted)
    pct = f"{100 * k / n:.1f}%" if n else "n/a"
    return f"{label}: {k} / {n} = {pct} (95% Wilson interval {100 * lo:.1f}% to {100 * hi:.1f}%)"


def _recall_lines(counts: Mapping[str, int]) -> list[str]:
    return [
        _recall_line("Recall within scope (Swiss or remote postings)", counts, False),
        _recall_line("Recall of all wanted jobs at covered employers", counts, True),
        f"Excluded from both bases: not_covered {counts['not_covered']}, "
        f"unknown_before_collection {counts['unknown_before_collection']}.",
    ]


def _summary(counts: Mapping[str, int], no_date: int, before_since: int) -> str:
    lines = ["| Outcome | Applications |", "|---|---|"]
    lines += [f"| {o} | {counts[o]} |" for o in OUTCOMES]
    lines.append(f"| no date (skipped) | {no_date} |")
    if before_since:
        lines.append(f"| before --since (skipped) | {before_since} |")
    return "\n".join([*lines, "", *_recall_lines(counts)])


def _outcome_rows(outcomes: list[Outcome]) -> list[list]:
    rows = []
    for o in sorted(outcomes, key=lambda o: o.app.key):
        timing = o.timing_text
        posting = o.posting
        rows.append(
            [
                o.app.key,
                o.app.date,
                o.employer.name if o.employer else "",
                posting.id if posting else "",
                posting.title if posting else "",
                o.method,
                ", ".join(x for x in (o.detail, timing) if x),
            ]
        )
    return rows


def render_report(
    outcomes: list[Outcome],
    counts: Mapping[str, int],
    no_date: list[str],
    before_since: int,
    dates_without_folder: int,
    since: date | None,
    today: date,
) -> str:
    lines = [
        f"# Search recall check ({today.isoformat()})",
        "",
        "Each application is replayed against the board postings in the database: the first "
        "step of the pipeline it fails is its outcome. `not_covered` (no board employer "
        "matches: a source gap) and `unknown_before_collection` (the application predates our "
        "first successful read of the employer's board, so the posting may have closed before "
        "we started) are left out of both recall bases.",
        "",
        "- " + RECALL_WITHIN_SCOPE,
        "- " + RECALL_ALL_WANTED,
        "",
        f"Applications after --since {since}." if since else "No --since filter.",
        f"Dates in the dates file without an application folder: {dates_without_folder}.",
        "",
        "## Summary",
        "",
        _summary(counts, len(no_date), before_since),
        "",
        "## Link source",
        "",
    ]
    sources = ["linkedin", "hiring.cafe", "ats-direct", "other", "none"]
    by_source = {s: [o for o in outcomes if link_source(o.app.url) == s] for s in sources}
    rows = []
    for s in sources:
        group = Counter(o.outcome for o in by_source[s])
        rows.append([s, len(by_source[s]), group["found"], sum(group[o] for o in RECALL_BASE)])
    lines += _table(["Link source", "Applications", "Found", "In recall base"], rows)
    for outcome in OUTCOMES[::-1]:
        group = [o for o in outcomes if o.outcome == outcome]
        if outcome == "not_covered" or not group:
            continue
        lines += [f"## {outcome} ({len(group)})", ""]
        lines += _table(
            ["Folder", "Date", "Employer", "Posting", "Title", "Match", "Detail"],
            _outcome_rows(group),
        )
    if no_date:
        lines += [f"## No date ({len(no_date)})", "", *[f"- {key}" for key in sorted(no_date)], ""]
    uncovered = Counter(o.app.company for o in outcomes if o.outcome == "not_covered")
    lines += [f"## not_covered companies ({sum(uncovered.values())} applications)", ""]
    lines += _table(
        ["Company", "Applications"],
        [[c, n] for c, n in sorted(uncovered.items(), key=lambda x: x[0].casefold())],
    )
    return "\n".join(lines)


def run(
    conn: psycopg.Connection,
    applications: Path,
    dates_file: Path,
    since: date | None = None,
    out: Path | None = None,
    today: date | None = None,
) -> EvalRun:
    """Replay the applications and write the Markdown report (to `out`, else the default)."""
    today = today or date.today()
    try:
        dates = json.loads(dates_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"dates file {dates_file} is not valid JSON: {error}") from error
    if not isinstance(dates, dict):
        raise ValueError(f"dates file {dates_file} must be a JSON object of folder key to date")
    apps = load_applications(applications, dates)
    dates_without_folder = len(set(dates) - {a.key for a in apps})
    no_date = [a for a in apps if a.date is None]
    dated = [a for a in apps if a.date is not None]
    kept = [a for a in dated if since is None or a.date >= since]
    outcomes = evaluate(conn, kept)
    counts = {o: 0 for o in OUTCOMES} | Counter(x.outcome for x in outcomes)
    before_since = len(dated) - len(kept)
    report = render_report(
        outcomes, counts, [a.key for a in no_date], before_since, dates_without_folder, since, today
    )
    path = out or default_output(today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report + "\n", encoding="utf-8")
    summary = _summary(counts, len(no_date), before_since)
    return EvalRun(
        outcomes, counts, len(no_date), before_since, dates_without_folder, path, summary, report
    )
