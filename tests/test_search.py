import pytest

from jobfacts import search
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
        self.run = store.create_run(conn, "jev", jev.MODEL, "v")

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
        store.save_extraction(self.conn, run or self.run, pid, r)
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
    run2 = store.create_run(conn, "jev", jev.MODEL, "v2")
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
    store.save_extraction(conn, run2, pid, r)
    assert ids(conn, cantons=["BE"]) == {"p"}
    assert ids(conn, cantons=["ZH"]) == set()
    # a newer run that failed does not hide the older good one
    run3 = store.create_run(conn, "jev", jev.MODEL, "v3")
    store.save_extraction(conn, run3, pid, Result(error="boom"))
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
            return "v"

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
    Fake.spec_version = lambda self: "v2"
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
def test_search_run_extraction_wins_over_newer_experiment_run(conn, seed):
    pid = seed.add(title="p", cantons=("ZH",))
    conn.execute("UPDATE extraction_runs SET purpose = 'search'")
    exp = store.create_run(conn, "jev", jev.MODEL, "v-exp")
    store.save_extraction(
        conn,
        exp,
        pid,
        Result(
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
        ),
    )
    assert ids(conn, cantons=["ZH"]) == {"p"}
    assert ids(conn, cantons=["BE"]) == set()
