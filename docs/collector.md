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

## Workday (opt-in, sitemap + JSON-LD only)

Employers with `ats = "workday"` (`board_id` = tenant, `wd` = host number, `site` = career
site) are read the way search engines read them: `robots.txt` first (disallowed URLs are never
requested; a disallowed sitemap fails the read), then `/<site>/siteMap.xml`, then the
`JobPosting` JSON-LD of each posting page. The `/wday/cxs/` JSON endpoint is not used. See
`notes/workday_terms.md`.

- One request per second per host (shared across threads), plus the usual retry/backoff.
  A 403/429 on a page stops that employer's read; the rest waits for the next run.
- Only pages whose URL location segment looks Swiss or remote are fetched. Set
  `prefilter = false` on an employer whose jobs are all Swiss (Swisscom).
  `scripts/workday_prefilter_check.py` samples skipped URLs to check the filter.
- Incremental: a page is fetched only if the job id is new or the sitemap `lastmod` changed
  (`postings.source_version`). The sitemaps seen so far carry no `lastmod`, so known
  postings are not re-read.
- A stored posting missing from the sitemap is closed. Exception: a sitemap with exactly 100
  entries is a capped list (most big tenants), so nothing is closed for it.
- "Remote" is added for `jobLocationType = TELECOMMUTE`, unless nearly every posting of the
  employer has it (several tenants flag everything).
