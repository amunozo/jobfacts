"""Import the owner's application job descriptions as private test material.

Source folders look like `<Company>/<Role>/job_description.md`. Each file becomes one posting
with origin 'application', attached to an employer row (ats = 'application', board_id = the
company folder). No collector reads such employers, so these postings are never closed.
"""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

import psycopg

_LINK_LINE = re.compile(r"https?://|not provided|^\s*url\b|^\s*link\b", re.IGNORECASE)


@dataclass
class Imported:
    new: int = 0
    updated: int = 0
    unchanged: int = 0


def split_link(text: str) -> tuple[str, str]:
    """Return (url, body). The first line is dropped when it is a link or a link placeholder."""
    first, _, rest = text.lstrip("﻿").partition("\n")
    if not _LINK_LINE.search(first):
        return "", text.strip()
    m = re.search(r"https?://[^\s<>]+", first)
    url = m.group(0) if m and "<" not in first else ""
    return url, rest.strip()


def import_applications(conn: psycopg.Connection, root: Path) -> Imported:
    counts = Imported()
    for path in sorted(root.glob("**/job_description.md")):
        folder = path.parent.relative_to(root)
        company, title = folder.parts[0], folder.parts[-1]
        url, body = split_link(path.read_text(encoding="utf-8"))
        digest = hashlib.sha256(body.encode()).hexdigest()
        employer_id = conn.execute(
            "INSERT INTO employers (name, ats, board_id, active) VALUES (%s, 'application', %s, "
            "false) ON CONFLICT (ats, board_id) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id",
            (company, company),
        ).fetchone()[0]
        job_id = folder.as_posix()
        row = conn.execute(
            "SELECT content_hash FROM postings WHERE employer_id = %s AND source_job_id = %s",
            (employer_id, job_id),
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO postings (employer_id, source_job_id, url, title, location_raw, "
                "description_text, content_hash, origin) "
                "VALUES (%s,%s,%s,%s,'',%s,%s,'application')",
                (employer_id, job_id, url, title, body, digest),
            )
            counts.new += 1
        elif row[0] != digest:
            conn.execute(
                "UPDATE postings SET url=%s, title=%s, description_text=%s, content_hash=%s "
                "WHERE employer_id=%s AND source_job_id=%s",
                (url, title, body, digest, employer_id, job_id),
            )
            counts.updated += 1
        else:
            counts.unchanged += 1
    conn.commit()
    return counts
