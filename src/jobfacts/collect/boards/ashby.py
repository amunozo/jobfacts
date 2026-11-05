"""Ashby public job board API."""

import re

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.locations import mentions_switzerland
from jobfacts.text import html_to_text

# Country names in the languages of the postings, for the text fallback (lower case).
COUNTRY_NAMES = {
    "CH": ("switzerland", "schweiz", "suisse", "svizzera", "svizra"),
    "DE": ("germany", "deutschland", "allemagne"),
    "AT": ("austria", "österreich", "autriche"),
    "FR": ("france", "frankreich"),
    "IT": ("italy", "italien", "italia", "italie"),
    "GB": ("united kingdom", "uk", "england", "great britain"),
    "US": ("united states", "usa", "u.s."),
}


def _in_country(location: dict, text: str, country: str) -> bool:
    """One location: its structured country if given, else its text."""
    structured = ((location.get("address") or {}).get("postalAddress") or {}).get("addressCountry")
    names = COUNTRY_NAMES.get(country, ())
    if structured:
        return structured.strip().lower() in (country.lower(), *names)
    if country == "CH":
        return mentions_switzerland(text, location_field=True)
    lowered = text.lower()
    return any(re.search(rf"(?<!\w){re.escape(n)}(?!\w)", lowered) for n in names)


def in_country(item: dict, country: str) -> bool:
    """True if the primary or any secondary location of a job is in `country`."""
    country = country.upper()
    if _in_country(item, item.get("location") or "", country):
        return True
    return any(
        _in_country(s, s.get("location") or "", country)
        for s in item.get("secondaryLocations") or []
    )


def parse(data: dict, country: str | None = None) -> list[Posting]:
    postings = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
            continue
        if country and not in_country(item, country):
            continue
        secondary = [e.get("location", "") for e in item.get("secondaryLocations") or []]
        postings.append(
            Posting(
                source_job_id=str(item["id"]),
                url=item.get("jobUrl", ""),
                title=item.get("title", ""),
                location_raw=" / ".join(filter(None, [item.get("location", ""), *secondary])),
                description_text=html_to_text(item.get("descriptionHtml"))
                or item.get("descriptionPlain", ""),
                posted_at=parse_time(item.get("publishedAt")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board_id}?includeCompensation=false"
    return parse(get_json(client, url), extra.get("country"))
