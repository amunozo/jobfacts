"""`jobfacts daily`: database up, migrate, collect, relevance, Jev extraction, embeddings."""

import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import psycopg

from jobfacts import db, embed, monitor
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
    """Start the compose database if it does not answer. Returns True if it had to be started.

    With JOBFACTS_NO_DB_START=1 (the server's container, where there is no docker and the
    database is a separate service) nothing is started: it only waits for the database."""
    if reachable():
        return False
    if os.environ.get("JOBFACTS_NO_DB_START") == "1":
        print("database not reachable; waiting for it (JOBFACTS_NO_DB_START=1)")
        waited = 0.0
        while waited < timeout:
            sleep(2)
            waited += 2
            if reachable():
                return False
        raise StepFailed("database", f"still not reachable after {timeout:.0f} s")
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
    totals.update(
        new=c.new,
        closed=c.closed,
        boards_ok=summary.boards_ok,
        boards_failed=summary.boards_failed,
    )


def _relevance(conn, totals: dict) -> None:
    from jobfacts.extract import jev
    from jobfacts.extract.jev_backup import backup_calls, backup_line
    from jobfacts.extract.run import run_relevance

    client = jev.make_client()
    r = run_relevance(conn, client)
    print(f"relevance flags updated for threshold {jev.RELEVANCE_THRESHOLD}: {r.flags_changed}")
    print(f"scored {r.scored}, relevant {r.relevant}, errors {r.errors}")
    if line := backup_line(backup_calls(client)):
        print(line)
    totals.update(relevant=r.relevant, errors=totals.get("errors", 0) + r.errors)


def _extract(conn, totals: dict) -> None:
    from jobfacts.extract.jev_backup import backup_line_for_results
    from jobfacts.extract.run import make_extractor, run_extraction

    run_id, results = run_extraction(conn, make_extractor("jev"), None, None, for_search=True)
    failed = sum(1 for x in results if x.error)
    cost = sum(x.cost_usd for x in results)
    print(f"run {run_id}: {len(results)} postings, {failed} errors, ${cost:.4f}")
    if line := backup_line_for_results(results):
        print(line)
    totals.update(
        extracted=len(results) - failed,
        errors=totals.get("errors", 0) + failed,
        cost=cost,
    )


def _embed(conn, totals: dict) -> None:
    """Embed new and changed pool postings for the default model. Never fails the daily run:
    a missing key, a network or API error or the cost guard only prints a warning (the
    vectors made before the error stay; the next run continues)."""
    totals.setdefault("embedded", 0)
    try:
        embedder = embed.make_embedder(embed.DEFAULT_MODEL)
        summary = embed.run_embed(conn, embedder)
    except Exception as error:
        conn.rollback()
        print(f"warning: embedding skipped ({type(error).__name__}: {error})")
        totals.setdefault("warnings", []).append(f"embedding skipped ({type(error).__name__})")
        return
    print(
        f"{summary.model} at {summary.budget} characters: embedded {summary.embedded}, "
        f"unchanged {summary.skipped}, tokens {summary.tokens}, cost ${summary.cost:.4f}"
    )
    totals["embedded"] = summary.embedded
    totals["cost"] = totals.get("cost", 0.0) + summary.cost


def open_postings(conn) -> int:
    row = conn.execute(
        "SELECT count(*) FROM postings WHERE closed_at IS NULL AND origin = 'board'"
    ).fetchone()
    return row[0]


def _rollback(conn) -> None:
    """Roll back, ignoring a dead connection (the caller handles that case)."""
    try:
        conn.rollback()
    except Exception:
        pass


def _record(conn, run_id: int | None, totals: dict, failed_step: str | None) -> bool:
    """Close the run's row; a database error here is only a warning (the run's result stands).

    If the connection is dead (the server restarted, the backend was killed), the row is closed
    through a fresh connection, best effort. Returns True when the row was written."""
    if run_id is None:
        return False
    try:
        _rollback(conn)
        monitor.finish_run(conn, run_id, totals, failed_step)
        return True
    except Exception:
        pass
    try:
        with db.connect() as fresh:
            monitor.finish_run(fresh, run_id, totals, failed_step)
        return True
    except Exception as error:
        print(f"warning: could not record the run ({type(error).__name__})")
        return False


def _terminate(signum, frame) -> None:
    """SIGTERM (`docker stop`, `systemctl stop`, a timeout) ends the run like any failure: the
    exception unwinds through run_daily, which records it and pings /fail."""
    raise SystemExit(128 + signum)


def _install_sigterm():
    """Install the SIGTERM handler; returns the previous one, or None if this is not the main
    thread (signals can only be set there)."""
    try:
        return signal.signal(signal.SIGTERM, _terminate)
    except ValueError:
        return None


def _reason(error: BaseException) -> str:
    if isinstance(error, SystemExit) and error.code == 128 + signal.SIGTERM:
        return "terminated (SIGTERM)"
    if isinstance(error, (KeyboardInterrupt, SystemExit)):
        return f"interrupted ({type(error).__name__})"
    return f"{type(error).__name__}: {error}"


def _work(skip_collect: bool, totals: dict, state: dict) -> str:
    """The run itself; `state["step"]` always names what is running, so any way out of here
    (an exception, a lost connection, KeyboardInterrupt, SIGTERM) can name the failing step.
    Returns the summary line."""
    state["step"] = "database"
    ensure_database()
    state["step"] = "connect"
    with db.connect() as conn:
        try:
            steps = [("migrate", _migrate)]
            if not skip_collect:
                steps.append(("collect", _collect))
            steps += [("relevance", _relevance), ("extract", _extract), ("embed", _embed)]
            for name, step in steps:
                state["step"] = name
                print(f"== {name}")
                try:
                    step(conn, totals)
                except StepFailed:
                    raise
                except Exception as error:
                    _rollback(conn)
                    raise StepFailed(name, f"{type(error).__name__}: {error}") from error
                if name == "migrate":  # the table exists from here on
                    state["run_id"] = monitor.start_run(conn)
            state["step"] = "summary"
            totals["open"] = open_postings(conn)
            new = f" (+{totals['new']} new, {totals['closed']} closed)" if "new" in totals else ""
            summary = (
                f"daily: {totals['open']} open postings{new}, "
                f"{totals['relevant']} relevant newly scored, {totals['extracted']} extracted, "
                f"{totals['embedded']} embedded, "
                f"{totals['errors']} errors, ${totals['cost']:.3f}"
            )
        except BaseException:
            _record(conn, state["run_id"], totals, state["step"])
            state["run_id"] = None
            raise
        _record(conn, state["run_id"], totals, None)
        state["run_id"] = None
    return summary


def run_daily(skip_collect: bool = False) -> int:
    """Run the steps in order; return 0, or 1 after printing the failing step's name.

    The run is recorded in `daily_runs` and, if JOBFACTS_HEALTHCHECK_URL is set, pinged to a
    dead-man service (start, then success or fail). Every way out after the start ping (an
    error in a step, a lost connection, KeyboardInterrupt, SIGTERM) records the row as failed
    and pings /fail with the step's name; KeyboardInterrupt and SystemExit are then re-raised."""
    totals: dict = {}
    state: dict = {"step": "database", "run_id": None}
    pinger = monitor.Pinger()
    pinger.start()
    previous = _install_sigterm()
    try:
        summary = _work(skip_collect, totals, state)
    except BaseException as error:
        step = error.step if isinstance(error, StepFailed) else state["step"]
        print(error if isinstance(error, StepFailed) else StepFailed(step, _reason(error)))
        pinger.fail(step)
        if isinstance(error, (KeyboardInterrupt, SystemExit)):
            raise
        return 1
    finally:
        if previous is not None:
            signal.signal(signal.SIGTERM, previous)
    print(summary)
    pinger.success(summary)
    return 0
