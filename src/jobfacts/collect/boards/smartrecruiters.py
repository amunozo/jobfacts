"""SmartRecruiters public postings API: a paged list plus one detail request per job."""

from jobfacts.collect.http import get_json
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse_list_item(item: dict, board_id: str) -> Posting:
    location = item.get("location") or {}
    place = ", ".join(filter(None, (location.get("city"), (location.get("country") or "").upper())))
    if location.get("remote"):
        place = f"{place} (remote)" if place else "remote"
    return Posting(
        source_job_id=str(item["id"]),
        url=f"https://jobs.smartrecruiters.com/{board_id}/{item['id']}",
        title=item.get("name", ""),
        location_raw=place,
        posted_at=parse_time(item.get("releasedDate")),
    )


def parse_detail_text(detail: dict) -> str:
    sections = (detail.get("jobAd") or {}).get("sections") or {}
    parts = []
    for section in sections.values():
        text = html_to_text(section.get("text"))
        if text:
            title = section.get("title")
            parts.append(f"{title}\n{text}" if title else text)
    return "\n\n".join(parts)


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    items, offset = [], 0
    base = f"https://api.smartrecruiters.com/v1/companies/{board_id}/postings"
    # Optional per-employer country: the API filters, on every page.
    country = f"&country={extra['country'].lower()}" if extra.get("country") else ""
    while True:
        data = get_json(client, f"{base}?limit=100&offset={offset}{country}")
        items.extend(data.get("content", []))
        offset += 100
        if offset >= data.get("totalFound", 0) or not data.get("content"):
            break
    postings = []
    for item in items:
        posting = parse_list_item(item, board_id)
        # A failed detail request raises: the board then counts as failed, nothing closes.
        detail = get_json(client, f"{base}/{item['id']}")
        text = parse_detail_text(detail)
        postings.append(
            Posting(**{**posting.__dict__, "description_text": text}) if text else posting
        )
    return postings
