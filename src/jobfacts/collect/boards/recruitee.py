"""Recruitee public offers API (<company>.recruitee.com)."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse(data: dict) -> list[Posting]:
    postings = []
    for item in data.get("offers", []):
        locations = [e.get("name", "") for e in item.get("locations") or []]
        description = "\n\n".join(
            part
            for part in (
                html_to_text(item.get("description")),
                html_to_text(item.get("requirements")),
            )
            if part
        )
        postings.append(
            Posting(
                source_job_id=str(item["id"]),
                url=item.get("careers_url", ""),
                title=item.get("title", ""),
                location_raw=" / ".join(filter(None, [item.get("location", ""), *locations])),
                description_text=description,
                posted_at=parse_time(item.get("published_at") or item.get("created_at")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    return parse(get_json(client, f"https://{board_id}.recruitee.com/api/offers/"))
