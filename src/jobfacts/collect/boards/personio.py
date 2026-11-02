"""Personio public XML feed (<company>.jobs.personio.de/xml), documented by Personio."""

import xml.etree.ElementTree as ET

from jobfacts.collect.http import get_text
from jobfacts.collect.posting import Posting, parse_time
from jobfacts.text import html_to_text


def parse(xml_text: str, board_id: str) -> list[Posting]:
    root = ET.fromstring(xml_text.strip())
    if root.tag != "workzag-jobs":
        raise ValueError(f"not a Personio feed (root element {root.tag!r})")
    postings = []
    for position in root.findall("position"):
        offices = [position.findtext("office") or ""]
        offices += [e.text or "" for e in position.findall("additionalOffices/office")]
        blocks = []
        for block in position.findall("jobDescriptions/jobDescription"):
            heading = (block.findtext("name") or "").strip()
            body = html_to_text(block.findtext("value"))
            if body:
                blocks.append(f"{heading}\n{body}" if heading else body)
        job_id = position.findtext("id") or ""
        postings.append(
            Posting(
                source_job_id=job_id,
                url=f"https://{board_id}.jobs.personio.de/job/{job_id}",
                title=(position.findtext("name") or "").strip(),
                location_raw=" / ".join(dict.fromkeys(filter(None, (o.strip() for o in offices)))),
                description_text="\n\n".join(blocks),
                posted_at=parse_time(position.findtext("createdAt")),
            )
        )
    return postings


def fetch(client, board_id: str, extra: dict) -> list[Posting]:
    return parse(get_text(client, f"https://{board_id}.jobs.personio.de/xml"), board_id)
