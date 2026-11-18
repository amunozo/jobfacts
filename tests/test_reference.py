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


def store_all(data_dir, edits=None, labellers=None):
    """Store an answer of each panel labeller (or of `labellers`) for every posting.
    `edits[(lab, pid)]` is a function that changes the answer (a deep copy of the valid one)."""
    edits = edits or {}
    for item in silver.items_for(data_dir, "all"):
        for lab in labellers or reference.PANEL:
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


# ----- a panel of two labellers ---------------------------------------------------------

TWO = ("gpt", "opus")


def store_two(data_dir, edits=None):
    """gpt and opus answer every posting; gemini_or has no answers at all."""
    store_all(data_dir, edits, labellers=TWO)


def two_split_data(data_dir):
    store_two(
        data_dir,
        {
            ("gpt", 1): set_value("min_years", "1", ["S001"]),
            ("opus", 1): set_value("min_years", "3", ["S003"]),
        },
    )


def test_vote_for_three_is_unchanged_and_for_two_is_agree_or_split():
    assert reference.vote({"a": "x", "b": "x", "c": "x"}) == ("x", "3/3")
    assert reference.vote({"a": "x", "b": "x", "c": "y"}) == ("x", "2/3")
    assert reference.vote({"a": "x", "b": "y", "c": "z"}) == (None, "split")
    assert reference.vote({"a": "x", "b": "x"}) == ("x", "2/2")
    assert reference.vote({"a": "x", "b": "y"}) == (None, "split")
    # four labellers: a strict majority is needed, two against two is a split
    assert reference.vote({"a": "x", "b": "x", "c": "x", "d": "y"}) == ("x", "3/4")
    assert reference.vote({"a": "x", "b": "x", "c": "y", "d": "y"}) == (None, "split")


def test_combine_with_two_labellers_and_no_third_answer(data_dir):
    two_split_data(data_dir)
    assert not (data_dir / "silver" / "gemini_or").exists()
    s = reference.combine(data_dir, "all", TWO)
    assert s["written"] == 40 and s["missing"] == [] and s["unresolved"] == {1: ["min_years"]}
    assert s["support"]["2/2"] == 40 * len(reference.KEYS) - 1 and s["support"]["split"] == 1
    recs = by_pid(data_dir)
    r1, r2 = recs[1], recs[2]
    assert list(r1["models"]) == list(TWO) == list(r2["models"])
    assert r1["support"]["min_years"] == "split" and r1["splits"] == ["min_years"]
    assert r1["fields"]["min_years"]["value"] is None
    assert r1["votes"]["min_years"] == {"gpt": "1", "opus": "3"}
    assert r2["support"]["work_mode"] == "2/2" and r2["splits"] == []
    assert r2["fields"]["min_years"]["value"] == "3"


def test_two_labeller_sentences_come_from_opus(data_dir):
    store_two(
        data_dir,
        {
            ("gpt", 1): set_value("min_years", "3", ["S001"]),
            ("opus", 1): set_value("min_years", "3", ["S003"]),
        },
    )
    reference.combine(data_dir, "all", TWO)
    assert by_pid(data_dir)[1]["fields"]["min_years"]["sentences"] == [3]


def test_default_panel_reports_missing_when_gemini_or_has_no_answer(data_dir):
    store_two(data_dir)
    s = reference.combine(data_dir, "all")
    assert s["written"] == 0 and s["missing"] == list(range(1, 41))


def test_review_sheet_for_two_labellers_and_decision_round_trip(data_dir):
    two_split_data(data_dir)
    reference.combine(data_dir, "all", TWO)
    s = reference.review_sheet(data_dir, "all", panel=TWO)
    assert s["splits"] == 1 and s["audit"] == 30 and s["incomplete"] == 0
    items = read_items(data_dir)
    assert items[0]["kind"] == "split" and (items[0]["posting_id"], items[0]["key"]) == (
        1,
        "min_years",
    )
    audit = [i for i in items if i["kind"] == "audit"]
    assert len(audit) == 30
    recs = by_pid(data_dir)
    assert all(recs[i["posting_id"]]["support"][i["key"]] == "2/2" for i in audit)
    text = (data_dir / "silver" / "review_sheet.md").read_text()
    assert "the two labellers disagree" in text and "both agree" in text
    top = text.split("### 1.")[0]  # the header (posting texts may say "three")
    assert "three" not in top and "no two labellers" not in top
    item1 = text.split("### 1. ")[1].split("###")[0]
    assert "**1** (gpt)" in item1 and "**3** (opus)" in item1

    decisions = data_dir / "d.jsonl"
    decisions.write_text(json.dumps({"item": 1, "verdict": "ok", "value": "3"}))
    reference.review_apply(data_dir, decisions)
    reference.combine(data_dir, "all", TWO)
    r1 = by_pid(data_dir)[1]
    assert r1["fields"]["min_years"]["value"] == "3" and r1["splits"] == []
    assert r1["fields"]["min_years"]["sentences"] == [3]  # sentences of the labeller with "3"
    assert r1["reviewed"] == {"min_years": "decided"}


def test_review_sheet_refuses_a_different_panel_than_the_reference(data_dir):
    two_split_data(data_dir)
    reference.combine(data_dir, "all", TWO)
    with pytest.raises(ValueError, match="gpt, opus"):
        reference.review_sheet(data_dir, "all")  # default three-labeller panel
    assert not (data_dir / "silver" / "review_sheet.md").exists()
    assert not (data_dir / "silver" / "review_items.json").exists()
    # the other way round: a three-labeller reference and a two-labeller sheet
    store_all(data_dir)
    reference.combine(data_dir, "all")
    with pytest.raises(ValueError, match="gpt, gemini_or, opus"):
        reference.review_sheet(data_dir, "all", panel=TWO)
    assert not (data_dir / "silver" / "review_sheet.md").exists()
    # without a reference file there is nothing to contradict
    (data_dir / reference.REFERENCE_FILE).unlink()
    assert reference.review_sheet(data_dir, "all", audit=1, panel=TWO)["audit"] == 1


@pytest.mark.parametrize(
    "panel, message",
    [
        (("gpt",), "at least 2"),
        ((), "at least 2"),
        (("gpt", "nobody"), "unknown labellers"),
        (("gpt", "gpt"), "distinct"),
        (("gpt", "opus", "opus"), "distinct"),
    ],
)
def test_invalid_panels_raise(data_dir, panel, message):
    store_two(data_dir)
    for call in (
        lambda: reference.validate_panel(panel),
        lambda: reference.combine(data_dir, "all", panel),
        lambda: reference.review_sheet(data_dir, "all", panel=panel),
        lambda: reference.load_panel(data_dir, [1], panel),
    ):
        with pytest.raises(ValueError, match=message):
            call()
    assert not (data_dir / reference.REFERENCE_FILE).exists()


def test_cli_panel_option(data_dir, capsys):
    from jobfacts import cli

    two_split_data(data_dir)
    base = ["silver", "--set", "all", "--data-dir", str(data_dir)]
    assert cli.main([*base[:1], "combine", *base[1:], "--panel", "gpt,opus"]) == 0
    out = capsys.readouterr().out
    assert "2/2" in out and "not yet answered" not in out
    assert cli.main(["silver", "review-sheet", *base[1:], "--panel", "gpt,opus"]) == 0
    capsys.readouterr()
    # a different panel than the reference: refused with the panel of the reference named
    assert cli.main(["silver", "review-sheet", *base[1:]]) == 2
    assert "gpt, opus" in capsys.readouterr().out
    for bad in ("gpt", "gpt,nobody", "gpt,gpt", ""):
        assert cli.main(["silver", "combine", *base[1:], "--panel", bad]) == 2
        assert cli.main(["silver", "review-sheet", *base[1:], "--panel", bad]) == 2
        capsys.readouterr()
    # the default panel is the three-labeller one: gemini_or has no answers, so all are missing
    assert cli.main(["silver", "combine", *base[1:]]) == 0
    assert "not yet answered by all labellers of the panel (gpt, gemini_or, opus)" in (
        capsys.readouterr().out
    )


def test_evaluate_header_names_the_labellers_of_the_reference(data_dir):
    two_split_data(data_dir)
    reference.combine(data_dir, "all", TWO)
    panel = reference.reference_panel(data_dir, "all")
    assert panel == TWO

    def ext():
        return {"error": None, "values": {n: next(iter(f.values)) for n, f in ev.FIELDS.items()},
                "sentences": {}, "office_cantons": ["BS"],
                "office_cantons_sentences": [], "office_places": [], "details": {},
                "cost_usd": 0, "latency_ms": 1, "tokens_in": 1, "tokens_out": 1}  # fmt: skip

    item = {"posting_id": 1, "language": "en", "group": "g", "origin": "board", "order": 0}
    ref = by_pid(data_dir)[1]
    text = ev.build_report([item], {1: ref}, {"m": {1: ext()}}, {"m": {}}, {1: 0.9}, "d",
                           "silver", "Audit: x.", {}, "all", panel)  # fmt: skip
    header = next(line for line in text.splitlines() if line.startswith("**Reference"))
    assert "two model labellers (gpt, opus)" in header and "owner decides" in header
    assert "three" not in header.lower() and "three" not in text.lower().split("## 1.")[0]
    assert "three" not in text.split("- The reference is")[1].split("\n")[0].lower()
    # the default (no panel given) still describes the usual three
    text3 = ev.build_report([item], {1: ref}, {"m": {1: ext()}}, {"m": {}}, {1: 0.9}, "d",
                            "silver", "Audit: x.", {})  # fmt: skip
    assert "three model labellers (gpt, gemini_or, opus)" in text3


def test_silver_report_with_two_complete_labellers_and_a_partial_third(data_dir):
    from jobfacts import silver_report

    two_split_data(data_dir)
    item = silver.items_for(data_dir, "all")[0]
    silver._store(data_dir, "gemini_or", item, silver.validate(valid_for(item), item), "sha")
    text, summary = silver_report.build_report(data_dir, "all")
    assert summary["labellers"] == ["gpt", "opus"] and summary["missing"] == ["gemini", "gemini_or"]
    assert "Labellers compared: gpt, opus." in text
    assert "| gpt - opus |" in text and "gemini_or" in text
    assert 0 < summary["all_agree"] < 1
    assert "1/40 answers" in text  # the partly answered labeller in the validation table
    assert "both labellers agree" not in text  # no audit yet: the sentence is not printed
