"""Duplicate grouping for search results.

Employers often post one job once per city or country with the same title. Two postings are
the same job when they have the same employer and the same normalised title.
"""

import re
from collections.abc import Iterable, Mapping
from typing import Any

from jobfacts.extract.rules import GENDER_TAG

_SPACES = re.compile(r"\s+")
_TRAILING_SEPARATORS = " -–—|/,:;"


def title_key(title: str) -> str:
    """The title without gender tags, case and spacing differences or trailing separators.

    Other bracketed parts such as "(100 % remote)" or requisition ids are kept on purpose.
    """
    t = title.replace("&nbsp;", " ").replace("\xa0", " ")
    t = GENDER_TAG.sub(" ", t)
    t = _SPACES.sub(" ", t).casefold().strip()
    return t.rstrip(_TRAILING_SEPARATORS)


def group_rows(rows: Iterable[Mapping[str, Any]]) -> list[tuple[Mapping[str, Any], list]]:
    """Group rows (already in result order) by employer_id and `title_key(title)`.

    Returns [(representative, duplicates)] in order of first appearance; the representative
    is the first row of its group, the duplicates are the later rows of the group in order.
    """
    groups: dict[tuple, tuple[Mapping[str, Any], list]] = {}
    for row in rows:
        key = (row["employer_id"], title_key(row["title"]))
        if key in groups:
            groups[key][1].append(row)
        else:
            groups[key] = (row, [])
    return list(groups.values())
