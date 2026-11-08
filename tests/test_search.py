import json
from dataclasses import replace

import pytest

from jobfacts import embed, search
from jobfacts.extract import jev, store
from jobfacts.extract.base import Result
from jobfacts.search import Filters, SearchError, build

# ---------- SQL builder (no database) ----------


def test_builder_always_restricts_to_open_board_postings():
    sql, params = build(Filters())
    assert "p.closed_at IS NULL" in sql and "p.origin = 'board'" in sql
    assert "ORDER BY p.first_seen_at DESC" in sql
    assert params["rel_model"] == jev.MODEL and "query" not in params


def test_builder_query_uses_simple_config_and_rank():
    sql, params = build(Filters(query="Maschinelles Lernen"))
    assert "websearch_to_tsquery('simple'" in sql and "ORDER BY rank DESC" in sql
    assert params["query"] == "Maschinelles Lernen"


@pytest.mark.parametrize(
    ("maximum", "show", "expected"),
    [
        ("none", False, {"not_mentioned", "nice_to_have"}),
        ("none", True, {"not_mentioned", "nice_to_have", "required_level_not_stated"}),
        (
            "intermediate",
            False,
            {"not_mentioned", "nice_to_have", "required_intermediate", "either_required"},
        ),
        (
            "fluent",
            False,
            {
                "not_mentioned",
                "nice_to_have",
                "required_intermediate",
                "either_required",
                "required_fluent",
                "required_level_not_stated",
            },
        ),
    ],
)
def test_german_max_values(maximum, show, expected):
    _, params = build(Filters(german_max=maximum, show_unclear_language=show))
    assert set(params["german_values"]) == expected
    assert "french_values" not in params


def test_remote_values_and_or_with_cantons():
    sql, params = build(Filters(cantons=["ZH"], remote=True))
    assert " OR " in sql and params["remote_values"] == ["allowed"]
    _, params = build(Filters(remote_unclear=True))
    assert set(params["remote_values"]) == {"allowed", "unclear", "not_stated"}


@pytest.mark.parametrize(
    "bad",
    [
        Filters(cantons=["XX"]),
        Filters(work_modes=["office"]),
        Filters(seniority=["boss"]),
        Filters(german_max="lots"),
        Filters(new_days=-1),
        Filters(limit=0),
    ],
)
def test_builder_rejects_values_outside_the_spec(bad):
    with pytest.raises(SearchError):
        build(bad)


def test_no_user_text_in_sql():
    sql, _ = build(Filters(query="x'; DROP TABLE postings; --", cantons=["ZH"]))
    assert "DROP" not in sql


# ---------- database ----------

DESC_DE = (
    "Wir entwickeln Maschinelles Lernen Systeme. Sie haben drei Jahre Erfahrung. "
    "Gute Deutschkenntnisse."
)
DESC_FR = "Nous développons des modèles d'apprentissage automatique. Français courant exigé."


class Seed:
    def __init__(self, conn):
        self.conn = conn
        self.emp = conn.execute(
            "INSERT INTO employers (name, ats, board_id) VALUES ('Acme','greenhouse','a') "
            "RETURNING id"
        ).fetchone()[0]
        self.n = 0
        self.run = store.create_run(conn, "jev", jev.MODEL, "v", purpose="search")

    def add(
        self,
        title="ML Engineer",
        desc="We build machine learning.",
        origin="board",
        closed=False,
        relevant=True,
        days_old=0,
        cantons=("ZH",),
        places=(),
        error=None,
        run=None,
        **values,
    ):
        self.n += 1
        pid = self.conn.execute(
            "INSERT INTO postings (employer_id, source_job_id, url, title, description_text, "
            "content_hash, origin, closed_at, first_seen_at) VALUES "
            "(%s,%s,%s,%s,%s,'h',%s, CASE WHEN %s THEN now() END, "
            "now() - make_interval(days => %s))"
            " RETURNING id",
            (self.emp, str(self.n), f"https://x/{self.n}", title, desc, origin, closed, days_old),
        ).fetchone()[0]
        store.save_relevance(self.conn, pid, jev.MODEL, 0.9 if relevant else 0.1, 0.5)
        v = {
            "work_mode": "onsite",
            "remote_from_switzerland": "not_applicable",
            "german": "not_mentioned",
            "french": "not_mentioned",
            "min_years": "not_stated",
            "title_level": "not_stated",
            "contract_type": "not_stated",
        }
        v.update(values)
        r = Result(
            values=v,
            sentences={"german": [2], "office_cantons": [-1]},
            office_cantons=list(cantons),
            office_places=list(places),
        )
        if error:
            r = Result(error=error)
        store.save_extraction(self.conn, run or self.run, pid, r, content_hash="h")
        return pid


def ids(conn, **kw):
    hits, total = search.search(conn, Filters(**kw))
    assert total == len(hits)
    return {h.title for h in hits}


@pytest.fixture
def seed(conn):
    return Seed(conn)


@pytest.mark.db
def test_closed_application_irrelevant_and_unextracted_are_excluded(conn, seed):
    seed.add(title="open")
    seed.add(title="closed", closed=True)
    seed.add(title="app", origin="application")
    seed.add(title="irrelevant", relevant=False)
    seed.add(title="failed", error="boom")
    assert ids(conn) == {"open"}


@pytest.mark.db
def test_latest_successful_extraction_is_used(conn, seed):
    pid = seed.add(title="p", cantons=("ZH",))
    run2 = store.create_run(conn, "jev", jev.MODEL, "v2", purpose="search")
    r = Result(
        values={
            "work_mode": "onsite",
            "remote_from_switzerland": "not_applicable",
            "german": "not_mentioned",
            "french": "not_mentioned",
            "min_years": "not_stated",
            "title_level": "not_stated",
            "contract_type": "not_stated",
        },
        office_cantons=["BE"],
    )
    store.save_extraction(conn, run2, pid, r, "h")
    assert ids(conn, cantons=["BE"]) == {"p"}
    assert ids(conn, cantons=["ZH"]) == set()
    # a newer run that failed does not hide the older good one
    run3 = store.create_run(conn, "jev", jev.MODEL, "v3", purpose="search")
    store.save_extraction(conn, run3, pid, Result(error="boom"), "h")
    assert ids(conn, cantons=["BE"]) == {"p"}


@pytest.mark.db
def test_other_extractors_are_ignored(conn, seed):
    pid = seed.add(title="p")
    conn.execute("UPDATE extraction_runs SET extractor = 'deepseek'")
    assert pid and ids(conn) == set()


@pytest.mark.db
def test_canton_remote_and_work_mode(conn, seed):
    seed.add(title="zh", cantons=("ZH",))
    seed.add(title="vd", cantons=("VD",))
    seed.add(title="rem-ok", cantons=(), work_mode="remote", remote_from_switzerland="allowed")
    seed.add(title="rem-unclear", cantons=(), work_mode="remote", remote_from_switzerland="unclear")
    seed.add(title="rem-ns", cantons=(), work_mode="remote", remote_from_switzerland="not_stated")
    seed.add(title="rem-no", cantons=(), work_mode="remote", remote_from_switzerland="not_allowed")
    seed.add(title="hyb", cantons=("ZH", "BE"), work_mode="hybrid")
    assert ids(conn, cantons=["ZH", "VD"]) == {"zh", "vd", "hyb"}
    assert ids(conn, cantons=["BE"]) == {"hyb"}
    assert ids(conn, remote=True) == {"rem-ok"}
    assert ids(conn, remote_unclear=True) == {"rem-ok", "rem-unclear", "rem-ns"}
    assert ids(conn, cantons=["VD"], remote=True) == {"vd", "rem-ok"}
    assert ids(conn, work_modes=["hybrid"]) == {"hyb"}
    assert ids(conn, work_modes=["onsite", "hybrid"]) == {"zh", "vd", "hyb"}


@pytest.mark.db
def test_german_and_french_max_logic(conn, seed):
    for value in (
        "not_mentioned",
        "nice_to_have",
        "required_intermediate",
        "either_required",
        "required_fluent",
        "required_level_not_stated",
    ):
        seed.add(title=f"de-{value}", german=value)
        seed.add(title=f"fr-{value}", french=value)

    def de(*v):
        return {f"de-{x}" for x in v}

    def fr(*v):
        return {f"fr-{x}" for x in v}

    all_fr = fr(
        "not_mentioned",
        "nice_to_have",
        "required_intermediate",
        "either_required",
        "required_fluent",
        "required_level_not_stated",
    )
    plain = de("not_mentioned", "nice_to_have")
    # the german limit leaves french alone (all fr- rows have german not_mentioned)
    assert ids(conn, german_max="none") - all_fr == plain
    assert ids(conn, german_max="none", show_unclear_language=True) - all_fr == plain | de(
        "required_level_not_stated"
    )
    assert ids(conn, german_max="intermediate") - all_fr == plain | de(
        "required_intermediate", "either_required"
    )
    assert len(ids(conn, german_max="fluent") - all_fr) == 6
    # french uses the same logic; both limits combine
    assert {t for t in ids(conn, french_max="none") if t.startswith("fr-")} == fr(
        "not_mentioned", "nice_to_have"
    )
    both = ids(conn, german_max="none", french_max="none")
    assert both == {"de-not_mentioned", "de-nice_to_have", "fr-not_mentioned", "fr-nice_to_have"}


@pytest.mark.db
def test_seniority_and_new_days(conn, seed):
    seed.add(title="j", min_years="0", title_level="junior", days_old=1)
    seed.add(title="m", min_years="3", days_old=10)
    seed.add(title="s", min_years="6", days_old=40)
    seed.add(title="n", days_old=2)
    assert ids(conn, seniority=["junior", "mid"]) == {"j", "m"}
    assert ids(conn, seniority=["not_stated"]) == {"n"}
    assert ids(conn, new_days=5) == {"j", "n"}
    assert ids(conn, new_days=365) == {"j", "m", "s", "n"}


@pytest.mark.db
def test_full_text_german_and_french_words_rank_and_limit(conn, seed):
    seed.add(title="Ingenieur", desc=DESC_DE)
    seed.add(title="Ingénieur", desc=DESC_FR)
    seed.add(title="Other", desc="Nothing relevant here.")
    seed.add(title="Lernen", desc="Lernen " * 5, days_old=3)
    assert ids(conn, query="Maschinelles Lernen") == {"Ingenieur"}
    assert ids(conn, query="Lernen") == {"Ingenieur", "Lernen"}
    assert ids(conn, query="apprentissage automatique") == {"Ingénieur"}
    assert ids(conn, query="Deutschkenntnisse") == {"Ingenieur"}
    assert ids(conn, query="lernen -maschinelles") == {"Lernen"}  # websearch syntax
    hits, total = search.search(conn, Filters(query="Lernen", limit=1))
    assert total == 2 and len(hits) == 1
    assert hits[0].title == "Lernen"  # title match weighs more than description


@pytest.mark.db
def test_no_query_orders_newest_first_and_combines_filters(conn, seed):
    seed.add(title="old", days_old=9, cantons=("ZH",))
    seed.add(title="new", days_old=1, cantons=("ZH",))
    seed.add(title="other", days_old=0, cantons=("GE",))
    hits, _ = search.search(conn, Filters(cantons=["ZH"]))
    assert [h.title for h in hits] == ["new", "old"]
    assert ids(conn, query="machine", cantons=["ZH"], new_days=3) == {"new"}


@pytest.mark.db
def test_evidence_json_and_table(conn, seed):
    seed.add(
        title="ML Engineer",
        desc="Intro sentence. Gute Deutschkenntnisse sind nötig. End.",
        german="required_intermediate",
        places=("place:261:Zürich",),
    )
    hits, total = search.search(conn, Filters(), evidence=True)
    h = hits[0]
    assert h.evidence["german"] == ["Gute Deutschkenntnisse sind nötig."]
    assert h.evidence["office_cantons"] == [""]  # location field of the seed is empty
    assert h.as_dict()["evidence"]["german"] and h.where == "Zürich"
    text = search.format_table(hits, total, evidence=True)
    assert "- german: Gute Deutschkenntnisse" in text and "1 matches" in text
    assert search.format_table([], 0) == "no matches"


@pytest.mark.db
def test_incremental_extraction_skips_unchanged_and_application_postings(conn, seed):
    from jobfacts.extract.run import run_extraction

    class Fake:
        name, model = "jev", jev.MODEL

        def spec_version(self):
            return "fake1"

        def extract(self, p):
            return Result(
                values={
                    "work_mode": "onsite",
                    "remote_from_switzerland": "not_applicable",
                    "german": "not_mentioned",
                    "french": "not_mentioned",
                    "min_years": "not_stated",
                    "title_level": "not_stated",
                    "contract_type": "not_stated",
                }
            )

    a = seed.add(title="a")
    b = seed.add(title="b")
    app = seed.add(title="app", origin="application")
    closed = seed.add(title="c", closed=True)
    ids_ = [a, b, app, closed]
    _, first = run_extraction(conn, Fake(), ids_, for_search=True)
    assert len(first) == 2  # only open board postings
    _, second = run_extraction(conn, Fake(), ids_, for_search=True)
    assert second == []
    conn.execute("UPDATE postings SET content_hash = 'changed' WHERE id = %s", (b,))
    _, third = run_extraction(conn, Fake(), ids_, for_search=True)
    assert len(third) == 1
    # new extraction rules (spec version) redo everything
    Fake.spec_version = lambda self: "fake2"
    _, fourth = run_extraction(conn, Fake(), ids_, for_search=True)
    assert len(fourth) == 2
    _, fifth = run_extraction(conn, Fake(), ids_, for_search=True)
    assert fifth == []
    assert store.pending_search_ids(conn, ids_, "v3") == sorted([a, b])


@pytest.mark.db
def test_no_fixed_term_keeps_permanent_not_stated_and_old_rows(conn, seed):
    seed.add(title="fixed", contract_type="fixed_term")
    seed.add(title="perm", contract_type="permanent")
    seed.add(title="unknown", contract_type="not_stated")
    old = seed.add(title="old")
    conn.execute("UPDATE extractions SET contract_type = NULL WHERE posting_id = %s", (old,))
    assert ids(conn) == {"fixed", "perm", "unknown", "old"}
    assert ids(conn, no_fixed_term=True) == {"perm", "unknown", "old"}


@pytest.mark.db
def test_contract_type_in_json_and_evidence(conn, seed):
    seed.add(title="fixed", contract_type="fixed_term")
    hits, _ = search.search(conn, Filters(), evidence=True)
    assert hits[0].as_dict()["contract_type"] == "fixed_term"
    assert "contract_type" in hits[0].evidence


@pytest.mark.db
def test_experiment_runs_and_stale_content_are_never_shown(conn, seed):
    pid = seed.add(title="p", cantons=("ZH",))
    values = {
        "work_mode": "onsite",
        "remote_from_switzerland": "not_applicable",
        "german": "not_mentioned",
        "french": "not_mentioned",
        "min_years": "not_stated",
        "title_level": "not_stated",
        "contract_type": "not_stated",
    }
    exp = store.create_run(conn, "jev", jev.MODEL, "v-exp")  # purpose = experiment
    store.save_extraction(conn, exp, pid, Result(values=values, office_cantons=["BE"]), "h")
    assert ids(conn, cantons=["ZH"]) == {"p"}
    assert ids(conn, cantons=["BE"]) == set()
    # an experiment-only posting is not shown at all
    only_exp = seed.add(title="only-exp", run=exp)
    assert "only-exp" not in ids(conn)
    assert only_exp
    # the text changed: the old facts are hidden until re-extraction
    conn.execute("UPDATE postings SET content_hash = 'new' WHERE id = %s", (pid,))
    assert "p" not in ids(conn)
    run2 = store.create_run(conn, "jev", jev.MODEL, "v2", purpose="search")
    store.save_extraction(conn, run2, pid, Result(values=values, office_cantons=["BE"]), "new")
    assert ids(conn, cantons=["BE"]) == {"p"}


@pytest.mark.db
def test_location_raw_in_hit_and_json(conn, seed):
    pid = seed.add(title="p")
    conn.execute("UPDATE postings SET location_raw = 'Zurich, CH' WHERE id = %s", (pid,))
    hits, _ = search.search(conn, Filters())
    assert hits[0].location_raw == "Zurich, CH"
    assert hits[0].as_dict()["location_raw"] == "Zurich, CH"
    hits, _ = search.search(conn, Filters(), evidence=True)
    assert hits[0].as_dict()["location_raw"] == "Zurich, CH"


@pytest.mark.db
def test_stale_relevance_count(conn, seed):
    pid = seed.add()  # saved without a spec version: counts as old
    assert search.stale_relevance_count(conn) == 1
    store.save_relevance(conn, pid, jev.MODEL, 0.9, 0.4, jev.relevance_version())
    conn.commit()
    assert search.stale_relevance_count(conn) == 0


@pytest.mark.db
def test_stale_relevance_count_ignores_closed_postings(conn, seed):
    pid = seed.add()  # old relevance row
    conn.execute("UPDATE postings SET closed_at = now() WHERE id = %s", (pid,))
    conn.commit()
    assert search.stale_relevance_count(conn) == 0


# ---------- duplicate grouping ----------


def other_employer(conn, seed, pid):
    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('Other','lever','o') RETURNING id"
    ).fetchone()[0]
    conn.execute("UPDATE postings SET employer_id = %s WHERE id = %s", (emp, pid))


def set_location(conn, pid, location):
    conn.execute("UPDATE postings SET location_raw = %s WHERE id = %s", (location, pid))


@pytest.mark.db
def test_same_title_at_one_employer_is_one_hit_with_duplicates(conn, seed):
    first = seed.add(title="AI Engineer (m/w/d)", days_old=0)
    second = seed.add(title="ai engineer (f/m/d)", days_old=1)
    set_location(conn, first, "Zurich")
    set_location(conn, second, "Berlin")
    hits, total = search.search(conn, Filters())
    assert total == 1 and len(hits) == 1
    assert hits[0].id == first  # newest first
    assert hits[0].duplicates == [
        {"id": second, "url": f"https://x/{seed.n}", "location_raw": "Berlin"}
    ]
    assert hits[0].as_dict()["duplicates"] == hits[0].duplicates


@pytest.mark.db
def test_same_title_at_two_employers_is_two_hits(conn, seed):
    seed.add(title="AI Engineer")
    other_employer(conn, seed, seed.add(title="AI Engineer"))
    hits, total = search.search(conn, Filters())
    assert total == 2 and len(hits) == 2
    assert all(h.duplicates == [] for h in hits)
    assert all(h.as_dict()["duplicates"] == [] for h in hits)


@pytest.mark.db
def test_group_appears_if_any_member_matches_and_that_member_represents(conn, seed):
    seed.add(title="AI Engineer", cantons=("GE",), days_old=0)
    zh = seed.add(title="AI Engineer", cantons=("ZH",), days_old=5)
    hits, total = search.search(conn, Filters(cantons=["ZH"]))
    assert total == 1 and [h.id for h in hits] == [zh]
    assert hits[0].duplicates == []


@pytest.mark.db
def test_limit_counts_groups(conn, seed):
    for title in ("A", "B", "C"):
        seed.add(title=title, days_old=3)
        seed.add(title=title, days_old=4)
    hits, total = search.search(conn, Filters(limit=2))
    assert len(hits) == 2 and total == 3
    assert all(len(h.duplicates) == 1 for h in hits)


@pytest.mark.db
def test_query_rank_picks_the_best_posting_as_representative(conn, seed):
    weak = seed.add(title="Engineer", desc="machine " * 1, days_old=0)
    strong = seed.add(title="Engineer", desc="machine " * 20, days_old=1)
    hits, total = search.search(conn, Filters(query="machine"))
    assert total == 1 and hits[0].id == strong
    assert [d["id"] for d in hits[0].duplicates] == [weak]


@pytest.mark.db
def test_evidence_for_a_group_and_table_lists_the_other_locations(conn, seed):
    first = seed.add(title="AI Engineer", desc="Intro. Gute Deutschkenntnisse. End.")
    set_location(conn, first, "Zurich")
    for days, place in ((2, "Abuja, Nigeria"), (3, "Astana, Kazakhstan")):
        set_location(conn, seed.add(title="AI Engineer (m/w/d)", days_old=days), place)
    hits, total = search.search(conn, Filters(), evidence=True)
    assert total == 1 and hits[0].id == first
    assert hits[0].evidence["german"] == ["Gute Deutschkenntnisse."]
    text = search.format_table(hits, total)
    assert "\n   + 2 more locations: Abuja, Nigeria; Astana, Kazakhstan" in text
    assert "1 matches, showing 1" in text
    no_dups = search.format_table([replace(hits[0], duplicates=[])], 1)
    assert "more locations" not in no_dups


@pytest.mark.db
def test_evidence_only_for_shown_hits(conn, seed, monkeypatch):
    for title in ("A", "B", "C"):
        seed.add(title=title, desc="Intro. Gute Deutschkenntnisse. End.")
    asked = []
    real = search.build_evidence

    def spy(ids):
        asked.append(list(ids))
        return real(ids)

    monkeypatch.setattr(search, "build_evidence", spy)
    hits, total = search.search(conn, Filters(limit=1), evidence=True)
    assert total == 3 and asked == [[hits[0].id]]
    assert hits[0].evidence["german"] == ["Gute Deutschkenntnisse."]


@pytest.mark.db
def test_one_duplicate_reads_location_singular(conn, seed):
    set_location(conn, seed.add(title="AI Engineer", days_old=1), "Zurich")
    set_location(conn, seed.add(title="AI Engineer", days_old=2), "Bern")
    hits, total = search.search(conn, Filters())
    assert "\n   + 1 more location: Bern" in search.format_table(hits, total)


# ---------- meaning and hybrid ----------

MODEL = "fake-model"


def vectors(conn, mapping, model=MODEL):
    """Store tiny fake vectors {posting id: vector}."""
    items = [(pid, "t", "h", vec) for pid, vec in mapping.items()]  # content hash h = the seed's
    embed.save_embeddings(conn, model, items, 800)


def run(conn, mode, query="anything", vector=(1.0, 0.0, 0.0), **kw):
    f = Filters(query=query, mode=mode, model=MODEL, **kw)
    hits, total = search.search(conn, f, query_vector=list(vector))
    return [h.title for h in hits], total


def test_rrf_adds_reciprocal_ranks():
    fused = search.rrf([[1, 2, 3], [3, 2]], k=60)
    scores = dict(fused)
    assert scores[1] == pytest.approx(1 / 61)
    assert scores[2] == pytest.approx(1 / 62 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert [i for i, _ in fused] == [3, 2, 1]
    assert search.rrf([]) == []


def test_rrf_ties_go_to_the_lower_id():
    assert [i for i, _ in search.rrf([[5], [4]])] == [4, 5]


def test_meaning_and_hybrid_need_a_query_and_a_model():
    with pytest.raises(SearchError):
        Filters(mode="meaning", model=MODEL).validate()
    with pytest.raises(SearchError):
        Filters(mode="hybrid", query="x").validate()
    with pytest.raises(SearchError):
        Filters(mode="nope").validate()
    with pytest.raises(SearchError):  # hybrid is built by search(), from two queries
        build(Filters(mode="hybrid", query="x", model=MODEL))
    with pytest.raises(SearchError):
        build(Filters(mode="meaning", query="x", model=MODEL))


def test_meaning_sql_joins_the_model_and_keeps_the_filters():
    sql, params = build(
        Filters(mode="meaning", query="x", model=MODEL, cantons=["ZH"]), [0.5, 0.5], top=200
    )
    assert "posting_embeddings" in sql and "<=>" in sql and "x.office_cantons" in sql
    assert "p.closed_at IS NULL" in sql and sql.endswith("LIMIT %(top)s")
    assert params["emb_model"] == MODEL and params["qvec"] == "[0.5,0.5]" and params["top"] == 200
    assert "search_tsv" not in sql


@pytest.mark.db
def test_meaning_ranks_by_cosine_similarity(conn, seed):
    a = seed.add(title="near")
    b = seed.add(title="middle")
    c = seed.add(title="far")
    seed.add(title="no vector")
    vectors(conn, {a: [1.0, 0.0, 0.0], b: [1.0, 1.0, 0.0], c: [0.0, 1.0, 0.0]})
    titles, total = run(conn, "meaning")
    assert titles == ["near", "middle", "far"] and total == 3  # no vector: not ranked
    hits, _ = search.search(
        conn, Filters(query="q", mode="meaning", model=MODEL), query_vector=[1.0, 0.0, 0.0]
    )
    assert hits[0].rank == pytest.approx(1.0) and hits[1].rank == pytest.approx(
        1 - 0.2929, abs=1e-3
    )


@pytest.mark.db
def test_meaning_ignores_other_models_with_other_dimensions(conn, seed):
    a = seed.add(title="a")
    vectors(conn, {a: [1.0, 0.0, 0.0]})
    vectors(conn, {a: [1.0, 0.0]}, model="other")
    assert run(conn, "meaning") == (["a"], 1)


@pytest.mark.db
def test_meaning_applies_every_filter_and_the_usual_exclusions(conn, seed):
    zh = seed.add(title="zh", cantons=("ZH",))
    be = seed.add(title="be", cantons=("BE",))
    de = seed.add(title="de", german="required_fluent")
    closed = seed.add(title="closed", closed=True)
    irrelevant = seed.add(title="irrelevant", relevant=False)
    vectors(conn, dict.fromkeys([zh, be, de, closed, irrelevant], [1.0, 0.0, 0.0]))
    assert set(run(conn, "meaning")[0]) == {"zh", "be", "de"}
    assert run(conn, "meaning", cantons=["BE"])[0] == ["be"]
    assert set(run(conn, "meaning", german_max="intermediate")[0]) == {"zh", "be"}


@pytest.mark.db
def test_meaning_groups_duplicates_like_words(conn, seed):
    a = seed.add(title="Same Job")
    b = seed.add(title="same job")
    vectors(conn, {a: [1.0, 0.0, 0.0], b: [0.9, 0.1, 0.0]})
    hits, total = search.search(
        conn, Filters(query="q", mode="meaning", model=MODEL), query_vector=[1.0, 0.0, 0.0]
    )
    assert total == 1 and len(hits[0].duplicates) == 1


@pytest.mark.db
def test_hybrid_fuses_words_and_meaning(conn, seed):
    both = seed.add(title="words and meaning", desc="We need a unicorn wrangler.")
    words_only = seed.add(title="words only", desc="unicorn unicorn unicorn wrangler")
    meaning_only = seed.add(title="meaning only", desc="We herd horned horses.")
    vectors(
        conn,
        {
            both: [1.0, 0.0, 0.0],
            words_only: [0.0, 0.0, 1.0],
            meaning_only: [0.99, 0.01, 0.0],
        },
    )
    words, _ = run(conn, "words", query="unicorn wrangler")
    assert set(words) == {"words and meaning", "words only"}
    titles, total = run(conn, "hybrid", query="unicorn wrangler")
    assert titles[0] == "words and meaning" and set(titles) == set(words) | {"meaning only"}
    assert total == 3
    hits, _ = search.search(
        conn,
        Filters(query="unicorn wrangler", mode="hybrid", model=MODEL),
        query_vector=[1.0, 0.0, 0.0],
    )
    assert hits[0].rank > 1 / 61  # in both lists: more than one list's best score


@pytest.mark.db
def test_hybrid_applies_filters_to_both_lists(conn, seed):
    zh = seed.add(title="zh job", desc="unicorn wrangler", cantons=("ZH",))
    be = seed.add(title="be job", desc="unicorn wrangler", cantons=("BE",))
    far_be = seed.add(title="be meaning", desc="horses", cantons=("BE",))
    vectors(conn, {zh: [1.0, 0, 0], be: [1.0, 0, 0], far_be: [1.0, 0, 0]})
    titles, _ = run(conn, "hybrid", query="unicorn wrangler", cantons=["BE"])
    assert set(titles) == {"be job", "be meaning"}


# ---------- stale and missing vectors; the command line (default hybrid, fallback) ----------


@pytest.mark.db
def test_stale_embedding_count_counts_missing_and_changed(conn, seed):
    fresh = seed.add(title="fresh")
    changed = seed.add(title="changed")
    seed.add(title="missing")
    seed.add(title="not in the pool", relevant=False)
    vectors(conn, {fresh: [1.0, 0.0, 0.0], changed: [1.0, 0.0, 0.0]})
    assert search.stale_embedding_count(conn, MODEL) == 1  # only "missing"
    conn.execute("UPDATE postings SET content_hash = 'new' WHERE id = %s", (changed,))
    assert search.stale_embedding_count(conn, MODEL) == 2
    assert search.stale_embedding_count(conn, "other-model") == 3  # no vectors for it


class QueryEmbedder:
    alias, model = "mistral", "mistral-embed"

    def __init__(self, vector=(1.0, 0.0, 0.0), error=None):
        self.vector, self.error, self.queries = list(vector), error, []

    def embed_query(self, text):
        self.queries.append(text)
        if self.error:
            raise self.error
        return self.vector


def cli_search(conn, monkeypatch, capsys, embedder, *argv):
    import contextlib

    from jobfacts import cli, db

    monkeypatch.setattr(db, "connect", lambda: contextlib.nullcontext(conn))
    monkeypatch.setattr(embed, "make_embedder", lambda alias: embedder)
    code = cli.main(["search", *argv])
    out = capsys.readouterr()
    return code, out.out, out.err


@pytest.mark.db
def test_search_defaults_to_hybrid_with_the_default_model(conn, seed, monkeypatch, capsys):
    near = seed.add(title="near", desc="horses")
    seed.add(title="words", desc="unicorn wrangler")
    vectors(conn, {near: [1.0, 0.0, 0.0]}, model="mistral-embed")
    fake = QueryEmbedder()
    code, out, err = cli_search(conn, monkeypatch, capsys, fake, "unicorn wrangler", "--json")
    titles = {r["title"] for r in json.loads(out)["results"]}
    assert code == 0 and titles == {"near", "words"} and fake.queries == ["unicorn wrangler"]
    assert "warning" in err and "1 pool postings" in err  # "words" has no vector: missing


@pytest.mark.db
def test_search_falls_back_to_words_when_the_query_cannot_be_embedded(
    conn, seed, monkeypatch, capsys
):
    near = seed.add(title="near", desc="horses")
    seed.add(title="words", desc="unicorn wrangler")
    vectors(conn, {near: [1.0, 0.0, 0.0]}, model="mistral-embed")
    fake = QueryEmbedder(error=KeyError("MISTRAL_API_KEY"))
    code, out, err = cli_search(conn, monkeypatch, capsys, fake, "unicorn wrangler", "--json")
    assert code == 0
    assert [r["title"] for r in json.loads(out)["results"]] == ["words"]  # valid JSON, words only
    assert err.count("searching by words only") == 1 and "MISTRAL_API_KEY" in err
    assert "pool postings have no current" not in err  # no vector warning in words mode


@pytest.mark.db
def test_search_without_a_query_is_the_plain_filter_list(conn, seed, monkeypatch, capsys):
    seed.add(title="a")
    fake = QueryEmbedder(error=RuntimeError("must not be called"))
    code, out, err = cli_search(conn, monkeypatch, capsys, fake, "--json")
    assert code == 0 and fake.queries == [] and "vector" not in err
    assert [r["title"] for r in json.loads(out)["results"]] == ["a"]


@pytest.mark.db
def test_search_words_mode_never_embeds_and_explicit_modes_report_errors(
    conn, seed, monkeypatch, capsys
):
    seed.add(title="words", desc="unicorn wrangler")
    fake = QueryEmbedder(error=RuntimeError("must not be called"))
    code, out, _ = cli_search(
        conn, monkeypatch, capsys, fake, "unicorn", "--mode", "words", "--json"
    )
    assert code == 0 and fake.queries == [] and json.loads(out)["total"] == 1
    code, _, err = cli_search(conn, monkeypatch, capsys, fake, "unicorn", "--mode", "meaning")
    assert code == 2 and "cannot embed the query" in err  # meaning alone cannot fall back
    code, _, err = cli_search(conn, monkeypatch, capsys, fake, "--mode", "hybrid")
    assert code == 2 and "needs a query" in err
    fake.queries.clear()
    code, _, err = cli_search(conn, monkeypatch, capsys, fake, "unicorn", "--canton", "XX")
    assert code == 2 and fake.queries == []  # a bad filter is caught before the API call


@pytest.mark.db
def test_cli_embed_failure_prints_exactly_one_warning_line(conn, seed, monkeypatch, capsys):
    import logging

    logging.lastResort.setLevel(logging.WARNING)  # what a bare CLI has, outside pytest's capture
    fake = QueryEmbedder(error=RuntimeError("down"))
    code, _, err = cli_search(conn, monkeypatch, capsys, fake, "unicorn", "--json")
    assert code == 0
    lines = err.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("warning: cannot embed the query (")


@pytest.mark.db
def test_cli_explicit_meaning_keeps_the_detailed_error(conn, seed, monkeypatch, capsys):
    fake = QueryEmbedder(error=RuntimeError("down"))
    code, _, err = cli_search(conn, monkeypatch, capsys, fake, "x", "--mode", "meaning")
    assert code == 2 and "cannot embed the query: RuntimeError('down')" in err


def test_new_days_is_bounded():
    with pytest.raises(SearchError):
        Filters(new_days=3651).validate()
    Filters(new_days=3650).validate()
