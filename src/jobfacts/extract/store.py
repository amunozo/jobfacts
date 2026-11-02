"""Database reads and writes for the relevance filter and extraction runs."""

import psycopg
from psycopg.types.json import Jsonb

from jobfacts.extract.base import Result
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import FIELDS


def load_postings(
    conn: psycopg.Connection, ids: list[int] | None = None, only_unscored_by: str | None = None
) -> list[PostingText]:
    """Open postings (or the given ids), optionally only those without a relevance row."""
    sql = "SELECT p.id, p.title, p.location_raw, p.description_text FROM postings p WHERE "
    params: list = []
    if ids:
        sql += "p.id = ANY(%s)"
        params.append(ids)
    else:
        sql += "p.closed_at IS NULL"
    if only_unscored_by:
        sql += (
            " AND NOT EXISTS (SELECT 1 FROM relevance r WHERE r.posting_id = p.id AND r.model = %s)"
        )
        params.append(only_unscored_by)
    rows = conn.execute(sql + " ORDER BY p.id", params).fetchall()
    return [PostingText.build(*row) for row in rows]


def save_relevance(
    conn: psycopg.Connection, posting_id: int, model: str, probability: float, threshold: float
) -> None:
    conn.execute(
        "INSERT INTO relevance (posting_id, model, probability, relevant) VALUES (%s,%s,%s,%s) "
        "ON CONFLICT (posting_id, model) DO UPDATE SET probability = EXCLUDED.probability, "
        "relevant = EXCLUDED.relevant, created_at = now()",
        (posting_id, model, probability, probability >= threshold),
    )


def relevant_ids(conn: psycopg.Connection, model: str, origin: str = "board") -> list[int]:
    rows = conn.execute(
        "SELECT r.posting_id FROM relevance r JOIN postings p ON p.id = r.posting_id "
        "WHERE r.model = %s AND r.relevant AND p.origin = %s ORDER BY r.posting_id",
        (model, origin),
    )
    return [r[0] for r in rows]


def create_run(
    conn: psycopg.Connection, extractor: str, model: str, spec_version: str, notes: str = ""
) -> int:
    row = conn.execute(
        "INSERT INTO extraction_runs (extractor, model, spec_version, notes) "
        "VALUES (%s,%s,%s,%s) RETURNING id",
        (extractor, model, spec_version, notes),
    ).fetchone()
    conn.commit()
    return row[0]


def finish_run(conn: psycopg.Connection, run_id: int) -> None:
    conn.execute("UPDATE extraction_runs SET finished_at = now() WHERE id = %s", (run_id,))
    conn.commit()


def save_extraction(conn: psycopg.Connection, run_id: int, posting_id: int, r: Result) -> None:
    columns = ["run_id", "posting_id"]
    values: list = [run_id, posting_id]
    if not r.error:
        for name in FIELDS:
            columns += [name, f"{name}_sentences"]
            values += [r.values[name], r.sentences.get(name, [])]
        columns += ["office_locations", "seniority"]
        values += [r.office_locations, r.seniority]
    details = dict(r.details)
    if r.sentences.get("office_locations"):
        details["office_locations_sentences"] = r.sentences["office_locations"]
    columns += ["details", "tokens_in", "tokens_out", "cost_usd", "latency_ms", "error"]
    values += [Jsonb(details), r.tokens_in, r.tokens_out, r.cost_usd, r.latency_ms, r.error]
    names = ", ".join(columns)
    marks = ", ".join(["%s"] * len(columns))
    conn.execute(
        f"INSERT INTO extractions ({names}) VALUES ({marks}) "
        "ON CONFLICT (run_id, posting_id) DO NOTHING",
        values,
    )
    conn.commit()
