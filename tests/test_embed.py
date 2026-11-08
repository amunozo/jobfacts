from types import SimpleNamespace

import pytest

from jobfacts import embed, search
from jobfacts.embed import ApiEmbedder, CostError, Embedded, Embedder
from jobfacts.extract import jev, store

# ---------- pure helpers ----------


def test_embedding_text_joins_title_and_description_and_cuts():
    assert embed.embedding_text(" ML Engineer ", " We build models. ", 6000) == (
        "ML Engineer\n\nWe build models."
    )
    text = embed.embedding_text("T", "x" * 100, budget=20)
    assert len(text) <= 20 and text.startswith("T\n\nxxx")


def test_text_hash_follows_the_text():
    assert embed.text_hash("a") == embed.text_hash("a") != embed.text_hash("b")


def test_vector_literal():
    assert embed.vector_literal([0.5, 1, -2.25]) == "[0.5,1.0,-2.25]"


def test_make_batches_by_items_and_characters():
    texts = ["a" * 4, "b" * 4, "c" * 4, "d" * 100, "e" * 4]
    assert embed.make_batches(texts, max_items=2, max_chars=1000) == [[0, 1], [2, 3], [4]]
    assert embed.make_batches(texts, max_items=10, max_chars=10) == [[0, 1], [2], [3], [4]]
    assert embed.make_batches([], 2, 10) == []


def test_qwen_instruction_goes_on_queries_only():
    q = embed.QwenEmbedder.query_text("data scientist")
    assert q.startswith("Instruct: ") and q.endswith("\nQuery: data scientist")


def test_unknown_model_names_are_rejected():
    with pytest.raises(ValueError):
        embed.make_embedder("nope")
    with pytest.raises(ValueError):
        embed.model_id("nope")
    assert embed.model_id("mistral") == "mistral-embed"


# ---------- API embedder with a fake client (no network) ----------


class FakeClient:
    def __init__(self):
        self.calls = []
        self.embeddings = SimpleNamespace(create=self.create)

    def create(self, model, input, encoding_format):
        self.calls.append(list(input))
        # returned out of order on purpose: the embedder must use `index`
        data = [
            SimpleNamespace(index=i, embedding=[float(len(t)), 1.0]) for i, t in enumerate(input)
        ]
        return SimpleNamespace(
            data=data[::-1], usage=SimpleNamespace(prompt_tokens=sum(len(t) for t in input))
        )


def test_api_embedder_batches_orders_and_counts_tokens():
    client = FakeClient()
    emb = ApiEmbedder("x", "x-model", 1.0, client, max_items=2, max_chars=1000)
    out = emb.embed_documents(["a", "bb", "ccc"])
    assert [len(c) for c in client.calls] == [2, 1]
    assert out.vectors == [[1.0, 1.0], [2.0, 1.0], [3.0, 1.0]]
    assert out.tokens == 6
    assert emb.embed_query("dddd") == [4.0, 1.0]
    assert emb.cost(2_000_000) == 2.0


# ---------- storage with a fake embedder ----------


class FakeEmbedder(Embedder):
    alias, model, price_per_mtok, chunk = "fake", "fake-model", 0.0, 2

    def __init__(self, price=0.0):
        self.price_per_mtok = price
        self.seen: list[str] = []

    def embed_documents(self, texts):
        self.seen += texts
        return Embedded([[float(len(t)), 1.0, 0.0] for t in texts], tokens=len(texts) * 10)

    def embed_query(self, text):
        return [1.0, 0.0, 0.0]


def _posting(conn, emp, n, title="ML Engineer", desc="We build models.", prob=0.9, **kw):
    origin = kw.get("origin", "board")
    pid = conn.execute(
        "INSERT INTO postings (employer_id, source_job_id, url, title, description_text, "
        "content_hash, origin, closed_at) VALUES (%s,%s,%s,%s,%s,'h',%s, "
        "CASE WHEN %s THEN now() END) RETURNING id",
        (emp, str(n), f"https://x/{n}", title, desc, origin, kw.get("closed", False)),
    ).fetchone()[0]
    store.save_relevance(conn, pid, jev.MODEL, prob, jev.RELEVANCE_THRESHOLD)
    return pid


@pytest.fixture
def pool(conn):
    emp = conn.execute(
        "INSERT INTO employers (name, ats, board_id) VALUES ('Acme','greenhouse','a') RETURNING id"
    ).fetchone()[0]
    ids = {
        "open": _posting(conn, emp, 1),
        "closed": _posting(conn, emp, 2, closed=True),  # closed postings are in the pool
        "low": _posting(conn, emp, 3, prob=jev.RELEVANCE_THRESHOLD - 0.05),
        "app": _posting(conn, emp, 4, origin="application"),
    }
    conn.commit()
    return emp, ids


def _stored(conn, model="fake-model"):
    return dict(
        conn.execute(
            "SELECT posting_id, text_hash FROM posting_embeddings WHERE model = %s", (model,)
        ).fetchall()
    )


@pytest.mark.db
def test_migration_creates_the_embedding_table(conn):
    assert conn.execute("SELECT count(*) FROM posting_embeddings").fetchone()[0] == 0
    ext = conn.execute("SELECT 1 FROM pg_extension WHERE extname = 'vector'").fetchone()
    assert ext is not None


@pytest.mark.db
def test_pool_is_board_postings_at_or_above_the_threshold_open_or_closed(conn, pool):
    _, ids = pool
    assert embed.pool_size(conn) == 2
    plan = embed.pending(conn, "fake-model", 6000)
    assert {t[0] for t in plan.todo} == {ids["open"], ids["closed"]} and plan.unchanged == 0


@pytest.mark.db
def test_embedding_is_idempotent_and_redone_when_the_text_changes(conn, pool):
    _, ids = pool
    fake = FakeEmbedder()
    first = embed.run_embed(conn, fake)
    assert (first.embedded, first.skipped, first.tokens) == (2, 0, 20)
    assert set(_stored(conn)) == {ids["open"], ids["closed"]}

    again = embed.run_embed(conn, fake)
    assert (again.embedded, again.skipped) == (0, 2) and len(fake.seen) == 2

    conn.execute("UPDATE postings SET description_text = 'New text.' WHERE id = %s", (ids["open"],))
    before = _stored(conn)
    third = embed.run_embed(conn, fake)
    after = _stored(conn)
    assert (third.embedded, third.skipped) == (1, 1)
    assert after[ids["open"]] != before[ids["open"]]
    assert after[ids["closed"]] == before[ids["closed"]] and len(after) == 2


@pytest.mark.db
def test_models_are_stored_side_by_side_and_limit_applies(conn, pool):
    embed.run_embed(conn, FakeEmbedder(), limit=1)
    assert len(_stored(conn)) == 1
    assert _stored(conn, "other-model") == {}
    other = FakeEmbedder()
    other.model = "other-model"
    embed.run_embed(conn, other)
    assert len(_stored(conn, "other-model")) == 2


@pytest.mark.db
def test_a_cost_above_the_limit_stops_before_any_call(conn, pool):
    fake = FakeEmbedder(price=1e9)
    with pytest.raises(CostError):
        embed.run_embed(conn, fake, max_cost=2.0)
    assert fake.seen == [] and _stored(conn) == {}


@pytest.mark.db
def test_save_embeddings_replaces_the_vector(conn, pool):
    _, ids = pool
    embed.save_embeddings(conn, "m", [(ids["open"], "h1", "c1", [1.0, 2.0])], 800)
    embed.save_embeddings(conn, "m", [(ids["open"], "h2", "c2", [3.0, 4.0, 5.0])], 6000)
    row = conn.execute(
        "SELECT text_hash, content_hash, char_budget, vector_dims(embedding) "
        "FROM posting_embeddings WHERE model = 'm'"
    ).fetchone()
    assert row == ("h2", "c2", 6000, 3)


@pytest.mark.db
def test_limit_zero_embeds_nothing(conn, pool):
    fake = FakeEmbedder()
    summary = embed.run_embed(conn, fake, limit=0)
    assert summary.embedded == 0 and fake.seen == [] and _stored(conn) == {}


# ---------- budgets, content hashes, stale vectors ----------


def test_every_model_has_a_default_budget():
    assert embed.CHAR_BUDGETS == {"mistral": 6000, "openai": 6000, "qwen": 800}
    assert set(embed.CHAR_BUDGETS) == set(embed.FACTORIES)
    assert embed.default_budget("qwen") == 800 and embed.default_budget("mistral") == 6000
    assert embed.DEFAULT_MODEL in embed.CHAR_BUDGETS


def _rows(conn, model="fake-model"):
    return {
        r[0]: r[1:]
        for r in conn.execute(
            "SELECT posting_id, char_budget, content_hash FROM posting_embeddings WHERE model = %s",
            (model,),
        )
    }


@pytest.mark.db
def test_budget_and_content_hash_are_recorded(conn, pool):
    _, ids = pool
    long_text = "word " * 400  # 2000 characters
    conn.execute("UPDATE postings SET description_text = %s, content_hash = 'c1'", (long_text,))
    fake = FakeEmbedder()
    summary = embed.run_embed(conn, fake)  # unknown model alias: the 6000 default
    assert summary.budget == 6000 and max(len(t) for t in fake.seen) > 1500
    assert _rows(conn) == {ids["open"]: (6000, "c1"), ids["closed"]: (6000, "c1")}
    assert embed.budgets(conn, "fake-model") == {6000: 2}

    # a different budget cuts a different text: everything is embedded again and recorded
    again = embed.run_embed(conn, fake, budget=300)
    assert (again.embedded, again.skipped) == (2, 0)
    assert _rows(conn)[ids["open"]] == (300, "c1") and embed.budgets(conn, "fake-model") == {300: 2}


@pytest.mark.db
def test_the_budget_of_a_known_model_is_its_own(conn, pool):
    fake = FakeEmbedder()
    fake.alias = "qwen"
    assert embed.run_embed(conn, fake).budget == 800
    fake.alias = "mistral"
    assert embed.run_embed(conn, fake, budget=500).budget == 500  # --char-budget overrides


@pytest.mark.db
def test_stale_and_missing_vectors_are_counted_and_fixed_by_embed(conn, pool):
    _, ids = pool
    fake = FakeEmbedder()
    assert search.stale_embedding_count(conn, "fake-model") == 2  # nothing embedded yet
    embed.run_embed(conn, fake, limit=1)
    assert search.stale_embedding_count(conn, "fake-model") == 1  # one missing
    embed.run_embed(conn, fake)
    assert search.stale_embedding_count(conn, "fake-model") == 0

    conn.execute(
        "UPDATE postings SET description_text = 'New text.', content_hash = 'c2' WHERE id = %s",
        (ids["open"],),
    )
    assert search.stale_embedding_count(conn, "fake-model") == 1
    summary = embed.run_embed(conn, fake)
    assert summary.embedded == 1
    assert _rows(conn)[ids["open"]] == (6000, "c2")
    assert search.stale_embedding_count(conn, "fake-model") == 0


@pytest.mark.db
def test_a_changed_posting_with_the_same_embedded_text_is_refreshed_without_a_call(conn, pool):
    _, ids = pool
    fake = FakeEmbedder()
    embed.run_embed(conn, fake)
    # only the text after the budget changes, so the embedded text and its hash stay the same
    conn.execute(
        "UPDATE postings SET description_text = description_text || ' tail', content_hash = 'c2'"
    )
    assert search.stale_embedding_count(conn, "fake-model") == 2
    fake.seen.clear()
    summary = embed.run_embed(conn, fake, budget=len("ML Engineer\n\nWe build models."))
    assert summary.embedded == 0 and fake.seen == []
    assert search.stale_embedding_count(conn, "fake-model") == 0
    assert _rows(conn)[ids["open"]][1] == "c2"


# ---------- retries ----------


class ApiError(Exception):
    def __init__(self, status_code, retry_after=None):
        super().__init__(f"status {status_code}")
        self.status_code = status_code
        headers = {} if retry_after is None else {"retry-after": str(retry_after)}
        self.response = SimpleNamespace(headers=headers)


class FlakyClient(FakeClient):
    def __init__(self, errors):
        super().__init__()
        self.errors = list(errors)

    def create(self, model, input, encoding_format):
        if self.errors:
            raise self.errors.pop(0)
        return super().create(model, input, encoding_format)


def _flaky(errors):
    waits = []
    client = FlakyClient(errors)
    emb = ApiEmbedder("x", "m", 1.0, client, 10, 1000, sleep=waits.append)
    return emb, client, waits


def test_rate_limits_and_server_errors_are_retried_with_backoff():
    emb, client, waits = _flaky([ApiError(429), ApiError(503), ApiError(500)])
    assert emb.embed_documents(["a", "bb"]).vectors == [[1.0, 1.0], [2.0, 1.0]]
    assert waits == [2.0, 4.0, 8.0]


def test_retry_after_is_honoured_and_capped():
    emb, _, waits = _flaky([ApiError(429, retry_after=7), ApiError(429, retry_after=9999)])
    emb.embed_documents(["a"])
    assert waits == [7.0, 120]


def test_a_persistent_error_is_raised_after_the_last_try():
    emb, client, waits = _flaky([ApiError(429)] * 10)
    with pytest.raises(ApiError):
        emb.embed_documents(["a"])
    assert len(waits) == embed.RETRY_ATTEMPTS - 1 and len(client.errors) == 10 - 6


def test_other_errors_are_not_retried():
    emb, client, waits = _flaky([ApiError(401)])
    with pytest.raises(ApiError):
        emb.embed_documents(["a"])
    assert waits == []
    emb, _, waits = _flaky([KeyError("x")])
    with pytest.raises(KeyError):
        emb.embed_documents(["a"])


def test_timeouts_and_connection_errors_are_retried():
    import httpx
    import openai

    request = httpx.Request("POST", "https://x")
    emb, _, waits = _flaky([openai.APIConnectionError(request=request)])
    assert emb.embed_query("dddd") == [4.0, 1.0] and len(waits) == 1  # a query: 2 tries only
    emb, _, waits = _flaky([openai.APITimeoutError(request)] * 3)
    with pytest.raises(openai.APITimeoutError):
        emb.embed_query("dddd")
    assert len(waits) == embed.QUERY_ATTEMPTS - 1


# ---------- the query cache ----------


def test_query_cache_calls_the_embedder_once_per_distinct_query():
    cache, calls = embed.QueryCache(), []

    def make(text):
        return lambda: calls.append(text) or [float(len(text))]

    assert cache.get_or_embed("m", "data job", make("data job")) == [8.0]
    assert cache.get_or_embed("m", "  data   job ", make("again")) == [8.0]  # same, whitespace
    assert calls == ["data job"] and (cache.hits, cache.misses) == (1, 1)
    cache.get_or_embed("m", "other job", make("other job"))  # a different query
    cache.get_or_embed("m2", "data job", make("data job m2"))  # the same text, another model
    cache.get_or_embed("m", "Data job", make("Data job"))  # case is part of the key
    assert calls == ["data job", "other job", "data job m2", "Data job"]


def test_query_cache_returns_copies_and_does_not_store_failures():
    cache = embed.QueryCache()
    first = cache.get_or_embed("m", "q", lambda: [1.0, 2.0])
    first.append(99.0)  # a caller changing its list must not change the cache
    assert cache.get_or_embed("m", "q", lambda: [0.0]) == [1.0, 2.0]

    def boom():
        raise RuntimeError("api down")

    with pytest.raises(RuntimeError):
        cache.get_or_embed("m", "new", boom)
    assert cache.get_or_embed("m", "new", lambda: [3.0]) == [3.0]


def test_query_cache_drops_the_least_recently_used_entry():
    cache = embed.QueryCache(max_entries=2)
    for q in ["a", "b"]:
        cache.get_or_embed("m", q, lambda q=q: [q])
    cache.get_or_embed("m", "a", lambda: ["unused"])  # "a" is now the newest
    cache.get_or_embed("m", "c", lambda: ["c"])  # evicts "b"
    assert len(cache) == 2
    assert cache.get_or_embed("m", "a", lambda: ["new"]) == ["a"]
    assert cache.get_or_embed("m", "b", lambda: ["new b"]) == ["new b"]


def test_query_cache_is_safe_with_many_threads():
    from concurrent.futures import ThreadPoolExecutor

    cache = embed.QueryCache(max_entries=8)
    with ThreadPoolExecutor(16) as pool:
        results = list(
            pool.map(
                lambda i: cache.get_or_embed("m", f"q{i % 20}", lambda i=i: [i % 20]), range(400)
            )
        )
    assert results == [[i % 20] for i in range(400)] and len(cache) <= 8
