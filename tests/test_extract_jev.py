from fakes import FakeJev, posting
from jobfacts.extract import jev
from jobfacts.extract.spec import FIELDS
from jobfacts.locations import CANTONS


def test_questions_for_normal_posting():
    p = posting()
    q = jev.build_questions(p)
    for name in FIELDS:
        assert f"v_{name}" in q and f"s_{name}_0" in q and f"s_{name}_1" not in q
    # sentence options: ids + none, labels not texts
    opts = list(q["s_work_mode_0"].criteria)
    assert opts[0] == "T" and opts[1] == "L" and opts[2] == "S001" and opts[-1] == "none"
    assert len(opts) == len(p.sentences) + 3
    # value options come from the spec, in order
    assert list(q["v_german"].criteria) == list(FIELDS["german"].values)
    assert [k for k in q if k.startswith("c_")] == [*(f"c_{c}" for c in CANTONS), "c_outside"]
    assert "canton Zurich (ZH; for example Zürich, Winterthur" in q["c_ZH"].instructions
    assert "outside Switzerland" in q["c_outside"].instructions
    assert "s_office_cantons_0" in q


def test_chunking_over_254_sentences():
    description = " ".join(f"Sentence number {i} is here." for i in range(300))
    p = posting(description)
    assert len(p.sentences) == 300
    q = jev.build_questions(p)
    parts = [k for k in q if k.startswith("s_work_mode_")]
    assert len(parts) == 2  # 302 ids -> 254 + 48
    for k in parts:
        assert len(q[k].criteria) <= jev.MAX_OPTIONS
    assert len(q["s_work_mode_0"].criteria) == 255


def test_chunks_function():
    assert jev.chunks([]) == [[]]
    assert [len(c) for c in jev.chunks(list(range(508)))] == [254, 254]


def test_extract_reads_values_sentences_and_cantons():
    p = posting()
    s_hybrid = p.sentences[-1].id
    pick = {
        "v_work_mode": "hybrid",
        "s_work_mode_0": f"S{s_hybrid:03d}",
        "v_german": "required_fluent",
        "s_german_0": "S004",
        "v_min_years": "3",
        "s_min_years_0": "S003",
        "v_french": "not_mentioned",
        "s_french_0": "S002",  # must be dropped: not_mentioned carries no quote
    }
    pick["s_office_cantons_0"] = "L"
    noul = {"c_ZH": 0.9, "c_outside_switzerland": 0.0}
    r = jev.JevExtractor(FakeJev(pick, noul=0.0, noul_pick=noul)).extract(p)
    assert r.error is None
    assert r.values["work_mode"] == "hybrid" and r.sentences["work_mode"] == [s_hybrid]
    assert r.sentences["french"] == []
    assert r.sentences["german"] == [4]
    assert r.seniority == "mid"
    assert r.office_cantons == ["ZH"]
    assert r.sentences["office_cantons"] == [-1]
    assert r.details["office_cantons"]["probabilities"] == {"ZH": 0.9}
    assert r.details["work_mode"]["probabilities"]["hybrid"] == 0.8
    assert r.tokens_in == 1000 and abs(r.cost_usd - 1000 * 0.042 / 1e6) < 1e-12


def test_outside_switzerland_and_several_cantons():
    noul = {"c_ZH": 0.9, "c_GE": 0.6, "c_outside": 0.8}
    p = posting(location="Zürich, Genève, London")
    r = jev.JevExtractor(FakeJev(noul=0.0, noul_pick=noul)).extract(p)
    assert r.office_cantons == ["ZH", "GE", "outside_switzerland"]


def test_low_canton_probability_is_not_confirmed():
    r = jev.JevExtractor(FakeJev(noul=0.2)).extract(posting())
    assert r.office_cantons == []
    assert "office_cantons" not in r.sentences  # no evidence without a canton


def test_best_sentence_across_chunks_and_none():
    from types import SimpleNamespace as NS

    answers = {
        "s_x_0": NS(probabilities={"S001": 0.4, "none": 0.6}),
        "s_x_1": NS(probabilities={"S300": 0.9, "none": 0.1}),
    }
    ids, _ = jev.best_sentence(answers, "x", 2)
    assert ids == [300]
    answers["s_x_1"] = NS(probabilities={"S300": 0.3, "none": 0.7})
    answers["s_x_0"] = NS(probabilities={"S001": 0.2, "none": 0.8})
    assert jev.best_sentence(answers, "x", 2)[0] == []


def test_api_failure_is_an_error_result():
    class Boom:
        def system_one(self, **kw):
            raise RuntimeError("down")

    r = jev.JevExtractor(Boom()).extract(posting())
    assert r.error and "down" in r.error and r.seniority is None


def test_relevance_probability_uses_title_and_head():
    client = FakeJev(noul=0.77)
    assert jev.relevance_probability(client, posting()) == 0.77
    state = client.calls[0][0]
    assert "Senior ML Engineer" in state and "We build machine learning" in state


def test_rules_run_after_the_model():
    # The fake picks the first option everywhere (title_level: not_stated); force a level the
    # title does not state, a canton the posting never names, and a non-remote work mode.
    pick = {
        "v_title_level": "staff_principal",
        "v_work_mode": "hybrid",
        "v_remote_from_switzerland": "allowed",
    }
    r = jev.JevExtractor(FakeJev(pick, noul=0.0, noul_pick={"c_GE": 0.9})).extract(posting())
    assert r.values["title_level"] == "senior"  # no staff word: falls back to the title level
    assert r.sentences["title_level"] == []
    assert r.values["remote_from_switzerland"] == "not_applicable"
    assert r.office_cantons == [] and r.sentences["office_cantons"] == []
    assert len(r.details["rules"]) == 3


def test_relevance_title_exclusion_skips_the_model():
    client = FakeJev(noul=0.9)
    p = posting()
    p.title = "Senior Sales Executive, AI Platform"
    assert jev.relevance_probability(client, p) == 0.0
    assert client.calls == []


def test_relevance_version_changes_with_question_and_threshold(monkeypatch):
    base = jev.relevance_version()
    for name, value in [("RELEVANCE_QUESTION", "other"), ("RELEVANCE_THRESHOLD", 0.6)]:
        with monkeypatch.context() as m:
            m.setattr(jev, name, value)
            assert jev.relevance_version() != base, name
    assert jev.relevance_version() == base
