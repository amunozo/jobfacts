"""Held-out set: selection, set routing in silver/reference, evaluation on a tiny holdout."""

import json
from collections import Counter

import pytest

from jobfacts import evaluate as ev
from jobfacts import holdout, reference, silver, testset
from test_silver import make_item, valid_for


def board_item(pid, language, prob, employer=None, title=None, eligible=True):
    return {
        "posting_id": pid,
        "origin": "board",
        "language": language,
        "employer": employer or f"emp{pid % 17}",
        "title": title or f"Data Scientist {pid}",
        "location_raw": "Zurich",
        "description_text": "text",
        "prob": prob,
        "eligible": eligible,
        "sentences": [],
    }


def pool():
    items = []
    pid = 1
    for lang, n in (("en", 300), ("de", 120), ("fr", 100)):
        for k in range(n):
            prob = [0.9, 0.45, 0.1][k % 3] if lang != "de" else [0.1, 0.1, 0.45, 0.9][k % 4]
            items.append(board_item(pid, lang, prob))
            pid += 1
    items.append(board_item(pid, "en", 0.9, eligible=False))
    items.append({**board_item(pid + 1, "en", 0.9), "origin": "application"})
    return items


def test_selection_counts_and_strata():
    out = testset.choose_holdout(pool(), set(), set(), 50, 1)
    assert len(out) == 50
    assert Counter(i["language"] for i in out) == {"en": 25, "de": 13, "fr": 12}
    strata = Counter(i["stratum"] for i in out)
    assert strata["en_borderline"] == 5 and strata["de_borderline"] == 3
    assert strata["fr_borderline"] == 2 and strata["en_ai"] == 20
    assert all(i["origin"] == "board" and i["group"] == i["stratum"] for i in out)
    assert sorted(i["order"] for i in out) == list(range(50))
    for i in out:
        if i["stratum"].endswith("_borderline"):
            assert 0.3 <= i["prob"] <= 0.6


def test_selection_disjoint_and_deterministic():
    items = pool()
    first = testset.choose_holdout(items, set(), set(), 50, 7)
    banned = {i["posting_id"] for i in first[:30]}
    titles = {(first[40]["employer"], first[40]["title"])}
    again = testset.choose_holdout(items, banned, titles, 50, 7)
    assert not banned & {i["posting_id"] for i in again}
    assert titles.isdisjoint({(i["employer"], i["title"]) for i in again})
    assert [i["posting_id"] for i in testset.choose_holdout(items, set(), set(), 50, 7)] == [
        i["posting_id"] for i in first
    ]
    other = testset.choose_holdout(items, set(), set(), 50, 8)
    assert [i["posting_id"] for i in other] != [i["posting_id"] for i in first]
    ineligible = {len(items) - 1, len(items)}
    assert not ineligible & {i["posting_id"] for i in first}


def test_select_holdout_writes_files_and_refuses_overwrite(tmp_path, monkeypatch):
    items = pool()
    monkeypatch.setattr(testset, "_load", lambda conn, origin: items)
    test_set = tmp_path / "test_set.jsonl"
    test_set.write_text(
        "".join(
            json.dumps({**make_item(i["posting_id"]), "employer": i["employer"]}) + "\n"
            for i in items[:20]
        )
    )
    (tmp_path / "labels.jsonl").write_text(
        json.dumps({"posting_id": items[25]["posting_id"]}) + "\n"
    )
    path = tmp_path / "holdout_set.jsonl"
    out = testset.select_holdout(None, 50, path, test_set_path=test_set)
    got = testset.load_holdout(path)
    ids = {i["posting_id"] for i in got}
    assert len(got) == 50 and ids == {i["posting_id"] for i in out}
    assert not ids & ({i["posting_id"] for i in items[:20]} | {items[25]["posting_id"]})
    assert {"posting_id", "title", "location_raw", "description_text", "language", "order",
            "stratum"} <= set(got[0])  # fmt: skip
    meta = json.loads(path.with_suffix(".meta.json").read_text())
    assert meta["seed"] == testset.HOLDOUT_SEED and meta["languages"] == {
        "en": 25,
        "de": 13,
        "fr": 12,
    }
    assert meta["excluded_test_set"] == 20 and meta["excluded_owner_labels"] == 1
    before = path.read_text()
    with pytest.raises(FileExistsError):
        testset.select_holdout(None, 50, path, test_set_path=test_set)
    assert path.read_text() == before
    testset.select_holdout(None, 10, path, force=True, test_set_path=test_set)
    assert len(testset.load_holdout(path)) == 10


@pytest.fixture
def hold_dir(tmp_path):
    with (tmp_path / "holdout_set.jsonl").open("w") as f:
        for i in range(1, 7):
            item = make_item(500 + i, i - 1, "de" if i % 2 else "en")
            item |= {"origin": "board", "group": "x_ai", "stratum": "x_ai"}
            f.write(json.dumps(item) + "\n")
    with (tmp_path / "test_set.jsonl").open("w") as f:
        f.write(json.dumps(make_item(1, 0, "en")) + "\n")
    return tmp_path


def test_silver_set_routing(hold_dir):
    assert [i["posting_id"] for i in silver.items_for(hold_dir, "holdout")] == list(range(501, 507))
    assert [i["posting_id"] for i in silver.items_for(hold_dir, "all")] == [1]
    assert silver.render_all(hold_dir, "holdout") == 6
    assert (hold_dir / "silver" / "prompts" / "503.md").exists()
    assert silver.status(hold_dir, "holdout")["total"] == 6


def test_missing_holdout_is_an_error(tmp_path):
    with pytest.raises(ValueError, match="select-holdout"):
        silver.items_for(tmp_path, "holdout")


def test_reference_combine_and_review_files_are_separate(hold_dir):
    for item in silver.items_for(hold_dir, "holdout"):
        for lab in reference.PANEL:
            silver._store(hold_dir, lab, item, silver.validate(valid_for(item), item), "sha")
    s = reference.combine(hold_dir, "holdout")
    assert s["written"] == 6 and s["path"].name == "reference_labels_holdout.jsonl"
    assert not (hold_dir / "reference_labels.jsonl").exists()
    assert set(reference.load_reference(hold_dir, "holdout")) == set(range(501, 507))
    assert reference.load_reference(hold_dir, "all") == {}
    r = reference.review_sheet(hold_dir, "holdout", 3)
    assert r["path"].name == "review_sheet_holdout.md" and r["audit"] == 3
    assert (hold_dir / "silver" / "review_items_holdout.json").exists()
    assert not (hold_dir / "silver" / "review_items.json").exists()
    assert reference.audit_stats(hold_dir, "all")["audited"] == 0


def test_evaluate_on_tiny_holdout(conn, hold_dir):
    for item in silver.items_for(hold_dir, "holdout"):
        for lab in reference.PANEL:
            silver._store(hold_dir, lab, item, silver.validate(valid_for(item), item), "sha")
    with pytest.raises(FileNotFoundError, match="combine"):
        ev.run_evaluate(conn, hold_dir, today="2026-11-04", set_name="holdout")
    reference.combine(hold_dir, "holdout")
    with pytest.raises(ValueError, match="silver"):
        ev.run_evaluate(conn, hold_dir, reference="owner", set_name="holdout")
    text, path = ev.run_evaluate(conn, hold_dir, today="2026-11-04", set_name="holdout")
    assert path.name == "accuracy_silver_holdout_2026-11-04.md"
    assert "Held-out set: 6 postings" in text


def test_run_holdout_records_run_and_refuses_repeat(hold_dir, monkeypatch):
    calls = []

    def fake_run(conn_, extractor, ids, limit=None, notes=""):
        calls.append((list(ids), notes))
        return 42, []

    monkeypatch.setattr(holdout, "run_extraction", fake_run)
    run_id, _ = holdout.run_holdout(None, object(), hold_dir, key="jev")
    assert run_id == 42 and calls[0][0] == list(range(501, 507))
    assert json.loads((hold_dir / "holdout_runs.json").read_text()) == {"jev": 42}
    with pytest.raises(FileExistsError):
        holdout.run_holdout(None, object(), hold_dir, key="jev")
    holdout.run_holdout(None, object(), hold_dir, key="jev", force=True)
    assert len(calls) == 2


def test_partial_holdout_run_is_not_recorded_without_explicit_key(hold_dir, monkeypatch):
    monkeypatch.setattr(holdout, "run_extraction", lambda *a, **k: (7, []))
    holdout.run_holdout(None, object(), hold_dir, key="jev", limit=2)
    assert not (hold_dir / "holdout_runs.json").exists()
    holdout.run_holdout(None, object(), hold_dir, key="trial", limit=2, record_partial=True)
    assert json.loads((hold_dir / "holdout_runs.json").read_text()) == {"trial": 7}


def test_cli_missing_holdout_file_is_a_clean_error(tmp_path, monkeypatch, capsys):
    import contextlib

    from jobfacts import cli, db

    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr("jobfacts.extract.run.make_extractor", lambda name: object())
    code = cli.main(
        ["extract", "--extractor", "jev", "--set", "holdout", "--data-dir", str(tmp_path)]
    )
    assert code == 2 and "select-holdout" in capsys.readouterr().err
