import base64
import contextlib
import json
import re

import psycopg
import pytest
from fastapi.testclient import TestClient

from jobfacts import cli, search
from jobfacts.web import app as webapp
from jobfacts.web import params
from test_search import QueryEmbedder, Seed, cli_search, vectors

PASSWORD = "correct horse"  # invented for the tests


def basic(password=PASSWORD, user="anyone"):
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def broken_source():
    raise psycopg.OperationalError("connection refused")


def make_client(conn=None, embedder=None, **kw):
    source = broken_source if conn is None else (lambda: contextlib.nullcontext(conn))
    factory = (lambda alias: embedder) if embedder is not None else None
    app = webapp.create_app(
        connection_source=source, embedder_factory=factory, password=PASSWORD, **kw
    )
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def seed(conn):
    return Seed(conn)


# ---------- authentication and headers (no database needed) ----------


@pytest.mark.parametrize("path", ["/", "/api/search", "/static/style.css", "/nothing-here"])
def test_every_path_but_health_needs_the_password(path):
    client = make_client()
    r = client.get(path)
    assert r.status_code == 401 and r.headers["WWW-Authenticate"].startswith("Basic")
    assert client.get(path, headers=basic("wrong")).status_code == 401
    assert client.get(path, headers={"Authorization": "Basic !!notbase64"}).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer " + PASSWORD}).status_code == 401
    assert client.get(path, headers=basic(PASSWORD + "x")).status_code == 401


def test_password_check_handles_odd_input():
    assert webapp.password_ok(basic("pässword")["Authorization"], "pässword")
    assert not webapp.password_ok(basic("pässword")["Authorization"], "password")
    assert not webapp.password_ok(None, "x")
    no_colon = "Basic " + base64.b64encode(b"nopassword").decode()
    assert not webapp.password_ok(no_colon, "nopassword")


def test_an_app_needs_a_password_unless_auth_is_off():
    with pytest.raises(ValueError):
        webapp.create_app(connection_source=broken_source)
    open_app = webapp.create_app(connection_source=broken_source, auth=False)
    assert TestClient(open_app).get("/favicon.ico").status_code == 204


def test_security_headers_are_on_every_answer_including_401():
    client = make_client()
    for r in (client.get("/"), client.get("/health"), client.get("/", headers=basic())):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        csp = r.headers["Content-Security-Policy"]
        assert "default-src 'self'" in csp and "http" not in csp and "unsafe" not in csp


def test_health_needs_no_password_and_says_503_without_a_database():
    r = make_client().get("/health")
    assert r.status_code == 503
    body = r.json()
    assert body["status"] == "unavailable" and body["database"] is False
    assert "refused" not in r.text  # no internals


def test_generated_docs_pages_are_off():
    assert make_client().get("/docs", headers=basic()).status_code == 404


# ---------- /health ----------


@pytest.mark.db
def test_health_reports_the_state_without_a_password(conn, seed):
    seed.add(title="open one")
    seed.add(title="closed one", closed=True)
    conn.execute("INSERT INTO collection_runs (finished_at) VALUES ('2026-11-08T06:00:00+00')")
    r = make_client(conn).get("/health")
    assert r.status_code == 200
    assert r.json() == {
        "status": "ok",
        "database": True,
        "open_postings": 1,
        "last_collection_finished": "2026-11-08T06:00:00+00:00",
        "last_daily_run": None,
        "embedding_model": "mistral-embed",
    }


@pytest.mark.db
def test_health_without_any_collection_run(conn):
    assert make_client(conn).get("/health").json()["last_collection_finished"] is None


@pytest.mark.db
def test_health_includes_the_last_daily_run(conn):
    conn.execute(
        "INSERT INTO daily_runs (started_at, finished_at, status, cost_usd) VALUES "
        "('2026-11-07T06:30:00+00', '2026-11-07T06:40:00+00', 'failed', 1.5), "
        "('2026-11-08T06:30:00+00', '2026-11-08T06:41:00+00', 'ok', 0.2)"
    )
    run = make_client(conn).get("/health").json()["last_daily_run"]
    assert run == {"finished_at": "2026-11-08T06:41:00+00:00", "status": "ok"}
    conn.execute("INSERT INTO daily_runs (started_at) VALUES ('2026-11-09T06:30:00+00')")
    run = make_client(conn).get("/health").json()["last_daily_run"]
    assert run == {"finished_at": None, "status": "running"}


@pytest.mark.db
def test_health_and_runs_survive_a_database_without_the_table(conn):
    conn.execute("DROP TABLE daily_runs")
    # The fixture's search_path ends in `public` (for the pgvector type), where the real
    # database has its own daily_runs: drop `public` here so the query cannot fall through.
    conn.execute("SET search_path TO test_jobfacts")
    client = make_client(conn)
    assert client.get("/health").json()["last_daily_run"] is None
    r = client.get("/runs", headers=basic())
    assert r.status_code == 200 and "No daily run recorded yet" in r.text


# ---------- /runs ----------


def test_runs_needs_the_password():
    client = make_client()
    assert client.get("/runs").status_code == 401
    assert client.get("/runs", headers=basic("wrong")).status_code == 401


@pytest.mark.db
def test_runs_shows_the_rows_in_zurich_time_and_escapes_text(conn):
    conn.execute(
        "INSERT INTO daily_runs (started_at, finished_at, status, failed_step, boards_ok, "
        "boards_failed, new_postings, closed, relevant, extracted, embedded, errors, "
        "cost_usd, warnings) VALUES "
        "('2026-07-08T04:30:00+00', '2026-07-08T04:43:25+00', 'ok', NULL, 120, 2, 14, 3, 5, "
        "5, 14, 0, 0.0421, '<script>alert(1)</script>'), "
        "('2026-11-08T05:30:00+00', '2026-11-08T05:30:09+00', 'failed', 'collect<b>', "
        "NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL, NULL)"
    )
    page = make_client(conn).get("/runs", headers=basic())
    assert page.status_code == 200
    html = page.text
    assert "<script>alert" not in html and "&lt;script&gt;alert(1)" in html
    assert "collect<b>" not in html and "collect&lt;b&gt;" in html
    assert "2026-11-08 06:30" in html  # winter: UTC+1
    assert "2026-07-08 06:30" in html  # summer: UTC+2
    assert "13 min 25 s" in html and "9 s" in html
    assert "0.042" in html
    assert html.index("2026-11-08 06:30") < html.index("2026-07-08 06:30")  # newest first
    assert 'href="/runs"' in make_client(conn).get("/", headers=basic()).text


@pytest.mark.db
def test_runs_shows_at_most_30_rows(conn):
    conn.execute(
        "INSERT INTO daily_runs (started_at, status) "
        "SELECT '2026-01-01T06:00:00+00'::timestamptz + n * interval '1 day', 'ok' "
        "FROM generate_series(0, 39) AS n"
    )
    html = make_client(conn).get("/runs", headers=basic()).text
    assert html.count('<span class="status') == 30
    assert "2026-02-09" in html and "2026-01-10" not in html  # days 40 down to 11 kept


@pytest.mark.db
def test_runs_without_rows(conn):
    assert "No daily run recorded yet" in make_client(conn).get("/runs", headers=basic()).text


# ---------- /api/search ----------

FILTER_CASES = [
    ([], {}),
    (["--canton", "ZH"], {"canton": "ZH"}),
    (["--canton", "zh,VD", "--german-max", "none"], {"canton": "zh,VD", "german_max": "none"}),
    (["--remote", "--no-fixed-term"], {"remote": "1", "no_fixed_term": "true"}),
    (
        ["--work-mode", "hybrid,remote", "--seniority", "mid"],
        {"work_mode": "hybrid,remote", "seniority": "mid"},
    ),
    (["--new-days", "3", "--limit", "2"], {"new_days": "3", "limit": "2"}),
]


@pytest.mark.db
@pytest.mark.parametrize(("argv", "query"), FILTER_CASES)
def test_api_results_equal_the_command_line_json(conn, seed, monkeypatch, capsys, argv, query):
    seed.add(title="zh job", cantons=("ZH",), days_old=1)
    seed.add(title="vd job", cantons=("VD",), german="required_fluent", days_old=2)
    seed.add(title="remote job", work_mode="remote", remote_from_switzerland="allowed", cantons=())
    seed.add(title="mid hybrid", work_mode="hybrid", seniority="mid", cantons=("BE",), days_old=9)
    seed.add(title="fixed", contract_type="fixed_term", cantons=("ZH",), days_old=4)
    code, out, err = cli_search(
        conn, monkeypatch, capsys, QueryEmbedder(), "--mode", "words", *argv, "--json"
    )
    assert code == 0
    r = make_client(conn).get("/api/search", params={**query, "mode": "words"}, headers=basic())
    assert r.status_code == 200
    expected = json.loads(out)
    body = r.json()
    assert body["results"] == expected["results"] and body["total"] == expected["total"]
    assert body["mode_used"] == "words"
    assert [f"warning: {w}" for w in body["warnings"]] == err.splitlines()  # same warnings


@pytest.mark.db
def test_api_default_is_hybrid_and_the_query_is_embedded_once(conn, seed):
    near = seed.add(title="near", desc="horses")
    seed.add(title="words", desc="unicorn wrangler")
    vectors(conn, {near: [1.0, 0.0, 0.0]}, model="mistral-embed")
    fake = QueryEmbedder()
    client = make_client(conn, fake)
    r = client.get("/api/search", params={"query": "unicorn wrangler"}, headers=basic())
    body = r.json()
    assert r.status_code == 200 and body["mode_used"] == "hybrid"
    assert {x["title"] for x in body["results"]} == {"near", "words"}
    assert fake.queries == ["unicorn wrangler"]
    assert any("1 pool postings" in w for w in body["warnings"])  # "words" has no vector


@pytest.mark.db
def test_api_falls_back_to_words_with_a_warning_that_hides_the_error(conn, seed):
    seed.add(title="words", desc="unicorn wrangler")
    seed.add(title="other", desc="horses")
    fake = QueryEmbedder(error=KeyError("MISTRAL_API_KEY"))
    r = make_client(conn, fake).get("/api/search", params={"query": "unicorn"}, headers=basic())
    body = r.json()
    assert r.status_code == 200 and body["mode_used"] == "words"
    assert [x["title"] for x in body["results"]] == ["words"]
    assert body["warnings"][0] == "cannot embed the query; searching by words only"
    assert "MISTRAL" not in r.text


@pytest.mark.db
def test_api_without_a_query_is_the_plain_list_and_never_embeds(conn, seed):
    seed.add(title="a")
    fake = QueryEmbedder(error=RuntimeError("must not be called"))
    body = make_client(conn, fake).get("/api/search", headers=basic()).json()
    assert body["mode_used"] == "words" and fake.queries == [] and body["total"] == 1


@pytest.mark.db
def test_api_evidence_is_opt_in(conn, seed):
    seed.add(
        title="a",
        desc="Intro. Gute Deutschkenntnisse sind nötig. End.",
        german="required_intermediate",
    )
    client = make_client(conn)
    plain = client.get("/api/search", headers=basic()).json()["results"][0]
    full = client.get("/api/search", params={"evidence": "1"}, headers=basic()).json()["results"][0]
    assert "evidence" not in plain and full["evidence"]["german"] == [
        "Gute Deutschkenntnisse sind nötig."
    ]


@pytest.mark.db
@pytest.mark.parametrize(
    "query",
    [
        {"canton": "XX"},
        {"german_max": "perfect"},
        {"work_mode": "everywhere"},
        {"new_days": "-1"},
        {"new_days": "soon"},
        {"limit": "0"},
        {"limit": "101"},
        {"remote": "maybe"},
        {"cantons": "ZH"},  # typo: unknown parameters are errors, not silently ignored
        {"mode": "meaning"},  # explicit mode without a query
        {"mode": "teleport", "query": "x"},
        {"query": "x" * 501},
        {"new_days": "3000000"},
        {"new_days": "99999999999999999999"},
        {"query": "a\x00b", "mode": "words"},
        {"query": "a\x07b"},
    ],
)
def test_invalid_filters_are_400_with_the_message(conn, query):
    r = make_client(conn, QueryEmbedder()).get("/api/search", params=query, headers=basic())
    assert r.status_code == 400 and r.json()["error"]


@pytest.mark.db
def test_explicit_meaning_that_cannot_embed_is_502(conn):
    fake = QueryEmbedder(error=RuntimeError("down"))
    r = make_client(conn, fake).get(
        "/api/search", params={"query": "x", "mode": "meaning"}, headers=basic()
    )
    assert r.status_code == 502
    assert r.json() == {"error": "cannot embed the query"}  # no exception text
    assert "down" not in r.text


@pytest.mark.db
def test_a_control_character_in_the_query_is_rejected_before_embedding(conn):
    fake = QueryEmbedder()
    r = make_client(conn, fake).get("/api/search", params={"query": "a\x00b"}, headers=basic())
    assert r.status_code == 400 and fake.queries == []


def test_a_non_ascii_authorization_header_is_401_with_the_security_headers():
    r = make_client().get("/api/search", headers={"Authorization": b"Basic \xc3\xa9"})
    assert r.status_code == 401 and "default-src" in r.headers["Content-Security-Policy"]


def test_an_unexpected_error_is_a_generic_500_with_the_security_headers(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("secret internals")

    monkeypatch.setattr(params, "parse", boom)
    r = make_client().get("/api/search", headers=basic())
    assert r.status_code == 500 and "secret" not in r.text
    assert "default-src" in r.headers["Content-Security-Policy"]
    assert r.headers["X-Content-Type-Options"] == "nosniff"


def test_a_database_failure_during_a_search_is_503():
    r = make_client().get("/api/search", headers=basic())
    assert r.status_code == 503 and r.json() == {"error": "database unavailable"}


# ---------- the page ----------


@pytest.mark.db
def test_page_shows_results_facts_quotes_and_the_mode(conn, seed):
    seed.add(
        title="Data Engineer",
        desc="Intro. Gute Deutschkenntnisse sind nötig. End.",
        german="required_intermediate",
        places=("place:261:Zürich",),
        seniority="mid",
        contract_type="permanent",
    )
    client = make_client(conn, QueryEmbedder(error=RuntimeError("down")))
    r = client.get("/", params={"query": "data", "canton": ["ZH", "VD"]}, headers=basic())
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    html = r.text
    assert "Data Engineer" in html and "Acme" in html and "Zürich" in html
    assert "required intermediate" in html and "mid" in html and "permanent" in html
    assert "<details>" in html and "Gute Deutschkenntnisse sind nötig." in html
    assert "Search mode: <strong>words</strong>" in html
    assert "cannot embed the query; searching by words only" in html
    assert (
        'value="data"' in html and 'value="ZH" checked' in html and 'value="BE" checked' not in html
    )
    assert "<script" not in html  # works without JavaScript and ships none


@pytest.mark.db
def test_page_without_parameters_lists_the_newest_postings(conn, seed):
    seed.add(title="old", days_old=5)
    seed.add(title="new", days_old=0)
    html = make_client(conn).get("/", headers=basic()).text
    assert html.index(">new<") < html.index(">old<") or html.index("new</a>") < html.index(
        "old</a>"
    )


@pytest.mark.db
def test_a_title_with_a_script_tag_is_escaped(conn, seed):
    seed.add(title="<script>alert(1)</script> Engineer", desc="<img src=x onerror=alert(2)> text")
    seed.add(title="quote", desc="<b>bold</b> sentence here.", german="required_fluent")
    html = make_client(conn).get("/", params={"query": "text"}, headers=basic()).text
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    html = make_client(conn).get("/", headers=basic()).text
    assert "<script>alert(1)" not in html and "<img src=x" not in html
    assert "<b>bold</b>" not in html


@pytest.mark.db
def test_the_query_is_escaped_in_the_form(conn):
    html = (
        make_client(conn)
        .get("/", params={"query": '"><script>alert(3)</script>'}, headers=basic())
        .text
    )
    assert "<script>alert(3)" not in html and "&#34;&gt;&lt;script&gt;" in html


@pytest.mark.db
def test_only_http_and_https_urls_become_links(conn, seed):
    seed.add(title="js", desc="x")
    seed.add(title="web", desc="x")
    seed.add(title="tabbed", desc="x")
    conn.execute("UPDATE postings SET url = 'javascript:alert(1)' WHERE title = 'js'")
    conn.execute("UPDATE postings SET url = 'https://jobs.example/web?a=1&b=2' WHERE title = 'web'")
    conn.execute("UPDATE postings SET url = 'java\tscript:alert(1)' WHERE title = 'tabbed'")
    html = make_client(conn).get("/", headers=basic()).text
    assert "javascript:" not in html and "java\tscript" not in html
    assert 'href="https://jobs.example/web?a=1&amp;b=2"' in html
    assert 'rel="noopener noreferrer"' in html
    assert html.count("<a href=") == 1
    assert re.search(r">\s*js\s*<", html) and re.search(r">\s*tabbed\s*<", html)


@pytest.mark.db
def test_page_invalid_filter_is_400_with_the_form_still_shown(conn):
    r = make_client(conn).get("/", params={"canton": "XX", "query": "kept"}, headers=basic())
    assert r.status_code == 400 and "unknown canton" in r.text and 'value="kept"' in r.text


@pytest.mark.db
def test_page_offers_more_results_with_the_filters_kept(conn, seed):
    for i in range(3):
        seed.add(title=f"job {i}")
    html = make_client(conn).get("/", params={"limit": "2", "canton": "ZH"}, headers=basic()).text
    assert "showing 2" in html and "limit=4" in html and "canton=ZH" in html


# ---------- helpers without a database ----------


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://a.example/x", True),
        ("HTTP://a.example", True),
        ("javascript:alert(1)", False),
        ("data:text/html,x", False),
        ("//a.example/x", False),
        ("/relative", False),
        ("https://", False),
        (" https://a.example", False),
        ("https://a.example/ x", False),
        ("", False),
        (None, False),
    ],
)
def test_safe_url(url, ok):
    assert (webapp.safe_url(url) is not None) is ok


def test_params_accept_repeated_and_comma_lists_and_empty_form_fields():
    p = params.parse(
        [
            ("query", "  ml "),
            ("canton", "zh,vd"),
            ("canton", "outside_switzerland"),
            ("german_max", ""),
            ("new_days", ""),
            ("remote", "on"),
            ("no_fixed_term", ""),
        ]
    )
    f = p.filters
    assert f.query == "  ml " and f.cantons == ["ZH", "VD", "outside_switzerland"]
    assert f.german_max is None and f.new_days is None and f.remote and not f.no_fixed_term
    assert f.limit == 20 and p.mode is None and not p.evidence


def test_params_reject_bad_values():
    with pytest.raises(search.SearchError):
        params.parse([("limit", "1000")])
    with pytest.raises(search.SearchError):
        params.parse([("surprise", "1")])


def test_the_embedder_is_made_once_and_only_on_first_use():
    made = []
    cache = webapp.CachedEmbedders(lambda alias: made.append(alias) or object())
    assert made == []
    assert cache("mistral") is cache("mistral") and made == ["mistral"]


def test_a_failed_embedder_is_not_cached():
    calls = []

    def factory(alias):
        calls.append(alias)
        if len(calls) == 1:
            raise KeyError("MISTRAL_API_KEY")
        return "ok"

    cache = webapp.CachedEmbedders(factory)
    with pytest.raises(KeyError):
        cache("mistral")
    assert cache("mistral") == "ok"


# ---------- jobfacts serve ----------


@pytest.fixture
def served(monkeypatch):
    import uvicorn

    calls = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: calls.append(kw))
    monkeypatch.delenv("JOBFACTS_WEB_PASSWORD", raising=False)
    return calls


def test_serve_refuses_without_a_password_on_a_public_address(served, capsys):
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 2
    assert "JOBFACTS_WEB_PASSWORD" in capsys.readouterr().err and served == []


def test_serve_refuses_without_a_password_even_on_loopback_unless_no_auth(served):
    assert cli.main(["serve"]) == 2 and served == []
    assert cli.main(["serve", "--no-auth"]) == 0 and served == [{"host": "127.0.0.1", "port": 8000}]


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.5", "::", "example.com"])
def test_no_auth_is_refused_on_a_non_loopback_host(served, capsys, host):
    assert cli.main(["serve", "--no-auth", "--host", host]) == 2
    assert "loopback" in capsys.readouterr().err and served == []


@pytest.mark.parametrize("host", ["127.0.0.1", "127.0.0.5", "::1", "localhost"])
def test_no_auth_is_allowed_on_loopback(served, host):
    assert cli.main(["serve", "--no-auth", "--host", host, "--port", "9"]) == 0
    assert served == [{"host": host, "port": 9}]


def test_serve_with_a_password_may_listen_on_any_address(served, monkeypatch):
    monkeypatch.setenv("JOBFACTS_WEB_PASSWORD", PASSWORD)
    assert cli.main(["serve", "--host", "0.0.0.0"]) == 0 and served == [
        {"host": "0.0.0.0", "port": 8000}
    ]
    assert cli.main(["serve", "--host", "0.0.0.0", "--no-auth"]) == 2  # the loopback rule wins
