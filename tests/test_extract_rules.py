import pytest

from jobfacts.extract.rules import apply_extraction_rules, title_excluded, title_named_level
from jobfacts.extract.rules import checked_title_level as chk


@pytest.mark.parametrize(
    "title,level,expected",
    [
        ("Senior ML Engineer", "senior", "senior"),
        ("ML Engineer", "senior", "not_stated"),
        ("(Senior) Consultant AI", "senior", "senior"),
        ("(Junior-) Projektleiter KI", "junior", "junior"),
        ("Member of Technical Staff", "staff_principal", "not_stated"),
        ("Research Staff Scientist", "staff_principal", "not_stated"),
        ("Staff Engineer", "staff_principal", "staff_principal"),
        ("Partner, AI Strategy", "staff_principal", "staff_principal"),
        ("Manager, Data Science", "staff_principal", "staff_principal"),
        ("Engineering Manager", "staff_principal", "staff_principal"),
        ("Product Manager", "staff_principal", "not_stated"),
        ("Group Product Manager", "staff_principal", "not_stated"),
        ("Projektleiter Data", "staff_principal", "not_stated"),
        ("Teamleiter Data", "staff_principal", "staff_principal"),
        ("Postdoc Researcher", "senior", "not_stated"),
        ("Praktikum Data Science", "internship", "internship"),
        ("Data Scientist", "not_stated", "not_stated"),
    ],
)
def test_title_level_needs_a_keyword(title, level, expected):
    assert chk(title, level) == expected


@pytest.mark.parametrize(
    "title",
    [
        "Senior Sales Executive",
        "Founding Sales Engineer",
        "Technical Sourcer",
        "Talent Acquisition Manager, Product",
        "Product Designer, AI-Native Products",
        "Data Center Technician",
        "Customer Success Engineer (CSE)",
        "Senior Insights Consultant (Brand Media)",
        "Not seeing the right opportunity right now?",
        "Strategic Data & Licensing Partnerships",
        "Billing Specialist",
        "Sales Engineer",
        "Solutions Sales Lead",
        "Talent Acquisition Partner",
        "Technician",
        "Lab Technician",
        "Graphic Designer",
        "Recruiter",
    ],
)
def test_excluded_titles(title):
    assert title_excluded(title)


@pytest.mark.parametrize(
    "title",
    [
        "Senior SWE (Agentic Search) - Billing",
        "Billing & Analytics Software Engineer",
        "Senior Data Engineer - Analytics Platform (Contract through our external payroll partner)",
        "Software Engineer (Early Talent)",
        "Design Engineer",
        "Partnerships Solutions Engineer",
        "Machine Learning Engineer",
        "Forward Deployed Engineer",
        "ML Engineer \u2013 Customer Success",
        "Recruitment Data Scientist",
        "Research Technician ML",
        "Data Scientist, Talent Acquisition analytics",
    ],
)
def test_kept_titles(title):
    assert not title_excluded(title)


@pytest.mark.parametrize(
    "title,level,expected",
    [
        ("Chef d\u2019\u00e9quipe Data", "staff_principal", "staff_principal"),
        ("Responsable d\u2019\u00e9quipe IA", "staff_principal", "staff_principal"),
        ("Cheffe d'\u00e9quipe Data", "staff_principal", "staff_principal"),
        ("Leitung Data & Analytics", "staff_principal", "staff_principal"),
        ("Leitender Data Scientist", "staff_principal", "staff_principal"),
        ("Bereichsleitung KI", "staff_principal", "staff_principal"),
        ("Directrice IA", "staff_principal", "staff_principal"),
        ("Directeur Data", "staff_principal", "staff_principal"),
        ("Direktorin Data", "staff_principal", "staff_principal"),
        ("Director of AI", "staff_principal", "staff_principal"),
        ("Leiterin Data", "staff_principal", "staff_principal"),
        ("Teamleiterin Data", "staff_principal", "staff_principal"),
        ("Projektleiterin KI", "staff_principal", "not_stated"),
        ("Projektleitung KI", "staff_principal", "not_stated"),
        ("Cheffe de projet IA", "staff_principal", "not_stated"),
        ("Lernende/r Informatiker", "internship", "internship"),
        ("Bachelorarbeit Machine Learning", "internship", "internship"),
        ("Diplomarbeit KI", "internship", "internship"),
        ("Masterarbeit NLP", "internship", "internship"),
        ("Praktikantin Data", "internship", "internship"),
        ("Stagiaire data", "internship", "internship"),
        ("Alternance data scientist", "internship", "internship"),
        ("Apprentie data", "internship", "internship"),
        ("Werkstudentin KI", "internship", "internship"),
        ("Absolvent Data Science", "junior", "junior"),
        ("Hochschulabsolventin KI", "junior", "junior"),
        ("Jeune dipl\u00f4m\u00e9(e) data", "junior", "junior"),
        ("Graduate Data Scientist", "junior", "junior"),
    ],
)
def test_title_keywords_forms_and_apostrophes(title, level, expected):
    assert chk(title, level) == expected


@pytest.mark.parametrize(
    "title,expected",
    [
        ("Senior Data Science Manager", "senior"),
        ("Data Scientist", "not_stated"),
        ("Confirm\u00e9 \u00e0 S\u00e9nior Data Engineer", "mid"),
        ("Senior Staff Engineer", "staff_principal"),
        ("Senior ML Intern", "internship"),
        ("Senior Projektleiter KI", "senior"),
    ],
)
def test_title_named_level(title, expected):
    assert title_named_level(title) == expected


def test_vetoed_level_falls_back_to_the_level_the_title_names():
    from jobfacts.extract.base import Result
    from jobfacts.extract.posting import PostingText

    p = PostingText.build(1, "Senior Data Science Manager", "Zurich", "Text here.")
    r = Result(values={"title_level": "staff_principal"}, sentences={"title_level": [1]})
    apply_extraction_rules(p, r)
    assert r.values["title_level"] == "senior" and r.sentences["title_level"] == []
    p = PostingText.build(1, "Data Scientist", "Zurich", "Text here.")
    r = Result(values={"title_level": "senior"}, sentences={})
    apply_extraction_rules(p, r)
    assert r.values["title_level"] == "not_stated"
