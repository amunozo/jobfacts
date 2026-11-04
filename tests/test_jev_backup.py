import json
from pathlib import Path

import httpx
import httpx2
import pytest
from typesafe_sdk import TypeSafeAPIError, TypeSafeError

from fakes import FakeJev, openrouter_payload, posting
from jobfacts.extract import jev, jev_backup
from jobfacts.extract.jev_backup import BackupError, FallbackClient, OpenRouterJev
from jobfacts.extract.posting import PostingText
from jobfacts.extract.run import run_relevance

PICK = {"v_work_mode": "hybrid", "s_work_mode_0": "S005", "v_german": "required_fluent"}
NOUL = {"c_ZH": 0.9}


def openrouter(handler=None, **kwargs):
    """An OpenRouterJev on a mock transport; `seen` collects the requests."""
    seen = []

    def default(request):
        body = json.loads(request.content)
        return httpx.Response(200, json=openrouter_payload(body, **kwargs))

    def wrapper(request):
        seen.append(request)
        return (handler or default)(request)

    http = httpx.Client(transport=httpx.MockTransport(wrapper))
    client = OpenRouterJev("secret-key", http=http)
    client.seen = seen
    return client


class Failing:
    def __init__(self, message="boom"):
        self.message, self.calls = message, 0

    def system_one(self, state, questions, model=None):
        self.calls += 1
        raise ConnectionError(self.message)


def test_backup_request_matches_the_sdk_body():
    backup = openrouter(pick=PICK, noul=0.0, noul_pick=NOUL)
    jev.JevExtractor(backup).extract(posting())
    (request,) = backup.seen
    assert str(request.url) == "https://openrouter.ai/api/alpha/decisions"
    assert request.headers["authorization"] == "Bearer secret-key"
    body = json.loads(request.content)
    assert set(body) == {"state", "model", "questions"}
    assert body["model"] == "typesafe/jev-1.14"
    assert body["state"].startswith("Job posting.")
    assert body["questions"]["c_ZH"]["type"] == "noul"
    assert "v_work_mode" in body["questions"]


def test_backup_gives_the_same_values_as_fake_jev():
    p = posting()
    expected = jev.JevExtractor(FakeJev(PICK, noul=0.0, noul_pick=NOUL)).extract(p)
    got = jev.JevExtractor(openrouter(pick=PICK, noul=0.0, noul_pick=NOUL)).extract(p)
    assert got.error is None
    assert got.values == expected.values and got.values["work_mode"] == "hybrid"
    assert got.sentences == expected.sentences
    assert got.office_cantons == expected.office_cantons == ["ZH"]
    assert (got.tokens_in, got.tokens_out, got.cost_usd) == (
        expected.tokens_in,
        expected.tokens_out,
        expected.cost_usd,
    )
    assert got.details["route"] == "openrouter"
    assert got.details["model_returned"] == "typesafe/jev-1.14-20261014"
    for key in ("work_mode", "german", "office_cantons"):
        assert got.details[key] == expected.details[key]
    assert "route" not in expected.details


def test_backup_parses_choice_noul_and_usage():
    backup = openrouter(noul=0.3)
    r = backup.system_one(
        state="s",
        questions={
            "q": jev.Choice(instructions="i", criteria={"a": None, "b": None}),
            "n": jev.Noul(instructions="i"),
        },
        model="ignored",
    )
    assert r.answers["q"].choice == "a" and r.answers["q"].confidence == 0.5
    assert r.answers["q"].probabilities == {"a": 0.8, "b": 0.2}
    assert r.answers["n"].noul == 0.3
    assert (r.usage.input_tokens, r.usage.output_tokens) == (1000, 5)
    assert r.model == "typesafe/jev-1.14-20261014" and r.route == "openrouter"


def test_backup_raises_on_non_2xx_with_status_and_body():
    backup = openrouter(lambda request: httpx.Response(402, text="insufficient credits " * 100))
    with pytest.raises(BackupError, match=r"HTTP 402: insufficient credits") as error:
        backup.system_one(state="s", questions={"n": jev.Noul(instructions="i")})
    assert len(str(error.value)) < 400  # the body is shortened


def test_backup_raises_on_network_error_and_bad_body():
    def down(request):
        raise httpx.ConnectError("no route")

    with pytest.raises(BackupError, match="ConnectError"):
        openrouter(down).system_one(state="s", questions={"n": jev.Noul(instructions="i")})
    bad = openrouter(lambda request: httpx.Response(200, json={"nothing": 1}))
    with pytest.raises(BackupError, match="not understood"):
        bad.system_one(state="s", questions={"n": jev.Noul(instructions="i")})


def test_primary_ok_never_calls_the_backup():
    primary, backup = FakeJev(PICK, noul=0.0), openrouter()
    client = FallbackClient(primary, backup)
    r = jev.JevExtractor(client).extract(posting())
    assert r.error is None and "route" not in r.details
    assert len(primary.calls) == 1 and backup.seen == [] and client.backup_calls == 0


def test_primary_failure_uses_the_backup_and_records_the_route():
    client = FallbackClient(Failing(), openrouter(pick=PICK, noul=0.0))
    r = jev.JevExtractor(client).extract(posting())
    assert r.error is None
    assert r.details["route"] == "openrouter" and r.values["work_mode"] == "hybrid"
    assert client.backup_calls == 1


def test_both_failing_gives_an_error_result_naming_both():
    backup = openrouter(lambda request: httpx.Response(500, text="upstream down"))
    r = jev.JevExtractor(FallbackClient(Failing("primary is down"), backup)).extract(posting())
    assert r.error and "primary is down" in r.error and "HTTP 500" in r.error
    assert "ConnectionError" in r.error and "BackupError" in r.error


def test_circuit_breaker_skips_the_primary_after_three_failures():
    primary, backup = Failing(), openrouter()
    client = FallbackClient(primary, backup)
    extractor = jev.JevExtractor(client)
    results = [extractor.extract(posting()) for _ in range(5)]
    assert all(x.error is None for x in results)
    assert primary.calls == 3 and len(backup.seen) == 5 and client.backup_calls == 5
    assert client.primary_disabled


def test_a_success_resets_the_failure_count():
    class Flaky:
        def __init__(self):
            self.n = 0

        def system_one(self, state, questions, model=None):
            self.n += 1
            if self.n % 3:  # fail, fail, succeed, ...
                raise ConnectionError("x")
            return FakeJev().system_one(state, questions, model)

    client = FallbackClient(Flaky(), openrouter())
    for _ in range(9):
        client.system_one(state="s", questions={"n": jev.Noul(instructions="i")})
    assert not client.primary_disabled and client.backup_calls == 6


def test_backup_error_when_primary_was_skipped_says_so():
    client = FallbackClient(Failing(), openrouter(lambda r: httpx.Response(500, text="x")))
    for _ in range(3):
        with pytest.raises(RuntimeError):
            client.system_one(state="s", questions={})
    with pytest.raises(RuntimeError, match="primary skipped"):
        client.system_one(state="s", questions={})


def test_relevance_goes_through_the_backup_and_is_counted():
    client = FallbackClient(Failing(), openrouter(noul=0.35))
    assert jev.relevance_probability(client, posting()) == pytest.approx(0.35)
    assert jev_backup.backup_calls(client) == 1
    assert jev_backup.backup_calls(FakeJev()) == 0
    assert jev_backup.backup_line(0) is None
    assert jev_backup.backup_line(37) == "Jev backup (OpenRouter) served 37 calls"


def test_make_client_without_the_key_is_the_plain_client(monkeypatch):
    monkeypatch.setattr(jev, "load_dotenv", lambda: None)  # never read the real .env
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert isinstance(jev.make_client(), jev.TypeSafeClient)
    monkeypatch.setenv("OPENROUTER_API_KEY", "   ")
    assert isinstance(jev.make_client(), jev.TypeSafeClient)


def test_make_client_with_the_key_has_the_fallback(monkeypatch):
    monkeypatch.setattr(jev, "load_dotenv", lambda: None)
    monkeypatch.setenv("TYPESAFE_API_KEY", "t")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    client = jev.make_client()
    assert isinstance(client, FallbackClient)
    assert isinstance(client.primary, jev.TypeSafeClient)
    assert isinstance(client.backup, OpenRouterJev)


@pytest.mark.db
def test_run_relevance_counts_backup_calls_in_the_database(conn):
    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('E','greenhouse','e') RETURNING id"
    ).fetchone()[0]
    p = posting()
    conn.execute(
        "INSERT INTO postings (employer_id, source_job_id, url, title, location_raw, "
        "description_text, content_hash) VALUES (%s,'1','u',%s,%s,%s,'h')",
        (emp, p.title, p.location_raw, p.description),
    )
    conn.commit()
    client = FallbackClient(Failing(), openrouter(noul=0.5))
    r = run_relevance(conn, client)
    assert (r.scored, r.relevant, r.errors) == (1, 1, 0) and client.backup_calls == 1


# ---------- review fixes (4 Nov 2026) ----------

RECORDED = Path(__file__).parent / "data" / "openrouter_decisions_response.json"


def test_a_recorded_openrouter_reply_is_read_like_a_typesafe_one():
    """A real reply of OpenRouter's decisions endpoint (4 Nov 2026, no posting text)."""
    payload = json.loads(RECORDED.read_text())
    client = openrouter(lambda request: httpx.Response(200, json=payload))
    sentences = " ".join(f"Sentence number {i} is here." for i in range(1, 34))
    result = jev.JevExtractor(client).extract(
        PostingText.build(1, "Senior M365 Engineer", "Bern, Switzerland", sentences)
    )
    assert result.error is None
    assert result.tokens_in == payload["usage"]["input_tokens"]
    assert result.values["work_mode"] == "remote"
    assert result.values["german"] == "not_mentioned"  # rule 4: no language named here
    assert result.details["route"] == "openrouter"
    assert result.details["model_returned"] == payload["model"]


class Status:
    """A primary that raises a TypeSafe API error with this status."""

    def __init__(self, status):
        self.status, self.calls = status, 0

    def system_one(self, state, questions, model=None):
        self.calls += 1
        raise TypeSafeAPIError(self.status, None, httpx2.Headers())


@pytest.mark.parametrize("status", [401, 402, 403, 429, 500, 503])
def test_unavailable_statuses_trip_the_breaker(status):
    primary = Status(status)
    client = FallbackClient(primary, openrouter())
    for _ in range(5):
        jev.JevExtractor(client).extract(posting())
    assert primary.calls == 3 and client.primary_disabled


@pytest.mark.parametrize("status", [400, 404, 413, 422])
def test_request_errors_use_the_backup_but_do_not_trip_the_breaker(status):
    primary = Status(status)
    client = FallbackClient(primary, openrouter())
    results = [jev.JevExtractor(client).extract(posting()) for _ in range(5)]
    assert all(x.error is None for x in results)
    assert primary.calls == 5 and not client.primary_disabled


def test_errors_before_sending_do_not_trip_the_breaker():
    assert not jev_backup.is_unavailable(TypeSafeError("empty questions"))
    assert jev_backup.is_unavailable(ConnectionError("down"))


def test_a_reply_missing_an_answer_is_an_error_row_not_a_crash():
    class Missing:
        def system_one(self, state, questions, model=None):
            response = FakeJev().system_one(state, questions, model)
            del response.answers["v_work_mode"]
            return response

    result = jev.JevExtractor(Missing()).extract(posting())
    assert result.error and result.error.startswith("BadResponse") and not result.values
