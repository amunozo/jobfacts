import pytest

from jobfacts import cli, daily, monitor

REAL_EMBED_STEP = daily._embed


@pytest.fixture(autouse=True)
def no_run_table(monkeypatch):
    """These tests use a fake connection; recording runs is tested in test_monitor.py."""
    monkeypatch.setattr(monitor, "start_run", lambda conn: 1)
    monkeypatch.setattr(monitor, "finish_run", lambda *a, **k: None)


class Done:
    returncode = 0


def fake_steps(monkeypatch, calls, fail=None):
    def make(name):
        def step(conn, totals):
            calls.append(name)
            if name == fail:
                raise RuntimeError("boom")
            if name == "collect":
                totals.update(new=3, closed=1)
            if name == "relevance":
                totals.update(relevant=2)
            if name == "extract":
                totals.update(extracted=2, errors=0, cost=0.004)
            if name == "embed":
                totals.update(embedded=5)

        return step

    for name in ("migrate", "collect", "relevance", "extract", "embed"):
        monkeypatch.setattr(daily, f"_{name}", make(name))
    monkeypatch.setattr(daily, "ensure_database", lambda: calls.append("db"))
    monkeypatch.setattr(daily, "open_postings", lambda conn: 10)
    monkeypatch.setattr(daily.db, "connect", lambda: FakeConn())


class FakeConn:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def rollback(self):
        pass


def test_order_and_summary(monkeypatch, capsys):
    calls = []
    fake_steps(monkeypatch, calls)
    assert daily.run_daily() == 0
    assert calls == ["db", "migrate", "collect", "relevance", "extract", "embed"]
    assert (
        "daily: 10 open postings (+3 new, 1 closed), 2 relevant newly scored, "
        "2 extracted, 5 embedded, 0 errors, $0.004" in capsys.readouterr().out
    )


def test_stops_at_first_failure_and_names_step(monkeypatch, capsys):
    calls = []
    fake_steps(monkeypatch, calls, fail="collect")
    assert daily.run_daily() == 1
    assert calls == ["db", "migrate", "collect"]
    assert "step 'collect' failed: RuntimeError: boom" in capsys.readouterr().out


def test_skip_collect(monkeypatch):
    calls = []
    fake_steps(monkeypatch, calls)
    assert daily.run_daily(skip_collect=True) == 0
    assert calls == ["db", "migrate", "relevance", "extract", "embed"]


def test_cli_flag(monkeypatch):
    seen = []
    monkeypatch.setattr(
        daily, "run_daily", lambda skip_collect=False: seen.append(skip_collect) or 0
    )
    assert cli.main(["daily", "--skip-collect"]) == 0
    assert seen == [True]


def test_database_already_up_does_not_start_docker():
    ran = []
    assert daily.ensure_database(lambda: True, lambda *a, **k: ran.append(a)) is False
    assert ran == []


def test_starts_docker_when_unreachable_then_waits(tmp_path):
    answers = iter([False, False, False, True])
    ran, slept = [], []

    def run(cmd, cwd):
        ran.append((cmd, cwd))
        return Done()

    started = daily.ensure_database(lambda: next(answers), run, slept.append, tmp_path)
    assert started is True
    assert ran == [(["docker", "compose", "up", "-d"], tmp_path)]
    assert len(slept) == 2


def test_database_never_comes_up(tmp_path):
    with pytest.raises(daily.StepFailed, match="not reachable"):
        daily.ensure_database(lambda: False, lambda *a, **k: Done(), lambda s: None, tmp_path, 6)


def test_no_db_start_waits_without_docker(monkeypatch):
    monkeypatch.setenv("JOBFACTS_NO_DB_START", "1")
    answers = iter([False, False, True])
    ran, slept = [], []
    started = daily.ensure_database(
        lambda: next(answers), lambda *a, **k: ran.append(a), slept.append
    )
    assert started is False
    assert ran == []
    assert len(slept) == 2


def test_no_db_start_gives_up_without_docker(monkeypatch):
    monkeypatch.setenv("JOBFACTS_NO_DB_START", "1")
    ran = []
    with pytest.raises(daily.StepFailed, match="not reachable"):
        daily.ensure_database(
            lambda: False, lambda *a, **k: ran.append(a), lambda s: None, timeout=6
        )
    assert ran == []


def test_docker_failure_names_database_step(tmp_path):
    bad = type("Bad", (), {"returncode": 1})()
    with pytest.raises(daily.StepFailed) as e:
        daily.ensure_database(lambda: False, lambda *a, **k: bad, lambda s: None, tmp_path)
    assert e.value.step == "database"


class BrokenEmbedder:
    alias, model = "mistral", "mistral-embed"

    def embed_documents(self, texts):
        raise RuntimeError("API down")


class RecordingConn(FakeConn):
    rolled_back = 0

    def rollback(self):
        self.rolled_back += 1


def test_embed_step_warns_and_continues_without_a_key(monkeypatch, capsys):
    def no_key(alias):
        raise KeyError("MISTRAL_API_KEY")

    monkeypatch.setattr(daily.embed, "make_embedder", no_key)
    totals = {"cost": 0.004}
    conn = RecordingConn()
    daily._embed(conn, totals)
    out = capsys.readouterr().out
    assert "warning: embedding skipped (KeyError: 'MISTRAL_API_KEY')" in out
    assert totals == {
        "cost": 0.004,
        "embedded": 0,
        "warnings": ["embedding skipped (KeyError)"],
    }
    assert conn.rolled_back == 1


def test_embed_step_warns_and_continues_when_the_api_fails(monkeypatch, capsys):
    def run_embed(conn, embedder):
        embedder.embed_documents(["x"])

    monkeypatch.setattr(daily.embed, "make_embedder", lambda alias: BrokenEmbedder())
    monkeypatch.setattr(daily.embed, "run_embed", run_embed)
    totals = {}
    daily._embed(RecordingConn(), totals)
    assert "warning: embedding skipped (RuntimeError: API down)" in capsys.readouterr().out
    assert totals["embedded"] == 0


def test_embed_step_reports_the_count_and_adds_the_cost(monkeypatch, capsys):
    seen = {}

    def run_embed(conn, embedder):
        seen["embedder"] = embedder
        return daily.embed.EmbedSummary("mistral-embed", 6000, 7, 3, 1200, 0.0012, 1.0)

    monkeypatch.setattr(daily.embed, "make_embedder", lambda alias: alias)
    monkeypatch.setattr(daily.embed, "run_embed", run_embed)
    totals = {"cost": 0.004}
    daily._embed(RecordingConn(), totals)
    assert seen["embedder"] == daily.embed.DEFAULT_MODEL == "mistral"
    assert totals["embedded"] == 7 and totals["cost"] == pytest.approx(0.0052)
    assert "embedded 7" in capsys.readouterr().out


def test_a_failing_embedder_does_not_fail_the_daily_run(monkeypatch, capsys):
    calls = []
    fake_steps(monkeypatch, calls)
    monkeypatch.setattr(daily, "_embed", REAL_EMBED_STEP)  # the real step; the others are fake
    monkeypatch.setattr(daily.embed, "make_embedder", lambda alias: BrokenEmbedder())
    monkeypatch.setattr(daily.embed, "run_embed", lambda c, e: e.embed_documents(["x"]))
    assert daily.run_daily() == 0
    out = capsys.readouterr().out
    assert "warning: embedding skipped" in out and "0 embedded" in out
