"""Workday sitemap + JSON-LD reader, with invented sitemaps and pages. No network, no sleeping."""

import json

import httpx
import pytest

from jobfacts.collect.boards import workday
from jobfacts.collect.posting import BoardRead
from jobfacts.collect.run import collect

BASE = "https://acme.wd3.myworkdayjobs.com"
SITE = f"{BASE}/Careers"
EXTRA = {"wd": "wd3", "site": "Careers"}

# urllib.robotparser uses the first matching rule, so the specific Disallow comes first
ROBOTS = "User-agent: *\nDisallow: /Careers/job/Basel/Secret-Role_R-9\nAllow: /Careers/\n"


def sitemap(entries):
    rows = ""
    for url, lastmod in entries:
        stamp = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        rows += (
            f'<url><loc>{url}</loc><xhtml:link rel="alternate" hreflang="de-DE" href="{url}"/>'
            f"{stamp}</url>"
        )
    return (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:xhtml="http://www.w3.org/TR/xhtml11/xhtml11_schema.html">' + rows + "</urlset>"
    )


def page(title="Data Engineer", locality="Bern", telecommute=False, description="<p>Build.</p>"):
    job = {
        "@context": "http://schema.org",
        "@type": "JobPosting",
        "title": title,
        "description": description,
        "datePosted": "2026-10-30",
        "identifier": {"@type": "PropertyValue", "value": "R-1"},
        "jobLocation": {
            "@type": "Place",
            "address": {
                "@type": "PostalAddress",
                "addressLocality": locality,
                "addressCountry": "Switzerland",
            },
        },
    }
    if telecommute:
        job["jobLocationType"] = "TELECOMMUTE"
    return f'<html><script type="application/ld+json">{json.dumps(job)}</script></html>'


def u(place, slug):
    return f"{SITE}/job/{place}/{slug}"


class Site:
    """A fake Workday host: serves robots, sitemap and pages, and logs every request."""

    def __init__(self, entries, pages, robots=ROBOTS, robots_status=200):
        self.entries, self.pages, self.robots, self.robots_status = (
            entries,
            pages,
            robots,
            robots_status,
        )
        self.requests = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        if url.endswith("/robots.txt"):
            return httpx.Response(self.robots_status, text=self.robots)
        if url.endswith("/siteMap.xml"):
            return httpx.Response(200, text=sitemap(self.entries))
        if url in self.pages:
            result = self.pages[url]
            return (
                httpx.Response(result, text="")
                if isinstance(result, int)
                else httpx.Response(200, text=result)
            )
        return httpx.Response(404)

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))


class FakeClock:
    def __init__(self):
        self.now, self.slept = 100.0, []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def limiter():
    return workday.RateLimiter(1.0, clock=(c := FakeClock()).clock, sleep=c.sleep)


# --- parsing ---------------------------------------------------------------------------------


def test_parse_sitemap_keeps_job_urls_and_lastmod():
    xml = sitemap([(u("Bern", "Data-Engineer_R-1"), "2026-10-01"), (f"{SITE}null", None)])
    assert workday.parse_sitemap(xml) == [(u("Bern", "Data-Engineer_R-1"), "2026-10-01")]


def test_job_id_and_location_text():
    url = u("USA---NY---New-York-City", "Senior-Analyst--Risk_R-0006447-1")
    assert workday.job_id_from_url(url) == "R-0006447-1"
    assert workday.url_location_text(url) == "USA NY New York City"


def test_parse_posting_maps_fields():
    html = page(title="Data Engineer", locality="Bern", description="<p>One</p><p>Two</p>")
    p = workday.parse_posting(html, u("Bern", "Data-Engineer_R-1"), "2026-10-01")
    assert (p.source_job_id, p.title, p.source_version) == ("R-1", "Data Engineer", "2026-10-01")
    assert p.location_raw == "Bern, Switzerland"
    assert p.description_text == "One\n\nTwo"
    assert p.posted_at.year == 2026


def test_parse_posting_decodes_entities_in_title_and_place():
    html = page(title="Research &amp; Innovation (R&amp;I)&nbsp;Lead", locality="Bern &amp; Co")
    p = workday.parse_posting(html, u("Bern", "Role_R-1"))
    assert p.title == "Research & Innovation (R&I) Lead"
    assert p.location_raw == "Bern & Co, Switzerland"
    twice = workday.parse_posting(page(title="Project &amp;amp; Facility"), u("Bern", "Role_R-1"))
    assert twice.title == "Project & Facility"
    assert workday.parse_posting(page(title="A &lt; B"), u("Bern", "Role_R-1")).title == "A < B"


def test_parse_posting_remote_and_workday_code_locality():
    html = page(locality="Wor-Ati6", telecommute=True)
    p = workday.parse_posting(html, u("Zurich", "Role_R-2"))
    assert p.location_raw == "Zurich, Switzerland / Remote"


def test_parse_posting_without_json_ld_raises():
    try:
        workday.parse_posting("<html>closed</html>", u("Bern", "Role_R-3"))
    except ValueError as error:
        assert "JSON-LD" in str(error)
    else:
        raise AssertionError("expected ValueError")


# --- pre-filter ------------------------------------------------------------------------------


def test_prefilter_keeps_swiss_and_remote_skips_others():
    keep = ["Bern", "Zurich-Switzerland", "Basel-Kaiseraugst", "Germany-Remote", "Home-Office"]
    skip = ["Singapore", "USA---NY---New-York-City", "Barcelona-Gran-Via", "London-United-Kingdom"]
    for place in keep:
        assert workday.url_is_candidate(u(place, "Role_R-1")), place
    for place in skip:
        assert not workday.url_is_candidate(u(place, "Role_R-1")), place


def test_prefilter_does_not_veto_foreign_looking_slugs():
    # a slug has no structure; eligibility checks the structured location later
    keep = [
        "Manufacture-VAC-LE-BRASSUS-Route-de-France-100B-CHLE0003",
        "Zurich---London-UK",
        "Geneva-Paris-France",
    ]
    for place in keep:
        assert workday.url_is_candidate(u(place, "Role_R-1")), place


# --- rate limiter ----------------------------------------------------------------------------


def test_rate_limiter_spaces_requests_per_host_without_real_sleep():
    c = FakeClock()
    lim = workday.RateLimiter(1.0, clock=c.clock, sleep=c.sleep)
    lim.wait("a.test")  # first request goes at once
    lim.wait("a.test")
    lim.wait("a.test")
    lim.wait("b.test")  # another host is independent
    assert c.slept == [1.0, 1.0]
    c.now += 5  # a pause longer than the interval needs no waiting
    lim.wait("a.test")
    assert c.slept == [1.0, 1.0]


def test_fetch_waits_between_requests():
    c = FakeClock()
    site = Site(
        [(u("Bern", "A_R-1"), None), (u("Zurich", "B_R-2"), None)],
        {u("Bern", "A_R-1"): page(), u("Zurich", "B_R-2"): page("B")},
    )
    workday.fetch(site.client(), "acme", EXTRA, None, workday.RateLimiter(1.0, c.clock, c.sleep))
    assert len(site.requests) == 4  # robots, sitemap, two pages
    assert len(c.slept) == len(site.requests) - 1


# --- fetch: robots, incremental, filtering ----------------------------------------------------


def test_fetch_reads_only_candidates_and_reports():
    site = Site(
        [(u("Bern", "A_R-1"), None), (u("Singapore", "B_R-2"), None)],
        {u("Bern", "A_R-1"): page()},
    )
    read = workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    assert [p.source_job_id for p in read.postings] == ["R-1"]
    assert read.seen_ids == {"R-1", "R-2"}
    assert u("Singapore", "B_R-2") not in site.requests
    assert "prefilter kept 1 / skipped 1" in read.notes


def test_robots_disallow_is_respected_and_recorded():
    secret = u("Basel", "Secret-Role_R-9")
    site = Site(
        [(secret, None), (u("Bern", "A_R-1"), None)], {secret: page(), u("Bern", "A_R-1"): page()}
    )
    read = workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    assert secret not in site.requests
    assert [p.source_job_id for p in read.postings] == ["R-1"]
    assert "robots disallowed 1" in read.notes
    assert "R-9" in read.seen_ids  # still listed, so it is not closed


def test_robots_disallowing_the_sitemap_fails_the_read():
    site = Site([], {}, robots="User-agent: *\nDisallow: /\n")
    try:
        workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    except PermissionError:
        pass
    else:
        raise AssertionError("expected PermissionError")
    assert site.requests == [f"{BASE}/robots.txt"]


def test_robots_error_fails_the_read():
    site = Site([], {}, robots_status=500)
    try:
        workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    except httpx.HTTPStatusError:
        pass
    else:
        raise AssertionError("expected HTTPStatusError")


def test_incremental_fetches_only_new_or_changed_lastmod():
    a, b, c = u("Bern", "A_R-1"), u("Bern", "B_R-2"), u("Bern", "C_R-3")
    site = Site(
        [(a, "2026-10-01"), (b, "2026-10-09"), (c, "2026-10-05")],
        {a: page("A"), b: page("B"), c: page("C")},
    )
    known = {"R-1": "2026-10-01", "R-2": "2026-10-01"}  # R-3 is new, R-2 changed, R-1 same
    read = workday.fetch(site.client(), "acme", EXTRA, known, limiter())
    assert sorted(p.source_job_id for p in read.postings) == ["R-2", "R-3"]
    assert a not in site.requests
    assert read.seen_ids == {"R-1", "R-2", "R-3"}
    assert "unchanged 1" in read.notes


def test_known_posting_without_lastmod_is_not_refetched():
    a = u("Bern", "A_R-1")
    site = Site([(a, None)], {a: page()})
    read = workday.fetch(site.client(), "acme", EXTRA, {"R-1": None}, limiter())
    assert read.postings == [] and read.seen_ids == {"R-1"}


def test_page_failures_are_counted_and_do_not_lose_the_rest():
    a, b = u("Bern", "A_R-1"), u("Zurich", "B_R-2")
    site = Site([(a, None), (b, None)], {a: 404, b: page("B")})
    read = workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    assert [p.source_job_id for p in read.postings] == ["R-2"]
    assert "failures 1 (HTTP 404 x1)" in read.notes


def test_too_many_requests_stops_the_read_early():
    a, b, c = u("Bern", "A_R-1"), u("Zurich", "B_R-2"), u("Geneva", "C_R-3")
    site = Site([(a, None), (b, None), (c, None)], {a: page(), b: 403, c: page()})
    read = workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    assert [p.source_job_id for p in read.postings] == ["R-1"]
    assert c not in site.requests
    assert "stopped early" in read.notes


# --- closing via the sitemap ------------------------------------------------------------------


def run_with(conn, tmp_path, reads):
    path = tmp_path / "e.toml"
    path.write_text(
        '[[employer]]\nname="Acme"\nats="workday"\nboard_id="acme"\nwd="wd3"\nsite="Careers"\n'
    )
    seen_known = []

    def fake(client, board_id, extra, known):
        seen_known.append(dict(known))
        return reads.pop(0)

    fake.incremental = True
    summary = collect(conn, fetchers={"workday": fake}, employers_file=path)
    return summary, seen_known


def posting(job_id, version="v1"):
    return workday.Posting(job_id, f"{SITE}/job/Bern/X_{job_id}", "T", "Bern", "d", None, version)


def closed(conn, job_id):
    row = conn.execute("SELECT closed_at FROM postings WHERE source_job_id = %s", (job_id,))
    return row.fetchone()[0] is not None


def test_sitemap_absence_closes_and_presence_keeps_open(conn, tmp_path):
    reads = [
        BoardRead([posting("R-1"), posting("R-2")], {"R-1", "R-2", "R-3"}),
        BoardRead([], {"R-1", "R-3"}),  # R-1 unchanged (not fetched), R-2 left the sitemap
    ]
    s1, _ = run_with(conn, tmp_path, reads)
    assert s1.counts.new == 2
    s2, known = run_with(conn, tmp_path, reads)
    assert known == [{"R-1": "v1", "R-2": "v1"}]
    assert (s2.counts.closed, closed(conn, "R-1"), closed(conn, "R-2")) == (1, False, True)


def test_empty_sitemap_counts_as_failed_read(conn, tmp_path):
    reads = [BoardRead([posting("R-1")], {"R-1"}), BoardRead([], set())]
    run_with(conn, tmp_path, reads)
    s2, _ = run_with(conn, tmp_path, reads)
    assert (s2.boards_failed, s2.counts.closed, closed(conn, "R-1")) == (1, 0, False)


def test_changed_version_updates_stored_posting(conn, tmp_path):
    reads = [BoardRead([posting("R-1", "v1")], {"R-1"}), BoardRead([posting("R-1", "v2")], {"R-1"})]
    run_with(conn, tmp_path, reads)
    run_with(conn, tmp_path, reads)
    row = conn.execute("SELECT source_version FROM postings WHERE source_job_id='R-1'")
    assert row.fetchone()[0] == "v2"


# --- sitemap cap, prefilter option, TELECOMMUTE on everything ---------------------------------


def test_capped_sitemap_does_not_close(conn, tmp_path):
    reads = [
        BoardRead([posting("R-1"), posting("R-2")], {"R-1", "R-2"}),
        BoardRead([], {"R-1"}, close_missing=False),  # R-2 fell out of a 100-entry window
    ]
    run_with(conn, tmp_path, reads)
    s2, _ = run_with(conn, tmp_path, reads)
    assert (s2.counts.closed, closed(conn, "R-2")) == (0, False)


def test_fetch_flags_a_capped_sitemap():
    entries = [(u("Bern", f"Role_R-{i}"), None) for i in range(workday.SITEMAP_CAP)]
    site = Site(entries, {})
    read = workday.fetch(
        site.client(), "acme", EXTRA, {f"R-{i}": None for i in range(100)}, limiter()
    )
    assert read.close_missing is False and "capped" in read.notes


def test_prefilter_can_be_switched_off():
    a = u("Hintertupfingen", "A_R-1")  # a place the register does not know
    site = Site([(a, None)], {a: page(locality="Hintertupfingen")})
    assert workday.fetch(site.client(), "acme", EXTRA, None, limiter()).postings == []
    read = workday.fetch(site.client(), "acme", {**EXTRA, "prefilter": False}, None, limiter())
    assert [p.source_job_id for p in read.postings] == ["R-1"]


def test_remote_label_dropped_when_every_posting_has_it():
    places = ["Bern", "Zurich", "Basel", "Geneva", "Lausanne"]
    urls = [u(p, f"Role_R-{i}") for i, p in enumerate(places)]
    site = Site([(x, None) for x in urls], {x: page(telecommute=True) for x in urls})
    read = workday.fetch(site.client(), "acme", EXTRA, None, limiter())
    assert all("Remote" not in p.location_raw for p in read.postings)
    one = Site([(urls[0], None)], {urls[0]: page(telecommute=True)})
    assert (
        "Remote"
        in workday.fetch(one.client(), "acme", EXTRA, None, limiter()).postings[0].location_raw
    )


# --- extra sites: one posting per requisition across sites -------------------------------------

TECH = f"{BASE}/Tech"
MERGE = {**EXTRA, "extra_sites": ["Tech"]}


class TwoSites(Site):
    """Careers and Tech sitemaps; `tech` may be a status code for a failing extra site."""

    def __init__(self, main, tech, pages, **kw):
        super().__init__(main, pages, **kw)
        self.tech = tech

    def handler(self, request):
        url = str(request.url)
        if url.endswith("/Tech/siteMap.xml"):
            self.requests.append(url)
            if isinstance(self.tech, int):
                return httpx.Response(self.tech, text="")
            return httpx.Response(200, text=sitemap(self.tech))
        return super().handler(request)


def tu(slug):
    return f"{TECH}/job/Bern/{slug}"


def test_requisition_key_strips_only_the_site_suffix():
    assert workday.requisition_key("r-17167-2") == "r-17167"
    assert workday.requisition_key("R-0006447-1") == "R-0006447"
    assert workday.requisition_key("r-19892") == "r-19892"
    assert workday.requisition_key("R-0006447") == "R-0006447"


def test_same_requisition_on_two_sites_is_one_posting_with_main_url():
    main = u("Bern", "Data-Scientist_r-17167-2")
    tech_dup, tech_only = tu("Data-Scientist_r-17167-4"), tu("Architect_r-18971-1")
    site = TwoSites(
        [(main, None)],
        [(tech_dup, None), (tech_only, None)],
        {main: page("Data Scientist"), tech_only: page("Architect")},
    )
    read = workday.fetch(site.client(), "acme", MERGE, None, limiter())
    got = {p.source_job_id: (p.url, p.title) for p in read.postings}
    assert got == {"r-17167": (main, "Data Scientist"), "r-18971": (tech_only, "Architect")}
    assert read.seen_ids == {"r-17167", "r-18971"}
    assert tech_dup not in site.requests  # the duplicate page is never fetched
    assert read.close_missing is True


def test_known_merged_id_is_not_refetched():
    main = u("Bern", "X_r-1-2")
    site = TwoSites([(main, None)], [(tu("X_r-1-4"), None)], {})
    read = workday.fetch(site.client(), "acme", MERGE, {"r-1": None}, limiter())
    assert read.postings == [] and read.seen_ids == {"r-1"}


def test_any_capped_site_means_nothing_is_closed():
    main = [(u("Bern", f"R_r-{i}-2"), None) for i in range(workday.SITEMAP_CAP)]
    site = TwoSites(main, [(tu("Z_r-999-1"), None)], {})
    read = workday.fetch(site.client(), "acme", MERGE, None, limiter())
    assert read.close_missing is False and "Careers" in read.notes
    # and the reverse: the extra site capped, main not
    tech = [(tu(f"R_r-{i}-1"), None) for i in range(workday.SITEMAP_CAP)]
    read = workday.fetch(
        TwoSites([(u("Bern", "A_r-1-2"), None)], tech, {}).client(), "acme", MERGE, None, limiter()
    )
    assert read.close_missing is False


def test_uncapped_sites_close_what_neither_lists():
    site = TwoSites([(u("Bern", "A_r-1-2"), None)], [(tu("B_r-2-1"), None)], {})
    assert workday.fetch(site.client(), "acme", MERGE, None, limiter()).close_missing is True


def test_unreadable_extra_site_reads_the_rest_and_closes_nothing():
    main = u("Bern", "A_r-1-2")
    site = TwoSites([(main, None)], 500, {main: page("A")})
    read = workday.fetch(site.client(), "acme", MERGE, None, limiter())
    assert [p.source_job_id for p in read.postings] == ["r-1"]
    assert read.close_missing is False and "Tech not read" in read.notes


def test_extra_site_disallowed_by_robots_is_not_requested():
    robots = "User-agent: *\nDisallow: /Tech/\nAllow: /Careers/\n"
    main = u("Bern", "A_r-1-2")
    site = TwoSites([(main, None)], [(tu("B_r-2-1"), None)], {main: page("A")}, robots=robots)
    read = workday.fetch(site.client(), "acme", MERGE, None, limiter())
    assert not any("/Tech/" in r for r in site.requests)
    assert read.close_missing is False and read.seen_ids == {"r-1"}


def test_without_extra_sites_ids_keep_their_suffix():
    main = u("Bern", "A_r-1-2")
    read = workday.fetch(
        Site([(main, None)], {main: page()}).client(), "acme", EXTRA, None, limiter()
    )
    assert read.postings[0].source_job_id == "r-1-2"


def test_main_site_failure_still_fails_the_read():
    site = TwoSites([], [], {}, robots="User-agent: *\nDisallow: /Careers/\n")
    with pytest.raises(PermissionError):
        workday.fetch(site.client(), "acme", MERGE, None, limiter())


# --- existing rows keep their row id when an employer starts merging ---------------------------


def test_existing_suffixed_rows_are_rekeyed_not_duplicated(conn, tmp_path):
    path = tmp_path / "e.toml"
    head = '[[employer]]\nname="Acme"\nats="workday"\nboard_id="acme"\nwd="wd3"\nsite="Careers"\n'
    path.write_text(head)  # before: no extra sites, ids keep the suffix

    def fake(client, board_id, extra, known):
        return BoardRead([posting("r-17167-2"), posting("r-5")], {"r-17167-2", "r-5"})

    fake.incremental = True
    collect(conn, fetchers={"workday": fake}, employers_file=path)
    before = conn.execute("SELECT id, source_job_id FROM postings ORDER BY 2").fetchall()

    path.write_text(head + 'extra_sites = ["Tech"]\n')
    seen = []

    def merged(client, board_id, extra, known):
        seen.append(dict(known))
        return BoardRead([], {"r-17167", "r-5", "r-18971"}, close_missing=False)

    merged.incremental = True
    collect(conn, fetchers={"workday": merged}, employers_file=path)
    collect(conn, fetchers={"workday": merged}, employers_file=path)  # idempotent
    after = conn.execute("SELECT id, source_job_id, closed_at FROM postings ORDER BY 2").fetchall()
    assert seen[0] == {"r-17167": "v1", "r-5": "v1"}
    assert [(r[0], r[1]) for r in after] == [(before[0][0], "r-17167"), (before[1][0], "r-5")]
    assert all(r[2] is None for r in after)


# --- capped lists: stored postings missing from them get their own page checked ---------------


class GoneSite(Site):
    """Capped sitemap; own-page answers: 404, redirect to the site home, 200 page, 500."""

    def handler(self, request):
        url = str(request.url)
        if url.endswith("/siteMap.xml") or url.endswith("/robots.txt"):
            return super().handler(request)
        self.requests.append(url)
        answer = self.pages.get(url)
        if isinstance(answer, int):
            return httpx.Response(answer, text="")
        if isinstance(answer, str) and answer.startswith("->"):
            return httpx.Response(302, headers={"location": answer[2:]})
        return httpx.Response(200, text=answer or "")


def capped_entries():
    return [(u("Bern", f"Role_R-{i}"), None) for i in range(workday.SITEMAP_CAP)]


def test_capped_read_closes_only_postings_whose_page_is_clearly_gone():
    listed = {f"R-{i}": None for i in range(workday.SITEMAP_CAP)}
    urls = {
        "R-404": u("Bern", "A_R-404"),
        "R-redir": u("Bern", "B_R-redir"),
        "R-open": u("Bern", "C_R-open"),
        "R-500": u("Bern", "D_R-500"),
        "R-shell": u("Bern", "E_R-shell"),
        "R-1": u("Bern", "Role_R-1"),  # listed: never checked
    }
    pages = {
        urls["R-404"]: 404,
        urls["R-redir"]: f"->{SITE}/",
        urls["R-open"]: page(),
        urls["R-500"]: 500,
        urls["R-shell"]: "<html>loading</html>",
    }
    site = GoneSite(capped_entries(), pages)
    read = workday.fetch(
        site.client(), "acme", EXTRA, {**listed, **{k: None for k in urls}}, limiter(), urls
    )
    assert read.close_missing is False
    assert read.close_ids == {"R-404", "R-redir"}
    assert "R-open" in read.seen_ids and "R-500" not in read.seen_ids
    assert "checked 5 postings" in read.notes and "closed 2" in read.notes
    assert urls["R-1"] not in site.requests


def test_gone_checks_respect_robots_cap_and_stop_signal():
    robots = "User-agent: *\nDisallow: /Careers/job/Bern/Hidden_R-h\nAllow: /Careers/\n"
    urls = {"R-h": u("Bern", "Hidden_R-h")}
    site = GoneSite(capped_entries(), {}, robots=robots)
    read = workday.fetch(site.client(), "acme", EXTRA, {"R-h": None}, limiter(), urls)
    assert read.close_ids == set() and urls["R-h"] not in site.requests
    # 429 ends the checks and closes nothing
    urls = {f"X-{i}": u("Bern", f"X_X-{i}") for i in range(5)}
    pages = {urls["X-0"]: 429, urls["X-1"]: 404}
    site = GoneSite(capped_entries(), pages)
    read = workday.fetch(site.client(), "acme", EXTRA, dict.fromkeys(urls), limiter(), urls)
    assert read.close_ids == set() and urls["X-1"] not in site.requests
    # the per-run cap
    many = {f"Y-{i}": u("Bern", f"Y_Y-{i}") for i in range(workday.MAX_GONE_CHECKS + 20)}
    site = GoneSite(capped_entries(), dict.fromkeys(many.values(), 404))
    read = workday.fetch(site.client(), "acme", EXTRA, dict.fromkeys(many), limiter(), many)
    assert len(read.close_ids) == workday.MAX_GONE_CHECKS


def test_uncapped_read_makes_no_page_checks():
    site = GoneSite([(u("Bern", "A_R-1"), None)], {})
    urls = {"R-2": u("Bern", "B_R-2")}
    read = workday.fetch(site.client(), "acme", EXTRA, {"R-1": None, "R-2": None}, limiter(), urls)
    assert read.close_ids == set() and read.close_missing is True
    assert urls["R-2"] not in site.requests


def test_close_ids_close_postings_in_the_database(conn, tmp_path):
    reads = [
        BoardRead([posting("R-1"), posting("R-2")], {"R-1", "R-2"}),
        BoardRead([], {"R-1"}, close_missing=False, close_ids={"R-2"}),
    ]
    run_with(conn, tmp_path, reads)
    s2, _ = run_with(conn, tmp_path, reads)
    assert (s2.counts.closed, closed(conn, "R-1"), closed(conn, "R-2")) == (1, False, True)


def test_collect_passes_stored_urls_to_fetchers_that_check_gone(conn, tmp_path):
    path = tmp_path / "e.toml"
    path.write_text(
        '[[employer]]\nname="Acme"\nats="workday"\nboard_id="acme"\nwd="wd3"\nsite="Careers"\n'
    )
    got = []

    def fake(client, board_id, extra, known, known_urls=None):
        got.append(known_urls)
        return BoardRead([posting("R-1")], {"R-1"})

    fake.incremental = True
    fake.checks_gone = True
    collect(conn, fetchers={"workday": fake}, employers_file=path)
    collect(conn, fetchers={"workday": fake}, employers_file=path)
    assert got == [{}, {"R-1": f"{SITE}/job/Bern/X_R-1"}]


# --- merging by requisition keeps the Swiss URL ---------------------------------------------


def test_merge_prefers_the_swiss_url_over_a_non_swiss_main_url():
    main = u("London", "Data-Scientist_r-17167-2")
    tech = tu("Data-Scientist_r-17167-4")  # Bern in the Tech helper
    site = TwoSites([(main, None)], [(tech, None)], {tech: page("Data Scientist")})
    read = workday.fetch(site.client(), "acme", MERGE, None, limiter())
    assert [(p.source_job_id, p.url) for p in read.postings] == [("r-17167", tech)]
    assert main not in site.requests


# --- re-keying is deterministic ----------------------------------------------------------------


def test_rekey_prefers_open_then_most_recently_seen(conn):
    from jobfacts.collect import store

    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('E','workday','e') RETURNING id"
    ).fetchone()[0]
    for job_id, closed_at, seen in [
        ("r-1-1", "now()", "now()"),  # closed, seen most recently
        ("r-1-2", "NULL", "now() - interval '2 days'"),  # open
        ("r-2-1", "now()", "now() - interval '3 days'"),
        ("r-2-3", "now()", "now() - interval '1 day'"),  # both closed: the fresher wins
    ]:
        conn.execute(
            "INSERT INTO postings (employer_id, source_job_id, url, title, content_hash, "
            f"closed_at, last_seen_at) VALUES (%s,%s,'u','t','h',{closed_at},{seen})",
            (emp, job_id),
        )
    conn.commit()
    store.rekey_source_ids(conn, emp, workday.requisition_key)
    rows = dict(conn.execute("SELECT source_job_id, url FROM postings").fetchall())
    ids = {r[0] for r in conn.execute("SELECT source_job_id FROM postings")}
    assert "r-1" in ids and "r-2" in ids and rows
    winner = conn.execute(
        "SELECT closed_at IS NULL FROM postings WHERE source_job_id = 'r-1'"
    ).fetchone()[0]
    assert winner is True
    fresher = conn.execute("SELECT 1 FROM postings WHERE source_job_id='r-2-1'").fetchone()
    assert fresher is not None  # the older closed row kept its old key


def test_page_status_follows_a_redirect_to_a_generic_page():
    gone, home = u("Bern", "A_R-7"), f"{SITE}/"

    def handler(request):
        if str(request.url) == gone:
            return httpx.Response(302, headers={"location": home})
        return httpx.Response(200, text="<html>search</html>")

    client = httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True)
    lim = workday.LimitedClient(client, limiter())
    robots = workday.RobotFileParser()
    robots.parse([])
    assert workday.page_status(lim, robots, gone) == "gone"


def _status_of(text: str) -> str:
    url = u("Bern", "A_R-8")
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, text=text)))
    robots = workday.RobotFileParser()
    robots.parse([])
    return workday.page_status(workday.LimitedClient(client, limiter()), robots, url)


def test_page_status_reads_an_empty_og_title_shell_as_gone():
    # Live Workday (3 Nov 2026): an unknown job is a 200 shell with an empty og:title.
    shell = '<html><meta name="title" property="og:title"><meta property="og:description"></html>'
    assert _status_of(shell) == "gone_shell"
    titled = '<html><meta name="title" property="og:title" content="Data Scientist"></html>'
    assert _status_of(titled) == "unknown"  # titled page without JSON-LD: cannot tell
    assert _status_of("<html>loading</html>") == "unknown"


def test_capped_read_closes_nothing_when_every_checked_page_is_an_empty_shell():
    listed = {f"R-{i}": None for i in range(workday.SITEMAP_CAP)}
    urls = {f"R-x{i}": u("Bern", f"X_R-x{i}") for i in range(6)}
    shell = '<html><meta name="title" property="og:title"></html>'
    site = GoneSite(capped_entries(), {url: shell for url in urls.values()})
    read = workday.fetch(
        site.client(), "acme", EXTRA, {**listed, **{k: None for k in urls}}, limiter(), urls
    )
    assert read.close_ids == set()
    assert "suspicious" in read.notes
