import json

import pytest

from fakes import FakeJev, posting
from jobfacts import evaluate as ev
from jobfacts import ordertest
from jobfacts.extract import jev
from jobfacts.extract.spec import FIELDS, order_values


def ext(error=None, locs=(), loc_sent=(), sentences=None, details=None, **values):
    base = {n: next(iter(f.values)) for n, f in FIELDS.items()}
    base.update(values)
    return {
        "error": error,
        "values": {} if error else base,
        "sentences": sentences or {},
        "office_locations": list(locs),
        "office_locations_sentences": list(loc_sent),
        "details": details or {},
        "cost_usd": 0.01,
        "latency_ms": 100,
        "tokens_in": 10,
        "tokens_out": 1,
    }


def label(pid=1, mode="blind", relevant="true", locs=(), loc_sent=(), sentences=None, **values):
    base = {n: next(iter(f.values)) for n, f in FIELDS.items()}
    base.update(values)
    sentences = sentences or {}
    return {
        "posting_id": pid,
        "mode": mode,
        "relevant": relevant,
        "fields": {
            n: {"value": v, "sentences": sentences.get(n, []), "note": ""} for n, v in base.items()
        },  # fmt: skip
        "office_locations": list(locs),
        "office_locations_sentences": list(loc_sent),
    }


def item(pid, language="en", group="en_ai", origin="board"):
    return {"posting_id": pid, "language": language, "group": group, "origin": origin}


def test_wilson_interval():
    lo, hi = ev.wilson(8, 10)
    assert lo == pytest.approx(0.490, abs=0.01) and hi == pytest.approx(0.943, abs=0.01)
    assert ev.wilson(0, 0) == (0.0, 1.0)
    assert ev.wilson(10, 10)[1] == 1.0 and ev.wilson(0, 10)[0] == 0.0
    assert ev.cell(8, 10) == "80% [49-94] n=10"


def test_accuracy_error_wrong_and_slices():
    items = [item(1), item(2, "de", "de_other"), item(3)]
    labels = {p: label(p, work_mode="hybrid") for p in (1, 2, 3)}
    exts = {
        "m": {
            1: ext(work_mode="hybrid"),
            2: ext(work_mode="onsite"),
            3: ext(error="boom"),
        }
    }
    records, counts = ev.score_records(items, labels, exts, {})
    assert ev.summarize(records, extractor="m", field="work_mode") == (1, 3, 1)
    assert ev.summarize(records, extractor="m", field="work_mode", language="de") == (0, 1, 0)
    assert ev.summarize(records, extractor="m", field="work_mode", group="en_ai") == (1, 2, 1)
    assert counts["labelled"] == 3


def test_latest_label_wins(tmp_path):
    from jobfacts import labels as lab

    path = tmp_path / "labels.jsonl"
    path.write_text(
        "\n".join(json.dumps(label(1, work_mode=v)) for v in ("onsite", "remote")) + "\n"
    )
    assert lab.latest(path)[1]["fields"]["work_mode"]["value"] == "remote"


def test_unsure_excluded_and_relevance_threshold():
    items = [item(1), item(2), item(3)]
    labels = {1: label(1, relevant="true"), 2: label(2, relevant="unsure"),
              3: label(3, relevant="false")}  # fmt: skip
    rel = {1: 0.5, 2: 0.9, 3: 0.49}
    records, counts = ev.score_records(items, labels, {}, rel)
    assert counts["unsure"] == 1
    assert ev.summarize(records, extractor=ev.RELEVANCE_EXTRACTOR, field="relevant") == (2, 2, 0)


def test_set_match_exact_and_canton():
    items = [item(1)]
    labels = {1: label(1, locs=["city:Zürich"])}
    exts = {"a": {1: ext(locs=["canton:ZH"])}, "b": {1: ext(locs=["city:Zürich"])},
            "c": {1: ext(locs=["city:Zürich", "city:Winterthur"])}}  # fmt: skip
    records, _ = ev.score_records(items, labels, exts, {})
    s = ev.summarize
    assert s(records, extractor="a", field="office_locations")[0] == 0
    assert s(records, extractor="a", field="office_locations_canton")[0] == 1
    assert s(records, extractor="b", field="office_locations")[0] == 1
    # Winterthur is also in ZH, so canton-level still matches; exact does not
    assert s(records, extractor="c", field="office_locations") == (0, 1, 0)
    assert s(records, extractor="c", field="office_locations_canton") == (1, 1, 0)


def test_seniority_derived_from_both_parts():
    items = [item(1)]
    labels = {1: label(1, min_years="6", title_level="not_stated")}
    exts = {"m": {1: ext(min_years="8", title_level="senior")}}
    records, _ = ev.score_records(items, labels, exts, {})
    assert ev.summarize(records, extractor="m", field="seniority")[0] == 1
    assert ev.summarize(records, extractor="m", field="min_years")[0] == 0


def test_evidence_agreement():
    items = [item(1), item(2), item(3)]
    labels = {
        1: label(1, work_mode="hybrid", sentences={"work_mode": [4]}),
        2: label(2, work_mode="hybrid", sentences={"work_mode": [4]}),
        3: label(3, work_mode="remote", sentences={"work_mode": [4]}),
    }
    exts = {"m": {
        1: ext(work_mode="hybrid", sentences={"work_mode": [4, 9]}),  # overlap
        2: ext(work_mode="hybrid", sentences={"work_mode": [7]}),  # no overlap
        3: ext(work_mode="onsite", sentences={"work_mode": [4]}),  # wrong value: skipped
    }}  # fmt: skip
    agree = ev.evidence_agreement(items, labels, exts)
    assert agree[("work_mode", "m")] == (1, 2)
    assert ("german", "m") not in agree  # not_mentioned needs no sentence


def test_calibration_bins():
    items = [item(1), item(2)]
    labels = {1: label(1, work_mode="hybrid"), 2: label(2, work_mode="hybrid")}

    def details(p):
        return {"work_mode": {"probabilities": {"hybrid": p, "onsite": p}, "confidence": p}}

    e = {1: ext(work_mode="hybrid", details=details(0.95)),
         2: ext(work_mode="onsite", details=details(0.6))}  # fmt: skip
    cal = ev.calibration(items, labels, e)
    assert cal["probability"][3] == [1, 1]
    assert cal["probability"][1] == [0, 1]
    assert cal["confidence"][0] == [0, 0]


def test_confusion_and_cost():
    items = [item(1), item(2)]
    labels = {1: label(1, german="required_fluent"), 2: label(2, german="required_fluent")}
    by_pid = {1: ext(german="nice_to_have"), 2: ext(error="x")}
    c = ev.confusion(items, labels, by_pid, "german")
    assert c[("required_fluent", "nice_to_have")] == 1 and c[("required_fluent", "ERROR")] == 1
    s = ev.cost_summary(by_pid)
    assert s["n"] == 2 and s["errors"] == 1 and s["cost"] == pytest.approx(0.02)


def test_blind_vs_prefilled_split():
    items = [item(1), item(2)]
    labels = {1: label(1, mode="blind", work_mode="hybrid"),
              2: label(2, mode="prefilled", work_mode="hybrid")}  # fmt: skip
    exts = {n: {p: ext(work_mode="hybrid") for p in (1, 2)} for n in ("jev", "deepseek", "mistral")}
    exts["mistral"][2] = ext(work_mode="onsite")  # still a 2-of-3 majority
    agree, none = ev.prefill_agreement(items, labels, exts)
    assert agree[("work_mode", "blind")] == [1, 1]
    assert agree[("work_mode", "prefilled")] == [1, 1]
    labels[2] = label(2, mode="prefilled", work_mode="remote")
    agree, _ = ev.prefill_agreement(items, labels, exts)
    assert agree[("work_mode", "prefilled")] == [0, 1]


def test_build_report_without_and_with_labels():
    items = [item(1)]
    exts = {"jev": {1: ext(work_mode="hybrid")}}
    text = ev.build_report(items, {}, exts, {}, {}, "2026-01-01")
    assert "No labels yet" in text and "## 5. Cost" in text
    text = ev.build_report(items, {1: label(1, work_mode="hybrid")}, exts, {}, {1: 0.9}, "d")
    assert "### work_mode" in text and "100% [" in text and "## 6. Blind" in text


def test_order_values_and_extractor_default_unchanged():
    values = FIELDS["work_mode"].values
    assert list(order_values("work_mode", values, "reversed")) == list(values)[::-1]
    shuffled = list(order_values("work_mode", values, "shuffled"))
    assert shuffled == list(order_values("work_mode", values, "shuffled"))
    assert sorted(shuffled) == sorted(values)
    default = jev.JevExtractor(FakeJev())
    assert default.spec_version() == jev.JevExtractor(FakeJev(), "original").spec_version()
    assert jev.JevExtractor(FakeJev(), "reversed").spec_version() != default.spec_version()
    client = FakeJev()
    r = jev.JevExtractor(client, ordering="reversed").extract(posting())
    assert r.values["work_mode"] == "remote"  # the fake picks the first option
    assert r.details["ordering"] == "reversed"
    assert "ordering" not in jev.JevExtractor(FakeJev()).extract(posting()).details


def test_order_compare():
    a = {1: ext(work_mode="not_stated", german="required_fluent", locs=["city:Zürich"]),
         2: ext(work_mode="hybrid"), 3: ext(error="x")}  # fmt: skip
    b = {1: ext(work_mode="hybrid", german="not_mentioned", locs=[]),
         2: ext(work_mode="hybrid"), 3: ext()}  # fmt: skip
    r = ordertest.compare(a, b)
    assert r["work_mode"]["n"] == 2 and r["work_mode"]["changed"] == 1
    assert r["work_mode"]["directions"] == {"neutral_to_value": 1}
    assert r["german"]["directions"] == {"value_to_neutral": 1}
    assert r["office_locations"]["directions"] == {"removed": 1}
    assert "work_mode" in ordertest.render({"reversed": r})
