import os

import psycopg
import pytest

from jobfacts import db


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
    connection.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
    connection.execute(f"CREATE SCHEMA {schema}")
    connection.execute(f"SET search_path TO {schema}")
    connection.commit()
    db.migrate(connection)
    yield connection
    connection.rollback()
    connection.execute(f"DROP SCHEMA {schema} CASCADE")
    connection.commit()
    connection.close()
