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
        sentences={"work_mode": [3], "german": []},
        office_locations=["city:Zürich"],
        details={"x": 1},
        tokens_in=10,
        cost_usd=0.5,
    )
    r.values.update(min_years="3", title_level="senior")
    store.save_extraction(conn, run_id, pid, r)
    store.save_extraction(conn, run_id, pid, r)  # unique (run, posting): second is ignored
    store.finish_run(conn, run_id)
    row = conn.execute(
        "SELECT work_mode_sentences, office_locations, seniority, details, error, cost_usd "
        "FROM extractions"
    ).fetchall()
    assert row == [([3], ["city:Zürich"], "mid", {"x": 1}, None, 0.5)]


def test_error_row_has_no_values(conn):
    pid = _seed(conn)
    run_id = store.create_run(conn, "mistral", "m", "v")
    store.save_extraction(conn, run_id, pid, Result(error="boom"))
    assert conn.execute("SELECT work_mode, seniority, error FROM extractions").fetchone() == (
        None,
        None,
        "boom",
    )
