"""Light monitoring of `jobfacts daily`: a row per run in `daily_runs` and a dead-man ping.

A dead-man switch is an outside service (such as healthchecks.io) that expects a ping every
day and sends an alert when none arrives, so a run that never started, hung or failed is
noticed even when this machine is down. The ping URL is `JOBFACTS_HEALTHCHECK_URL`; without
it nothing is sent. A ping that fails only prints a warning: monitoring never fails the run.
"""

import os
from collections.abc import Callable

import httpx

ENV_VAR = "JOBFACTS_HEALTHCHECK_URL"
PING_TIMEOUT = 5.0  # seconds
STALE_AFTER = "6 hours"  # a row still `running` after this was killed, not slow

Send = Callable[[str, str, str | None], None]  # (method, url, body)


def _send(method: str, url: str, body: str | None) -> None:
    content = body.encode("utf-8") if body is not None else None
    response = httpx.request(method, url, content=content, timeout=PING_TIMEOUT)
    response.raise_for_status()


class Pinger:
    """The three pings of a healthchecks.io-style check: `<url>/start`, `<url>`, `<url>/fail`.

    `send` is injectable so tests make no network calls."""

    def __init__(self, url: str | None = None, send: Send | None = None):
        self.url = (url if url is not None else os.environ.get(ENV_VAR, "")).strip().rstrip("/")
        self.send = send or _send

    def _ping(self, method: str, suffix: str, body: str | None = None) -> None:
        if not self.url:
            return
        try:
            self.send(method, self.url + suffix, body)
        except Exception as error:
            # Only the error class: the URL is a secret and some messages contain it.
            print(f"warning: health check ping failed ({type(error).__name__})")

    def start(self) -> None:
        self._ping("GET", "/start")

    def success(self, summary: str) -> None:
        self._ping("POST", "", summary)

    def fail(self, step: str) -> None:
        self._ping("POST", "/fail", f"daily failed at step '{step}'")


def start_run(conn) -> int:
    """Insert a `running` row and return its id. A row left `running` by a killed earlier run
    is marked failed (step `interrupted`) first, so the table never shows it as still going."""
    conn.execute(
        "UPDATE daily_runs SET status = 'failed', failed_step = 'interrupted', "
        "finished_at = now() WHERE status = 'running' "
        f"AND started_at < now() - interval '{STALE_AFTER}'"
    )
    run_id = conn.execute("INSERT INTO daily_runs DEFAULT VALUES RETURNING id").fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, totals: dict, failed_step: str | None = None) -> None:
    """Close the row: status `failed` with the step name, or `ok`, plus the run's numbers."""
    warnings = "; ".join(totals.get("warnings", [])) or None
    boards_failed = totals.get("boards_failed")
    if boards_failed:
        boards = f"{boards_failed} boards failed"
        warnings = f"{warnings}; {boards}" if warnings else boards
    conn.execute(
        "UPDATE daily_runs SET finished_at = now(), status = %s, failed_step = %s, "
        "boards_ok = %s, boards_failed = %s, new_postings = %s, closed = %s, relevant = %s, "
        "extracted = %s, embedded = %s, errors = %s, cost_usd = %s, warnings = %s "
        "WHERE id = %s",
        (
            "failed" if failed_step else "ok",
            failed_step,
            totals.get("boards_ok"),
            totals.get("boards_failed"),
            totals.get("new"),
            totals.get("closed"),
            totals.get("relevant"),
            totals.get("extracted"),
            totals.get("embedded"),
            totals.get("errors"),
            totals.get("cost"),
            warnings,
            run_id,
        ),
    )
    conn.commit()
