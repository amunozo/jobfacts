import os

import psycopg
import pytest

from jobfacts import db

SCHEMA_LOCK = 5433_2026  # any fixed number; names the advisory lock that guards the test schema


@pytest.fixture
def conn():
    """A throwaway schema in the compose database; skipped if unreachable, fails if CI is set."""
    url = os.environ.get("DATABASE_URL", db.DEFAULT_URL)
    try:
        connection = psycopg.connect(url, connect_timeout=3)
    except psycopg.OperationalError as error:
        if os.environ.get("CI"):
            pytest.fail(f"PostgreSQL unreachable in CI ({error})")
        pytest.skip(f"PostgreSQL unreachable ({error}); run `docker compose up -d`")
    schema = "test_jobfacts"
    # One test at a time across processes. Every test drops and recreates this schema; if a
    # second pytest process did that in the middle of a test here, this test's statements
    # would fall through to `public` (the real tables). The lock lasts until the connection
    # closes; a second process waits for it instead.
    connection.execute("SELECT pg_advisory_lock(%s)", (SCHEMA_LOCK,))
    connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    connection.execute(f"CREATE SCHEMA {schema}")
    # public stays on the path: the pgvector extension (type `vector`) may live there
    connection.execute(f"SET search_path TO {schema}, public")
    connection.commit()
    db.migrate(connection)
    yield connection
    connection.rollback()
    connection.execute(f"DROP SCHEMA {schema} CASCADE")
    connection.commit()
    connection.close()


@pytest.fixture(autouse=True)
def no_health_ping(monkeypatch):
    """A developer's own JOBFACTS_HEALTHCHECK_URL must never be pinged by a test."""
    monkeypatch.delenv("JOBFACTS_HEALTHCHECK_URL", raising=False)
