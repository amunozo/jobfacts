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
        "contract_type": {"value": "not_stated", "sentences": []},
        "office_cantons": {"value": ["ZH"], "sentences": ["L"]},
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
    assert r.office_cantons == ["ZH"] and r.sentences["office_cantons"] == [-1]
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


def test_missing_key_and_unknown_canton():
    obj = json.loads(reply())
    del obj["french"]
    r, _ = run([json.dumps(obj)] * 2)
    assert r.error and "missing keys" in r.error
    r, _ = run([reply(office_cantons={"value": ["XX"], "sentences": []})] * 2)
    assert r.error and "not a canton code" in r.error


def test_outside_switzerland_empty_and_unquoted_cantons():
    ok = reply(office_cantons={"value": ["ZH", "outside_switzerland"], "sentences": ["S001"]})
    r, _ = run([ok])
    assert r.office_cantons == ["ZH", "outside_switzerland"]
    r, _ = run([reply(office_cantons={"value": [], "sentences": ["S001"]})])
    assert r.office_cantons == [] and r.sentences["office_cantons"] == []  # not stated: no quote
    r, _ = run([reply(office_cantons={"value": ["ZH"], "sentences": []})])
    assert r.error is None and r.details["missing_quote"] == ["office_cantons"]


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
    assert "- ZH: Zurich / Zürich / Zurich / Zurigo; Zürich, Winterthur" in prompt
    assert "office_cantons" in prompt and "{" not in prompt.split("Reply with")[0]


def test_places_found_by_code_in_chosen_cantons():
    from jobfacts.extract.base import Result
    from jobfacts.extract.run import add_places

    p = posting("Our offices are in Zürich and Winterthur. Remote from Bern is possible.")
    zh = p.places_in(["ZH"])
    assert [(m.name, where) for m, where in zh] == [
        ("Zürich", "location field"),
        ("Winterthur", "posting text"),
    ]
    r = Result(office_cantons=["ZH", "outside_switzerland"])
    add_places(p, r)
    assert r.office_places == ["place:261:Zürich", "place:230:Winterthur"]
    r = Result(office_cantons=[])
    add_places(p, r)
    assert r.office_places == []
    r = Result(error="boom")
    add_places(p, r)
    assert r.office_places == []
    assert p.canton_hints() == ["ZH", "BE"]
