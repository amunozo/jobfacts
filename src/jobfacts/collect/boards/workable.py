"""Workable widget API (apply.workable.com), with descriptions via details=true."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse(data: dict, board_id: str) -> list[Posting]:
    postings = []
    for item in data.get("jobs", []):
        shortcode = item.get("shortcode", "")
        locations = [
            ", ".join(filter(None, (e.get("city"), e.get("country"))))
            for e in item.get("locations") or []
        ] or [", ".join(filter(None, (item.get("city"), item.get("country"))))]
        postings.append(
            Posting(
                source_job_id=str(shortcode),
                url=item.get("url") or f"https://apply.workable.com/{board_id}/j/{shortcode}/",
                title=item.get("title", ""),
                location_raw=" / ".join(filter(None, locations)),
                description_text=html_to_text(item.get("description")),
                posted_at=parse_time(item.get("published_on") or item.get("created_at")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    url = f"https://apply.workable.com/api/v1/widget/accounts/{board_id}?details=true"
    return parse(get_json(client, url), board_id)
