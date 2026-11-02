import re
from pathlib import Path

import pytest

from jobfacts.extract.spec import FIELDS, derive_seniority, spec_hash

FIELDS_MD = (Path(__file__).resolve().parents[1] / "docs" / "fields.md").read_text()


def _section(heading: str) -> str:
    start = FIELDS_MD.index(heading)
    nxt = re.search(r"^#{2,3} ", FIELDS_MD[start + len(heading) :], re.MULTILINE)
    return FIELDS_MD[start : start + len(heading) + (nxt.start() if nxt else 10**6)]


def _table_values(text: str) -> list[str]:
    return re.findall(r"^\| `([a-z0-9_]+)` \|", text, re.MULTILINE)


@pytest.mark.parametrize(
    ("field", "heading"),
    [
        ("work_mode", "### `work_mode`"),
        ("remote_from_switzerland", "### `remote_from_switzerland`"),
        ("german", "## 2. Language requirement"),
        ("french", "## 2. Language requirement"),
        ("title_level", "## 3. `seniority`"),
    ],
)
def test_table_values_match_fields_md(field, heading):
    text = _section(heading)
    if field == "title_level":  # listed in a bullet, not a table
        line = re.search(r"- `title_level`: (.*?)\(from", FIELDS_MD, re.DOTALL).group(1)
        documented = set(re.findall(r"`([a-z_]+)`", line))
    else:
        documented = set(_table_values(text))
    assert set(FIELDS[field].values) == documented


def test_min_years_matches_fields_md():
    line = re.search(r"- `min_years`: (.*)", FIELDS_MD).group(1)
    assert "`0` to `15`" in line and "`15_plus`" in line and "`not_stated`" in line
    assert set(FIELDS["min_years"].values) == {str(n) for n in range(16)} | {
        "15_plus",
        "not_stated",
    }


def test_no_quote_values_exist_in_each_field():
    for f in FIELDS.values():
        assert f.no_quote <= set(f.values)
        assert next(iter(f.values)) in f.no_quote or f.name == "remote_from_switzerland"


def test_spec_hash_changes_with_template():
    assert spec_hash("a") != spec_hash("b")
    assert spec_hash("a") == spec_hash("a")


@pytest.mark.parametrize(
    ("years", "title", "expected"),
    [
        ("0", "not_stated", "junior"),
        ("1", "not_stated", "junior"),
        ("2", "not_stated", "mid"),
        ("4", "not_stated", "mid"),
        ("5", "not_stated", "senior"),
        ("12", "not_stated", "senior"),
        ("15_plus", "not_stated", "senior"),
        ("not_stated", "junior", "junior"),
        ("not_stated", "mid", "mid"),
        ("not_stated", "senior", "senior"),
        ("not_stated", "not_stated", "not_stated"),
        ("not_stated", "internship", "internship"),
        ("not_stated", "staff_principal", "staff_principal"),
        ("3", "senior", "mid"),  # years win over the title
        ("8", "junior", "senior"),
        ("2", "staff_principal", "staff_principal"),  # staff title always wins
        ("15_plus", "staff_principal", "staff_principal"),
        ("0", "internship", "internship"),
        ("3", "mid", "mid"),
    ],
)
def test_derive_seniority(years, title, expected):
    assert derive_seniority(years, title) == expected
