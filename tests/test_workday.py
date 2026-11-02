"""Workday sitemap + JSON-LD reader, with invented sitemaps and pages. No network, no sleeping."""

import json

import httpx

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
    a = u("Netstal", "A_R-1")  # a small place the location list does not know
    site = Site([(a, None)], {a: page(locality="Netstal")})
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
