"""Embeddings of postings for search by meaning (`jobfacts embed`, `jobfacts search --mode`).

Three models behind one small interface: Mistral `mistral-embed` and OpenAI
`text-embedding-3-small` (APIs, both through the OpenAI client) and `Qwen/Qwen3-Embedding-0.6B`
(local, CPU, optional dependency group `embed-local`). Every model embeds the same kind of text
(`embedding_text`) cut to a per-model character budget. A stored vector remembers the hash of
that text, the budget and the posting's content hash at embed time, so a changed posting is
embedded again, an unchanged one never is, and a vector of an older posting text can be counted.
See docs/search.md.
"""

import hashlib
import os
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass

import psycopg
from dotenv import load_dotenv

from jobfacts.extract import jev

# Characters of title + description that each model sees by default (`--char-budget` overrides).
# The local Qwen3-Embedding-0.6B does only about 50-120 tokens a second on this 12-core CPU, so
# the 2504-posting pool takes about 90 minutes at 800 characters (about 150-200 tokens) and about
# 7 hours at 6000; the API models cost cents at 6000. The first comparison ran at 800 for all
# three; see docs/decisions.md (2026-11-08). The budget used is stored with every vector.
CHAR_BUDGETS = {"mistral": 6000, "openai": 6000, "qwen": 800}
UNKNOWN_MODEL_BUDGET = 6000  # for an embedder that is not one of the three (tests)
DEFAULT_MODEL = "mistral"  # alias used by `jobfacts daily` and the default search
MAX_COST_USD = 2.0
# Tokens are estimated as characters / 3 (German and French run a little denser than English)
# for the cost check made before any API call.
CHARS_PER_TOKEN_ESTIMATE = 3

QWEN_TASK = "Given a job search query, retrieve job postings that match the role the person wants"


def default_budget(alias: str) -> int:
    return CHAR_BUDGETS.get(alias, UNKNOWN_MODEL_BUDGET)


def embedding_text(title: str, description: str, budget: int) -> str:
    """Title and description, cut to `budget` characters."""
    return f"{title.strip()}\n\n{description.strip()}"[:budget].strip()


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def vector_literal(vector: list[float]) -> str:
    """The text form pgvector accepts: `[0.1,0.2,...]` (used with `%s::vector`)."""
    return "[" + ",".join(repr(float(x)) for x in vector) + "]"


def make_batches(texts: list[str], max_items: int, max_chars: int) -> list[list[int]]:
    """Indexes of `texts` in order, grouped so that no group has more than `max_items` texts or
    `max_chars` characters (a single longer text is its own group)."""
    batches: list[list[int]] = []
    current: list[int] = []
    chars = 0
    for i, text in enumerate(texts):
        if current and (len(current) >= max_items or chars + len(text) > max_chars):
            batches.append(current)
            current, chars = [], 0
        current.append(i)
        chars += len(text)
    if current:
        batches.append(current)
    return batches


@dataclass
class Embedded:
    vectors: list[list[float]]
    tokens: int  # tokens counted by the provider (or the local tokenizer)


class Embedder:
    """`model` is the id stored in the database; `alias` the name used on the command line."""

    alias = ""
    model = ""
    price_per_mtok = 0.0  # USD per million tokens
    chunk = 32  # documents per stored batch (a commit after each)

    def embed_documents(self, texts: list[str]) -> Embedded:
        raise NotImplementedError

    def embed_query(self, text: str) -> list[float]:
        raise NotImplementedError

    def cost(self, tokens: int) -> float:
        return tokens * self.price_per_mtok / 1e6


RETRY_ATTEMPTS = 6  # document batches: about 2 + 4 + 8 + 16 + 32 seconds of waiting at most
QUERY_ATTEMPTS = 2  # a search must not hang: one retry
MAX_WAIT_SECONDS = 120


def is_retryable(error: Exception) -> bool:
    """Rate limit (429), server errors (5xx), timeouts and connection errors; not a bad key
    or a bad request."""
    import openai

    if isinstance(error, openai.APITimeoutError | openai.APIConnectionError):
        return True
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and (status == 429 or status >= 500)


def retry_after(error: Exception) -> float | None:
    """Seconds named by a Retry-After header (the numeric form), if the error has one."""
    headers = getattr(getattr(error, "response", None), "headers", None) or {}
    try:
        return float(headers.get("retry-after"))
    except (TypeError, ValueError, AttributeError):
        return None


def with_retries(call: Callable, attempts: int, sleep: Callable[[float], None] = time.sleep):
    """Run `call()`; on a retryable error wait (Retry-After if the server gave one, else 2, 4,
    8 ... seconds, at most 120) and try again, up to `attempts` calls in all."""
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as error:
            if attempt == attempts or not is_retryable(error):
                raise
            wait = retry_after(error)
            if wait is None:
                wait = 2.0**attempt
            wait = min(max(wait, 0.0), MAX_WAIT_SECONDS)
            print(
                f"  embedding call failed ({type(error).__name__}); retry {attempt} in {wait:.0f}s",
                file=sys.stderr,
                flush=True,
            )
            sleep(wait)


class ApiEmbedder(Embedder):
    """An OpenAI-compatible embeddings endpoint; the API reports the tokens it counted."""

    def __init__(
        self, alias, model, price_per_mtok, client, max_items, max_chars, sleep=time.sleep
    ):
        self.alias, self.model, self.price_per_mtok = alias, model, price_per_mtok
        self.client, self.max_items, self.max_chars = client, max_items, max_chars
        self.chunk = 64
        self.sleep = sleep

    def _call(self, texts: list[str], attempts: int = RETRY_ATTEMPTS):
        def create():
            return self.client.embeddings.create(
                model=self.model, input=texts, encoding_format="float"
            )

        return with_retries(create, attempts, lambda s: self.sleep(s))

    def embed_documents(self, texts: list[str]) -> Embedded:
        vectors: list[list[float] | None] = [None] * len(texts)
        tokens = 0
        for batch in make_batches(texts, self.max_items, self.max_chars):
            response = self._call([texts[i] for i in batch])
            ordered = sorted(response.data, key=lambda d: d.index)
            for i, item in zip(batch, ordered, strict=True):
                vectors[i] = list(item.embedding)
            tokens += response.usage.prompt_tokens
        return Embedded(vectors, tokens)  # type: ignore[arg-type]

    def embed_query(self, text: str) -> list[float]:
        return list(self._call([text], QUERY_ATTEMPTS).data[0].embedding)


def mistral_embedder() -> ApiEmbedder:
    from openai import OpenAI

    load_dotenv()
    client = OpenAI(
        api_key=os.environ["MISTRAL_API_KEY"],
        base_url="https://api.mistral.ai/v1",
        timeout=120,
        max_retries=1,  # retries with a longer backoff are made by `with_retries`
    )
    # List price read in 2026-11: $0.10 per million tokens. Mistral limits the tokens of one
    # request (16384), so a request carries at most 16 texts and 20000 characters.
    return ApiEmbedder("mistral", "mistral-embed", 0.10, client, 16, 20000)


def openai_embedder() -> ApiEmbedder:
    from openai import OpenAI

    load_dotenv()
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"], timeout=120, max_retries=1)
    # List price read in 2026-11: $0.02 per million tokens.
    return ApiEmbedder("openai", "text-embedding-3-small", 0.02, client, 128, 200000)


class QwenEmbedder(Embedder):
    """Qwen3-Embedding-0.6B on the CPU through sentence-transformers (imported lazily: the
    server and CI do not install torch). Queries get the instruction line of the model card;
    documents do not."""

    alias = "qwen"
    model = "Qwen/Qwen3-Embedding-0.6B"
    price_per_mtok = 0.0
    chunk = 16

    def __init__(self, max_seq_length: int = 2048, batch_size: int = 8, threads: int = 8):
        self.max_seq_length, self.batch_size, self.threads = max_seq_length, batch_size, threads
        self._model = None

    def _load(self):
        if self._model is None:
            try:
                import torch
                from sentence_transformers import SentenceTransformer
            except ImportError as error:
                raise RuntimeError(
                    "the qwen model needs the optional group: uv run --group embed-local ..."
                ) from error
            # 8 threads on 12 cores: with all cores torch was 40% slower (the desktop also
            # needs some); float32: the default (bfloat16 on this CPU) is 2-4 times slower
            self._model = SentenceTransformer(
                self.model, device="cpu", model_kwargs={"dtype": torch.float32}
            )
            torch.set_num_threads(self.threads)
            self._model.max_seq_length = self.max_seq_length
        return self._model

    @staticmethod
    def query_text(query: str) -> str:
        return f"Instruct: {QWEN_TASK}\nQuery: {query}"

    def embed_documents(self, texts: list[str]) -> Embedded:
        model = self._load()
        vectors = model.encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, show_progress_bar=False
        )
        ids = model.tokenizer(texts, truncation=True, max_length=self.max_seq_length)["input_ids"]
        return Embedded([v.tolist() for v in vectors], sum(len(i) for i in ids))

    def embed_query(self, text: str) -> list[float]:
        model = self._load()
        return model.encode([self.query_text(text)], normalize_embeddings=True)[0].tolist()


FACTORIES = {"mistral": mistral_embedder, "openai": openai_embedder, "qwen": QwenEmbedder}
MODEL_IDS = {
    "mistral": "mistral-embed",
    "openai": "text-embedding-3-small",
    "qwen": QwenEmbedder.model,
}


def make_embedder(alias: str) -> Embedder:
    if alias not in FACTORIES:
        raise ValueError(f"unknown embedding model {alias!r}; allowed: {sorted(FACTORIES)}")
    return FACTORIES[alias]()


def model_id(alias: str) -> str:
    if alias not in MODEL_IDS:
        raise ValueError(f"unknown embedding model {alias!r}; allowed: {sorted(MODEL_IDS)}")
    return MODEL_IDS[alias]


# ---------- the posting pool and the stored vectors ----------

# Postings worth embedding: board postings (open or closed: closed ones are needed for the
# evaluation) whose relevance score of the current Jev model is at or above the threshold.
POOL_FROM = """
FROM postings p
JOIN relevance rel ON rel.posting_id = p.id AND rel.model = %(rel_model)s
    AND rel.probability >= %(threshold)s
"""
POOL_WHERE = "WHERE p.origin = 'board'"


def pool_params() -> dict:
    return {"rel_model": jev.MODEL, "threshold": jev.RELEVANCE_THRESHOLD}


def pool_size(conn: psycopg.Connection) -> int:
    return conn.execute(f"SELECT count(*) {POOL_FROM} {POOL_WHERE}", pool_params()).fetchone()[0]


@dataclass
class Plan:
    todo: list[tuple[int, str, str, str]]  # (posting id, text, text hash, content hash)
    unchanged: int  # pool postings whose stored vector is for this text
    refresh: list[tuple[int, str]]  # unchanged text, but the stored content hash is older


def pending(conn: psycopg.Connection, model: str, budget: int, limit: int | None = None) -> Plan:
    """The pool postings without a stored vector for this text at this budget (`todo`, ordered by
    id, at most `limit`), those that have one (`unchanged`), and those whose posting changed
    in a way that left the embedded text the same (`refresh`: only the stored content hash and
    budget are brought up to date, no embedding is made)."""
    rows = conn.execute(
        "SELECT p.id, p.title, p.description_text, p.content_hash, "
        "e.text_hash, e.content_hash, e.char_budget "
        f"{POOL_FROM} LEFT JOIN posting_embeddings e ON e.posting_id = p.id "
        f"AND e.model = %(model)s {POOL_WHERE} ORDER BY p.id",
        {**pool_params(), "model": model},
    ).fetchall()
    todo, unchanged, refresh = [], 0, []
    for pid, title, description, content, stored, stored_content, stored_budget in rows:
        text = embedding_text(title, description, budget)
        digest = text_hash(text)
        if digest != stored:
            todo.append((pid, text, digest, content))
            continue
        unchanged += 1
        if stored_content != content or stored_budget != budget:
            refresh.append((pid, content))
    return Plan(todo if limit is None else todo[:limit], unchanged, refresh)


def save_embeddings(
    conn: psycopg.Connection,
    model: str,
    items: list[tuple[int, str, str, list[float]]],
    budget: int,
) -> None:
    """Insert or replace the vectors of (posting id, text hash, content hash, vector)."""
    with conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO posting_embeddings "
            "(posting_id, model, text_hash, char_budget, content_hash, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s::vector) "
            "ON CONFLICT (posting_id, model) DO UPDATE SET text_hash = EXCLUDED.text_hash, "
            "char_budget = EXCLUDED.char_budget, content_hash = EXCLUDED.content_hash, "
            "embedding = EXCLUDED.embedding, created_at = now()",
            [
                (pid, model, digest, budget, content, vector_literal(vec))
                for pid, digest, content, vec in items
            ],
        )


def budgets(conn: psycopg.Connection, model: str) -> dict[int, int]:
    """{character budget: number of pool postings} of the stored vectors of this model id."""
    rows = conn.execute(
        f"SELECT e.char_budget, count(*) {POOL_FROM} "
        "JOIN posting_embeddings e ON e.posting_id = p.id AND e.model = %(model)s "
        f"{POOL_WHERE} GROUP BY 1 ORDER BY 1",
        {**pool_params(), "model": model},
    ).fetchall()
    return dict(rows)


class CostError(RuntimeError):
    """The estimated cost is above the limit."""


@dataclass
class EmbedSummary:
    model: str
    budget: int
    embedded: int
    skipped: int
    tokens: int
    cost: float
    seconds: float


def estimate_cost(embedder: Embedder, todo: list[tuple[int, str, str, str]]) -> float:
    return embedder.cost(sum(len(t) for _, t, _, _ in todo) // CHARS_PER_TOKEN_ESTIMATE + 1)


def run_embed(
    conn: psycopg.Connection,
    embedder: Embedder,
    limit: int | None = None,
    budget: int | None = None,
    max_cost: float = MAX_COST_USD,
    progress=None,
) -> EmbedSummary:
    """Embed the pool postings that have no vector for their current text, committing after
    every chunk, so an interrupted run loses at most one chunk and a rerun continues. `budget`
    defaults to the model's own (`CHAR_BUDGETS`); the vector stores it and the posting's
    content hash."""
    budget = default_budget(embedder.alias) if budget is None else budget
    plan = pending(conn, embedder.model, budget, limit)
    todo, skipped = plan.todo, plan.unchanged
    if plan.refresh:
        with conn.cursor() as cur:
            cur.executemany(
                "UPDATE posting_embeddings SET content_hash = %s, char_budget = %s "
                "WHERE posting_id = %s AND model = %s",
                [(content, budget, pid, embedder.model) for pid, content in plan.refresh],
            )
        conn.commit()
    estimate = estimate_cost(embedder, todo)
    if estimate > max_cost:
        raise CostError(
            f"estimated cost ${estimate:.2f} for {len(todo)} postings is above ${max_cost:.2f}"
        )
    started = time.monotonic()
    tokens = 0
    for start in range(0, len(todo), embedder.chunk):
        part = todo[start : start + embedder.chunk]
        result = embedder.embed_documents([text for _, text, _, _ in part])
        tokens += result.tokens
        save_embeddings(
            conn,
            embedder.model,
            [
                (pid, digest, content, vec)
                for (pid, _, digest, content), vec in zip(part, result.vectors, strict=True)
            ],
            budget,
        )
        conn.commit()
        if progress:
            progress(start + len(part), len(todo), time.monotonic() - started)
    return EmbedSummary(
        embedder.model,
        budget,
        len(todo),
        skipped,
        tokens,
        embedder.cost(tokens),
        time.monotonic() - started,
    )


def print_progress(done: int, total: int, seconds: float) -> None:
    print(f"  {done}/{total} embedded, {seconds:.0f}s", file=sys.stderr, flush=True)
