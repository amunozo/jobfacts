"""Labels file and labelling API, with synthetic postings in a temporary directory."""

import json

import pytest
from fastapi.testclient import TestClient

from jobfacts import labels
from jobfacts.extract.spec import FIELDS
from jobfacts.label_app import prefill
from jobfacts.label_app.app import create_app

GOOD = {
    "work_mode": ("hybrid", [1]),
    "remote_from_switzerland": ("not_applicable", []),
    "german": ("not_mentioned", []),
    "french": ("not_mentioned", []),
    "min_years": ("3", [2]),
    "title_level": ("not_stated", []),
}


def payload(**overrides):
    fields = {k: {"value": v, "sentences": s} for k, (v, s) in GOOD.items()}
    fields.update(overrides)
    return {
        "fields": fields,
        "relevant": "true",
        "office_cantons": ["ZH"],
        "office_cantons_sentences": [3],
    }


def item(pid, order, language="en"):
    text = "We work in a hybrid mode. You have 3 years of experience. Office in Zürich."
    return {
        "posting_id": pid,
        "order": order,
        "language": language,
        "group": "en_ai",
        "employer": "Acme",
        "title": "ML Engineer",
        "location_raw": "Zürich, Switzerland",
        "description_text": text,
        "sentences": [
            {"id": 1, "text": "We work in a hybrid mode."},
            {"id": 2, "text": "You have 3 years of experience."},
            {"id": 3, "text": "Office in Zürich."},
        ],
    }


def extraction(**values):
    base = {n: next(iter(f.values)) for n, f in FIELDS.items()}
    base.update(values)
    return {
        "values": base,
        "sentences": {n: ([1] if v not in FIELDS[n].no_quote else []) for n, v in base.items()},
        "office_cantons": ["ZH"],
        "office_cantons_sentences": [3],
    }


@pytest.fixture
def client(tmp_path):
    items = [item(1, 0), item(2, 1, "de"), item(3, 2)]
    ext = {
        2: {
            "a": extraction(work_mode="hybrid", min_years="3", german="required_fluent"),
            "b": extraction(work_mode="hybrid", min_years="5", german="nice_to_have"),
            "c": extraction(work_mode="onsite", min_years="7", german="required_level_not_stated"),
        },
        1: {"a": extraction(work_mode="remote")},
    }
    app = create_app(items, tmp_path / "labels.jsonl", ext, blind_count=2)
    c = TestClient(app)
    c.labels_path = tmp_path / "labels.jsonl"
    return c


def test_validation_errors(client):
    bad = payload(
        work_mode={"value": "sometimes", "sentences": [1]},
        min_years={"value": "3", "sentences": [99]},
        german={"value": "required_fluent", "sentences": []},
        french={"value": "not_mentioned", "sentences": [1]},
    )
    bad["relevant"] = "maybe"
    bad["office_cantons"] = ["XX"]
    r = client.put("/api/items/1/label", json=bad)
    assert r.status_code == 422
    errors = r.json()["errors"]
    assert set(errors) == {
        "work_mode",
        "min_years",
        "german",
        "french",
        "relevant",
        "office_cantons",
    }
    assert not client.labels_path.exists()  # nothing written


def test_missing_field_and_unknown_posting(client):
    p = payload()
    del p["fields"]["title_level"]
    assert client.put("/api/items/1/label", json=p).json()["errors"] == {"title_level": "missing"}
    assert client.put("/api/items/99/label", json=payload()).status_code == 404
    assert client.get("/api/items/99").status_code == 404


def test_latest_wins_and_append_only(client):
    assert client.put("/api/items/1/label", json=payload()).status_code == 200
    second = payload(work_mode={"value": "remote", "sentences": [1], "note": "changed"})
    assert client.put("/api/items/1/label", json=second).status_code == 200
    lines = client.labels_path.read_text().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["fields"]["work_mode"]["value"] == "hybrid"
    saved = client.get("/api/items/1").json()["label"]
    assert saved["fields"]["work_mode"]["value"] == "remote"
    assert saved["fields"]["work_mode"]["note"] == "changed"
    assert saved["mode"] == "blind" and saved["labeller"] == "owner"
    assert len(labels.latest(client.labels_path)) == 1


def test_blind_shows_no_model_output(client):
    r = client.get("/api/items/1").json()  # order 0 < blind_count, and it has an extraction
    assert r["mode"] == "blind" and r["prefill"] is None
    assert r["canton_hints"] == ["ZH"]
    assert r["place_candidates"] == {
        "ZH": [
            {
                "key": "place:261:Zürich",
                "name": "Zürich",
                "commune": "Zürich",
                "matched": "Zürich",
                "where": "location field",
            }  # fmt: skip
        ]
    }
    assert {f["name"] for f in r["fields"]} == set(FIELDS)


def test_prefilled_majority_rule(client):
    r = client.get("/api/items/2").json()
    assert r["mode"] == "blind"  # order 1 is still inside blind_count=2
    r = client.get("/api/items/3").json()
    assert r["mode"] == "prefilled" and r["prefill"] is None  # no extractions at all


def test_majority_two_of_three_and_three_way_disagreement():
    by_model = {
        "a": extraction(work_mode="hybrid", min_years="3", german="required_fluent"),
        "b": extraction(work_mode="hybrid", min_years="5", german="nice_to_have"),
        "c": extraction(work_mode="onsite", min_years="7", german="required_level_not_stated"),
    }
    pre = prefill.majority(by_model)["fields"]
    assert pre["work_mode"] == {"value": "hybrid", "sentences": [1]}
    assert "min_years" not in pre and "german" not in pre  # all three differ
    assert pre["french"]["value"] == "not_mentioned"  # 3 of 3
    assert "model" not in json.dumps(pre).lower()


def test_prefill_sentences_and_locations():
    a, b, c = (extraction(work_mode="hybrid") for _ in range(3))
    a["sentences"]["work_mode"] = [1, 2]
    b["sentences"]["work_mode"] = [2]
    c["sentences"]["work_mode"] = [5]
    pre = prefill.majority({"a": a, "b": b, "c": c})
    assert pre["fields"]["work_mode"]["sentences"] == [2]  # chosen by at least two
    assert pre["office_cantons"] == ["ZH"] and pre["office_cantons_sentences"] == [3]
    b["office_cantons"] = ["BE"]
    c["office_cantons"] = ["GE", "outside_switzerland"]
    assert prefill.majority({"a": a, "b": b, "c": c}).get("office_cantons") is None
    b["office_cantons"] = ["outside_switzerland", "GE"]  # sets, not lists, are compared
    assert prefill.majority({"a": a, "b": b, "c": c})["office_cantons"] == [
        "GE",
        "outside_switzerland",
    ]


def test_prefilled_item_via_api(tmp_path):
    ext = {
        1: {
            "a": extraction(work_mode="hybrid"),
            "b": extraction(work_mode="hybrid"),
            "c": extraction(work_mode="remote"),
        }
    }
    c = TestClient(create_app([item(1, 5)], tmp_path / "l.jsonl", ext, blind_count=2))
    r = c.get("/api/items/1").json()
    assert r["mode"] == "prefilled"
    assert r["prefill"]["fields"]["work_mode"]["value"] == "hybrid"
    assert r["prefill"]["office_cantons"] == ["ZH"]
    assert r["prefill"]["office_places"] == ["place:261:Zürich"]
    assert c.put("/api/items/1/label", json=payload()).json()["saved"]["mode"] == "prefilled"


def test_items_and_progress(client):
    items = client.get("/api/items").json()
    assert [i["order"] for i in items] == [0, 1, 2]
    assert [i["mode"] for i in items] == ["blind", "blind", "prefilled"]
    assert not any(i["labelled"] for i in items)
    client.put("/api/items/2/label", json=payload())
    assert client.get("/api/items").json()[1]["labelled"] is True
    prog = client.get("/api/progress").json()
    assert prog == {
        "done": 1,
        "total": 3,
        "by_language": {"en": {"done": 0, "total": 2}, "de": {"done": 1, "total": 1}},
    }


def test_spec_endpoint(client):
    spec = client.get("/api/spec").json()
    work = next(f for f in spec["fields"] if f["name"] == "work_mode")
    assert {v["value"] for v in work["values"]} == set(FIELDS["work_mode"].values)
    assert spec["outside_value"] == "outside_switzerland"
    assert len(spec["cantons"]) == 26
    zh = spec["cantons"][0]
    assert zh["code"] == "ZH" and zh["name_de"] == "Zürich" and zh["name_it"] == "Zurigo"
    assert "Winterthur" in zh["places"]


def test_office_places_default_and_validation(client):
    r = client.put("/api/items/1/label", json=payload())
    assert r.json()["saved"]["office_places"] == ["place:261:Zürich"]  # filled in by code
    own = payload() | {"office_places": []}
    assert client.put("/api/items/1/label", json=own).json()["saved"]["office_places"] == []
    bad = payload() | {"office_places": ["place:351:Bern"]}
    errors = client.put("/api/items/1/label", json=bad).json()["errors"]
    assert "outside the chosen cantons" in errors["office_places"]
    bad = payload() | {"office_places": ["place:0:Nowhere"]}
    assert (
        "unknown place keys"
        in client.put("/api/items/1/label", json=bad).json()["errors"]["office_places"]
    )


def test_office_cantons_evidence_rules(client):
    no_evidence = payload() | {"office_cantons_sentences": []}
    assert (
        "office_cantons_sentences"
        in client.put("/api/items/1/label", json=no_evidence).json()["errors"]
    )
    stated_none = payload() | {"office_cantons": [], "office_cantons_sentences": []}
    saved = client.put("/api/items/1/label", json=stated_none).json()["saved"]
    assert saved["office_cantons"] == [] and saved["office_places"] == []
    outside = payload() | {"office_cantons": ["outside_switzerland", "ZH"]}
    assert client.put("/api/items/1/label", json=outside).json()["saved"]["office_cantons"] == [
        "ZH",
        "outside_switzerland",
    ]


def test_build_app_reads_and_writes_the_given_data_dir(tmp_path, conn):
    from jobfacts.label_app.server import build_app

    (tmp_path / "test_set.jsonl").write_text(json.dumps(item(1, 0)) + "\n")
    (tmp_path / "test_set_runs.json").write_text('{"jev": 999}')
    c = TestClient(build_app(tmp_path, conn))
    assert [x["posting_id"] for x in c.get("/api/items").json()] == [1]
    assert c.put("/api/items/1/label", json=payload()).status_code == 200
    assert len(labels.load_all(tmp_path / "labels.jsonl")) == 1


def test_prefill_ignores_old_and_variant_runs():
    from jobfacts.label_app import server

    assert server.PREFILL_EXTRACTORS == ("jev", "deepseek", "mistral")
