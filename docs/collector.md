# Collector: run it

The collector reads each employer's public job board (Greenhouse, Lever,
Ashby, SmartRecruiters, Recruitee, Workable, plus the Personio XML feed and the
Teamtailor jobs RSS feed) and stores the postings in
PostgreSQL. Employers are listed in `config/employers.toml`.

## Run it

```bash
cp .env.example .env          # optional; the default URL is the same
docker compose up -d          # PostgreSQL 16 + pgvector on host port 5433
uv sync
uv run jobfacts migrate       # applies migrations/*.sql in order
uv run jobfacts collect       # reads all boards (about 1.5 minutes)
uv run jobfacts collect --only Proton   # one employer or one ATS name
```

The database URL comes from `DATABASE_URL`
(default `postgresql://jobfacts:jobfacts@localhost:5433/jobfacts`).

## Tests and lint

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

Parser tests use invented JSON and need no network. Store tests use a
throwaway schema `test_jobfacts` in the compose database and are skipped
(with a message) when it is not running.

## How postings are opened and closed

- Each board is read in one go. Only if the whole read succeeds (including
  every per-job detail request) are its results stored.
- A posting in the response is inserted (new), or updated when its content
  hash (url, title, location, description) changed.
- A stored open posting missing from a successful read gets `closed_at`.
- A failed read is only logged in `board_reads`; it never closes anything.
- A closed posting that appears again is reopened (`closed_at` cleared,
  counted as updated).
- An empty response from a board that still has open postings is treated
  as a failed read (likely a glitch), so nothing is closed. If the employer
  really closed every role, its postings stay open until a non-empty read;
  check `board_reads` errors if a board keeps returning empty.
