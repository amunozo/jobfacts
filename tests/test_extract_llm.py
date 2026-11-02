import json

from fakes import FakeChat, posting
from jobfacts.extract import llm


def reply(**overrides):
    base = {
        "work_mode": {"value": "hybrid", "sentences": ["S005"]},
        "remote_from_switzerland": {"value": "not_applicable", "sentences": []},
        "german": {"value": "required_fluent", "sentences": ["S004"]},
        "french": {"value": "not_mentioned", "sentences": []},
        "min_years": {"value": 3, "sentences": ["S003"]},
        "title_level": {"value": "senior", "sentences": ["T"]},
        "office_locations": {"value": ["city:Zürich"], "sentences": ["L"]},
    }
    base.update(overrides)
    return json.dumps(base)


def extractor(replies):
    return llm.LLMExtractor("deepseek", "m", FakeChat(replies), 1.0, 2.0), None


def run(replies, p=None):
    client = FakeChat(replies)
    ex = llm.LLMExtractor("deepseek", "m", client, 1.0, 2.0)
    return ex.extract(p or posting()), client


def test_valid_output():
    r, client = run([reply()])
    assert r.error is None
    assert r.values["min_years"] == "3" and r.values["work_mode"] == "hybrid"
    assert r.sentences["german"] == [4] and r.sentences["title_level"] == [0]
    assert r.sentences["french"] == []
    assert r.office_locations == ["city:Zürich"]
    assert r.seniority == "mid"
    assert r.tokens_in == 100 and r.tokens_out == 50
    assert abs(r.cost_usd - (100 * 1.0 + 50 * 2.0) / 1e6) < 1e-12
    assert client.calls[0]["temperature"] == 0 and len(client.calls) == 1


def test_fenced_json_is_accepted():
    r, _ = run(["```json\n" + reply() + "\n```"])
    assert r.error is None


def test_invalid_value_then_retry_succeeds():
    bad = reply(work_mode={"value": "sometimes", "sentences": []})
    r, client = run([bad, reply()])
    assert r.error is None and r.details["attempts"] == 2
    assert len(client.calls) == 2
    assert "not allowed" in client.calls[1]["messages"][-1]["content"]
    assert r.tokens_in == 200  # both attempts are paid


def test_unknown_sentence_id_twice_is_stored_as_error():
    bad = reply(german={"value": "required_fluent", "sentences": ["S999"]})
    r, _ = run([bad, bad])
    assert r.error and "unknown sentence id" in r.error
    assert r.values == {} and r.seniority is None


def test_not_json_is_error():
    r, _ = run(["I think it is hybrid.", "still no"])
    assert r.error and r.error.startswith("invalid output")


def test_missing_key_and_unknown_candidate():
    obj = json.loads(reply())
    del obj["french"]
    r, _ = run([json.dumps(obj)] * 2)
    assert r.error and "missing keys" in r.error
    r, _ = run([reply(office_locations={"value": ["city:Bern"], "sentences": []})] * 2)
    assert r.error and "not one of the candidates" in r.error


def test_missing_quote_is_flagged_not_hidden():
    r, _ = run([reply(german={"value": "required_fluent", "sentences": []})])
    assert r.error is None and r.details["missing_quote"] == ["german"]


def test_api_exception_is_error():
    class Boom(FakeChat):
        def _create(self, **kw):
            raise RuntimeError("503")

    client = Boom([])
    r = llm.LLMExtractor("x", "m", client, 1, 1).extract(posting())
    assert r.error and "503" in r.error


def test_prompt_is_generated_from_spec():
    prompt = llm.system_prompt()
    from jobfacts.extract.spec import FIELDS

    for f in FIELDS.values():
        for v in f.values:
            assert f'"{v}"' in prompt
