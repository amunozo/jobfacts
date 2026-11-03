"""`jobfacts daily`: database up, migrate, collect, relevance, Jev extraction for search."""

import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import psycopg

from jobfacts import db
from jobfacts.collect.run import collect

REPO_ROOT = (
    Path(__file__).resolve().parents[2]
)  # holds docker-compose.yml (same base as migrations)


class StepFailed(Exception):
    def __init__(self, step: str, reason: str):
        super().__init__(f"daily: step '{step}' failed: {reason}")
        self.step = step


def _reachable() -> bool:
    try:
        db.connect().close()
    except psycopg.OperationalError:
        return False
    return True


def ensure_database(
    reachable: Callable[[], bool] = _reachable,
    run: Callable = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    root: Path = REPO_ROOT,
    timeout: float = 60,
) -> bool:
    """Start the compose database if it does not answer. Returns True if it had to be started."""
    if reachable():
        return False
    print("database not reachable; running `docker compose up -d`")
    try:
        result = run(["docker", "compose", "up", "-d"], cwd=root)
    except OSError as error:  # docker not installed or not runnable
        raise StepFailed("database", f"cannot run docker: {error}") from error
    if result.returncode != 0:
        raise StepFailed("database", f"`docker compose up -d` exited with {result.returncode}")
    waited = 0.0
    while waited < timeout:
        if reachable():
            return True
        sleep(2)
        waited += 2
    raise StepFailed("database", f"still not reachable after {timeout:.0f} s")


def _migrate(conn, totals: dict) -> None:
    applied = db.migrate(conn)
    print("applied: " + ", ".join(applied) if applied else "nothing to apply")


def _collect(conn, totals: dict) -> None:
    summary = collect(conn)
    c = summary.counts
    print(
        f"run {summary.run_id}: boards ok {summary.boards_ok}, failed {summary.boards_failed}; "
        f"new {c.new}, updated {c.updated}, closed {c.closed}; {summary.seconds:.0f}s"
    )
    for name, note in summary.notes:
        print(f"  {name}: {note}")
    for name, error in summary.failures:
        print(f"  FAILED {name}: {error}")
    totals.update(new=c.new, closed=c.closed)


def _relevance(conn, totals: dict) -> None:
    from jobfacts.extract import jev
    from jobfacts.extract.run import run_relevance

    r = run_relevance(conn, jev.make_client())
    print(f"scored {r.scored}, relevant {r.relevant}, errors {r.errors}")
    totals.update(relevant=r.relevant, errors=totals.get("errors", 0) + r.errors)


def _extract(conn, totals: dict) -> None:
    from jobfacts.extract.run import make_extractor, run_extraction

    run_id, results = run_extraction(conn, make_extractor("jev"), None, None, for_search=True)
    failed = sum(1 for x in results if x.error)
    cost = sum(x.cost_usd for x in results)
    print(f"run {run_id}: {len(results)} postings, {failed} errors, ${cost:.4f}")
    totals.update(
        extracted=len(results) - failed,
        errors=totals.get("errors", 0) + failed,
        cost=cost,
    )


def open_postings(conn) -> int:
    row = conn.execute(
        "SELECT count(*) FROM postings WHERE closed_at IS NULL AND origin = 'board'"
    ).fetchone()
    return row[0]


def run_daily(skip_collect: bool = False) -> int:
    """Run the steps in order; return 0, or 1 after printing the failing step's name."""
    totals: dict = {}
    try:
        ensure_database()
        with db.connect() as conn:
            steps = [("migrate", _migrate)]
            if not skip_collect:
                steps.append(("collect", _collect))
            steps += [("relevance", _relevance), ("extract", _extract)]
            for name, step in steps:
                print(f"== {name}")
                try:
                    step(conn, totals)
                except StepFailed:
                    raise
                except Exception as error:
                    conn.rollback()
                    raise StepFailed(name, f"{type(error).__name__}: {error}") from error
            totals["open"] = open_postings(conn)
    except StepFailed as failure:
        print(failure)
        return 1
    new = f" (+{totals['new']} new, {totals['closed']} closed)" if "new" in totals else ""
    print(
        f"daily: {totals['open']} open postings{new}, "
        f"{totals['relevant']} relevant newly scored, {totals['extracted']} extracted, "
        f"{totals['errors']} errors, ${totals['cost']:.3f}"
    )
    return 0
