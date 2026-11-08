"""Which embedding model finds the jobs the owner wanted? (`jobfacts meaning-eval`)

The jobs the owner applied to are the wanted jobs (matched to board postings with the code of
`search_eval`). Each search phrase is run as the owner would run it, with three kinds of ranking
over the whole posting pool, with no eligibility or canton filter (this measures ranking only):
`words` (the current full-text rank), `meaning:<model>` (cosine similarity of embeddings) and
`hybrid:<model>` (reciprocal rank fusion of the two, best 200 of each). Rankings are of jobs:
postings of one employer with the same normalised title count once, as in `jobfacts search`.

For every wanted job the best rank over all phrases is kept; the metrics are recall@k (the share
of wanted jobs within rank k for at least one phrase) and the mean reciprocal rank (MRR) of
that best rank. The metric functions are pure; see docs/search.md.
"""

import sys
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from jobfacts import embed
from jobfacts import search_eval as se
from jobfacts.grouping import title_key
from jobfacts.search import RRF_K, TOP_PER_LIST, rrf, stale_embedding_count
from jobfacts.stats import wilson
from jobfacts.testset import PRIVATE_DIR

CUTOFFS = (10, 20, 50)
WITHIN_SCOPE = ("found", "filtered")  # the search outcomes of a Swiss or remote posting


# ---------- phrases ----------


def load_phrases(path: Path) -> list[str]:
    """One query per line; blank lines and lines starting with # are skipped."""
    lines = (line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    return [line for line in lines if line and not line.startswith("#")]


# ---------- pure metrics ----------


def group_ranks(order: Sequence[int], key_of: Mapping[int, tuple]) -> dict[int, int]:
    """Rank (from 1) of the job that each posting of `order` belongs to. `order` is posting ids
    best first; `key_of` gives the job key of a posting (employer, normalised title). A job
    takes the position of its first posting, so duplicates do not push other jobs down."""
    job_rank: dict[tuple, int] = {}
    ranks: dict[int, int] = {}
    for posting_id in order:
        key = key_of[posting_id]
        if key not in job_rank:
            job_rank[key] = len(job_rank) + 1
        ranks[posting_id] = job_rank[key]
    return ranks


def best_rank(
    ranks_per_phrase: Iterable[Mapping[int, int]], posting_ids: Iterable[int]
) -> int | None:
    """The best rank of any of the postings in any phrase's ranking; None if none is ranked."""
    ids = list(posting_ids)
    found = [r[i] for r in ranks_per_phrase for i in ids if i in r]
    return min(found) if found else None


def recall_at(best: Sequence[int | None], k: int) -> tuple[int, int, float, float]:
    """(wanted jobs within rank k, wanted jobs, Wilson 95% low, high)."""
    hits = sum(1 for r in best if r is not None and r <= k)
    return (hits, len(best), *wilson(hits, len(best)))


def mrr(best: Sequence[int | None]) -> float:
    """Mean of 1 / best rank; a job that no phrase ranks counts 0."""
    return sum(1 / r for r in best if r) / len(best) if best else 0.0


def top_counts(
    ranks_per_phrase: Sequence[Mapping[int, int]],
    jobs: Sequence[Sequence[int]],
    k: int = 20,
) -> list[int]:
    """Per phrase: how many wanted jobs (lists of posting ids) it puts in its top k."""
    return [
        sum(1 for ids in jobs if (b := best_rank([r], ids)) is not None and b <= k)
        for r in ranks_per_phrase
    ]


# ---------- wanted jobs ----------


@dataclass
class Wanted:
    app: se.Application
    posting_ids: list[int]  # the matched postings that are in the pool
    outcome: str  # search_eval outcome of the application
    title: str  # title of the closest matched posting

    @property
    def within_scope(self) -> bool:
        return self.outcome in WITHIN_SCOPE


def find_wanted(
    conn: psycopg.Connection, apps: Iterable[se.Application], pool: set[int]
) -> tuple[list[Wanted], int]:
    """Wanted jobs with at least one matched posting in the pool, and the number of
    applications that matched a posting at all."""
    data = se.load_board_data(conn)
    cache: dict[int, se.PostingState] = {}

    def state_of(posting_id: int) -> se.PostingState:
        if posting_id not in cache:
            cache[posting_id] = se.posting_state(conn, posting_id)
        return cache[posting_id]

    wanted, matched = [], 0
    for app in apps:
        match = se.match_application(app, data)
        if not match.postings:
            continue
        matched += 1
        in_pool = [p for p in match.postings if p.id in pool]
        if in_pool:
            outcome = se.classify(app, match, state_of).outcome
            wanted.append(Wanted(app, [p.id for p in in_pool], outcome, in_pool[0].title))
    return wanted, matched


# ---------- rankings over the pool ----------


@dataclass
class Pool:
    ids: list[int]
    key_of: dict[int, tuple]  # posting id -> (employer id, title_key)
    titles: dict[int, str]


def load_pool(conn: psycopg.Connection) -> Pool:
    rows = conn.execute(
        f"SELECT p.id, p.employer_id, p.title {embed.POOL_FROM} {embed.POOL_WHERE} ORDER BY p.id",
        embed.pool_params(),
    ).fetchall()
    return Pool(
        [r[0] for r in rows],
        {r[0]: (r[1], title_key(r[2])) for r in rows},
        {r[0]: r[2] for r in rows},
    )


def words_order(conn: psycopg.Connection, phrase: str) -> list[int]:
    """Pool postings that match the phrase as `jobfacts search` does, best first."""
    tsquery = "websearch_to_tsquery('simple', %(q)s)"
    sql = (
        f"SELECT p.id {embed.POOL_FROM} {embed.POOL_WHERE} AND p.search_tsv @@ {tsquery} "
        f"ORDER BY ts_rank(p.search_tsv, {tsquery}) DESC, p.first_seen_at DESC, p.id"
    )
    return [r[0] for r in conn.execute(sql, {**embed.pool_params(), "q": phrase})]


def meaning_order(conn: psycopg.Connection, model: str, vector: list[float]) -> list[int]:
    """Pool postings with a vector for `model`, closest to the query vector first."""
    sql = (
        f"SELECT p.id {embed.POOL_FROM} "
        "JOIN posting_embeddings emb ON emb.posting_id = p.id AND emb.model = %(model)s "
        f"{embed.POOL_WHERE} ORDER BY emb.embedding <=> %(qvec)s::vector, p.id"
    )
    params = {**embed.pool_params(), "model": model, "qvec": embed.vector_literal(vector)}
    return [r[0] for r in conn.execute(sql, params)]


def hybrid_order(words: Sequence[int], meaning: Sequence[int]) -> list[int]:
    """Reciprocal rank fusion (k = 60) of the best 200 postings of each list."""
    return [i for i, _ in rrf([list(words[:TOP_PER_LIST]), list(meaning[:TOP_PER_LIST])], RRF_K)]


def coverage(conn: psycopg.Connection, model: str) -> int:
    """Pool postings that have a vector for `model`."""
    sql = (
        f"SELECT count(*) {embed.POOL_FROM} "
        "JOIN posting_embeddings emb ON emb.posting_id = p.id AND emb.model = %(model)s "
        f"{embed.POOL_WHERE}"
    )
    return conn.execute(sql, {**embed.pool_params(), "model": model}).fetchone()[0]


def vector_warnings(
    models: Mapping[str, str],
    budgets: Mapping[str, Mapping[int, int]],
    stale: Mapping[str, int],
) -> list[str]:
    """One line per problem: a model whose vectors were made with different character budgets,
    or that has stale (older posting text) or missing vectors in the pool. `models` maps alias to
    model id."""
    lines = []
    for alias, model in models.items():
        if len(budgets.get(alias, {})) > 1:
            parts = ", ".join(f"{b} characters: {n}" for b, n in budgets[alias].items())
            lines.append(f"{model} has mixed character budgets ({parts})")
        if stale.get(alias):
            lines.append(
                f"{model} has {stale[alias]} stale or missing vectors in the pool "
                f"(run `jobfacts embed --model {alias}`)"
            )
    return lines


@dataclass
class MethodResult:
    name: str
    ranks: list[dict[int, int]]  # per phrase: posting id -> job rank
    best: list[int | None]  # per wanted job


def run_methods(
    conn: psycopg.Connection,
    phrases: Sequence[str],
    pool: Pool,
    wanted: Sequence[Wanted],
    embedders: Mapping[str, embed.Embedder],
) -> list[MethodResult]:
    word_orders = [words_order(conn, p) for p in phrases]
    orders: dict[str, list[list[int]]] = {"words": word_orders}
    for alias, embedder in embedders.items():
        vectors = [embedder.embed_query(p) for p in phrases]
        meaning = [meaning_order(conn, embedder.model, v) for v in vectors]
        orders[f"meaning:{alias}"] = meaning
        orders[f"hybrid:{alias}"] = [
            hybrid_order(w, m) for w, m in zip(word_orders, meaning, strict=True)
        ]
    results = []
    for name, per_phrase in orders.items():
        ranks = [group_ranks(order, pool.key_of) for order in per_phrase]
        results.append(MethodResult(name, ranks, [best_rank(ranks, w.posting_ids) for w in wanted]))
    return results


# ---------- report ----------


def _pct(k: int, n: int) -> str:
    return f"{k}/{n} ({100 * k / n:.0f}%)" if n else "n/a"


def metric_rows(results: Sequence[MethodResult], select: Callable[[int], bool]) -> list[list]:
    rows = []
    for r in results:
        best = [b for i, b in enumerate(r.best) if select(i)]
        k20 = recall_at(best, 20)
        rows.append(
            [
                r.name,
                _pct(*recall_at(best, 10)[:2]),
                f"{_pct(*k20[:2])}, {100 * k20[2]:.0f}-{100 * k20[3]:.0f}%",
                _pct(*recall_at(best, 50)[:2]),
                f"{mrr(best):.3f}",
            ]
        )
    return rows


def best_method(results: Sequence[MethodResult]) -> MethodResult:
    """Highest recall@20 over all wanted jobs, ties broken by MRR."""
    return max(results, key=lambda r: (recall_at(r.best, 20)[0], mrr(r.best)))


def _table(headers: list[str], rows: list[list]) -> list[str]:
    return se._table(headers, rows)


def render_report(
    today: date,
    phrases: Sequence[str],
    pool_size: int,
    covered: Mapping[str, int],
    budgets: Mapping[str, Mapping[int, int]],
    warnings: Sequence[str],
    applications: int,
    matched: int,
    wanted: Sequence[Wanted],
    results: Sequence[MethodResult],
    titles: Mapping[int, str],
) -> str:
    scope = [i for i, w in enumerate(wanted) if w.within_scope]
    in_scope = set(scope)
    lines = [
        f"# Search by meaning: which embedding model? ({today.isoformat()})",
        "",
        f"- Pool: {pool_size} board postings (open or closed) with a relevance score of the "
        "current Jev model at or above the threshold. Embedded text: title + description, "
        "cut to a character budget per model, as stored with the vectors: "
        + "; ".join(
            f"{embed.MODEL_IDS.get(a, a)} "
            + (" and ".join(f"{b} ({n} vectors)" for b, n in bud.items()) or "no vectors")
            for a, bud in budgets.items()
        )
        + ".",
        "- Vectors in the pool: "
        + ", ".join(f"{embed.MODEL_IDS.get(a, a)} {n}" for a, n in covered.items())
        + ".",
        *[f"- WARNING: {w}." for w in warnings],
        f"- Phrases: {len(phrases)} (each ranking below is the best rank over all phrases).",
        f"- Dated applications: {applications}; matched to a board posting: {matched}; "
        f"wanted jobs whose posting is in the pool: {len(wanted)}; within scope (outcome "
        f"found or filtered): {len(scope)}.",
        "- Rankings are over the whole pool, with no eligibility or canton filter, of jobs "
        "(postings of one employer with the same normalised title count once). `words` is the "
        "current full-text rank; `hybrid` fuses the best 200 of `words` and of `meaning` with "
        f"reciprocal rank fusion (k = {RRF_K}).",
        "- recall@k: wanted jobs ranked within k by at least one phrase. MRR: mean of "
        "1 / best rank (0 if no phrase ranks the job). The interval is Wilson 95% for recall@20.",
        "",
    ]
    headers = ["Method", "recall@10", "recall@20 (Wilson 95%)", "recall@50", "MRR"]
    lines += [f"## All wanted jobs (n = {len(wanted)})", ""]
    lines += _table(headers, metric_rows(results, lambda i: True))
    lines += [f"## Within scope (n = {len(scope)})", ""]
    lines += _table(headers, metric_rows(results, lambda i: i in in_scope))

    jobs = [w.posting_ids for w in wanted]
    lines += ["## Wanted jobs in each phrase's top 20 (all wanted jobs)", ""]
    counts = [top_counts(r.ranks, jobs) for r in results]
    lines += _table(
        ["Phrase", *[r.name for r in results]],
        [[p, *[c[i] for c in counts]] for i, p in enumerate(phrases)],
    )
    top = best_method(results)
    scope_jobs = [jobs[i] for i in scope]
    lines += [
        f"## Best method: {top.name}",
        "",
        "Highest recall@20 over all wanted jobs (ties by MRR). Wanted jobs in each phrase's top "
        "20:",
        "",
    ]
    lines += _table(
        ["Phrase", "All wanted", "Within scope"],
        [
            [p, a, b]
            for p, a, b in zip(
                phrases, top_counts(top.ranks, jobs), top_counts(top.ranks, scope_jobs), strict=True
            )
        ],
    )

    lines += ["## Every wanted job: best rank per method", ""]
    rows = []
    for i, w in enumerate(wanted):
        rows.append(
            [
                w.app.key,
                titles.get(w.posting_ids[0], w.title),
                w.outcome,
                *[r.best[i] if r.best[i] is not None else "-" for r in results],
            ]
        )
    lines += _table(["Application", "Posting title", "Outcome", *[r.name for r in results]], rows)
    missed = [
        w for i, w in enumerate(wanted) if all(r.best[i] is None or r.best[i] > 50 for r in results)
    ]
    lines += [f"## Wanted jobs that no method ranks within 50 ({len(missed)})", ""]
    lines += [f"- {w.app.key}: {w.title} ({w.outcome})" for w in missed] or ["None."]
    outcomes = Counter(w.outcome for w in wanted)
    lines += [
        "",
        "Outcomes of the wanted jobs: " + ", ".join(f"{k} {v}" for k, v in outcomes.items()),
    ]
    return "\n".join(lines)


def default_output(today: date) -> Path:
    return PRIVATE_DIR / "reports" / f"meaning_eval_{today.isoformat()}.md"


@dataclass
class EvalResult:
    path: Path
    report: str
    results: list[MethodResult]
    wanted: list[Wanted]


def run(
    conn: psycopg.Connection,
    phrases_file: Path,
    applications: Path,
    dates_file: Path,
    models: Sequence[str],
    out: Path | None = None,
    today: date | None = None,
    embedders: Mapping[str, embed.Embedder] | None = None,
) -> EvalResult:
    import json

    today = today or date.today()
    phrases = load_phrases(phrases_file)
    if not phrases:
        raise ValueError(f"no phrases in {phrases_file}")
    dates = json.loads(dates_file.read_text(encoding="utf-8"))
    apps = [a for a in se.load_applications(applications, dates) if a.date is not None]
    pool = load_pool(conn)
    wanted, matched = find_wanted(conn, apps, set(pool.ids))
    embedders = embedders or {m: embed.make_embedder(m) for m in models}
    covered = {a: coverage(conn, e.model) for a, e in embedders.items()}
    used = {a: embed.budgets(conn, e.model) for a, e in embedders.items()}
    stale = {a: stale_embedding_count(conn, e.model) for a, e in embedders.items()}
    warnings = vector_warnings({a: e.model for a, e in embedders.items()}, used, stale)
    for line in warnings:
        print(f"warning: {line}", file=sys.stderr)
    results = run_methods(conn, phrases, pool, wanted, embedders)
    report = render_report(
        today,
        phrases,
        len(pool.ids),
        covered,
        used,
        warnings,
        len(apps),
        matched,
        wanted,
        results,
        pool.titles,
    )
    path = out or default_output(today)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report + "\n", encoding="utf-8")
    return EvalResult(path, report, results, wanted)
