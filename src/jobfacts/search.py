"""Search over open board postings: a pure SQL builder (`build`) and a runner (`search`).

Only open board postings that the relevance filter kept are searched, using each posting's
latest successful Jev extraction. Filter semantics are written down in docs/search.md.
"""

import logging
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field, replace

import psycopg

from jobfacts import embed, locations
from jobfacts import sentences as splitter
from jobfacts.embed import vector_literal
from jobfacts.extract import jev
from jobfacts.extract.spec import FIELDS, LOCATION_ID, SENIORITY_VALUES, TITLE_ID
from jobfacts.grouping import group_rows

# What each --german-max / --french-max level keeps (values of the language fields).
_ALWAYS = ["not_mentioned", "nice_to_have"]
LANGUAGE_MAX: dict[str, list[str]] = {
    "none": _ALWAYS,
    "intermediate": [*_ALWAYS, "required_intermediate", "either_required"],
    "fluent": [*_ALWAYS, "required_intermediate", "either_required", "required_fluent"],
}
UNCLEAR_LEVEL = "required_level_not_stated"
REMOTE_ALLOWED = ["allowed"]
REMOTE_UNCLEAR = ["allowed", "unclear", "not_stated"]
WORK_MODES = [v for v in FIELDS["work_mode"].values if v != "not_stated"]
MODES = ["words", "meaning", "hybrid"]
TOP_PER_LIST = 200  # hybrid: each list contributes its best 200 postings to the fusion
RRF_K = 60
EVIDENCE_FIELDS = [
    "office_cantons",
    "work_mode",
    "remote_from_switzerland",
    "german",
    "french",
    "min_years",
    "title_level",
    "contract_type",
]


log = logging.getLogger(__name__)


class SearchError(ValueError):
    """A filter value that the spec does not allow."""


MAX_NEW_DAYS = 3650


class EmbedError(SearchError):
    """The query could not be embedded and the mode was `meaning` (no fallback exists)."""


def canton_codes(values: list[str]) -> list[str]:
    """Canton codes as the spec writes them: upper case, but `outside_switzerland` lower case."""
    return [c.upper() if c.lower() != "outside_switzerland" else c.lower() for c in values]


@dataclass
class Filters:
    query: str = ""
    cantons: list[str] = field(default_factory=list)
    remote: bool = False
    remote_unclear: bool = False
    work_modes: list[str] = field(default_factory=list)
    german_max: str | None = None
    french_max: str | None = None
    show_unclear_language: bool = False
    no_fixed_term: bool = False
    seniority: list[str] = field(default_factory=list)
    new_days: int | None = None
    limit: int = 20
    mode: str = "words"  # words | meaning | hybrid
    model: str = ""  # embedding model id (posting_embeddings.model), for meaning and hybrid

    def validate(self) -> None:
        if self.mode not in MODES:
            raise SearchError(f"unknown mode {self.mode!r}; allowed: {MODES}")
        if self.mode != "words" and not self.query.strip():
            raise SearchError(f"--mode {self.mode} needs a query")
        if self.mode != "words" and not self.model:
            raise SearchError(f"--mode {self.mode} needs --model")
        allowed_cantons = set(locations.office_canton_values())
        for c in self.cantons:
            if c not in allowed_cantons:
                raise SearchError(f"unknown canton {c!r}; allowed: {sorted(allowed_cantons)}")
        for m in self.work_modes:
            if m not in WORK_MODES:
                raise SearchError(f"unknown work mode {m!r}; allowed: {WORK_MODES}")
        for s in self.seniority:
            if s not in SENIORITY_VALUES:
                raise SearchError(f"unknown seniority {s!r}; allowed: {SENIORITY_VALUES}")
        for name, value in (("german", self.german_max), ("french", self.french_max)):
            if value is not None and value not in LANGUAGE_MAX:
                raise SearchError(f"--{name}-max must be one of {list(LANGUAGE_MAX)}")
        if self.new_days is not None and not 0 <= self.new_days <= MAX_NEW_DAYS:
            raise SearchError(f"--new-days must be between 0 and {MAX_NEW_DAYS}")
        if any(unicodedata.category(c) == "Cc" and c not in "\t\n\r" for c in self.query):
            raise SearchError("the query contains control characters")
        if self.limit < 1:
            raise SearchError("--limit must be at least 1")


# The latest successful Jev extraction of each relevant, open board posting, counted only if
# it comes from a search run and was made for the posting's current text (content_hash).
# Experiment runs are never shown; postings without such an extraction are not shown until
# `jobfacts daily` re-extracts them. An older spec version with the current text is shown.
_BASE = """
FROM postings p
JOIN employers emp ON emp.id = p.employer_id
JOIN relevance rel ON rel.posting_id = p.id AND rel.model = %(rel_model)s AND rel.relevant
JOIN LATERAL (
    SELECT e.* FROM extractions e JOIN extraction_runs r ON r.id = e.run_id
    WHERE e.posting_id = p.id AND r.extractor = 'jev' AND r.purpose = 'search'
      AND e.error IS NULL AND e.content_hash = p.content_hash
    ORDER BY e.run_id DESC LIMIT 1
) x ON true
"""


def stale_relevance_count(conn) -> int:
    """Relevance rows of Jev for open postings not made with the current relevance version
    (closed postings are never re-scored, so they do not count)."""
    return conn.execute(
        "SELECT count(*) FROM relevance r JOIN postings p ON p.id = r.posting_id "
        "WHERE p.closed_at IS NULL AND r.model = %s AND r.spec_version IS DISTINCT FROM %s",
        (jev.MODEL, jev.relevance_version()),
    ).fetchone()[0]


def stale_embedding_count(conn, model: str) -> int:
    """Pool postings (`embed.POOL_FROM`) with no vector for the embedding model id `model`, or
    whose vector was made from an older version of the posting (content hash differs)."""
    return conn.execute(
        f"SELECT count(*) {embed.POOL_FROM} LEFT JOIN posting_embeddings e "
        "ON e.posting_id = p.id AND e.model = %(model)s "
        f"{embed.POOL_WHERE} AND (e.posting_id IS NULL OR e.content_hash <> p.content_hash)",
        {**embed.pool_params(), "model": model},
    ).fetchone()[0]


def _language_values(maximum: str, show_unclear: bool) -> list[str]:
    values = list(LANGUAGE_MAX[maximum])
    if show_unclear or maximum == "fluent":
        values.append(UNCLEAR_LEVEL)
    return values


def build(
    f: Filters, query_vector: list[float] | None = None, top: int | None = None
) -> tuple[str, dict]:
    """Return (SQL, parameters) for all matching postings, in result order.

    Pure: no database access. Mode `words` ranks by full-text rank and keeps only postings that
    match the query words; mode `meaning` needs the query's embedding (`query_vector`) and ranks
    the postings that have a stored vector for `f.model` by cosine similarity. Every filter
    applies in both. `top` adds a LIMIT (hybrid takes the best 200 of each list). Without it
    there is no LIMIT: `search` groups duplicates first and then applies the limit to the groups.
    Hybrid is built by `search` from one query of each mode.
    """
    f.validate()
    if f.mode == "hybrid":
        raise SearchError("hybrid is the fusion of a words and a meaning query; use search()")
    if f.mode == "meaning" and query_vector is None:
        raise SearchError("meaning search needs the query vector")
    params: dict = {"rel_model": jev.MODEL}
    where = ["p.closed_at IS NULL", "p.origin = 'board'"]

    location_parts = []
    if f.cantons:
        location_parts.append("x.office_cantons && %(cantons)s::text[]")
        params["cantons"] = f.cantons
    if f.remote or f.remote_unclear:
        params["remote_values"] = REMOTE_UNCLEAR if f.remote_unclear else REMOTE_ALLOWED
        location_parts.append(
            "(x.work_mode = 'remote' AND x.remote_from_switzerland = ANY(%(remote_values)s))"
        )
    if location_parts:  # cantons OR remote when both are asked for
        where.append("(" + " OR ".join(location_parts) + ")")
    if f.work_modes:
        where.append("x.work_mode = ANY(%(work_modes)s)")
        params["work_modes"] = f.work_modes
    for column, maximum in (("german", f.german_max), ("french", f.french_max)):
        if maximum is not None:
            where.append(f"x.{column} = ANY(%({column}_values)s)")
            params[f"{column}_values"] = _language_values(maximum, f.show_unclear_language)
    if f.no_fixed_term:  # not_stated counts as permanent; NULL = extracted before the field
        where.append("x.contract_type IS DISTINCT FROM 'fixed_term'")
    if f.seniority:
        where.append("x.seniority = ANY(%(seniority)s)")
        params["seniority"] = f.seniority
    if f.new_days is not None:
        where.append("p.first_seen_at >= now() - make_interval(days => %(new_days)s)")
        params["new_days"] = f.new_days

    rank = "0::real"
    join = ""
    if f.mode == "meaning":
        join = " JOIN posting_embeddings emb ON emb.posting_id = p.id AND emb.model = %(emb_model)s"
        rank = "1 - (emb.embedding <=> %(qvec)s::vector)"
        params["emb_model"] = f.model
        params["qvec"] = vector_literal(query_vector)
        order = "rank DESC, p.first_seen_at DESC, p.id"
    elif f.query.strip():
        where.append("p.search_tsv @@ websearch_to_tsquery('simple', %(query)s)")
        rank = "ts_rank(p.search_tsv, websearch_to_tsquery('simple', %(query)s))"
        params["query"] = f.query
        order = "rank DESC, p.first_seen_at DESC, p.id"
    else:
        order = "p.first_seen_at DESC, p.id"

    columns = (
        "p.id, p.employer_id, p.title, emp.name, p.url, p.first_seen_at, x.office_cantons, "
        "x.office_places, "
        "x.work_mode, x.remote_from_switzerland, x.german, x.french, x.seniority, x.contract_type, "
        f"p.location_raw, {rank} AS rank"
    )
    sql = f"SELECT {columns} {_BASE}{join} WHERE {' AND '.join(where)} ORDER BY {order}"
    if top is not None:
        sql += " LIMIT %(top)s"
        params["top"] = top
    return sql, params


def build_evidence(ids: list[int]) -> tuple[str, dict]:
    """SQL for the description and the evidence sentence ids of the given postings."""
    columns = "p.id, p.title, p.location_raw, p.description_text, " + ", ".join(
        f"x.{n}_sentences AS {n}_sentences" for n in EVIDENCE_FIELDS
    )
    return (
        f"SELECT {columns} {_BASE} WHERE p.id = ANY(%(ids)s)",
        {"rel_model": jev.MODEL, "ids": ids},
    )


@dataclass
class Hit:
    id: int
    title: str
    employer: str
    url: str
    first_seen: str
    cantons: list[str]
    places: list[str]
    work_mode: str
    remote_from_switzerland: str
    german: str
    french: str
    seniority: str
    contract_type: str | None
    rank: float
    location_raw: str | None = None
    evidence: dict[str, list[str]] | None = None
    duplicates: list[dict] = field(default_factory=list)  # same job at other locations

    @property
    def where(self) -> str:
        """Places (or cantons) of the office, plus the remote status when remote."""
        parts = [p.split(":", 2)[-1] for p in self.places] or list(self.cantons)
        if self.work_mode == "remote":
            parts.append(f"remote: {self.remote_from_switzerland}")
        return ", ".join(parts) or "not stated"

    def as_dict(self) -> dict:
        d = {
            "id": self.id,
            "title": self.title,
            "employer": self.employer,
            "url": self.url,
            "first_seen": self.first_seen,
            "office_cantons": self.cantons,
            "office_places": self.places,
            "work_mode": self.work_mode,
            "remote_from_switzerland": self.remote_from_switzerland,
            "german": self.german,
            "french": self.french,
            "seniority": self.seniority,
            "contract_type": self.contract_type,
            "rank": self.rank,
            "location_raw": self.location_raw,
            "duplicates": self.duplicates,
        }
        if self.evidence is not None:
            d["evidence"] = self.evidence
        return d


def _evidence(row: dict) -> dict[str, list[str]]:
    sentences = {s.id: s.text for s in splitter.split(row["description_text"])}
    sentences[TITLE_ID] = row["title"]
    sentences[LOCATION_ID] = row["location_raw"]
    return {
        name: [sentences[i] for i in (row[f"{name}_sentences"] or []) if i in sentences]
        for name in EVIDENCE_FIELDS
    }


def _rows(conn: psycopg.Connection, sql: str, params: dict) -> list[dict]:
    cur = conn.execute(sql, params)
    names = [c.name for c in cur.description]
    return [dict(zip(names, values, strict=True)) for values in cur.fetchall()]


def rrf(rankings: list[list[int]], k: int = RRF_K) -> list[tuple[int, float]]:
    """Reciprocal rank fusion: each list gives an id 1 / (k + position), position from 1; the
    scores add up. Returns (id, score) best first, ties by id."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for position, item in enumerate(ranking, 1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + position)
    return sorted(scores.items(), key=lambda pair: (-pair[1], pair[0]))


def _hybrid_rows(conn: psycopg.Connection, f: Filters, query_vector: list[float]) -> list[dict]:
    """Fuse the best 200 postings of the words list and of the meaning list (same filters)."""
    lists = [
        _rows(conn, *build(replace(f, mode="words"), top=TOP_PER_LIST)),
        _rows(conn, *build(replace(f, mode="meaning"), query_vector, top=TOP_PER_LIST)),
    ]
    by_id = {row["id"]: row for rows in lists for row in rows}
    fused = rrf([[row["id"] for row in rows] for rows in lists])
    return [{**by_id[i], "rank": score} for i, score in fused]


def search(
    conn: psycopg.Connection,
    f: Filters,
    evidence: bool = False,
    query_vector: list[float] | None = None,
) -> tuple[list[Hit], int]:
    """Run the search. Returns (hits, total number of matching jobs before the limit).

    Postings of the same employer with the same normalised title are one job (see
    `grouping`): the best-ranked matching posting is shown and the others are its
    `duplicates`. The total and the limit count jobs, not postings. Modes `meaning` and
    `hybrid` need `query_vector`, the embedding of the query made with model `f.model`.
    """
    f.validate()
    if f.mode != "words" and query_vector is None:
        raise SearchError(f"--mode {f.mode} needs the query vector")
    if f.mode == "hybrid":
        rows = _hybrid_rows(conn, f, query_vector)
    else:
        rows = _rows(conn, *build(f, query_vector))
    groups = group_rows(rows)
    shown = groups[: f.limit]
    evidence_by_id: dict[int, dict] = {}
    if evidence and shown:  # sentences are split only for the jobs that are shown
        sql, params = build_evidence([row["id"] for row, _ in shown])
        evidence_by_id = {r["id"]: _evidence(r) for r in _rows(conn, sql, params)}
    hits = []
    for row, dups in shown:
        hits.append(
            Hit(
                row["id"],
                row["title"],
                row["name"],
                row["url"],
                row["first_seen_at"].date().isoformat(),
                row["office_cantons"] or [],
                row["office_places"] or [],
                row["work_mode"],
                row["remote_from_switzerland"],
                row["german"],
                row["french"],
                row["seniority"],
                row["contract_type"],
                float(row["rank"]),
                row["location_raw"],
                evidence_by_id.get(row["id"]),
                [{"id": d["id"], "url": d["url"], "location_raw": d["location_raw"]} for d in dups],
            )
        )
    return hits, len(groups)


def _cut(text: str, width: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= width else text[: width - 1] + "…"


def format_table(hits: list[Hit], total: int, evidence: bool = False) -> str:
    if not hits:
        return "no matches"
    lines = [f"{total} matches, showing {len(hits)}", ""]
    for i, h in enumerate(hits, 1):
        lines.append(f"{i}. {_cut(h.title, 90)}  ({h.employer})")
        lines.append(
            f"   {_cut(h.where, 60)} | {h.work_mode} | de: {h.german} | fr: {h.french} "
            f"| {h.seniority} | seen {h.first_seen}"
        )
        flag = "  [check language first]" if UNCLEAR_LEVEL in (h.german, h.french) else ""
        lines.append(f"   {h.url}{flag}")
        if h.duplicates:
            places = "; ".join(d["location_raw"] or "location not stated" for d in h.duplicates)
            n = len(h.duplicates)
            label = f"+ {n} more location{'s' if n > 1 else ''}: {places}"
            lines.append("   " + _cut(label, 107))
        if evidence and h.evidence:
            for name, texts in h.evidence.items():
                if texts:
                    lines.append(f"   - {name}: " + " / ".join(_cut(t, 200) for t in texts))
        lines.append("")
    return "\n".join(lines).rstrip()


@dataclass
class Plan:
    """What `prepare` decided before any database access: the mode that will run, the query's
    embedding (for meaning and hybrid) and the warnings so far."""

    mode: str
    model: str  # embedding model id (posting_embeddings.model)
    alias: str  # embedding model alias (`mistral`, ...), for the warning texts
    vector: list[float] | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class RunResult:
    hits: list[Hit]
    total: int
    mode_used: str
    warnings: list[str]


def prepare(
    f: Filters,
    mode: str | None = None,
    alias: str = embed.DEFAULT_MODEL,
    embedder_factory: Callable[[str], embed.Embedder] | None = None,
    error_detail: bool = True,
    query_cache: embed.QueryCache | None = None,
) -> Plan:
    """Validate the filters, pick the mode and embed the query. No database access, so a web
    server does not hold a connection while it waits for the embedding API.

    `mode=None` is the default: hybrid, which becomes words when there is no query (the plain
    filter list) or when the query cannot be embedded (a warning says so). An explicit `meaning`
    or `hybrid` without a query raises `SearchError`; an explicit `meaning` that cannot be
    embedded raises `EmbedError`. `error_detail=False` leaves the exception text out of the
    warning (a web page must not show internals). `query_cache` (the web server passes one)
    keeps the vectors of queries already embedded.
    """
    try:
        model = embed.model_id(alias)
    except ValueError as error:
        raise SearchError(str(error)) from error
    f = replace(f, mode="words", model=model)
    f.validate()  # before any API call
    explicit = mode is not None
    mode = mode or "hybrid"
    if mode not in MODES:
        raise SearchError(f"unknown mode {mode!r}; allowed: {MODES}")
    plan = Plan("words", model, alias)
    if mode != "words" and not f.query.strip():
        if explicit:
            raise SearchError(f"--mode {mode} needs a query")
        return plan  # the default mode with no query is the plain filter list
    if mode == "words":
        return plan
    try:
        make = embedder_factory or embed.make_embedder  # looked up now: tests replace it
        if query_cache is None:
            plan.vector = make(alias).embed_query(f.query)
        else:
            plan.vector = query_cache.get_or_embed(
                model, f.query, lambda: make(alias).embed_query(f.query)
            )
    except Exception as error:  # no API key, missing optional group, network or API error
        if mode == "meaning":
            detail = f": {error!r}" if error_detail else ""
            if not error_detail:
                log.error("cannot embed the query: %r", error)
            raise EmbedError(f"cannot embed the query{detail}") from error
        # The CLI prints the warning itself; only a server (error_detail=False) logs at warning.
        log.log(
            logging.DEBUG if error_detail else logging.WARNING,
            "cannot embed the query (%r); searching by words only",
            error,
        )
        detail = f" ({error!r})" if error_detail else ""
        plan.warnings.append(f"cannot embed the query{detail}; searching by words only")
        return plan
    plan.mode = mode
    return plan


def execute(conn: psycopg.Connection, f: Filters, plan: Plan, evidence: bool = False) -> RunResult:
    """Run the search of a `Plan` and add the warnings about stale data."""
    warnings = list(plan.warnings)
    if plan.mode != "words":
        missing = stale_embedding_count(conn, plan.model)
        if missing:
            warnings.append(
                f"{missing} pool postings have no current {plan.alias} vector (new, "
                f"changed or not embedded); run `jobfacts embed --model {plan.alias}`"
            )
    stale = stale_relevance_count(conn)
    if stale:
        warnings.append(
            f"{stale} relevance rows are from an older version; run `jobfacts relevance`"
        )
    f = replace(f, mode=plan.mode, model=plan.model)
    hits, total = search(conn, f, evidence=evidence, query_vector=plan.vector)
    return RunResult(hits, total, plan.mode, warnings)


def run(
    conn: psycopg.Connection,
    f: Filters,
    mode: str | None = None,
    alias: str = embed.DEFAULT_MODEL,
    embedder_factory: Callable[[str], embed.Embedder] | None = None,
    evidence: bool = False,
    error_detail: bool = True,
    query_cache: embed.QueryCache | None = None,
) -> RunResult:
    """The whole search as the command line and the web API run it: `prepare` then `execute`.
    `f.mode` and `f.model` are ignored; `mode` and `alias` decide."""
    plan = prepare(f, mode, alias, embedder_factory, error_detail, query_cache)
    return execute(conn, f, plan, evidence)
