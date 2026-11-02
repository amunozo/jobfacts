"""Run the relevance filter and the extractors over postings (modest concurrency)."""

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import psycopg

from jobfacts.extract import jev, store
from jobfacts.extract.base import Extractor, Result
from jobfacts.extract.posting import PostingText

CONCURRENCY = 6


@dataclass
class RelevanceSummary:
    scored: int
    relevant: int
    errors: int


def run_relevance(
    conn: psycopg.Connection,
    client,
    model: str = jev.MODEL,
    threshold: float = jev.RELEVANCE_THRESHOLD,
    rescore: bool = False,
    progress: Callable[[int, int], None] | None = None,
) -> RelevanceSummary:
    postings = store.load_postings(conn, only_unscored_by=None if rescore else model)

    def score(p: PostingText) -> tuple[PostingText, float | None]:
        try:
            return p, jev.relevance_probability(client, p, model)
        except Exception:
            return p, None

    scored = relevant = errors = 0
    with ThreadPoolExecutor(CONCURRENCY) as pool:
        for i, (p, prob) in enumerate(pool.map(score, postings), 1):
            if prob is None:
                errors += 1
                continue
            store.save_relevance(conn, p.id, model, prob, threshold)
            scored += 1
            relevant += prob >= threshold
            if i % 100 == 0:
                conn.commit()
                if progress:
                    progress(i, len(postings))
    conn.commit()
    return RelevanceSummary(scored, relevant, errors)


def eligible_ids(conn: psycopg.Connection, model: str = jev.MODEL) -> list[int]:
    """Relevant postings that pass decision 7a (Swiss location, or remote wording)."""
    ids = store.relevant_ids(conn, model, origin="board")
    return [p.id for p in store.load_postings(conn, ids) if p.is_extraction_eligible()]


def run_extraction(
    conn: psycopg.Connection,
    extractor: Extractor,
    posting_ids: list[int] | None = None,
    limit: int | None = None,
    notes: str = "",
) -> tuple[int, list[Result]]:
    """Extract and store one run. Without ids: all eligible postings. Returns (run id, results)."""
    ids = posting_ids or eligible_ids(conn)
    if limit:
        ids = ids[:limit]
    postings = store.load_postings(conn, ids)
    run_id = store.create_run(
        conn, extractor.name, extractor.model, extractor.spec_version(), notes
    )
    results: list[Result] = []
    with ThreadPoolExecutor(CONCURRENCY) as pool:
        for p, result in zip(postings, pool.map(extractor.extract, postings), strict=True):
            store.save_extraction(conn, run_id, p.id, result)
            results.append(result)
    store.finish_run(conn, run_id)
    return run_id, results


def make_extractor(name: str) -> Extractor:
    from jobfacts.extract import llm

    if name == "jev":
        return jev.JevExtractor()
    if name == "deepseek":
        return llm.deepseek_extractor()
    if name == "mistral":
        return llm.mistral_extractor()
    raise ValueError(f"unknown extractor {name!r}")
