import json
from datetime import UTC, date, datetime, timedelta

import pytest

from jobfacts import search
from jobfacts import search_eval as se
from jobfacts.extract import jev, store
from jobfacts.extract.base import Result
from jobfacts.search_eval import (
    Application,
    BoardData,
    BoardPosting,
    Employer,
    PostingState,
)

UUID = "8f2c1a3e-5b7d-4e9f-a1c2-3d4e5f6a7b8c"
UUID2 = "11111111-2222-3333-4444-555555555555"

# ---------- company names ----------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Acme AG", "acme"),
        ("Acme, Inc.", "acme"),
        ("Société Exemple SA", "societe exemple"),
        ("Muster GmbH", "muster"),
        ("Beispiel Sàrl", "beispiel"),
        ("Foo & Bar Ltd", "foo bar"),
        ("Example SE", "example"),
        ("Nova Quark", "nova quark"),
        ("AG", "ag"),  # never reduced to nothing
    ],
)
def test_normalise_company(raw, expected):
    assert se.normalise_company(raw) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("acme", "acme", "exact"),
        ("nova quark", "quark", "contained"),
        ("quark", "nova quark", "contained"),
        ("acme", "acmetron", None),  # whole words only
        ("orange", "red orange green", "contained"),
        ("blue", "red blue green", None),  # a short single word is too weak
        ("a1", "a1 digital", None),
        ("blue green", "blue red green", None),  # words must be adjacent
        ("", "acme", None),
    ],
)
def test_company_matches(a, b, expected):
    assert se.company_matches(a, b) == expected


# ---------- job ids and boards in links ----------


@pytest.mark.parametrize(
    ("url", "hint", "expected"),
    [
        (f"https://jobs.ashbyhq.com/acme/{UUID}", None, ("ashby", UUID)),
        (f"https://jobs.ashbyhq.com/acme/{UUID.upper()}/application", None, ("ashby", UUID)),
        (f"https://acme.example/careers?ashby_jid={UUID}", None, ("ashby", UUID)),
        (
            "https://job-boards.greenhouse.io/acme/jobs/1111111111",
            None,
            ("greenhouse", "1111111111"),
        ),
        (
            "https://job-boards.eu.greenhouse.io/acme/jobs/2222222222?gh_src=abc",
            None,
            ("greenhouse", "2222222222"),
        ),
        ("https://boards.greenhouse.io/acme/jobs/123456", None, ("greenhouse", "123456")),
        ("https://acme.example/careers/open?gh_jid=987654", None, ("greenhouse", "987654")),
        (f"https://jobs.lever.co/acme/{UUID}", None, ("lever", UUID)),
        (f"https://jobs.eu.lever.co/acme/{UUID}/apply", None, ("lever", UUID)),
        (
            "https://jobs.smartrecruiters.com/AcmeCo2/3333333333-ai-engineer",
            None,
            ("smartrecruiters", "3333333333"),
        ),
        ("https://apply.workable.com/acme/j/ABCDEF1234/", None, ("workable", "abcdef1234")),
        ("https://apply.workable.com/j/FEDCBA9876", None, ("workable", "fedcba9876")),
        (
            "https://acme.recruitee.com/o/ml-engineer-zurich",
            None,
            ("recruitee", "acme/ml-engineer-zurich"),
        ),
        ("https://acme.jobs.personio.de/job/2771234?language=en", None, ("personio", "2771234")),
        (
            "https://acme.teamtailor.com/jobs/9990001-ai-engineer",
            None,
            ("teamtailor", "9990001"),
        ),
        (
            "https://acme.wd3.myworkdayjobs.com/en-US/ext/job/Zurich/Data-Scientist_R-20370-1/apply",
            None,
            ("workday", "acme/r-20370"),
        ),
        (
            "https://acme.wd3.myworkdayjobs.com/ext/job/Zurich/Data-Scientist_R-20370-4",
            None,
            ("workday", "acme/r-20370"),
        ),
        (
            "https://acme.wd3.myworkdayjobs.com/ext/job/Basel/Apprentice_202607-117627",
            None,
            ("workday", "acme/202607-117627"),
        ),
        (
            "https://acme.wd3.myworkdayjobs.com/ext/job/Bern/Analyst_202610-125078-1",
            None,
            ("workday", "acme/202610-125078"),
        ),
        (
            "https://acme.wd3.myworkdayjobs.com/ext/job/Bern/Analyst_202610-125078-2/apply",
            None,
            ("workday", "acme/202610-125078"),
        ),
        ("https://www.linkedin.com/jobs/view/4012345678", None, None),
        ("", None, None),
    ],
)
def test_job_keys(url, hint, expected):
    keys = se.job_keys(url, hint)
    assert keys == ({expected} if expected else set())


def test_job_keys_hint_covers_custom_domains():
    url = "https://careers.acme.example/o/ml-engineer-zurich"
    # a custom domain is its own namespace; the stored board id is another one
    assert se.job_keys(url) == set()
    assert se.job_keys(url, "recruitee") == {
        ("recruitee", "careers.acme.example/ml-engineer-zurich")
    }
    keys = se.job_keys(url, "recruitee", "acmeboard")
    assert keys == {
        ("recruitee", "careers.acme.example/ml-engineer-zurich"),
        ("recruitee", "acmeboard/ml-engineer-zurich"),
    }
    keys = se.job_keys("https://jobs.acme.example/jobs/777-ml-engineer", "teamtailor")
    assert keys == {("teamtailor", "777")}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (f"https://jobs.ashbyhq.com/Acme-Labs/{UUID}", ("ashby", "acme-labs")),
        ("https://job-boards.eu.greenhouse.io/acme/jobs/1", ("greenhouse", "acme")),
        ("https://boards.greenhouse.io/embed/job_app?for=acme&token=55", ("greenhouse", "acme")),
        (f"https://jobs.lever.co/acme/{UUID}", ("lever", "acme")),
        ("https://jobs.smartrecruiters.com/AcmeCo2/1-x", ("smartrecruiters", "acmeco2")),
        ("https://apply.workable.com/acme/j/ABC/", ("workable", "acme")),
        ("https://apply.workable.com/j/ABC", None),
        ("https://acme.recruitee.com/o/x", ("recruitee", "acme")),
        ("https://acme.jobs.personio.de/job/1", ("personio", "acme")),
        ("https://acme.teamtailor.com/jobs/1-x", ("teamtailor", "acme")),
        ("https://acme.wd3.myworkdayjobs.com/ext/job/Zurich/X_R-1", ("workday", "acme")),
        ("https://www.linkedin.com/jobs/view/1", None),
    ],
)
def test_board_key(url, expected):
    assert se.board_key(url) == expected


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://www.linkedin.com/jobs/view/1", "linkedin"),
        ("https://hiring.cafe/viewjob/abc", "hiring.cafe"),
        (f"https://jobs.ashbyhq.com/acme/{UUID}", "ats-direct"),
        ("https://acme.wd3.myworkdayjobs.com/x/job/y", "ats-direct"),
        ("https://acme.teamtailor.com/jobs/1", "ats-direct"),
        ("https://www.example.org/careers/1", "other"),
        ("", "none"),
    ],
)
def test_link_source(url, expected):
    assert se.link_source(url) == expected


# ---------- title matching ----------


def _posting(pid, title, days_after=0, employer_id=1, url="", sid=None, closed=None):
    seen = datetime(2026, 11, 3, tzinfo=UTC) + timedelta(days=days_after)
    return BoardPosting(
        pid, employer_id, title, url or f"https://x/{pid}", sid or str(pid), seen, closed
    )


def test_title_exact_ignores_gender_tags_and_case():
    posts = [_posting(1, "Machine Learning Engineer (m/f/d)"), _posting(2, "Data Analyst")]
    best, method = se.best_title_matches("machine learning engineer", posts, None)
    assert [p.id for p in best] == [1] and method == "title_exact"


def test_title_underscores_in_folder_names_count_as_spaces():
    posts = [_posting(1, "Sr AI Engineer")]
    best, method = se.best_title_matches("Sr_AI_Engineer", posts, None)
    assert [p.id for p in best] == [1] and method == "title_exact"


def test_title_fuzzy_threshold():
    assert se.TITLE_RATIO == 0.9
    posts = [_posting(1, "Senior Machine Learning Engineer NLP")]
    best, method = se.best_title_matches("Senior Machine Learning Engineer", posts, None)
    assert [p.id for p in best] == [1] and method == "title_fuzzy"  # ratio about 0.92
    best, method = se.best_title_matches("Machine Learning Engineer", posts, None)
    assert best == [] and method == ""  # ratio about 0.82


@pytest.mark.parametrize(
    ("wanted", "posted"),
    [
        ("Senior Machine Learning Engineer", "Staff Machine Learning Engineer"),
        ("AI Platform Engineer", "Senior AI Platform Engineer"),
        ("Member of Technical Staff, MLE", "Member of Technical Staff, Modeling"),
        # similar enough (ratio above 0.9) but a different level word
        (
            "Senior Machine Learning Engineer Platform Team",
            "Staff Machine Learning Engineer Platform Team",
        ),
        (
            "Junior Data Engineer Analytics Platform",
            "Principal Data Engineer Analytics Platform",
        ),
    ],
)
def test_title_fuzzy_never_matches_a_different_level_or_role(wanted, posted):
    assert se.best_title_matches(wanted, [_posting(1, posted)], None) == ([], "")


def test_title_fuzzy_needs_the_same_level_words():
    posts = [_posting(1, "Senior Machine Learning Engineer Platform Team NLP")]
    wanted = "Sr Machine Learning Engineer Platform Team"  # Sr = senior
    best, method = se.best_title_matches(wanted, posts, None)
    assert [p.id for p in best] == [1] and method == "title_fuzzy"
    assert se.level_words("Staff ML Engineer") != se.level_words("Principal ML Engineer")
    assert se.level_words("Founding Engineer") == {"founding"}
    assert se.level_words("Member of Technical Staff") == frozenset()


def test_title_ties_keep_all_equal_titles_and_order_by_date():
    posts = [
        _posting(1, "ML Engineer", days_after=10),
        _posting(2, "ML Engineer", days_after=1),
        _posting(3, "Other"),
    ]
    best, _ = se.best_title_matches("ML Engineer", posts, date(2026, 11, 4))
    assert [p.id for p in best] == [2, 1]


# ---------- filter reasons ----------


def _x(**kw):
    base = {
        "work_mode": "onsite",
        "remote_from_switzerland": "not_applicable",
        "german": "not_mentioned",
        "contract_type": "permanent",
        "office_cantons": ["ZH"],
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    ("kw", "expected"),
    [
        ({}, None),
        ({"german": "required_fluent"}, "german required_fluent"),
        ({"german": "required_level_not_stated"}, None),
        ({"contract_type": "fixed_term"}, "fixed-term contract"),
        ({"contract_type": None}, None),
        (
            {"work_mode": "remote", "remote_from_switzerland": "not_allowed", "office_cantons": []},
            "remote, not allowed from Switzerland",
        ),
        (
            {
                "work_mode": "remote",
                "remote_from_switzerland": "not_allowed",
                "office_cantons": ["GE"],
            },
            None,
        ),
        ({"work_mode": "remote", "remote_from_switzerland": "unclear", "office_cantons": []}, None),
        ({"office_cantons": ["outside_switzerland"]}, "no Swiss office"),
        ({"office_cantons": []}, "no Swiss office"),
        ({"office_cantons": None}, "no Swiss office"),
        ({"work_mode": "hybrid", "office_cantons": ["outside_switzerland", "VD"]}, None),
        # the first failing rule is reported
        ({"german": "required_fluent", "contract_type": "fixed_term"}, "german required_fluent"),
    ],
)
def test_filter_reason(kw, expected):
    assert se.filter_reason(_x(**kw)) == expected


# ---------- matching (pure) ----------

EMP = Employer(1, "Acme AG", "ashby", "acme", date(2026, 11, 2))
EMP2 = Employer(2, "Beta Labs", "greenhouse", "betalabs", date(2026, 11, 2))


def _app(key="Acme/ML Engineer", day=date(2026, 11, 4), url=""):
    company, _, title = key.partition("/")
    return Application(key, company, title.rsplit("/", 1)[-1], day, url)


def test_match_by_url_id_beats_title():
    data = BoardData.build(
        [EMP],
        [
            _posting(
                1, "Totally Different Title", url=f"https://jobs.ashbyhq.com/acme/{UUID}", sid=UUID
            ),
            _posting(2, "ML Engineer", url=f"https://jobs.ashbyhq.com/acme/{UUID2}", sid=UUID2),
        ],
    )
    m = se.match_application(_app(url=f"https://jobs.ashbyhq.com/acme/{UUID}/application"), data)
    assert [p.id for p in m.postings] == [1] and m.method == "url"
    assert m.employers == [EMP]


def test_match_by_url_through_source_job_id_and_gh_jid():
    data = BoardData.build(
        [EMP2],
        [
            _posting(
                5, "Other", employer_id=2, url="https://jobs.betalabs.example/x", sid="2222222222"
            )
        ],
    )
    link = "https://job-boards.eu.greenhouse.io/betalabs/jobs/2222222222?gh_src=q"
    assert [p.id for p in se.match_application(_app("Whatever/Role", url=link), data).postings] == [
        5
    ]


def test_match_by_company_name_and_title():
    data = BoardData.build(
        [EMP, EMP2], [_posting(1, "ML Engineer"), _posting(2, "ML Engineer", employer_id=2)]
    )
    m = se.match_application(_app("acme/ML Engineer"), data)
    assert m.employer_method == "name_exact" and m.posting_method == "title_exact"
    assert [p.id for p in m.postings] == [1]
    assert m.method == "name_exact + title_exact"


def test_match_name_contained_in_other_name():
    deep = Employer(3, "Quark", "greenhouse", "quark", None)
    data = BoardData.build([deep], [_posting(1, "Research Engineer", employer_id=3)])
    m = se.match_application(_app("Nova Quark/Research Engineer"), data)
    assert m.employer_method == "name_contained" and [p.id for p in m.postings] == [1]


def test_match_employer_from_url_board_when_name_differs():
    data = BoardData.build([EMP], [_posting(1, "ML Engineer")])
    link = f"https://jobs.ashbyhq.com/acme/{UUID2}"
    m = se.match_application(_app("Completely Other Name/ML Engineer", url=link), data)
    assert m.employer_method == "url_board" and [p.id for p in m.postings] == [1]


def test_url_match_brings_its_group_mates():
    data = BoardData.build(
        [EMP],
        [
            _posting(
                1, "ML Engineer (m/f/d)", url=f"https://jobs.ashbyhq.com/acme/{UUID}", sid=UUID
            ),
            _posting(2, "ML Engineer", days_after=1),
            _posting(3, "Data Analyst"),
        ],
    )
    m = se.match_application(_app(url=f"https://jobs.ashbyhq.com/acme/{UUID}"), data)
    assert [p.id for p in m.postings] == [1, 2] and m.method == "url"


def test_recruitee_slugs_are_per_board():
    one = Employer(1, "Alpha", "recruitee", "alpha", date(2026, 11, 2))
    two = Employer(2, "Beta", "recruitee", "beta", date(2026, 11, 2))
    data = BoardData.build(
        [one, two],
        [
            _posting(
                1,
                "Open application",
                employer_id=1,
                url="https://alpha.recruitee.com/o/open-app",
                sid="11",
            ),
            _posting(
                2,
                "Open application",
                employer_id=2,
                url="https://careers.beta.example/o/open-app",
                sid="22",
            ),
        ],
    )
    m = se.match_application(_app("X/Y", url="https://beta.recruitee.com/o/open-app"), data)
    assert [p.id for p in m.postings] == [2]
    m = se.match_application(_app("X/Y", url="https://alpha.recruitee.com/o/open-app"), data)
    assert [p.id for p in m.postings] == [1]


def test_title_match_ignores_postings_closed_before_the_application():
    old = _posting(1, "ML Engineer", closed=datetime(2026, 11, 3, 12, tzinfo=UTC))
    late = _posting(2, "ML Engineer", closed=datetime(2026, 11, 4, 12, tzinfo=UTC))
    data = BoardData.build([EMP], [old, late])
    m = se.match_application(_app(day=date(2026, 11, 4)), data)
    assert [p.id for p in m.postings] == [2]
    assert se.match_application(_app(day=date(2026, 11, 5)), data).postings == []


def test_title_match_ignores_reposts_after_the_repost_window():
    assert se.REPOST_DAYS == 30
    near = _posting(1, "ML Engineer", days_after=30)  # first seen 3 Dec
    far = _posting(2, "ML Engineer", days_after=31)
    data = BoardData.build([EMP], [near, far])
    # employer read on 2 Nov, application on 3 Nov: only the posting within 30 days counts
    m = se.match_application(_app(day=date(2026, 11, 3)), data)
    assert [p.id for p in m.postings] == [1]


def test_repost_window_does_not_apply_when_the_application_predates_collection():
    late = Employer(1, "Acme AG", "ashby", "acme", date(2026, 12, 1))
    far = _posting(2, "ML Engineer", days_after=60)
    m = se.match_application(_app(day=date(2026, 11, 3)), BoardData.build([late], [far]))
    assert [p.id for p in m.postings] == [2]


def test_dates_are_compared_in_zurich_time():
    # 23:30 UTC on 3 Nov is already 4 Nov in Zurich
    seen = datetime(2026, 11, 3, 23, 30, tzinfo=UTC)
    post = BoardPosting(1, 1, "ML Engineer", "u", "1", seen, None)
    out = se.classify(_app(day=date(2026, 11, 4)), _match([post]), lambda p: _state())
    assert out.timing == "same day"
    closed = BoardPosting(
        1, 1, "ML Engineer", "u", "1", seen, datetime(2026, 11, 3, 23, 30, tzinfo=UTC)
    )
    data = BoardData.build([EMP], [closed])
    assert se.match_application(_app(day=date(2026, 11, 4)), data).postings == [closed]


def test_match_no_employer_and_no_posting():
    data = BoardData.build([EMP], [_posting(1, "ML Engineer")])
    assert se.match_application(_app("Unknown Corp/ML Engineer"), data).employers == []
    m = se.match_application(_app("Acme/Pastry Chef"), data)
    assert m.employers == [EMP] and m.postings == []


# ---------- classification (pure) ----------

GOOD = _x()


def _match(postings=(), employers=(EMP,)):
    return se.Match(
        list(employers), "name_exact", list(postings), "title_exact" if postings else ""
    )


def _state(relevant=True, prob=0.9, x=GOOD, stale=False, has_rel=True, eligible=True, loc="Zurich"):
    return PostingState((relevant, prob) if has_rel else None, x, stale, eligible, loc)


def test_not_covered():
    out = se.classify(_app(), se.Match([], "", [], ""), lambda pid: None)
    assert out.outcome == "not_covered"


def test_not_collected_vs_unknown_before_collection():
    m = _match()
    assert se.classify(_app(day=date(2026, 11, 2)), m, lambda p: None).outcome == "not_collected"
    out = se.classify(_app(day=date(2026, 11, 1)), m, lambda p: None)
    assert out.outcome == "unknown_before_collection"
    never = _match(employers=[Employer(1, "Acme", "ashby", "acme", None)])
    assert se.classify(_app(), never, lambda p: None).outcome == "unknown_before_collection"


def test_first_read_is_the_earliest_over_candidate_employers():
    m = _match(employers=[EMP, Employer(9, "Acme Two", "lever", "acme", date(2026, 1, 1))])
    assert se.classify(_app(day=date(2026, 2, 1)), m, lambda p: None).outcome == "not_collected"


@pytest.mark.parametrize(
    ("state", "outcome"),
    [
        (_state(relevant=False, prob=0.12), "not_relevant"),
        (_state(has_rel=False), "no_relevance"),
        (_state(x=None, eligible=False), "not_eligible"),
        (_state(x=None), "not_extracted"),
        (_state(x=_x(german="required_fluent")), "filtered"),
        (_state(), "found"),
    ],
)
def test_outcome_order(state, outcome):
    out = se.classify(_app(), _match([_posting(1, "ML Engineer")]), lambda pid: state)
    assert out.outcome == outcome


def test_outcomes_list_is_in_pipeline_order():
    assert se.OUTCOMES == [
        "not_covered",
        "unknown_before_collection",
        "not_collected",
        "no_relevance",
        "not_relevant",
        "not_eligible",
        "not_extracted",
        "filtered",
        "found",
    ]


def test_not_eligible_comes_after_relevance_and_before_extraction():
    post = [_match([_posting(1, "ML Engineer")])][0]
    out = se.classify(_app(), post, lambda p: _state(relevant=False, x=None, eligible=False))
    assert out.outcome == "not_relevant"
    out = se.classify(_app(), post, lambda p: _state(has_rel=False, x=None, eligible=False))
    assert out.outcome == "no_relevance"
    out = se.classify(_app(), post, lambda p: _state(x=None, eligible=False, loc="Berlin, Germany"))
    assert out.outcome == "not_eligible" and out.detail == "Berlin, Germany"


def test_an_existing_extraction_beats_eligibility():
    post = _match([_posting(1, "ML Engineer")])
    out = se.classify(_app(), post, lambda p: _state(eligible=False))
    assert out.outcome == "found"


def test_details():
    post = [_posting(1, "ML Engineer")]
    out = se.classify(_app(), _match(post), lambda p: _state(relevant=False, prob=0.12))
    assert "0.12" in out.detail
    out = se.classify(_app(), _match(post), lambda p: _state(x=_x(german="required_fluent")))
    assert out.detail == "german required_fluent"
    # an extraction for older text: not shown for an open posting, noted for a closed one
    out = se.classify(_app(), _match(post), lambda p: _state(stale=True))
    assert out.outcome == "not_extracted" and out.detail == "extraction is for older text"
    closed = [_posting(1, "ML Engineer", closed=datetime(2026, 11, 20, tzinfo=UTC))]
    out = se.classify(_app(), _match(closed), lambda p: _state(stale=True))
    assert out.outcome == "found" and "text changed" in out.detail


@pytest.mark.parametrize(("days_after", "timing"), [(-3, "before"), (0, "same day"), (5, "after")])
def test_found_timing(days_after, timing):
    post = _posting(1, "ML Engineer", days_after=days_after)  # first seen 3 Nov + days_after
    out = se.classify(_app(day=date(2026, 11, 3)), _match([post]), lambda p: _state())
    assert out.timing == timing


def test_found_when_collection_started_after_the_application():
    post = _posting(1, "ML Engineer", days_after=0)
    out = se.classify(_app(day=date(2026, 10, 30)), _match([post]), lambda p: _state())
    assert out.timing == "collection started later"
    assert out.timing_text == (
        "collected from 2026-11-02 (after the application; collection started then)"
    )
    out = se.classify(_app(day=date(2026, 11, 2)), _match([post]), lambda p: _state())
    assert out.timing_text == "posting first seen after the application"


BAD = _x(office_cantons=["outside_switzerland"])  # dropped: no Swiss office


def test_group_is_judged_by_the_posting_search_shows_first():
    # one job in two places, search order is first_seen_at DESC: the newer posting is shown
    older, newer = _posting(1, "ML Engineer"), _posting(2, "ML Engineer", days_after=2)
    states = {1: _state(), 2: _state(x=BAD)}
    out = se.classify(_app(), _match([older, newer]), states.__getitem__)
    assert out.outcome == "filtered" and out.posting.id == 2
    states = {1: _state(x=BAD), 2: _state()}
    out = se.classify(_app(), _match([older, newer]), states.__getitem__)
    assert out.outcome == "found" and out.posting.id == 2


def test_group_order_ties_on_first_seen_use_the_lower_id():
    a, b = _posting(5, "ML Engineer"), _posting(4, "ML Engineer")
    out = se.classify(_app(), _match([a, b]), {4: _state(x=BAD), 5: _state()}.__getitem__)
    assert out.outcome == "filtered" and out.posting.id == 4


def test_group_mate_that_is_not_visible_does_not_hide_the_shown_posting():
    # the newer posting has no extraction, so search shows the older one
    older, newer = _posting(1, "ML Engineer"), _posting(2, "ML Engineer", days_after=2)
    states = {1: _state(), 2: _state(x=None)}
    out = se.classify(_app(), _match([older, newer]), states.__getitem__)
    assert out.outcome == "found" and out.posting.id == 1


def test_group_without_a_visible_posting_reports_the_furthest_failure():
    a, b = _posting(1, "ML Engineer"), _posting(2, "ML Engineer", days_after=2)
    states = {1: _state(relevant=False), 2: _state(x=None)}
    out = se.classify(_app(), _match([a, b]), states.__getitem__)
    assert out.outcome == "not_extracted" and out.posting.id == 2


def test_different_titles_are_different_groups_and_the_furthest_wins():
    a, b = _posting(1, "ML Engineer"), _posting(2, "ML Engineer Platform")
    states = {1: _state(x=BAD), 2: _state()}
    out = se.classify(_app(), _match([a, b]), states.__getitem__)
    assert out.outcome == "found" and out.posting.id == 2


def test_recall_all_wanted_adds_not_eligible():
    counts = {"found": 6, "filtered": 2, "not_collected": 2, "not_eligible": 10, "not_covered": 5}
    assert se.recall(counts)[:2] == (6, 10)
    assert se.recall(counts, all_wanted=True)[:2] == (6, 20)


def test_recall_excludes_uncovered_and_unknown():
    counts = {
        "found": 6,
        "filtered": 2,
        "not_collected": 2,
        "not_covered": 50,
        "unknown_before_collection": 7,
    }
    k, n, lo, hi = se.recall(counts)
    assert (k, n) == (6, 10) and lo < 0.6 < hi
    assert se.recall({})[:2] == (0, 0)


# ---------- database ----------


class World:
    """Employers, a collection history, board postings, relevance and extractions."""

    def __init__(self, conn):
        self.conn = conn
        self.run = store.create_run(conn, "jev", jev.MODEL, "v", purpose="search")
        self.n = 0

    def employer(self, name, ats="ashby", board="acme", first_read="2026-11-02", ok=True):
        eid = self.conn.execute(
            "INSERT INTO employers (name, ats, board_id) VALUES (%s,%s,%s) RETURNING id",
            (name, ats, board),
        ).fetchone()[0]
        if first_read:
            self.read(eid, first_read, ok)
        return eid

    def read(self, eid, day, ok=True):
        run = self.conn.execute(
            "INSERT INTO collection_runs (started_at) VALUES (%s::timestamptz) RETURNING id",
            (f"{day} 08:00+00",),
        ).fetchone()[0]
        self.conn.execute(
            "INSERT INTO board_reads (run_id, employer_id, ok) VALUES (%s,%s,%s)", (run, eid, ok)
        )

    def posting(
        self,
        eid,
        title="ML Engineer",
        sid=None,
        url=None,
        seen="2026-11-03",
        origin="board",
        rel=0.9,
        values=None,
        cantons=("ZH",),
        location="Zurich, Switzerland",
        extract=True,
        ext_hash="h",
        closed=False,
    ):
        self.n += 1
        pid = self.conn.execute(
            "INSERT INTO postings (employer_id, source_job_id, url, title, location_raw, "
            "content_hash, origin, first_seen_at, closed_at) VALUES "
            "(%s,%s,%s,%s,%s,'h',%s,%s::timestamptz, CASE WHEN %s THEN now() END) RETURNING id",
            (
                eid,
                sid or str(self.n),
                url or f"https://x/{self.n}",
                title,
                location,
                origin,
                f"{seen} 10:00+00",
                closed,
            ),
        ).fetchone()[0]
        if rel is not None:
            store.save_relevance(self.conn, pid, jev.MODEL, rel, 0.2)
        if extract:
            v = {
                "work_mode": "onsite",
                "remote_from_switzerland": "not_applicable",
                "german": "not_mentioned",
                "french": "not_mentioned",
                "min_years": "not_stated",
                "title_level": "not_stated",
                "contract_type": "not_stated",
            }
            v.update(values or {})
            result = Result(values=v, office_cantons=list(cantons))
            store.save_extraction(self.conn, self.run, pid, result, content_hash=ext_hash)
        return pid


@pytest.fixture
def world(conn):
    return World(conn)


def _eval(conn, **app_kw):
    apps = [_app(**app_kw)]
    return se.evaluate(conn, apps)[0]


@pytest.mark.db
def test_db_not_covered(conn, world):
    world.employer("Acme AG")
    assert _eval(conn, key="Nobody/ML Engineer").outcome == "not_covered"


@pytest.mark.db
def test_db_application_origin_and_application_employers_are_ignored(conn, world):
    conn.execute(
        "INSERT INTO employers (name, ats, board_id, active) "
        "VALUES ('Nobody','application','Nobody',false)"
    )
    eid = conn.execute("SELECT id FROM employers WHERE ats = 'application'").fetchone()[0]
    world.posting(eid, origin="application")
    assert _eval(conn, key="Nobody/ML Engineer").outcome == "not_covered"
    acme = world.employer("Acme AG")
    world.posting(acme, origin="application")
    assert _eval(conn, key="Acme/ML Engineer").outcome == "not_collected"


@pytest.mark.db
def test_db_not_collected_and_unknown_before_collection(conn, world):
    world.employer("Acme AG", first_read="2026-11-02")
    assert _eval(conn, day=date(2026, 11, 4)).outcome == "not_collected"
    assert _eval(conn, day=date(2026, 10, 20)).outcome == "unknown_before_collection"


@pytest.mark.db
def test_db_failed_reads_do_not_count_as_first_read(conn, world):
    eid = world.employer("Acme AG", first_read="2026-10-01", ok=False)
    world.read(eid, "2026-11-02", ok=True)
    assert _eval(conn, day=date(2026, 10, 20)).outcome == "unknown_before_collection"


@pytest.mark.db
def test_db_not_relevant_and_no_relevance(conn, world):
    eid = world.employer("Acme AG")
    pid = world.posting(eid, rel=0.05)
    out = _eval(conn)
    assert out.outcome == "not_relevant" and "0.05" in out.detail
    conn.execute("DELETE FROM relevance WHERE posting_id = %s", (pid,))
    assert _eval(conn).outcome == "no_relevance"


@pytest.mark.db
def test_db_not_extracted_and_text_changed_fallback(conn, world):
    eid = world.employer("Acme AG")
    world.posting(eid, extract=False)
    assert _eval(conn).outcome == "not_extracted"
    beta = world.employer("Beta Labs", ats="lever", board="beta")
    world.posting(beta, ext_hash="old")  # extraction made for an older text: search hides it
    out = _eval(conn, key="Beta Labs/ML Engineer")
    assert out.outcome == "not_extracted" and out.detail == "extraction is for older text"
    gamma = world.employer("Gamma Works", ats="lever", board="gamma")
    world.posting(gamma, ext_hash="old", closed=True)  # closed: the latest extraction is used
    out = _eval(conn, key="Gamma Works/ML Engineer")
    assert out.outcome == "found" and "text changed" in out.detail


@pytest.mark.db
def test_db_experiment_runs_and_failed_extractions_do_not_count(conn, world):
    eid = world.employer("Acme AG")
    world.posting(eid)
    conn.execute("UPDATE extraction_runs SET purpose = 'experiment'")
    assert _eval(conn).outcome == "not_extracted"
    conn.execute("UPDATE extraction_runs SET purpose = 'search'")
    conn.execute("UPDATE extractions SET error = 'boom'")
    assert _eval(conn).outcome == "not_extracted"


@pytest.mark.db
def test_db_not_eligible_uses_the_extraction_eligibility_rule(conn, world):
    eid = world.employer("Acme AG")
    world.posting(eid, title="ML Engineer", location="Berlin, Germany", extract=False)
    out = _eval(conn)
    assert out.outcome == "not_eligible" and out.detail == "Berlin, Germany"
    world.posting(eid, title="Remote ML Engineer", location="Berlin, Germany", extract=False)
    assert _eval(conn, key="Acme/Remote ML Engineer").outcome == "not_extracted"


@pytest.mark.db
def test_db_filtered(conn, world):
    eid = world.employer("Acme AG")
    world.posting(eid, values={"german": "required_fluent"})
    out = _eval(conn)
    assert out.outcome == "filtered" and out.detail == "german required_fluent"


@pytest.mark.db
def test_db_found_includes_closed_postings(conn, world):
    eid = world.employer("Acme AG")
    pid = world.posting(eid, closed=True, seen="2026-11-03")
    out = _eval(conn, day=date(2026, 11, 4))
    assert out.outcome == "found" and out.posting.id == pid and out.timing == "before"


@pytest.mark.db
def test_db_url_match_picks_posting_from_any_employer(conn, world):
    eid = world.employer("Some Other Name")
    pid = world.posting(eid, title="Renamed", sid=UUID, url=f"https://jobs.ashbyhq.com/acme/{UUID}")
    out = _eval(conn, key="Acme/ML Engineer", url=f"https://jobs.ashbyhq.com/acme/{UUID}")
    assert out.outcome == "found" and out.posting.id == pid and out.method == "url"


def _shown_by_search(conn, title):
    hits, _ = search.search(conn, search.Filters(limit=100))
    return [h.id for h in hits if h.title == title]


@pytest.mark.db
def test_db_duplicate_locations_are_judged_like_search_does(conn, world):
    eid = world.employer("Acme AG")
    # same job in two places; search shows the newer one, which the owner filters drop
    world.posting(eid, cantons=("BE",), seen="2026-11-01")
    newer = world.posting(eid, cantons=("outside_switzerland",), seen="2026-11-03")
    assert _shown_by_search(conn, "ML Engineer") == [newer]
    out = _eval(conn)
    assert out.outcome == "filtered" and out.posting.id == newer


@pytest.mark.db
def test_db_duplicate_locations_url_match_uses_the_same_group_rule(conn, world):
    eid = world.employer("Acme AG")
    linked = world.posting(
        eid,
        cantons=("BE",),
        seen="2026-11-01",
        sid=UUID,
        url=f"https://jobs.ashbyhq.com/acme/{UUID}",
    )
    newer = world.posting(eid, cantons=("outside_switzerland",), seen="2026-11-03")
    assert _shown_by_search(conn, "ML Engineer") == [newer]
    out = _eval(conn, url=f"https://jobs.ashbyhq.com/acme/{UUID}")
    assert linked and out.outcome == "filtered" and out.posting.id == newer


@pytest.mark.db
def test_db_duplicate_locations_shown_posting_passes(conn, world):
    eid = world.employer("Acme AG")
    world.posting(eid, cantons=("outside_switzerland",), seen="2026-11-01")
    newer = world.posting(eid, cantons=("BE",), seen="2026-11-03")
    assert _shown_by_search(conn, "ML Engineer") == [newer]
    out = _eval(conn)
    assert out.outcome == "found" and out.posting.id == newer


@pytest.mark.db
def test_db_zurich_dates_for_first_read(conn, world):
    # first read at 23:30 UTC on 2 Nov is 3 Nov in Zurich: an application on 3 Nov is covered
    eid = world.employer("Acme AG", first_read=None)
    run = conn.execute(
        "INSERT INTO collection_runs (started_at) VALUES ('2026-11-02 23:30+00') RETURNING id"
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO board_reads (run_id, employer_id, ok) VALUES (%s,%s,true)", (run, eid)
    )
    assert _eval(conn, day=date(2026, 11, 3)).outcome == "not_collected"
    assert _eval(conn, day=date(2026, 11, 2)).outcome == "unknown_before_collection"


@pytest.mark.db
def test_db_end_to_end_report(conn, world, tmp_path):
    eid = world.employer("Acme AG")
    world.posting(eid, title="ML Engineer", seen="2026-11-03")
    world.posting(eid, title="Data Scientist", values={"contract_type": "fixed_term"})
    world.employer("Beta Labs", ats="greenhouse", board="beta")
    apps = tmp_path / "apps"
    jobs = {
        "Acme/ML Engineer": ("https://www.linkedin.com/jobs/view/1\nbody", "2026-11-04"),
        "Acme/Data Scientist": ("Not provided\nbody", "2026-11-04"),
        "Acme/Pastry Chef": ("body only", "2026-11-04"),
        "Beta Labs/Analyst": ("body", "2026-10-01"),
        "Ghost Inc/Nested/Role": ("body", "2026-11-04"),
        "Acme/Undated": ("body", None),
        "Acme/Old": ("body", "2026-09-01"),
    }
    dates = {}
    for key, (text, day) in jobs.items():
        folder = apps / key
        folder.mkdir(parents=True)
        (folder / "job_description.md").write_text(text, encoding="utf-8")
        if day:
            dates[key] = day
    dates_file = tmp_path / "dates.json"
    dates_file.write_text(json.dumps(dates), encoding="utf-8")
    out = tmp_path / "reports" / "r.md"

    result = se.run(
        conn, apps, dates_file, since=date(2026, 10, 1), out=out, today=date(2026, 11, 5)
    )

    counts = result.counts
    assert counts["found"] == 1 and counts["filtered"] == 1 and counts["not_collected"] == 1
    assert counts["unknown_before_collection"] == 1 and counts["not_covered"] == 1
    assert result.no_date == 1 and result.before_since == 1
    text = out.read_text(encoding="utf-8")
    assert (
        "Recall within scope" in text and "1 / 3" in text
    )  # found / (found + filtered + not_collected)
    assert "fixed-term contract" in text and "Ghost Inc" in text
    assert "linkedin" in text
    assert "Recall of all wanted jobs at covered employers: 1 / 3" in result.summary
    assert "Recall within scope" in result.summary


def test_default_output_path_is_in_the_private_folder():
    path = se.default_output(date(2026, 11, 5))
    assert path.parts[-4:] == ("data", "private", "reports", "search_eval_2026-11-05.md")


@pytest.mark.db
def test_db_run_skips_a_file_directly_under_the_applications_folder(conn, world, tmp_path, capsys):
    world.employer("Acme AG")
    apps = tmp_path / "apps"
    (apps / "Acme" / "ML Engineer").mkdir(parents=True)
    (apps / "Acme" / "ML Engineer" / "job_description.md").write_text("x", encoding="utf-8")
    (apps / "job_description.md").write_text("stray", encoding="utf-8")
    dates = tmp_path / "d.json"
    dates.write_text(json.dumps({"Acme/ML Engineer": "2026-11-04"}), encoding="utf-8")
    result = se.run(conn, apps, dates, out=tmp_path / "r.md", today=date(2026, 11, 5))
    assert sum(result.counts.values()) == 1
    assert "job_description.md" in capsys.readouterr().err


@pytest.mark.parametrize("content", ["[1, 2]", '"text"', "not json", "null"])
def test_run_rejects_a_dates_file_that_is_not_a_json_object(tmp_path, content):
    dates = tmp_path / "d.json"
    dates.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match="dates"):
        se.run(None, tmp_path, dates, out=tmp_path / "r.md")
