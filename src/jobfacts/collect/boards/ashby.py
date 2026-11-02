"""Ashby public job board API."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse(data: dict) -> list[Posting]:
    postings = []
    for item in data.get("jobs", []):
        if item.get("isListed") is False:
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
    return parse(get_json(client, url))
