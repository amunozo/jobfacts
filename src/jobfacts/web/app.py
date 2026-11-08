"""The web app: `GET /health`, `GET /api/search` (JSON), `GET /` (the search page) and
`GET /runs` (the table of daily runs).

`create_app` takes the database connection source and the embedder as arguments, so tests
use a throwaway schema and a fake embedder. HTTP Basic protects everything except /health.
"""

import base64
import contextlib
import logging
import secrets
import threading
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import psycopg
import psycopg.rows
import psycopg_pool
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from jinja2 import Environment, FileSystemLoader
from psycopg import errors as pg_errors

from jobfacts import db, embed, locations, search
from jobfacts.extract.spec import SENIORITY_VALUES
from jobfacts.web import params

log = logging.getLogger(__name__)

HERE = Path(__file__).parent
REALM = 'Basic realm="jobfacts", charset="UTF-8"'
# Only this site: no CDN, no web fonts, no scripts. Forms may only post back here.
CSP = "default-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
EVIDENCE_LABELS = {
    "office_cantons": "Place",
    "work_mode": "Work mode",
    "remote_from_switzerland": "Remote from Switzerland",
    "german": "German",
    "french": "French",
    "min_years": "Years required",
    "title_level": "Seniority in the title",
    "contract_type": "Contract type",
}
LEVELS = [
    ("", "any"),
    ("none", "none required"),
    ("intermediate", "up to intermediate"),
    ("fluent", "up to fluent"),
]
WORK_MODE_CHOICES = search.WORK_MODES
RUNS_SHOWN = 30
ZURICH = ZoneInfo("Europe/Zurich")
SENIORITY_CHOICES = [v for v in SENIORITY_VALUES if v != "not_stated"]

ConnectionSource = Callable[[], contextlib.AbstractContextManager[psycopg.Connection]]
EmbedderFactory = Callable[[str], embed.Embedder]


def safe_url(url: str | None) -> str | None:
    """The URL if it is an absolute http(s) link, else None (so `javascript:` is not a link)."""
    if not url or any(ord(c) <= 0x20 or ord(c) == 0x7F for c in url):
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    return url if parts.scheme.lower() in ("http", "https") and parts.netloc else None


def human(value: str | None) -> str:
    return "not stated" if value is None else str(value).replace("_", " ")


def make_environment() -> Environment:
    env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=True)
    env.filters["safe_url"] = safe_url
    env.filters["human"] = human
    return env


def duration_text(started, finished) -> str:
    if finished is None:
        return ""
    seconds = max(int((finished - started).total_seconds()), 0)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min {seconds:02d} s" if minutes else f"{seconds} s"


def run_rows(rows) -> list[dict]:
    """Template-ready values for the `daily_runs` rows (newest first)."""
    return [
        {
            "started": r["started_at"].astimezone(ZURICH).strftime("%Y-%m-%d %H:%M"),
            "duration": duration_text(r["started_at"], r["finished_at"]),
            "status": r["status"],
            "status_class": r["status"] if r["status"] in ("failed", "running") else "",
            "failed_step": r["failed_step"],
            "boards_ok": r["boards_ok"],
            "boards_failed": r["boards_failed"],
            "new_postings": r["new_postings"],
            "closed": r["closed"],
            "relevant": r["relevant"],
            "extracted": r["extracted"],
            "embedded": r["embedded"],
            "errors": r["errors"],
            "cost": "" if r["cost_usd"] is None else f"{r['cost_usd']:.3f}",
            "warnings": r["warnings"],
        }
        for r in rows
    ]


class CachedEmbedders:
    """Makes each embedder once, on first use (it needs an API key and builds a client).
    A failed attempt is not cached, so fixing the key does not need a restart."""

    def __init__(self, factory: EmbedderFactory):
        self.factory = factory
        self.cache: dict[str, embed.Embedder] = {}
        self.lock = threading.Lock()

    def __call__(self, alias: str) -> embed.Embedder:
        with self.lock:
            if alias not in self.cache:
                self.cache[alias] = self.factory(alias)
            return self.cache[alias]


def password_ok(header: str | None, password: str) -> bool:
    """Is the Authorization header HTTP Basic with this password? (Any user name is accepted.)"""
    scheme, _, token = (header or "").partition(" ")
    if scheme.lower() != "basic":
        return False
    try:
        decoded = base64.b64decode(token.strip(), validate=True).decode("utf-8")
    except ValueError:  # bad base64 (also non-ASCII text) or not UTF-8
        return False
    _, colon, given = decoded.partition(":")
    return bool(colon) and secrets.compare_digest(given.encode(), password.encode())


def form_values(items: list[tuple[str, str]]) -> dict:
    """What to show in the form: the raw URL parameters, whether or not they are valid."""

    def many(name: str) -> set[str]:
        return {p.strip() for k, v in items for p in v.split(",") if k == name and p.strip()}

    def one(name: str) -> str:
        return next((v.strip() for k, v in reversed(items) if k == name), "")

    return {
        "query": next((v for k, v in reversed(items) if k == "query"), ""),
        "cantons": set(search.canton_codes(list(many("canton")))),
        "remote": one("remote").lower() in params.TRUE,
        "work_modes": many("work_mode"),
        "german_max": one("german_max"),
        "french_max": one("french_max"),
        "seniority": many("seniority"),
        "no_fixed_term": one("no_fixed_term").lower() in params.TRUE,
        "new_days": one("new_days"),
    }


def create_app(
    connection_source: ConnectionSource | None = None,
    embedder_factory: EmbedderFactory | None = None,
    password: str | None = None,
    auth: bool = True,
    model: str = embed.DEFAULT_MODEL,
    pool_size: int = 4,
) -> FastAPI:
    """`connection_source()` returns a context manager that yields a connection (default: a
    small psycopg connection pool on DATABASE_URL, opened when the server starts)."""
    if auth and not password:
        raise ValueError("a password is required unless auth is switched off")
    pool: psycopg_pool.ConnectionPool | None = None
    if connection_source is None:
        pool = psycopg_pool.ConnectionPool(
            db.database_url(),
            min_size=1,
            max_size=pool_size,
            timeout=5,  # seconds to wait for a free connection before giving up
            check=psycopg_pool.ConnectionPool.check_connection,
            open=False,
        )
        connection_source = pool.connection
    embedders = CachedEmbedders(embedder_factory or embed.make_embedder)
    env = make_environment()
    model_id = embed.model_id(model)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if pool is not None:
            pool.open(wait=False)  # an unreachable database must not stop the server starting
        yield
        if pool is not None:
            pool.close()

    # No generated docs page: it loads scripts from a CDN, which the policy forbids.
    app = FastAPI(
        title="jobfacts", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )

    @app.middleware("http")
    async def basic_auth(request: Request, call_next):
        if auth and request.url.path != "/health":
            if not password_ok(request.headers.get("authorization"), password or ""):
                return Response("Password required", 401, headers={"WWW-Authenticate": REALM})
        return await call_next(request)

    @app.middleware("http")  # added last = outermost: also on the 401 answers
    async def security_headers(request: Request, call_next):
        try:
            response = await call_next(request)
        except Exception:  # any unexpected failure: generic body, details only in the log
            log.exception("unhandled error on %s", request.url.path)
            response = Response("Internal server error", 500)
        response.headers["Content-Security-Policy"] = CSP
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(psycopg.OperationalError)
    async def database_down(request: Request, error: psycopg.OperationalError):
        log.error("database unavailable: %r", error)
        return JSONResponse({"error": "database unavailable"}, status_code=503)

    def last_daily_run_status(conn) -> dict | None:
        """finished_at and status of the newest daily run; None if none (or no table yet:
        a server upgraded before the first `jobfacts daily` must still answer 200)."""
        try:
            with conn.transaction():
                row = conn.execute(
                    "SELECT finished_at, status FROM daily_runs ORDER BY started_at DESC LIMIT 1"
                ).fetchone()
        except pg_errors.UndefinedTable:
            return None
        if row is None:
            return None
        finished, status = row
        return {"finished_at": finished.isoformat() if finished else None, "status": status}

    @app.get("/health")
    def health():
        try:
            with connection_source() as conn:
                open_postings, last_run = conn.execute(
                    "SELECT (SELECT count(*) FROM postings WHERE closed_at IS NULL "
                    "AND origin = 'board'), (SELECT max(finished_at) FROM collection_runs)"
                ).fetchone()
                last_daily_run = last_daily_run_status(conn)
        except Exception as error:  # any failure to reach or read the database
            log.error("health check failed: %r", error)
            return JSONResponse(
                {"status": "unavailable", "database": False, "embedding_model": model_id},
                status_code=503,
            )
        return {
            "status": "ok",
            "database": True,
            "open_postings": open_postings,
            "last_collection_finished": last_run.isoformat() if last_run else None,
            "last_daily_run": last_daily_run,
            "embedding_model": model_id,
        }

    def run_search(request: Request, evidence: bool):
        """Parse, embed (no connection held), then search. Returns (parsed, result)."""
        parsed = params.parse(request.query_params.multi_items())
        plan = search.prepare(parsed.filters, parsed.mode, model, embedders, error_detail=False)
        with connection_source() as conn:
            result = search.execute(conn, parsed.filters, plan, evidence or parsed.evidence)
        return parsed, result

    @app.get("/api/search")
    def api_search(request: Request):
        try:
            _, result = run_search(request, evidence=False)
        except search.EmbedError as error:
            return JSONResponse({"error": str(error)}, status_code=502)
        except search.SearchError as error:
            return JSONResponse({"error": str(error)}, status_code=400)
        return {
            "total": result.total,
            "results": [h.as_dict() for h in result.hits],
            "mode_used": result.mode_used,
            "warnings": result.warnings,
        }

    @app.get("/", response_class=HTMLResponse)
    def page(request: Request):
        items = request.query_params.multi_items()
        context = {
            "cantons": locations.office_canton_values(),
            "work_modes": WORK_MODE_CHOICES,
            "seniorities": SENIORITY_CHOICES,
            "levels": LEVELS,
            "labels": EVIDENCE_LABELS,
            "selected": {},
            "result": None,
            "error": None,
            "more_url": None,
        }
        context["selected"] = form_values(items)
        status = 200
        try:
            parsed, result = run_search(request, evidence=True)
            context["result"] = result
            f = parsed.filters
            if result.total > len(result.hits) and f.limit < params.MAX_LIMIT:
                more = [(k, v) for k, v in items if k != "limit"]
                more.append(("limit", str(min(f.limit * 2, params.MAX_LIMIT))))
                context["more_url"] = "/?" + urlencode(more)
        except search.SearchError as error:
            status = 502 if isinstance(error, search.EmbedError) else 400
            context["error"] = str(error)
        html = env.get_template("index.html").render(**context)
        return HTMLResponse(html, status_code=status)

    @app.get("/runs", response_class=HTMLResponse)
    def runs():
        try:
            with (
                connection_source() as conn,
                conn.transaction(),
                conn.cursor(row_factory=psycopg.rows.dict_row) as cur,
            ):
                cur.execute(
                    "SELECT * FROM daily_runs ORDER BY started_at DESC LIMIT %s", (RUNS_SHOWN,)
                )
                rows = cur.fetchall()
        except pg_errors.UndefinedTable:  # not migrated yet: same as no run recorded
            rows = []
        return HTMLResponse(env.get_template("runs.html").render(runs=run_rows(rows)))

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    return app
