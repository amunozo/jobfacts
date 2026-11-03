"""Workable widget API (apply.workable.com), with descriptions via details=true."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def _locations(item: dict) -> list[str]:
    return [
        ", ".join(filter(None, (e.get("city"), e.get("country"))))
        for e in item.get("locations") or []
    ] or [", ".join(filter(None, (item.get("city"), item.get("country"))))]


def parse(data: dict, board_id: str) -> list[Posting]:
    """One Posting per shortcode.

    The widget API lists a job once per location (same shortcode, one location each), so the
    entries are merged and every location is kept, in the order the API gives them.
    """
    items: dict[str, dict] = {}
    locations: dict[str, list[str]] = {}
    for item in data.get("jobs", []):
        shortcode = str(item.get("shortcode", ""))
        items.setdefault(shortcode, item)
        locations.setdefault(shortcode, []).extend(_locations(item))
    postings = []
    for shortcode, item in items.items():
        postings.append(
            Posting(
                source_job_id=shortcode,
                url=item.get("url") or f"https://apply.workable.com/{board_id}/j/{shortcode}/",
                title=item.get("title", ""),
                location_raw=" / ".join(dict.fromkeys(filter(None, locations[shortcode]))),
                description_text=html_to_text(item.get("description")),
                posted_at=parse_time(item.get("published_on") or item.get("created_at")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{board_id}?details=true"
    return parse(get_json(client, url), board_id)
