"""Greenhouse job board API (boards-api.greenhouse.io)."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse(data: dict) -> list[Posting]:
    return [
        Posting(
            source_job_id=str(item["id"]),
            url=item.get("absolute_url", ""),
            title=item.get("title", ""),
            location_raw=(item.get("location") or {}).get("name", ""),
            description_text=html_to_text(item.get("content")),
            posted_at=parse_time(item.get("first_published") or item.get("updated_at")),
        )
        for item in data.get("jobs", [])
    ]


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_id}/jobs?content=true"
    return parse(get_json(client, url))
