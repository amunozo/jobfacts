import pytest

from jobfacts import cli, daily


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

        return step

    for name in ("migrate", "collect", "relevance", "extract"):
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
    assert calls == ["db", "migrate", "collect", "relevance", "extract"]
    assert (
        "daily: 10 open postings (+3 new, 1 closed), 2 relevant newly scored, "
        "2 extracted, 0 errors, $0.004" in capsys.readouterr().out
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
    assert calls == ["db", "migrate", "relevance", "extract"]


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


def test_docker_failure_names_database_step(tmp_path):
    bad = type("Bad", (), {"returncode": 1})()
    with pytest.raises(daily.StepFailed) as e:
        daily.ensure_database(lambda: False, lambda *a, **k: bad, lambda s: None, tmp_path)
    assert e.value.step == "database"
