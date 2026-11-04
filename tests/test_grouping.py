import pytest

from jobfacts.grouping import group_rows, title_key


@pytest.mark.parametrize(
    "tag",
    ["(m/w/d)", "(f/m/d)", "(H/F)", "(m/f/d)", "m/w/d", "(all genders)"],
)
def test_gender_tags_are_removed(tag):
    assert title_key(f"Data Engineer {tag}") == "data engineer"
    assert title_key(f"Data Engineer {tag}") == title_key("Data Engineer")


def test_case_and_whitespace_do_not_matter():
    assert title_key("  Data   ENGINEER\t") == title_key("data engineer")


def test_nbsp_entity_and_character_are_spaces():
    assert title_key("Data&nbsp;Engineer") == "data engineer"
    assert title_key("Data Engineer (m/w/d)") == "data engineer"
    assert title_key("Data&nbsp;Engineer") == title_key("Data Engineer")


def test_trailing_separators_are_stripped():
    assert title_key("Data Engineer -") == "data engineer"
    assert title_key("Data Engineer |") == "data engineer"
    assert title_key("Data Engineer (m/w/d) - ") == "data engineer"


def test_slash_in_a_title_is_kept():
    assert title_key("Senior / Lead Engineer") == "senior / lead engineer"


def test_other_brackets_are_kept():
    assert title_key("Co-Founder (100 % remote) (m/f/d)") == "co-founder (100 % remote)"
    assert title_key("Engineer (REQ-123)") != title_key("Engineer (REQ-124)")
    assert title_key("Engineer (100 % remote)") != title_key("Engineer")


def test_different_titles_differ():
    assert title_key("Data Engineer") != title_key("Data Scientist")


def row(id, employer, title):
    return {"id": id, "employer_id": employer, "title": title}


def test_group_rows_keeps_order_and_collects_duplicates():
    rows = [
        row(1, 10, "Engineer (m/w/d)"),
        row(2, 10, "Analyst"),
        row(3, 10, "engineer (f/m/d)"),
        row(4, 10, "Engineer"),
        row(5, 10, "Analyst "),
    ]
    groups = group_rows(rows)
    assert [(r["id"], [d["id"] for d in dups]) for r, dups in groups] == [
        (1, [3, 4]),
        (2, [5]),
    ]


def test_group_rows_does_not_merge_employers():
    groups = group_rows([row(1, 10, "Engineer"), row(2, 11, "Engineer")])
    assert [(r["id"], dups) for r, dups in groups] == [(1, []), (2, [])]


def test_group_rows_empty():
    assert group_rows([]) == []


@pytest.mark.parametrize(
    "variant",
    [
        "Engineer ( m/w/d )",
        "Engineer (m/w/d)*",
        "Engineer (m/w/div)",
        "Engineer (m,w,d)",
        "Engineer (gn)",
        "Engineer m/w/d",
        "Engineer H/F",
    ],
)
def test_title_key_more_gender_tag_forms(variant):
    assert title_key(variant) == "engineer"


def test_title_key_unbracketed_letters_inside_a_title_are_kept():
    assert title_key("Head of F/X Trading") != title_key("Head of Trading")


def test_title_key_unbracketed_tag_before_a_separator():
    assert title_key("Consultant BI - H/F - CDI") == title_key("Consultant BI - F/H - CDI")


def test_title_key_strips_only_trailing_separators():
    assert title_key("- Engineer -") == "- engineer"
