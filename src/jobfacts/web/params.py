"""Query parameters of /api/search and /: the command-line filters as URL parameters.

Lists accept repeated parameters (`canton=ZH&canton=VD`, what an HTML form sends) and commas
(`canton=ZH,VD`). Empty values mean "not set". Unknown parameters are an error, so that a
typo cannot silently drop a filter.
"""

from dataclasses import dataclass

from jobfacts import search

MAX_LIMIT = 100
MAX_QUERY_CHARS = 500
LISTS = {"canton", "work_mode", "seniority"}
FLAGS = {"remote", "remote_unclear", "show_unclear_language", "no_fixed_term", "evidence"}
SCALARS = {"query", "german_max", "french_max", "new_days", "limit", "mode"}
TRUE = {"1", "true", "on", "yes"}
FALSE = {"", "0", "false", "off", "no"}


@dataclass
class Parsed:
    filters: search.Filters
    mode: str | None  # None = the default (hybrid)
    evidence: bool


def _int(name: str, value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        raise search.SearchError(f"{name} must be a whole number, not {value!r}") from None


def parse(items: list[tuple[str, str]]) -> Parsed:
    """Turn `request.query_params.multi_items()` into filters. Raises `search.SearchError`."""
    unknown = sorted({name for name, _ in items} - LISTS - FLAGS - SCALARS)
    if unknown:
        allowed = sorted(LISTS | FLAGS | SCALARS)
        raise search.SearchError(f"unknown parameter {unknown[0]!r}; allowed: {allowed}")
    lists: dict[str, list[str]] = {name: [] for name in LISTS}
    flags = dict.fromkeys(FLAGS, False)
    scalars: dict[str, str | None] = dict.fromkeys(SCALARS)
    for name, value in items:
        if name in LISTS:
            lists[name] += [v.strip() for v in value.split(",") if v.strip()]
        elif name in FLAGS:
            v = value.strip().lower()
            if v not in TRUE | FALSE:
                raise search.SearchError(f"{name} must be true or false, not {value!r}")
            flags[name] = v in TRUE
        else:
            scalars[name] = value.strip() if name != "query" else value
    query = scalars["query"] or ""
    if len(query) > MAX_QUERY_CHARS:
        raise search.SearchError(f"query is longer than {MAX_QUERY_CHARS} characters")
    limit = _int("limit", scalars["limit"] or None)
    if limit is not None and limit > MAX_LIMIT:
        raise search.SearchError(f"limit must be at most {MAX_LIMIT}")
    new_days = _int("new_days", scalars["new_days"] or None)
    if new_days is not None and not 0 <= new_days <= search.MAX_NEW_DAYS:
        raise search.SearchError(f"new_days must be between 0 and {search.MAX_NEW_DAYS}")
    filters = search.Filters(
        query=query,
        cantons=search.canton_codes(lists["canton"]),
        remote=flags["remote"],
        remote_unclear=flags["remote_unclear"],
        work_modes=lists["work_mode"],
        german_max=scalars["german_max"] or None,
        french_max=scalars["french_max"] or None,
        show_unclear_language=flags["show_unclear_language"],
        no_fixed_term=flags["no_fixed_term"],
        seniority=lists["seniority"],
        new_days=new_days,
        limit=limit if limit is not None else 20,
    )
    return Parsed(filters, scalars["mode"] or None, flags["evidence"])
