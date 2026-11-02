"""Teamtailor public jobs RSS feed (<career site>/jobs.rss), documented by Teamtailor.

`board_id` is the subdomain of <board_id>.teamtailor.com. Employers on their own
career-site domain set the option `url` to the full feed address.
"""

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from jobfacts.collect.http import get_text
from jobfacts.collect.posting import Posting
from jobfacts.text import html_to_text

TT = "{https://teamtailor.com/locations}"
PAGE_SIZE = 100
MAX_PAGES = 20


def _pub_date(value: str | None):
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None


def parse(xml_text: str) -> list[Posting]:
    root = ET.fromstring(xml_text.strip())
    if root.tag != "rss":
        raise ValueError(f"not an RSS feed (root element {root.tag!r})")
    postings = []
    for item in root.iterfind("channel/item"):
        places = []
        for loc in item.iterfind(f"{TT}locations/{TT}location"):
            city = (loc.findtext(f"{TT}city") or loc.findtext(f"{TT}name") or "").strip()
            country = (loc.findtext(f"{TT}country") or "").strip()
            places.append(", ".join(filter(None, (city, country))))
        remote = (item.findtext("remoteStatus") or "").strip()
        if remote and remote != "none":
            places.append(f"({remote})")
        link = (item.findtext("link") or "").strip()
        postings.append(
            Posting(
                source_job_id=(item.findtext("guid") or link).strip(),
                url=link,
                title=(item.findtext("title") or "").strip(),
                location_raw=" / ".join(dict.fromkeys(filter(None, places))),
                description_text=html_to_text(item.findtext("description")),
                posted_at=_pub_date(item.findtext("pubDate")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    base = extra.get("url") or f"https://{board_id}.teamtailor.com/jobs.rss"
    postings: dict[str, Posting] = {}
    for page in range(MAX_PAGES):
        # The feed shows 100 jobs by default; offset and per_page page through more.
        url = f"{base}?per_page={PAGE_SIZE}&offset={page * PAGE_SIZE}"
        batch = parse(get_text(client, url))
        new = [p for p in batch if p.source_job_id not in postings]
        postings.update({p.source_job_id: p for p in new})
        if len(batch) < PAGE_SIZE or not new:
            break
    return list(postings.values())
