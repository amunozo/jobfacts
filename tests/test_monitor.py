"""Run recording in `daily_runs` and the dead-man ping (no network: `send` is a fake)."""

import os
import signal

import httpx
import psycopg
import pytest

from jobfacts import daily, db, monitor
from test_daily import fake_steps

URL = "https://hc.example.test/ping/0000-invented"


class Recorder:
    """Fake `send`: remembers (method, url, body); raises `error` if given."""

    def __init__(self, error=None):
        self.calls = []
        self.error = error

    def __call__(self, method, url, body):
        self.calls.append((method, url, body))
        if self.error:
            raise self.error


class SharedConn:
    """What `db.connect()` returns in the tests: the fixture's connection, never closed."""

    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self.conn

    def __exit__(self, *a):
        return False


@pytest.fixture
def pinger(monkeypatch):
    sent = Recorder()
    monkeypatch.setenv("JOBFACTS_HEALTHCHECK_URL", URL + "/")  # a trailing slash is fine
    monkeypatch.setattr(monitor, "_send", sent)
    return sent


def daily_with_database(monkeypatch, conn, fail=None):
    calls = []
    fake_steps(monkeypatch, calls, fail=fail)
    # test_daily's autouse stub of start_run/finish_run does not apply in this module: the
    # real ones run, on the fixture's connection.
    monkeypatch.setattr(daily.db, "connect", lambda: SharedConn(conn))
    monkeypatch.setattr(daily, "open_postings", lambda c: 10)
    return calls


def rows(conn):
    cur = conn.execute("SELECT * FROM daily_runs ORDER BY id")
    names = [c.name for c in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


@pytest.mark.db
def test_an_ok_run_is_recorded(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)
    assert daily.run_daily() == 0
    (row,) = rows(conn)
    assert row["status"] == "ok" and row["failed_step"] is None
    assert row["finished_at"] >= row["started_at"]
    assert (row["new_postings"], row["closed"], row["relevant"]) == (3, 1, 2)
    assert (row["extracted"], row["embedded"], row["errors"]) == (2, 5, 0)
    assert float(row["cost_usd"]) == pytest.approx(0.004)
    assert row["warnings"] is None


@pytest.mark.db
def test_the_row_is_running_while_the_steps_run(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)
    seen = []
    original = daily._relevance

    def relevance(c, totals):
        seen.append([r["status"] for r in rows(c)])
        original(c, totals)

    monkeypatch.setattr(daily, "_relevance", relevance)
    daily.run_daily()
    assert seen == [["running"]]


@pytest.mark.db
def test_a_failed_step_is_recorded_with_its_name(monkeypatch, conn, pinger, capsys):
    daily_with_database(monkeypatch, conn, fail="extract")
    assert daily.run_daily() == 1
    (row,) = rows(conn)
    assert row["status"] == "failed" and row["failed_step"] == "extract"
    assert row["finished_at"] is not None
    assert row["relevant"] == 2  # the numbers of the steps that did finish are kept


@pytest.mark.db
def test_an_unexpected_error_after_the_steps_is_recorded_too(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)

    def broken(c):
        raise ValueError("odd")

    monkeypatch.setattr(daily, "open_postings", broken)
    assert daily.run_daily() == 1
    (row,) = rows(conn)
    assert row["status"] == "failed" and row["failed_step"] == "summary"


@pytest.mark.db
def test_a_warning_and_failed_boards_are_recorded(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)

    def embed(c, totals):
        totals["embedded"] = 0
        totals.setdefault("warnings", []).append("embedding skipped (RuntimeError)")

    monkeypatch.setattr(daily, "_embed", embed)
    original = daily._collect

    def collect(c, totals):
        original(c, totals)
        totals.update(boards_ok=100, boards_failed=3)

    monkeypatch.setattr(daily, "_collect", collect)
    assert daily.run_daily() == 0
    (row,) = rows(conn)
    assert row["status"] == "ok"
    assert row["warnings"] == "embedding skipped (RuntimeError); 3 boards failed"
    assert (row["boards_ok"], row["boards_failed"]) == (100, 3)


@pytest.mark.db
def test_skip_collect_leaves_the_collection_numbers_empty(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)
    daily.run_daily(skip_collect=True)
    (row,) = rows(conn)
    assert row["new_postings"] is None and row["boards_ok"] is None


@pytest.mark.db
def test_a_stale_running_row_is_marked_interrupted(conn):
    conn.execute("INSERT INTO daily_runs (started_at) VALUES (now() - interval '2 days'), (now())")
    new_id = monitor.start_run(conn)
    by_id = {r["id"]: r for r in rows(conn)}
    old, recent, new = (by_id[i] for i in sorted(by_id))
    assert (old["status"], old["failed_step"]) == ("failed", "interrupted")
    assert recent["status"] == "running" and new["id"] == new_id


@pytest.mark.db
def test_a_recording_error_does_not_change_the_result(monkeypatch, conn, pinger, capsys):
    daily_with_database(monkeypatch, conn)

    def broken(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(monitor, "finish_run", broken)
    assert daily.run_daily() == 0
    assert "could not record the run" in capsys.readouterr().out


# ---------- every way out records the failure and pings /fail ----------


def own_connection():
    """A second connection into the fixture's schema (the code under test closes or loses it).
    The fixture's migrations are still uncommitted: `conn.commit()` first (see the tests)."""
    url = os.environ.get("DATABASE_URL", db.DEFAULT_URL)
    return psycopg.connect(url, options="-c search_path=test_jobfacts,public")


def fail_pings(pinger):
    return [u for m, u, _ in pinger.calls if u.endswith("/fail")]


@pytest.mark.db
def test_a_lost_connection_still_records_the_row_and_pings_fail(monkeypatch, conn, pinger, capsys):
    calls = daily_with_database(monkeypatch, conn)
    conn.commit()  # make the fixture's tables visible to the second connection
    monkeypatch.setattr(daily.db, "connect", own_connection)

    def kill_own_backend(c, totals):
        calls.append("extract")
        try:
            c.execute("SELECT pg_terminate_backend(pg_backend_pid())")
        except psycopg.OperationalError:
            pass  # the server closed this very connection, as a restart would
        c.execute("SELECT 1")  # raises OperationalError

    monkeypatch.setattr(daily, "_extract", kill_own_backend)
    assert daily.run_daily() == 1
    (row,) = rows(conn)
    assert row["status"] == "failed" and row["failed_step"] == "extract"
    assert row["finished_at"] is not None
    assert fail_pings(pinger) == [URL + "/fail"]
    assert "step 'extract' failed" in capsys.readouterr().out


@pytest.mark.db
def test_a_lost_connection_and_no_fresh_one_only_warns(monkeypatch, conn, pinger, capsys):
    daily_with_database(monkeypatch, conn)
    conn.commit()
    opened = []

    def connect_once():
        if opened:
            raise psycopg.OperationalError("database gone")
        opened.append(1)
        return own_connection()

    monkeypatch.setattr(daily.db, "connect", connect_once)

    def kill_own_backend(c, totals):
        try:
            c.execute("SELECT pg_terminate_backend(pg_backend_pid())")
        except psycopg.OperationalError:
            pass
        c.execute("SELECT 1")

    monkeypatch.setattr(daily, "_extract", kill_own_backend)
    assert daily.run_daily() == 1  # no traceback
    assert fail_pings(pinger) == [URL + "/fail"]
    assert "could not record the run" in capsys.readouterr().out
    assert [r["status"] for r in rows(conn)] == ["running"]  # the next run marks it interrupted


@pytest.mark.db
def test_a_lost_connection_in_connect_names_the_step(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)

    def refused():
        raise psycopg.OperationalError("refused")

    monkeypatch.setattr(daily.db, "connect", refused)
    assert daily.run_daily() == 1
    assert "connect" in pinger.calls[-1][2] and pinger.calls[-1][1].endswith("/fail")


@pytest.mark.db
def test_keyboard_interrupt_is_recorded_then_reraised(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)

    def interrupted(c, totals):
        raise KeyboardInterrupt

    monkeypatch.setattr(daily, "_collect", interrupted)
    with pytest.raises(KeyboardInterrupt):
        daily.run_daily()
    (row,) = rows(conn)
    assert row["status"] == "failed" and row["failed_step"] == "collect"
    assert fail_pings(pinger) == [URL + "/fail"]
    assert "collect" in pinger.calls[-1][2]


@pytest.mark.db
def test_sigterm_is_recorded_as_a_failure(monkeypatch, conn, pinger, capsys):
    daily_with_database(monkeypatch, conn)

    def slow(c, totals):
        os.kill(os.getpid(), signal.SIGTERM)  # what `docker stop` does
        raise AssertionError("the handler should have raised")

    monkeypatch.setattr(daily, "_relevance", slow)
    before = signal.getsignal(signal.SIGTERM)
    with pytest.raises(SystemExit) as exit_info:
        daily.run_daily()
    assert exit_info.value.code == 128 + signal.SIGTERM
    assert signal.getsignal(signal.SIGTERM) is before  # the handler is put back
    (row,) = rows(conn)
    assert row["status"] == "failed" and row["failed_step"] == "relevance"
    assert fail_pings(pinger) == [URL + "/fail"]
    assert "terminated (SIGTERM)" in capsys.readouterr().out


@pytest.mark.db
def test_a_failure_in_the_summary_code_is_step_summary(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)
    monkeypatch.setattr(daily, "_extract", lambda c, totals: None)  # totals lack 'extracted'
    assert daily.run_daily() == 1
    (row,) = rows(conn)
    assert row["status"] == "failed" and row["failed_step"] == "summary"
    assert "summary" in pinger.calls[-1][2]


# ---------- pings ----------


@pytest.mark.db
def test_success_pings_start_then_posts_the_summary(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn)
    assert daily.run_daily() == 0
    assert [(m, u) for m, u, _ in pinger.calls] == [("GET", URL + "/start"), ("POST", URL)]
    assert pinger.calls[0][2] is None
    assert pinger.calls[1][2].startswith("daily: 10 open postings (+3 new, 1 closed)")


@pytest.mark.db
def test_failure_pings_fail_with_the_step_name(monkeypatch, conn, pinger):
    daily_with_database(monkeypatch, conn, fail="collect")
    assert daily.run_daily() == 1
    assert [(m, u) for m, u, _ in pinger.calls] == [
        ("GET", URL + "/start"),
        ("POST", URL + "/fail"),
    ]
    body = pinger.calls[1][2]
    assert "collect" in body and "boom" not in body  # the step, not the error text


def test_a_database_failure_still_pings_fail(monkeypatch, pinger):
    def no_database():
        raise daily.StepFailed("database", "down")

    monkeypatch.setattr(daily, "ensure_database", no_database)
    assert daily.run_daily() == 1
    assert [(m, u) for m, u, _ in pinger.calls] == [
        ("GET", URL + "/start"),
        ("POST", URL + "/fail"),
    ]
    assert "database" in pinger.calls[1][2]


def test_a_ping_error_only_warns(capsys):
    sent = Recorder(error=httpx.ConnectTimeout(f"timeout for {URL}"))
    pinger = monitor.Pinger(URL, sent)
    pinger.start()
    pinger.success("daily: ok")
    pinger.fail("collect")
    out = capsys.readouterr().out
    assert out.count("warning: health check ping failed (ConnectTimeout)") == 3
    assert "hc.example.test" not in out  # the URL is a secret


@pytest.mark.db
def test_a_ping_error_does_not_fail_the_run(monkeypatch, conn):
    sent = Recorder(error=httpx.ConnectError("refused"))
    monkeypatch.setenv("JOBFACTS_HEALTHCHECK_URL", URL)
    monkeypatch.setattr(monitor, "_send", sent)
    daily_with_database(monkeypatch, conn)
    assert daily.run_daily() == 0
    assert len(sent.calls) == 2
    assert rows(conn)[0]["status"] == "ok"


@pytest.mark.db
def test_without_the_variable_nothing_is_sent(monkeypatch, conn):
    sent = Recorder()
    monkeypatch.setattr(monitor, "_send", sent)
    daily_with_database(monkeypatch, conn)
    assert daily.run_daily() == 0
    assert sent.calls == []


def test_the_real_send_uses_a_timeout_and_the_given_method():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, str(request.url), request.content))
        return httpx.Response(200, text="OK")

    real_request = httpx.request

    def request(method, url, content=None, timeout=None):
        assert timeout is not None and timeout <= 10
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            return client.request(method, url, content=content)

    httpx.request = request
    try:
        monitor._send("POST", URL + "/fail", "daily failed at step 'x'")
    finally:
        httpx.request = real_request
    assert seen == [("POST", URL + "/fail", b"daily failed at step 'x'")]
