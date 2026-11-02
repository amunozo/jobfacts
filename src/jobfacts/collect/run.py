"""One collection run: read every active board, store what was read."""

import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import psycopg

from jobfacts.collect import store
from jobfacts.collect.boards import BOARDS
from jobfacts.collect.http import make_client

MAX_WORKERS = 6


@dataclass
class RunSummary:
    run_id: int = 0
    boards_ok: int = 0
    boards_failed: int = 0
    counts: store.Counts = field(default_factory=store.Counts)
    failures: list[tuple[str, str]] = field(default_factory=list)
    seconds: float = 0.0


def collect(
    conn: psycopg.Connection, only: str | None = None, fetchers=None, employers_file=None
) -> RunSummary:
    """`fetchers` maps ats name to a fetch function; tests pass fakes, nothing else does."""
    fetchers = fetchers or BOARDS
    started = time.monotonic()
    store.load_employers(conn, employers_file or store.EMPLOYERS_FILE)
    sql = "SELECT id, name, ats, board_id, extra FROM employers WHERE active"
    employers = conn.execute(sql).fetchall()
    employers = [
        e for e in employers if e[2] in fetchers and (only is None or only in (e[1], e[2]))
    ]
    summary = RunSummary(run_id=store.start_run(conn))

    def read(employer):
        _, _, ats, board_id, extra = employer
        try:
            with make_client() as client:
                return employer, fetchers[ats](client, board_id, extra or {}), None
        except Exception as error:  # noqa: BLE001 - any failure means "board not read"
            return employer, None, f"{type(error).__name__}: {error}"

    # Boards are fetched in parallel (limited); all database writes stay in this thread.
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        for employer, postings, error in pool.map(read, employers):
            employer_id, name = employer[0], employer[1]
            if error is None and not postings:
                open_now = store.open_count(conn, employer_id)
                if open_now:
                    # An empty answer from a board that had open postings is more
                    # likely a glitch than every role closing at once.
                    error = f"empty response while {open_now} postings were open; nothing closed"
            if error is None:
                c = store.record_success(conn, summary.run_id, employer_id, postings)
                summary.boards_ok += 1
                summary.counts.new += c.new
                summary.counts.updated += c.updated
                summary.counts.closed += c.closed
            else:
                store.record_failure(conn, summary.run_id, employer_id, error)
                summary.boards_failed += 1
                summary.failures.append((name, error))
    store.finish_run(conn, summary.run_id, summary.boards_ok, summary.boards_failed, summary.counts)
    summary.seconds = time.monotonic() - started
    return summary
