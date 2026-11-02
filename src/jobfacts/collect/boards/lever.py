"""Lever postings API (api.lever.co, or api.eu.lever.co for EU accounts)."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse(data: list) -> list[Posting]:
    postings = []
    for item in data:
        categories = item.get("categories") or {}
        locations = categories.get("allLocations") or [categories.get("location", "")]
        parts = [item.get("descriptionPlain") or html_to_text(item.get("description"))]
        for block in item.get("lists") or []:
            parts.append(f"{block.get('text', '')}\n{html_to_text(block.get('content'))}")
        parts.append(item.get("additionalPlain") or html_to_text(item.get("additional")))
        postings.append(
            Posting(
                source_job_id=str(item["id"]),
                url=item.get("hostedUrl", ""),
                title=item.get("text", ""),
                location_raw=" / ".join(filter(None, locations)),
                description_text="\n\n".join(p.strip() for p in parts if p and p.strip()),
                posted_at=parse_time(item.get("createdAt")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    host = "api.eu.lever.co" if extra.get("eu") else "api.lever.co"
    return parse(get_json(client, f"https://{host}/v0/postings/{board_id}?mode=json"))
