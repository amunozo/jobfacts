import pytest

from jobfacts.extract.rules import checked_title_level as chk
from jobfacts.extract.rules import title_excluded


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
    ],
)
def test_kept_titles(title):
    assert not title_excluded(title)
