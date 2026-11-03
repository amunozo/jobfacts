import copy
import json

import pytest

from jobfacts import evaluate as ev
from jobfacts import reference, silver
from jobfacts.stats import wilson
from test_silver import make_item, valid_for


@pytest.fixture
def data_dir(tmp_path):
    # 40 de postings: enough unanimous (posting, key) pairs to sample from
    with (tmp_path / "test_set.jsonl").open("w") as f:
        for i in range(1, 41):
            f.write(json.dumps(make_item(i, i - 1, "de" if i % 2 else "en")) + "\n")
    return tmp_path


def store_all(data_dir, edits=None):
    """Store an answer of each panel labeller for every posting. `edits[(lab, pid)]` is a
    function that changes the answer (a deep copy of the valid one)."""
    edits = edits or {}
    for item in silver.items_for(data_dir, "all"):
        for lab in reference.PANEL:
            a = copy.deepcopy(valid_for(item))
            if (lab, item["posting_id"]) in edits:
                edits[(lab, item["posting_id"])](a)
            silver._store(data_dir, lab, item, silver.validate(a, item), "sha")


def set_value(key, value, ids=None):
    def edit(a):
        a[key]["value"] = value
        if ids is not None:
            a[key]["sentences"] = ids
            a[key]["translation"] = "x" if ids else ""

    return edit


def by_pid(data_dir):
    return reference.load_reference(data_dir)


def test_vote_majority_split_and_cantons():
    assert reference.vote({"a": "x", "b": "x", "c": "x"}) == ("x", "3/3")
    assert reference.vote({"a": "x", "b": "y", "c": "x"}) == ("x", "2/3")
    assert reference.vote({"a": "x", "b": "y", "c": "z"}) == (None, "split")
    # cantons are compared as sets: order does not matter
    a = {"office_cantons": {"value": ["ZH", "BS"]}}
    b = {"office_cantons": {"value": ["BS", "ZH"]}}
    c = {"office_cantons": {"value": ["BS"]}}
    cmp = {n: reference._cmp(x, "office_cantons") for n, x in zip("abc", (a, b, c), strict=True)}
    assert reference.vote(cmp)[1] == "2/3"


def test_combine_majority_split_and_record_shape(data_dir):
    store_all(
        data_dir,
        {
            ("opus", 1): set_value("min_years", "2", ["S003"]),  # gpt, gemini_or agree on "3"
            ("gpt", 2): set_value("min_years", "1", ["S003"]),  # three different values
            ("gemini_or", 2): set_value("min_years", "2", ["S003"]),
            ("opus", 2): set_value("min_years", "3", ["S003"]),
        },
    )
    s = reference.combine(data_dir, "all")
    assert s["written"] == 40 and s["missing"] == [] and s["unresolved"] == {2: ["min_years"]}
    recs = by_pid(data_dir)
    r1, r2 = recs[1], recs[2]
    assert r1["labeller"] == "silver" and set(r1["models"]) == set(reference.PANEL)
    assert r1["fields"]["min_years"]["value"] == "3"
    assert r1["support"]["min_years"] == "2/3" and r1["support"]["work_mode"] == "3/3"
    assert r1["votes"]["min_years"] == {"gpt": "3", "gemini_or": "3", "opus": "2"}
    assert r2["fields"]["min_years"]["value"] is None and r2["splits"] == ["min_years"]
    assert r2["support"]["min_years"] == "split" and r2["fields"]["min_years"]["sentences"] == []
    # shape of an owner label
    assert r1["relevant"] == "true" and r1["office_cantons"] == ["BS"]
    assert r1["office_places"] and r1["office_cantons_sentences"] == [-1]
    assert set(r1["fields"]) == set(silver.FIELDS)


def test_combine_skips_postings_without_all_three(data_dir):
    store_all(data_dir)
    (data_dir / "silver" / "gpt" / "5.json").unlink()
    s = reference.combine(data_dir, "all")
    assert s["written"] == 39 and s["missing"] == [5]
    assert 5 not in by_pid(data_dir)


def test_sentences_from_opus_when_in_majority_else_first_majority(data_dir):
    store_all(
        data_dir,
        {
            ("gpt", 1): set_value("min_years", "3", ["S001"]),
            ("gemini_or", 1): set_value("min_years", "3", ["S002"]),
            ("opus", 1): set_value("min_years", "3", ["S003"]),
            # opus dissents: sentences of the first majority labeller (gpt)
            ("gpt", 2): set_value("min_years", "2", ["S001"]),
            ("gemini_or", 2): set_value("min_years", "2", ["S002"]),
            ("opus", 2): set_value("min_years", "3", ["S003"]),
        },
    )
    reference.combine(data_dir, "all")
    recs = by_pid(data_dir)
    assert recs[1]["fields"]["min_years"]["sentences"] == [3]
    assert recs[2]["fields"]["min_years"]["value"] == "2"
    assert recs[2]["fields"]["min_years"]["sentences"] == [1]


def test_office_cantons_set_majority_and_places(data_dir):
    store_all(
        data_dir,
        {
            ("gpt", 1): set_value("office_cantons", ["BS", "ZH"], ["L"]),
            ("gemini_or", 1): set_value("office_cantons", ["ZH", "BS"], ["L"]),
            ("opus", 1): set_value("office_cantons", ["BS"], ["L"]),
        },
    )
    reference.combine(data_dir, "all")
    r = by_pid(data_dir)[1]
    assert set(r["office_cantons"]) == {"BS", "ZH"} and r["support"]["office_cantons"] == "2/3"
    assert all(k.startswith("place:") for k in r["office_places"])


def test_low_confidence_flags(data_dir):
    def low(a):
        a["german"]["confidence"] = "low"

    store_all(data_dir, {("gpt", 1): low})
    reference.combine(data_dir, "all")
    assert by_pid(data_dir)[1]["low_confidence"] == {"german": ["gpt"]}


def split_data(data_dir):
    store_all(
        data_dir,
        {
            ("gpt", 1): set_value("min_years", "1", ["S003"]),
            ("gemini_or", 1): set_value("min_years", "2", ["S003"]),
            ("opus", 1): set_value("min_years", "3", ["S003"]),
        },
    )


def read_items(data_dir):
    return reference.load_issued(data_dir)


def test_review_sheet_is_deterministic_and_has_splits_and_audit(data_dir, tmp_path_factory):
    split_data(data_dir)
    s = reference.review_sheet(data_dir, "all")
    assert s["splits"] == 1 and s["audit"] == 30
    items = read_items(data_dir)
    assert items[0]["kind"] == "split" and items[0]["key"] == "min_years"
    sample = [(i["posting_id"], i["key"]) for i in items if i["kind"] == "audit"]
    assert len(set(sample)) == 30 and (1, "min_years") not in sample
    text = (data_dir / "silver" / "review_sheet.md").read_text()
    assert "### 1. [split] posting 1" in text and "allowed:" in text and "EN:" in text
    assert "mindestens drei Jahre" in text  # the cited sentence in the original language

    other = tmp_path_factory.mktemp("other")
    (other / "test_set.jsonl").write_text((data_dir / "test_set.jsonl").read_text())
    split_data(other)
    reference.review_sheet(other, "all")
    again = [(i["posting_id"], i["key"]) for i in read_items(other) if i["kind"] == "audit"]
    assert again == sample


def test_review_sheet_excludes_earlier_items_and_keeps_pending(data_dir):
    split_data(data_dir)
    reference.review_sheet(data_dir, "all")
    first = {(i["posting_id"], i["key"]) for i in read_items(data_dir)}
    # nothing decided: the audit items wait, so no new sample is drawn and numbers are kept
    s = reference.review_sheet(data_dir, "all")
    assert s["new"] == 0 and {(i["posting_id"], i["key"]) for i in read_items(data_dir)} == first
    # decide everything: the next sheet samples only keys not on the first sheet
    decisions = data_dir / "d.jsonl"
    lines = [
        {"item": i["item"], "verdict": "ok"}
        if i["kind"] == "audit"
        else {"item": i["item"], "verdict": "ok", "value": "2"}
        for i in read_items(data_dir)
    ]
    decisions.write_text("\n".join(json.dumps(x) for x in lines))
    reference.review_apply(data_dir, decisions)
    s = reference.review_sheet(data_dir, "all")
    assert s["audit"] == 30 and s["splits"] == 0
    second = {(i["posting_id"], i["key"]) for i in read_items(data_dir) if i["sheet"] == 2}
    assert len(second) == 30 and not second & first
    assert min(i["item"] for i in read_items(data_dir) if i["sheet"] == 2) == 32


def test_review_apply_validates_and_combine_overrides(data_dir):
    split_data(data_dir)
    reference.review_sheet(data_dir, "all", audit=2)
    items = read_items(data_dir)
    audit = [i for i in items if i["kind"] == "audit"]
    bad = data_dir / "bad.jsonl"
    bad.write_text(json.dumps({"item": 1, "verdict": "ok", "value": "99"}) + "\n")
    with pytest.raises(ValueError, match="not allowed"):
        reference.review_apply(data_dir, bad)
    bad.write_text(json.dumps({"item": 1, "verdict": "ok"}) + "\n")
    with pytest.raises(ValueError, match="needs a value"):
        reference.review_apply(data_dir, bad)
    bad.write_text(json.dumps({"item": 1, "posting_id": 7, "key": "german", "verdict": "ok"}))
    with pytest.raises(ValueError, match="not 7"):
        reference.review_apply(data_dir, bad)
    assert not (data_dir / "silver" / reference.DECISIONS_FILE).exists()

    a0, a1 = audit
    good = data_dir / "good.jsonl"
    wrong_value = next(v for v in reference.allowed_values(a1["key"]) if v != "")
    good.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"item": 1, "verdict": "wrong", "value": "2"},
                {"item": a0["item"], "verdict": "ok"},
                {"item": a1["item"], "verdict": "wrong", "value": wrong_value},
            ]
        )
    )
    assert reference.review_apply(data_dir, good)["stored"] == 3
    reference.combine(data_dir, "all")
    recs = by_pid(data_dir)
    r1 = recs[1]
    assert r1["fields"]["min_years"]["value"] == "2" and r1["splits"] == []
    assert r1["fields"]["min_years"]["sentences"] == [3]  # sentences of the labeller with "2"
    assert r1["reviewed"] == {"min_years": "decided"}
    assert recs[a0["posting_id"]]["reviewed"][a0["key"]] == "confirmed"
    got = recs[a1["posting_id"]]
    if a1["key"] == "office_cantons":
        assert got["office_cantons"] == [wrong_value]
    elif a1["key"] == "relevant":
        assert got["relevant"] == wrong_value
    else:
        assert got["fields"][a1["key"]]["value"] == wrong_value
    assert got["reviewed"][a1["key"]] == "corrected"
    # a later decision replaces an earlier one
    again = data_dir / "again.jsonl"
    again.write_text(json.dumps({"item": 1, "verdict": "wrong", "value": "3"}))
    reference.review_apply(data_dir, again)
    reference.combine(data_dir, "all")
    assert by_pid(data_dir)[1]["fields"]["min_years"]["value"] == "3"


def test_audit_stats_and_wilson(data_dir):
    split_data(data_dir)
    reference.review_sheet(data_dir, "all", audit=4)
    items = read_items(data_dir)
    audit = [i for i in items if i["kind"] == "audit"]
    lines = [{"item": 1, "verdict": "ok", "value": "2"}]
    for n, i in enumerate(audit):
        if n == 0 and i["key"] != "office_cantons":
            lines.append(
                {
                    "item": i["item"],
                    "verdict": "wrong",
                    "value": reference.allowed_values(i["key"])[-1],
                }
            )
        else:
            lines.append({"item": i["item"], "verdict": "ok"})
    f = data_dir / "d.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in lines))
    reference.review_apply(data_dir, f)
    st = reference.audit_stats(data_dir)
    wrong = sum(x["verdict"] == "wrong" for x in lines)
    assert st["audited"] == 4 and st["correct"] == 4 - wrong
    assert st["splits"] == 1 and st["splits_resolved"] == 1
    assert "of 4 unanimous answers correct" in reference.audit_text(st)
    lo, hi = wilson(27, 30)
    assert 0.74 < lo < 0.76 and 0.96 < hi < 0.97
    assert wilson(0, 0) == (0.0, 1.0)

    from jobfacts import silver_report

    text, summary = silver_report.build_report(data_dir, "all")
    assert "## 7. Audit of the reference" in text and summary["audit"]["audited"] == 4


def test_evaluate_silver_mode_skips_null_keys():
    def ext(**v):
        return {"error": None, "values": {n: next(iter(f.values)) for n, f in ev.FIELDS.items()}
                | v, "sentences": {}, "office_cantons": ["BS"], "office_cantons_sentences": [],
                "office_places": [], "details": {}, "cost_usd": 0, "latency_ms": 1,
                "tokens_in": 1, "tokens_out": 1}  # fmt: skip

    ref_fields = {
        n: {"value": next(iter(f.values)), "sentences": [], "note": ""}
        for n, f in ev.FIELDS.items()
    }
    ref_fields["min_years"]["value"] = None  # split
    ref = {"posting_id": 1, "relevant": None, "fields": ref_fields, "office_cantons": None,
           "office_cantons_sentences": [], "office_places": None}  # fmt: skip
    item = {"posting_id": 1, "language": "en", "group": "g", "origin": "board", "order": 0}
    records, counts = ev.score_records([item], {1: ref}, {"m": {1: ext()}}, {1: 0.9})
    fields = {r["field"] for r in records}
    assert "min_years" not in fields and "seniority" not in fields
    assert "office_cantons" not in fields and "office_places" not in fields
    assert "relevant" not in fields and counts["null"] == 1
    assert "work_mode" in fields and all(r["ok"] for r in records)
    # the report builder copes with null keys everywhere
    text = ev.build_report([item], {1: ref}, {"m": {1: ext()}}, {"m": {}}, {1: 0.9}, "d",
                           "silver", "Audit: x.", {})  # fmt: skip
    assert "Reference: silver labels" in text and "Audit: x." in text
    assert "Blind vs pre-filled" not in text and "## 9." in text


def test_reference_vs_owner_agreement():
    def lab(value, cantons, relevant="true"):
        return {"relevant": relevant, "office_cantons": cantons,
                "fields": {"work_mode": {"value": value}}}  # fmt: skip

    ref = {1: lab("hybrid", ["BS"]), 2: lab(None, None, None), 3: lab("onsite", ["ZH"])}
    own = {1: lab("hybrid", ["BS"]), 2: lab("hybrid", ["BS"]), 3: lab("hybrid", ["ZH"], "false")}
    out = ev.reference_vs_owner(ref, own)
    assert out["work_mode"] == [1, 2] and out["office_cantons"] == [2, 2]
    assert out["relevant"] == [1, 2] and out["german"] == [0, 0]
