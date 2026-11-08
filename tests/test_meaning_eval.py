import json

import pytest

from jobfacts import embed
from jobfacts import meaning_eval as me
from jobfacts.extract import jev, store
from jobfacts.extract.base import Result
from test_embed import FakeEmbedder, _posting

# ---------- phrases ----------


def test_load_phrases_skips_comments_and_blank_lines(tmp_path):
    f = tmp_path / "p.txt"
    f.write_text("# comment\n\nmachine learning engineer \n  # another\ndata scientist\n")
    assert me.load_phrases(f) == ["machine learning engineer", "data scientist"]


# ---------- pure metrics ----------


def test_group_ranks_count_jobs_not_postings():
    key_of = {1: ("e", "a"), 2: ("e", "b"), 3: ("e", "a"), 4: ("f", "a")}
    # posting 3 is a duplicate of posting 1 (same employer and title) and does not push 4 down
    assert me.group_ranks([1, 2, 3, 4], key_of) == {1: 1, 2: 2, 3: 1, 4: 3}
    assert me.group_ranks([], key_of) == {}


def test_best_rank_takes_the_best_posting_in_the_best_phrase():
    phrases = [{1: 5, 2: 9}, {2: 3}, {}]
    assert me.best_rank(phrases, [1, 2]) == 3
    assert me.best_rank(phrases, [1]) == 5
    assert me.best_rank(phrases, [7]) is None
    assert me.best_rank([], [1]) is None


def test_recall_at_and_wilson_interval():
    best = [1, 10, 11, 20, 21, 50, 51, None]
    assert me.recall_at(best, 10)[:2] == (2, 8)
    assert me.recall_at(best, 20)[:2] == (4, 8)
    assert me.recall_at(best, 50)[:2] == (6, 8)
    hits, n, low, high = me.recall_at(best, 20)
    assert low < hits / n < high and 0 <= low and high <= 1
    assert me.recall_at([], 20) == (0, 0, 0.0, 1.0)


def test_mrr_counts_unranked_jobs_as_zero():
    assert me.mrr([1, 2, None, 4]) == pytest.approx((1 + 0.5 + 0 + 0.25) / 4)
    assert me.mrr([]) == 0.0


def test_top_counts_per_phrase():
    ranks = [{1: 1, 2: 25}, {1: 30, 2: 2, 3: 20}]
    jobs = [[1], [2], [3, 4]]
    assert me.top_counts(ranks, jobs, 20) == [1, 2]
    assert me.top_counts(ranks, jobs, 50) == [2, 3]


def test_hybrid_order_fuses_the_best_200_of_each_list():
    assert me.hybrid_order([1, 2, 3], [3, 4]) == [3, 1, 2, 4]
    order = me.hybrid_order([1, 2, 3], [3, 1])
    assert order[:2] == [1, 3] and set(order) == {1, 2, 3}
    long_words = list(range(1000, 1300))
    assert set(me.hybrid_order(long_words, [])) == set(long_words[:200])


def test_best_method_is_highest_recall_at_20_then_mrr():
    a = me.MethodResult("a", [], [1, 30])
    b = me.MethodResult("b", [], [5, 6])
    c = me.MethodResult("c", [], [2, 3])
    assert me.best_method([a, b, c]).name == "c"


# ---------- database: rankings, wanted jobs, the whole run ----------


@pytest.fixture
def world(conn):
    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('Acme AG','greenhouse','acme') "
        "RETURNING id"
    ).fetchone()[0]
    ids = {
        "ml": _posting(conn, emp, 1, "Machine Learning Engineer", "unicorn wrangler models"),
        "closed": _posting(conn, emp, 2, "Research Scientist", "horses", closed=True),
        "dup": _posting(conn, emp, 3, "Machine Learning Engineer", "unicorn wrangler again"),
        "other": _posting(conn, emp, 4, "Accountant", "books", prob=0.9),
        "low": _posting(conn, emp, 5, "Low Relevance", "unicorn", prob=0.01),
    }
    run = store.create_run(conn, "jev", jev.MODEL, "v", purpose="search")
    values = {
        "work_mode": "onsite",
        "remote_from_switzerland": "not_applicable",
        "german": "not_mentioned",
        "french": "not_mentioned",
        "min_years": "not_stated",
        "title_level": "not_stated",
        "contract_type": "not_stated",
    }
    store.save_extraction(
        conn, run, ids["ml"], Result(values=values, office_cantons=["ZH"]), content_hash="h"
    )
    conn.commit()
    return ids


@pytest.mark.db
def test_orders_over_the_pool(conn, world):
    pool = me.load_pool(conn)
    assert set(pool.ids) == {world[k] for k in ("ml", "closed", "dup", "other")}  # not "low"
    words = me.words_order(conn, "unicorn wrangler")
    assert set(words) == {world["ml"], world["dup"]}
    embed.save_embeddings(
        conn,
        "m",
        [
            (world["ml"], "t", "h", [1.0, 0.0]),
            (world["closed"], "t", "h", [0.9, 0.1]),
            (world["other"], "t", "h", [0.0, 1.0]),
            (world["low"], "t", "h", [1.0, 0.0]),  # outside the pool
        ],
        800,
    )
    meaning = me.meaning_order(conn, "m", [1.0, 0.0])
    assert meaning == [world["ml"], world["closed"], world["other"]]
    assert me.coverage(conn, "m") == 3
    ranks = me.group_ranks(words, pool.key_of)
    assert ranks[world["ml"]] == ranks[world["dup"]] == 1


@pytest.mark.db
def test_wanted_jobs_and_the_full_run(conn, world, tmp_path, capsys):
    apps = tmp_path / "apps"
    for role in ("Machine Learning Engineer", "Research Scientist", "Accountant", "Unknown Role"):
        folder = apps / "Acme AG" / role
        folder.mkdir(parents=True)
        (folder / "job_description.md").write_text("not provided\n\nText.")
    dates = {
        f"Acme AG/{role}": "2026-11-08"
        for role in ("Machine Learning Engineer", "Research Scientist", "Accountant")
    }
    dates_file = tmp_path / "dates.json"
    dates_file.write_text(json.dumps(dates))  # "Unknown Role" has no date: skipped
    phrases = tmp_path / "phrases.txt"
    phrases.write_text("# c\nunicorn wrangler\nhorses\n")
    # the fake embedder puts every query on the first axis; postings differ in the first value
    fake = FakeEmbedder()
    fake.chunk = 10
    embed.run_embed(conn, fake)

    out = tmp_path / "reports" / "r.md"
    result = me.run(
        conn,
        phrases,
        apps,
        dates_file,
        ["fake"],
        out=out,
        embedders={"fake": fake},
    )
    wanted = {w.app.key.split("/")[1]: w for w in result.wanted}
    assert set(wanted) == {"Machine Learning Engineer", "Research Scientist", "Accountant"}
    assert wanted["Machine Learning Engineer"].outcome == "found"
    assert wanted["Machine Learning Engineer"].within_scope
    assert set(wanted["Machine Learning Engineer"].posting_ids) == {world["ml"], world["dup"]}
    assert not wanted["Accountant"].within_scope
    by = {r.name: r for r in result.results}
    assert set(by) == {"words", "meaning:fake", "hybrid:fake"}
    names = [w.app.key.split("/")[1] for w in result.wanted]
    words = by["words"].best
    assert words[names.index("Machine Learning Engineer")] == 1
    assert words[names.index("Research Scientist")] == 1  # phrase "horses"
    assert words[names.index("Accountant")] is None  # no phrase matches
    assert all(b is not None for b in by["meaning:fake"].best)  # meaning ranks the whole pool
    text = out.read_text()
    assert "recall@20" in text and "meaning:fake" in text and "Within scope (n = 1)" in text
    assert "unicorn wrangler" in text
    # budgets are read from the stored rows, not assumed
    assert "fake 6000 (4 vectors)" in text and "WARNING" not in text
    assert capsys.readouterr().err == ""

    # a changed posting and a second budget: the report and stderr warn
    conn.execute("UPDATE postings SET content_hash = 'changed' WHERE id = %s", (world["ml"],))
    conn.execute(
        "UPDATE posting_embeddings SET char_budget = 800 WHERE posting_id = %s", (world["other"],)
    )
    again = me.run(
        conn, phrases, apps, dates_file, ["fake"], out=tmp_path / "r2.md", embedders={"fake": fake}
    )
    assert (
        "WARNING: fake-model has mixed character budgets (800 characters: 1, 6000" in again.report
    )
    assert "WARNING: fake-model has 1 stale or missing vectors in the pool" in again.report
    err = capsys.readouterr().err
    assert "mixed character budgets" in err and "stale or missing" in err


def test_vector_warnings_name_each_problem():
    models = {"a": "model-a", "b": "model-b"}
    assert me.vector_warnings(models, {"a": {6000: 5}, "b": {800: 5}}, {"a": 0, "b": 0}) == []
    lines = me.vector_warnings(models, {"a": {800: 2, 6000: 3}, "b": {}}, {"a": 0, "b": 4})
    assert len(lines) == 2 and "model-a has mixed" in lines[0] and "model-b has 4 stale" in lines[1]
