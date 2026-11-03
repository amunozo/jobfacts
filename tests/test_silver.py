import copy
import json
from types import SimpleNamespace

import pytest

from jobfacts import silver
from jobfacts.extract.posting import PostingText

DESC = (
    "Wir suchen Dich als Data Scientist in Basel. Du arbeitest im Büro und zwei Tage zuhause. "
    "Du hast mindestens drei Jahre Erfahrung. Gute Deutschkenntnisse sind erforderlich."
)


def make_item(pid=1, order=0, language="de"):
    return {
        "order": order,
        "posting_id": pid,
        "language": language,
        "title": "Data Scientist (m/w/d)",
        "location_raw": "Basel",
        "description_text": DESC,
    }


def field(value="not_stated", ids=(), tr="", conf="high"):
    return {
        "value": value,
        "sentences": list(ids),
        "translation": tr,
        "confidence": conf,
        "note": "",
    }


def good_answer(item):
    p = PostingText.build(item["posting_id"], item["title"], item["location_raw"], DESC)
    assert len(p.sentences) >= 3
    return {
        "work_mode": field("hybrid", ["S002"], "You work in the office and two days at home."),
        "remote_from_switzerland": field("not_stated"),
        "german": field("not_mentioned"),
        "french": field("not_mentioned"),
        "min_years": field("3", ["S003"], "At least three years."),
        "title_level": field("not_stated"),
        "contract_type": field("not_stated"),
        "office_cantons": field_cantons(["BS"], ["L"], "Basel"),
        "relevant": {"value": "true", "reason": "Data science role."},
    }


def field_cantons(value, ids, tr):
    return {"value": value, "sentences": ids, "translation": tr, "confidence": "high", "note": ""}


@pytest.fixture
def data_dir(tmp_path):
    items = [make_item(1, 0), make_item(2, 1), make_item(3, 2, "en")]
    with (tmp_path / "test_set.jsonl").open("w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    return tmp_path


def valid_for(item):
    from jobfacts.extract.spec import FIELDS

    a = good_answer(item)
    for name, spec in FIELDS.items():
        assert a[name]["value"] in spec.values, name
    return a


def test_render_prompt(data_dir):
    item = make_item()
    text = silver.render_prompt(item)
    for ph in ("{fields_md}", "{cantons}", "{posting}"):
        assert ph not in text
    assert "S001| " in text and "T| Data Scientist" in text and "L| Basel" in text


def test_render_all_sets(data_dir):
    assert silver.render_all(data_dir, "de") == 2
    assert (data_dir / "silver" / "prompts" / "1.md").exists()
    assert not (data_dir / "silver" / "prompts" / "3.md").exists()


def test_validate_good_and_normalised():
    item = make_item()
    out = silver.validate(valid_for(item), item)
    assert out["work_mode"]["sentences"] == [2]
    assert out["office_cantons"]["sentences"] == [-1]


def bad(mutate):
    item = make_item()
    a = copy.deepcopy(valid_for(item))
    mutate(a)
    with pytest.raises(ValueError) as e:
        silver.validate(a, item)
    return str(e.value)


def test_validate_rejects():
    assert "not allowed" in bad(lambda a: a["work_mode"].update(value="sometimes"))
    assert "unknown sentence id" in bad(lambda a: a["work_mode"].update(sentences=["S099"]))
    assert "no sentences" in bad(lambda a: a["german"].update(sentences=["S001"], translation="x"))
    assert "translation" in bad(lambda a: a["work_mode"].update(translation=""))
    assert "missing keys" in bad(lambda a: a.pop("french"))
    assert "canton" in bad(lambda a: a["office_cantons"].update(value=["XX"]))
    assert "duplicate" in bad(lambda a: a["office_cantons"].update(value=["BS", "BS"]))
    assert "confidence" in bad(lambda a: a["work_mode"].update(confidence="medium"))
    assert "relevant.value" in bad(lambda a: a["relevant"].update(value="maybe"))


def test_extract_json_fenced_and_surrounded():
    assert silver.extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert silver.extract_json('Here you go: {"a": {"b": 2}} done') == {"a": {"b": 2}}
    with pytest.raises(ValueError):
        silver.extract_json("no json")


def test_run_labeller_store_skip_retry_fail(data_dir):
    good = json.dumps(valid_for(make_item()))
    calls = []

    def call(model, prompt):
        calls.append(prompt)
        return f"```json\n{good}\n```"

    s = silver.run_labeller("gpt", data_dir, set_name="de", call=call)
    assert s["done"] == [1, 2] and not s["failed"]
    rec = json.loads((data_dir / "silver" / "gpt" / "1.json").read_text())
    assert rec["model"] == silver.LABELLERS["gpt"] and rec["answer"]["work_mode"]["sentences"] == [
        2
    ]
    assert (data_dir / "silver" / "raw" / "gpt" / "1.txt").exists()
    # existing answers are skipped
    s = silver.run_labeller("gpt", data_dir, set_name="de", call=call)
    assert s["skipped"] == [1, 2] and not s["done"]

    # retry once with the error text
    (data_dir / "silver" / "gpt" / "1.json").unlink()
    replies = iter(["not json", good])
    calls.clear()
    s = silver.run_labeller(
        "gpt",
        data_dir,
        posting_id=1,
        set_name="de",
        call=lambda m, p: (calls.append(p), next(replies))[1],
    )
    assert s["done"] == [1] and len(calls) == 2
    assert "Your previous answer was invalid" in calls[1] and "no JSON object" in calls[1]

    # two bad answers give a recorded failure
    (data_dir / "silver" / "gpt" / "1.json").unlink()
    s = silver.run_labeller("gpt", data_dir, posting_id=1, set_name="de", call=lambda m, p: "nope")
    assert list(s["failed"]) == [1] and not s["done"]


def test_run_labeller_survives_call_error(data_dir):
    def call(model, prompt):
        raise RuntimeError("pi exited with 1")

    s = silver.run_labeller("gemini", data_dir, limit=1, set_name="de", call=call)
    assert list(s["failed"]) == [1]


def test_check_labeller_reads_raw_files(data_dir):
    raw = data_dir / "silver" / "raw" / "opus"
    raw.mkdir(parents=True)
    (raw / "1.json").write_text(json.dumps(valid_for(make_item())))
    (raw / "2.json").write_text('{"work_mode": {}}')
    s = silver.check_labeller("opus", data_dir, "de")
    assert s["done"] == [1] and list(s["failed"]) == [2] and not s["missing"]
    rec = json.loads((data_dir / "silver" / "opus" / "1.json").read_text())
    assert rec["model"] == silver.LABELLERS["opus"]
    assert silver.status(data_dir, "de")["opus"] == 1


def test_contract_type_required_and_checked():
    assert "missing keys: contract_type" in bad(lambda a: a.pop("contract_type"))
    assert "contract_type" in bad(lambda a: a["contract_type"].update(value="temp"))
    assert "needs at least one sentence" in bad(
        lambda a: a["contract_type"].update(value="permanent")
    )
    item = make_item()
    a = valid_for(item)
    a["contract_type"] = field("fixed_term", ["S001"], "We are looking for you.")
    assert silver.validate(a, item)["contract_type"]["sentences"] == [1]


# ----- pi JSON events, usage ------------------------------------------------------------


def pi_stdout(stop="stop", error="", text="{}"):
    events = [
        {"type": "session"},
        {"type": "message_end", "message": {"role": "user", "content": [{"type": "text"}]}},
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": "hmm"},
                    {"type": "text", "text": text},
                ],
                "usage": {
                    "input": 1000,
                    "output": 300,
                    "reasoning": 200,
                    "cacheRead": 0,
                    "cacheWrite": 0,
                    "totalTokens": 1300,
                    "cost": {"total": 0.0123},
                },  # fmt: skip
                "durationMs": 4200,
                "stopReason": stop,
                "errorMessage": error,
            },
        },
        {"type": "agent_end"},
    ]
    return "garbage line\n" + "\n".join(json.dumps(e) for e in events)


def test_parse_pi_events_reply_and_usage():
    r = silver.parse_pi_events(pi_stdout(text='{"a": 1}'))
    assert r.text == '{"a": 1}'
    assert r.usage == {
        "input": 1000, "output": 300, "reasoning": 200, "cost_usd": 0.0123, "duration_ms": 4200
    }  # fmt: skip


def test_parse_pi_events_error_stop_reason():
    with pytest.raises(silver.CallError, match="Model is unavailable") as e:
        silver.parse_pi_events(pi_stdout(stop="error", error="Model is unavailable", text=""))
    assert e.value.usage["duration_ms"] == 4200
    with pytest.raises(silver.CallError, match="no assistant message"):
        silver.parse_pi_events('{"type": "session"}')


def test_usage_stored_per_attempt_including_failures(data_dir):
    good = json.dumps(valid_for(make_item()))
    replies = iter(
        [
            silver.Reply("not json", silver.make_usage(10, 5, 1, 0.01, 100)),
            silver.Reply(good, silver.make_usage(20, 6, 2, 0.02, 200)),
        ]
    )
    s = silver.run_labeller(
        "gpt", data_dir, posting_id=1, set_name="de", call=lambda m, p: next(replies)
    )
    assert s["done"] == [1]
    rec = json.loads((data_dir / "silver" / "gpt" / "1.json").read_text())
    assert [u["input"] for u in rec["usage"]] == [10, 20]
    assert rec["usage"][1] == {
        "input": 20, "output": 6, "reasoning": 2, "cost_usd": 0.02, "duration_ms": 200
    }  # fmt: skip

    # an error attempt (CallError) is recorded too
    (data_dir / "silver" / "gpt" / "1.json").unlink()
    seq = iter([silver.CallError("boom", silver.make_usage(duration_ms=7)), None])

    def call(m, p):
        x = next(seq)
        if x:
            raise x
        return silver.Reply(good, silver.make_usage(1, 1))

    silver.run_labeller("gpt", data_dir, posting_id=1, set_name="de", call=call)
    rec = json.loads((data_dir / "silver" / "gpt" / "1.json").read_text())
    assert [u["duration_ms"] for u in rec["usage"]] == [7, 0]


class FakeOR:
    def __init__(self, text="{}"):
        self.kwargs = {}
        self.text = text
        outer = self

        class Completions:
            def create(self, **kw):
                outer.kwargs = kw
                usage = SimpleNamespace(
                    prompt_tokens=500, completion_tokens=250, cost=0.004,
                    completion_tokens_details=SimpleNamespace(reasoning_tokens=100),
                )  # fmt: skip
                msg = SimpleNamespace(content=outer.text)
                choice = SimpleNamespace(message=msg, finish_reason="stop")
                return SimpleNamespace(choices=[choice], usage=usage)

        self.chat = SimpleNamespace(completions=Completions())


def test_gemini_or_with_fake_client(data_dir):
    fake = FakeOR('{"a": 1}')
    r = silver.openrouter_call("google/gemini-4-argon", "PROMPT", client=fake)
    assert r.text == '{"a": 1}'
    assert r.usage["input"] == 500 and r.usage["output"] == 250
    assert r.usage["reasoning"] == 100 and r.usage["cost_usd"] == 0.004
    assert fake.kwargs["max_tokens"] == 16000 and fake.kwargs["model"] == "google/gemini-4-argon"
    assert fake.kwargs["messages"][0] == {"role": "system", "content": silver.SYSTEM_PROMPT}
    assert fake.kwargs["messages"][1]["content"] == "PROMPT"
    assert fake.kwargs["extra_body"]["usage"] == {"include": True}
    # through run_labeller
    good = json.dumps(valid_for(make_item()))
    s = silver.run_labeller(
        "gemini_or", data_dir, posting_id=1, set_name="de",
        call=lambda m, p: silver.openrouter_call(m, p, client=FakeOR(good)),
    )  # fmt: skip
    assert s["done"] == [1]
    rec = json.loads((data_dir / "silver" / "gemini_or" / "1.json").read_text())
    assert rec["model"] == "google/gemini-4-argon" and rec["usage"][0]["input"] == 500


def test_openrouter_empty_reply_is_a_failed_call():
    with pytest.raises(silver.CallError, match="empty reply") as e:
        silver.openrouter_call("m", "p", client=FakeOR(""))
    assert e.value.usage["output"] == 250


# ----- pilot and sets -------------------------------------------------------------------


@pytest.fixture
def big_dir(tmp_path):
    items = []
    order = 0
    for lang, n in (("de", 6), ("fr", 5), ("en", 9)):
        for _ in range(n):
            # order 0 is English; orders run interleaved so en order 0 is not the first en row
            items.append(make_item(100 + order, order, lang))
            order += 1
    items[-1]["order"], items[0]["order"] = 0, items[-1]["order"]  # en row becomes order 0
    with (tmp_path / "test_set.jsonl").open("w") as f:
        for it in items:
            f.write(json.dumps(it) + "\n")
    return tmp_path


def test_pilot_select_four_per_language_with_order_zero(big_dir):
    ids = silver.pilot_select(big_dir)
    assert len(ids) == 12
    items = {i["posting_id"]: i for i in silver.items_for(big_dir, "all")}
    by_lang = {
        lg: [items[p] for p in ids if items[p]["language"] == lg] for lg in ("en", "de", "fr")
    }
    assert all(len(v) == 4 for v in by_lang.values())
    assert 0 in [i["order"] for i in by_lang["en"]]
    pilot = silver.items_for(big_dir, "pilot")
    assert [i["posting_id"] for i in pilot] == [
        i["posting_id"] for i in sorted(pilot, key=lambda i: i["order"])
    ]
    assert len(silver.items_for(big_dir, "de")) == 6
    assert len(silver.items_for(big_dir, "all")) == 20


def test_pilot_set_needs_selection(tmp_path):
    (tmp_path / "test_set.jsonl").write_text(json.dumps(make_item()) + "\n")
    with pytest.raises(ValueError, match="pilot-select"):
        silver.items_for(tmp_path, "pilot")


# ----- report ---------------------------------------------------------------------------


def make_answer(item, **values):
    a = copy.deepcopy(valid_for(item))
    for k, v in values.items():
        if k == "office_cantons":
            a[k]["value"] = v
        else:
            a[k]["value"] = v
    return a


def store(data_dir, labeller, item, answer, usage=None):
    silver._store(data_dir, labeller, item, answer, "sha", usage)


def test_report_agreement_math(data_dir):
    from jobfacts import silver_report

    items = silver.items_for(data_dir, "all")  # ids 1 (de), 2 (de), 3 (en)
    for it in items:
        # a and b always equal; c differs on min_years of posting 1 only
        base = valid_for(it)
        for lab in ("gpt", "gemini_or"):
            store(
                data_dir,
                lab,
                it,
                silver.validate(base, it),
                [silver.make_usage(100, 50, 10, 0.01, 1000)],
            )
        c = copy.deepcopy(base)
        if it["posting_id"] == 1:
            c["min_years"]["value"] = "2"
        store(
            data_dir,
            "gemini",
            it,
            silver.validate(c, it),
            [silver.make_usage(200, 20, 0, 0.02, 2000)],
        )
    pids = [1, 2, 3]
    answers = {
        lab: silver_report.load_answers(data_dir, lab, pids)
        for lab in ("gpt", "gemini", "gemini_or")
    }
    counts = silver_report.agreement_counts(answers, items)
    my = counts[("min_years", "all")]
    assert my["all"] == 2 and my["partial"] == 1 and my["none"] == 0
    assert counts[("min_years", "de")]["partial"] == 1 and counts[("min_years", "en")]["all"] == 1
    pw = silver_report.pairwise(answers, items)
    assert pw[("gpt", "gemini_or")]["min_years"] == [3, 3]
    assert pw[("gpt", "gemini")]["min_years"] == [2, 3]
    assert silver_report.agreement_class(["a", "b", "c"]) == "none"
    assert silver_report.agreement_class(["a", "a"]) == "all"

    # owner labelled posting 1 (no contract_type: skipped)
    owner = {
        1: {
            "relevant": "true",
            "fields": {"min_years": {"value": "3"}, "work_mode": {"value": "hybrid"}},
            "office_cantons": ["BS"],
        }
    }
    oa = silver_report.owner_agreement(answers, owner, items)
    assert oa["gpt"]["min_years"] == [1, 1] and oa["gemini"]["min_years"] == [0, 1]
    assert oa["gpt"]["contract_type"] == [0, 0] and oa["gpt"]["office_cantons"] == [1, 1]

    text, summary = silver_report.build_report(data_dir, "all")
    assert "gemini_or" in text and "Missing" in text and "opus" in summary["missing"]
    assert "| gpt |" in text and "$0.0300" in text  # 3 postings x $0.01
    assert summary["labellers"] == ["gpt", "gemini", "gemini_or"]


def test_report_with_one_labeller_does_not_crash(data_dir):
    from jobfacts import silver_report

    it = silver.items_for(data_dir, "all")
    for i in it:
        store(data_dir, "gpt", i, silver.validate(valid_for(i), i))
    text, summary = silver_report.build_report(data_dir, "all")
    assert "Fewer than two" in text and summary["labellers"] == ["gpt"]
