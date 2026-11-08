"""Writing a board read to PostgreSQL, including the closing rule."""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

import psycopg

from jobfacts.collect.posting import Posting
from jobfacts.text import unescape_field

EMPLOYERS_FILE = Path(__file__).resolve().parents[3] / "config" / "employers.toml"


# Job systems whose collector can keep only one country's postings (`country = "CH"`).
COUNTRY_FILTER_ATS = ("smartrecruiters", "ashby")


@dataclass
class Counts:
    new: int = 0
    updated: int = 0
    closed: int = 0


def load_employers(conn: psycopg.Connection, path: Path = EMPLOYERS_FILE) -> None:
    """Upsert employers from the TOML file. Employers no longer listed become inactive."""
    entries = tomllib.loads(path.read_text())["employer"]
    for entry in entries:
        if "country" in entry:
            country = entry["country"]
            if not (isinstance(country, str) and len(country) == 2 and country.isalpha()):
                raise ValueError(
                    f"{entry['name']}: country must be a two-letter ISO code, got {country!r}"
                )
            if entry["ats"] not in COUNTRY_FILTER_ATS:
                raise ValueError(
                    f"{entry['name']}: `country` is not supported for ats = {entry['ats']!r} "
                    f"(only {', '.join(COUNTRY_FILTER_ATS)})"
                )
    keys = []
    for entry in entries:
        extra = {k: v for k, v in entry.items() if k not in ("name", "ats", "board_id")}
        if "country" in extra:
            extra["country"] = extra["country"].upper()
        conn.execute(
            "INSERT INTO employers (name, ats, board_id, extra, active) "
            "VALUES (%s, %s, %s, %s, true) "
            "ON CONFLICT (ats, board_id) DO UPDATE "
            "SET name = EXCLUDED.name, extra = EXCLUDED.extra, active = true",
            (entry["name"], entry["ats"], entry["board_id"], json.dumps(extra)),
        )
        keys.append((entry["ats"], entry["board_id"]))
    conn.execute(
        "UPDATE employers SET active = false WHERE (ats || ' ' || board_id) <> ALL(%s)",
        ([f"{a} {b}" for a, b in keys],),
    )
    conn.commit()


def start_run(conn: psycopg.Connection) -> int:
    run_id = conn.execute("INSERT INTO collection_runs DEFAULT VALUES RETURNING id").fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, ok: int, failed: int, c: Counts) -> None:
    conn.execute(
        "UPDATE collection_runs SET finished_at = now(), boards_ok = %s, boards_failed = %s, "
        "postings_new = %s, postings_updated = %s, postings_closed = %s WHERE id = %s",
        (ok, failed, c.new, c.updated, c.closed, run_id),
    )
    conn.commit()


def record_failure(conn: psycopg.Connection, run_id: int, employer_id: int, error: str) -> None:
    """A failed read is only logged. It never touches postings, so nothing is closed."""
    conn.execute(
        "INSERT INTO board_reads (run_id, employer_id, ok, error, postings_seen) "
        "VALUES (%s, %s, false, %s, 0)",
        (run_id, employer_id, error[:1000]),
    )
    conn.commit()


def open_count(conn: psycopg.Connection, employer_id: int) -> int:
    return conn.execute(
        "SELECT count(*) FROM postings WHERE employer_id = %s AND closed_at IS NULL",
        (employer_id,),
    ).fetchone()[0]


def known_urls(conn: psycopg.Connection, employer_id: int) -> dict[str, str]:
    """Open postings of an employer with their URL, least recently seen first."""
    rows = conn.execute(
        "SELECT source_job_id, url FROM postings WHERE employer_id = %s AND closed_at IS NULL "
        "ORDER BY last_seen_at, id",
        (employer_id,),
    )
    return {job_id: url for job_id, url in rows}


def known_versions(conn: psycopg.Connection, employer_id: int) -> dict[str, str | None]:
    """Open postings of an employer with their stored source version (for incremental reads)."""
    rows = conn.execute(
        "SELECT source_job_id, source_version FROM postings "
        "WHERE employer_id = %s AND closed_at IS NULL",
        (employer_id,),
    )
    return {job_id: version for job_id, version in rows}


def rekey_source_ids(conn: psycopg.Connection, employer_id: int, key) -> int:
    """Rewrite stored `source_job_id`s to `key(id)`, keeping each posting row (and its id).

    Used when an employer starts merging several Workday sites: `r-17167-2` becomes `r-17167`.
    Idempotent. A row whose new id is already taken by another row is left alone.
    """
    rows = conn.execute(
        "SELECT id, source_job_id FROM postings WHERE employer_id = %s "
        "ORDER BY (closed_at IS NOT NULL), last_seen_at DESC, id",  # open first, then freshest
        (employer_id,),
    ).fetchall()
    taken = {job_id for _, job_id in rows}
    changed = 0
    with conn.transaction():
        for row_id, job_id in rows:
            new = key(job_id)
            if new != job_id and new not in taken:
                conn.execute("UPDATE postings SET source_job_id = %s WHERE id = %s", (new, row_id))
                taken.add(new)
                changed += 1
    conn.commit()  # the block above is only a savepoint if a SELECT opened a transaction
    return changed


def record_success(
    conn: psycopg.Connection,
    run_id: int,
    employer_id: int,
    postings: list[Posting],
    seen_ids: frozenset[str] | set[str] = frozenset(),
    close_missing: bool = True,
    close_ids: frozenset[str] | set[str] = frozenset(),
) -> Counts:
    """Store a successful read in one transaction: insert, update, reopen, close the missing.

    `seen_ids` are postings the board listed but the collector did not re-read; they stay open.
    """
    counts = Counts()
    unique = {p.source_job_id: p for p in postings}
    with conn.transaction():
        existing = {
            row[0]: (row[1], row[2])
            for row in conn.execute(
                "SELECT source_job_id, content_hash, closed_at "
                "FROM postings WHERE employer_id = %s",
                (employer_id,),
            )
        }
        for job_id, p in unique.items():
            if job_id not in existing:
                conn.execute(
                    "INSERT INTO postings (employer_id, source_job_id, url, title, location_raw, "
                    "description_text, posted_at, content_hash, source_version) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        employer_id,
                        job_id,
                        p.url,
                        p.title,
                        p.location_raw,
                        p.description_text,
                        p.posted_at,
                        p.content_hash,
                        p.source_version,
                    ),
                )
                counts.new += 1
                continue
            old_hash, closed_at = existing[job_id]
            if old_hash != p.content_hash or closed_at is not None:
                conn.execute(
                    "UPDATE postings SET url=%s, title=%s, location_raw=%s, description_text=%s, "
                    "posted_at=%s, content_hash=%s, source_version=%s, last_seen_at=now(), "
                    "closed_at=NULL "
                    "WHERE employer_id=%s AND source_job_id=%s",
                    (
                        p.url,
                        p.title,
                        p.location_raw,
                        p.description_text,
                        p.posted_at,
                        p.content_hash,
                        p.source_version,
                        employer_id,
                        job_id,
                    ),
                )
                counts.updated += 1
            else:
                conn.execute(
                    "UPDATE postings SET last_seen_at = now(), source_version = %s "
                    "WHERE employer_id = %s AND source_job_id = %s",
                    (p.source_version, employer_id, job_id),
                )
        keep = list(set(unique) | set(seen_ids))
        if seen_ids:
            conn.execute(
                "UPDATE postings SET last_seen_at = now() WHERE employer_id = %s "
                "AND closed_at IS NULL AND source_job_id = ANY(%s)",
                (employer_id, list(seen_ids)),
            )
        if close_missing:
            closed = conn.execute(
                "UPDATE postings SET closed_at = now() WHERE employer_id = %s "
                "AND closed_at IS NULL AND NOT (source_job_id = ANY(%s))",
                (employer_id, keep),
            )
            counts.closed = closed.rowcount
        if close_ids:  # postings the collector verified one by one to be gone
            closed = conn.execute(
                "UPDATE postings SET closed_at = now() WHERE employer_id = %s "
                "AND closed_at IS NULL AND source_job_id = ANY(%s)",
                (employer_id, list(close_ids)),
            )
            counts.closed += closed.rowcount
        conn.execute(
            "INSERT INTO board_reads (run_id, employer_id, ok, postings_seen) "
            "VALUES (%s, %s, true, %s)",
            (run_id, employer_id, len(keep)),
        )
    conn.commit()  # durable now, not at the end of the run (see rekey_source_ids)
    return counts


def repair_html_entities(conn: psycopg.Connection, ats: str = "workday") -> int:
    """Decode HTML entities in the stored titles and places of one job system ("R&amp;I" ->
    "R&I") and recompute the content hash the way the collector does. Idempotent: a second
    run changes nothing. Returns the number of postings changed.

    Workday pages are read again only when the sitemap entry changes, so postings stored
    before the collector decoded entities would keep the escaped text. The new hash makes
    the next `jobfacts daily` extract these postings again (the relevance score is keyed by
    posting and version, not by hash, so it is not repeated).
    """
    rows = conn.execute(
        "SELECT p.id, p.url, p.title, p.location_raw, p.description_text "
        "FROM postings p JOIN employers e ON e.id = p.employer_id WHERE e.ats = %s",
        (ats,),
    ).fetchall()
    changed = 0
    with conn.transaction():
        for post_id, url, title, place, text in rows:
            new_title, new_place = unescape_field(title), unescape_field(place)
            if (new_title, new_place) == (title, place):
                continue
            new_hash = Posting("", url, new_title, new_place, text).content_hash
            conn.execute(
                "UPDATE postings SET title = %s, location_raw = %s, content_hash = %s "
                "WHERE id = %s",
                (new_title, new_place, new_hash, post_id),
            )
            changed += 1
    return changed
