"""Database connection and the migration runner."""

import os
from pathlib import Path

import psycopg

DEFAULT_URL = "postgresql://jobfacts:jobfacts@localhost:5433/jobfacts"
MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


def connect(url: str | None = None) -> psycopg.Connection:
    return psycopg.connect(url or database_url())


def migrate(conn: psycopg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply every not-yet-applied `NNN_name.sql` file in order. Returns the names applied."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "name text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    conn.commit()
    done = {row[0] for row in conn.execute("SELECT name FROM schema_migrations")}
    applied = []
    for path in sorted(directory.glob("*.sql")):
        if path.name in done:
            continue
        with conn.transaction():
            conn.execute(path.read_text())
            conn.execute("INSERT INTO schema_migrations (name) VALUES (%s)", (path.name,))
        applied.append(path.name)
    return applied
