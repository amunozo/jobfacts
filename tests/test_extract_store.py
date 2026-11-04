import pytest

from fakes import posting
from jobfacts.extract import store
from jobfacts.extract.base import Result
from jobfacts.extract.spec import FIELDS

pytestmark = pytest.mark.db


def _seed(conn):
    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('E','greenhouse','e') RETURNING id"
    ).fetchone()[0]
    p = posting()
    row = conn.execute(
        "INSERT INTO postings (employer_id, source_job_id, url, title, location_raw, "
        "description_text, content_hash) VALUES (%s,'1','u',%s,%s,%s,'h') RETURNING id",
        (emp, p.title, p.location_raw, p.description),
    ).fetchone()
    conn.commit()
    return row[0]


def test_relevance_and_extraction_roundtrip(conn):
    pid = _seed(conn)
    assert [p.id for p in store.load_postings(conn, only_unscored_by="jev")] == [pid]
    store.save_relevance(conn, pid, "jev", 0.8, 0.5)
    assert store.relevant_ids(conn, "jev") == [pid]
    assert store.load_postings(conn, only_unscored_by="jev") == []

    run_id = store.create_run(conn, "jev", "jev-1.14.0", "abc")
    r = Result(
        values={n: next(iter(f.values)) for n, f in FIELDS.items()},
        sentences={"work_mode": [3], "german": [], "office_cantons": [-1]},
        office_cantons=["ZH"],
        office_places=["place:261:Zürich"],
        details={"x": 1},
        tokens_in=10,
        cost_usd=0.5,
    )
    r.values.update(min_years="3", title_level="senior")
    store.save_extraction(conn, run_id, pid, r)
    store.save_extraction(conn, run_id, pid, r)  # unique (run, posting): second is ignored
    store.finish_run(conn, run_id)
    row = conn.execute(
        "SELECT work_mode_sentences, office_cantons, office_cantons_sentences, office_places, "
        "seniority, details, error, cost_usd "
        "FROM extractions"
    ).fetchall()
    assert row == [([3], ["ZH"], [-1], ["place:261:Zürich"], "mid", {"x": 1}, None, 0.5)]


def test_error_row_has_no_values(conn):
    pid = _seed(conn)
    run_id = store.create_run(conn, "mistral", "m", "v")
    store.save_extraction(conn, run_id, pid, Result(error="boom"))
    assert conn.execute("SELECT work_mode, seniority, error FROM extractions").fetchone() == (
        None,
        None,
        "boom",
    )


def test_relevance_is_rescored_when_the_version_changes(conn):
    pid = _seed(conn)
    store.save_relevance(conn, pid, "jev", 0.8, 0.4, "v1")
    assert store.load_postings(conn, only_unscored_by="jev", relevance_version="v1") == []
    rescore = store.load_postings(conn, only_unscored_by="jev", relevance_version="v2")
    assert [p.id for p in rescore] == [pid]


def _seed_two(conn, probabilities, version="v1", model="jev"):
    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('E','greenhouse','e') RETURNING id"
    ).fetchone()[0]
    ids = []
    for i, prob in enumerate(probabilities):
        p = posting()
        pid = conn.execute(
            "INSERT INTO postings (employer_id, source_job_id, url, title, location_raw, "
            "description_text, content_hash) VALUES (%s,%s,'u',%s,%s,%s,'h') RETURNING id",
            (emp, str(i), p.title, p.location_raw, p.description),
        ).fetchone()[0]
        store.save_relevance(conn, pid, model, prob, 0.5, version)
        ids.append(pid)
    conn.commit()
    return ids


def _flags(conn):
    return [r[0] for r in conn.execute("SELECT relevant FROM relevance ORDER BY probability")]


def test_refresh_relevance_flags_flips_both_ways(conn):
    _seed_two(conn, [0.1, 0.3, 0.7])  # saved with threshold 0.5: [F, F, T]
    assert _flags(conn) == [False, False, True]
    assert store.refresh_relevance_flags(conn, "jev", 0.2) == 1  # only 0.3 flips
    assert _flags(conn) == [False, True, True]
    assert store.refresh_relevance_flags(conn, "jev", 0.2) == 0  # nothing left to change
    assert store.refresh_relevance_flags(conn, "jev", 0.8) == 2  # 0.3 and 0.7 flip back
    assert _flags(conn) == [False, False, False]
    assert store.refresh_relevance_flags(conn, "other-model", 0.0) == 0


def test_refresh_relevance_flags_keeps_scores_and_versions(conn):
    _seed_two(conn, [0.3])
    store.refresh_relevance_flags(conn, "jev", 0.2)
    assert conn.execute("SELECT probability, spec_version FROM relevance").fetchall() == [
        (0.3, "v1")
    ]


def test_rekey_relevance_version(conn):
    _seed_two(conn, [0.3, 0.4])
    assert store.rekey_relevance_version(conn, "jev", "zzz", "v2") == 0
    assert store.rekey_relevance_version(conn, "other", "v1", "v2") == 0
    assert store.rekey_relevance_version(conn, "jev", "v1", "v2") == 2
    assert {r[0] for r in conn.execute("SELECT spec_version FROM relevance")} == {"v2"}
    assert store.load_postings(conn, only_unscored_by="jev", relevance_version="v2") == []


def test_run_relevance_applies_a_new_threshold_without_model_calls(conn):
    from fakes import FakeJev
    from jobfacts.extract import jev
    from jobfacts.extract.run import run_relevance

    ids = _seed_two(conn, [0.1, 0.3, 0.7], jev.relevance_version(), jev.MODEL)
    client = FakeJev()
    r = run_relevance(conn, client, threshold=0.2)
    assert r.flags_changed == 1 and r.scored == 0 and client.calls == []
    assert _flags(conn) == [False, True, True]
    assert ids  # all three were already scored under the current version
