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
uv run jobfacts daily         # the whole daily update, see below
```

`daily` starts the database if needed (`docker compose up -d`), then runs migrate, collect,
relevance and `extract --extractor jev --for-search` in order, stops at the first failing step
(named in the message) and prints one summary line. `--skip-collect` reruns only the
relevance and extraction steps. It works from any directory: `uv run --directory <repo> jobfacts daily`.

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
  every per-job detail request) are its results stored, in one transaction that
  is committed as soon as the board is stored (a crash keeps finished boards).
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
- Extra sites: a tenant with a capped main sitemap can list more sites by hand, e.g. Julius
  Baer `site = "External"` (capped at 100) plus `extra_sites = ["Technology"]` (81 URLs, allowed
  and listed in its robots.txt). Every site is read (sitemap, robots check, same rate limit).
  The same requisition has a different `-N` suffix per site (Data Scientist: `r-17167-2` on
  External, `r-17167-4` on Technology), so for employers with `extra_sites` a posting is keyed
  by the requisition without the suffix (`r-17167`) and appears once; the main site's URL wins
  when both list it, and the extra site's page is then never fetched. Employers without
  `extra_sites` keep the full id.
  Existing rows: at the start of a run, stored ids of such employers are rewritten from
  `r-17167-2` to `r-17167` (`store.rekey_source_ids`; same row, same posting id, so extractions
  stay attached; idempotent; a clash with an existing row is skipped).
- Closing rule with several sites: stored postings are closed only if EVERY site was read and
  none is capped (fewer than 100 URLs). If any site is capped, or an extra site could not be
  read (error or robots.txt disallow; the main site failing fails the whole board), nothing is
  closed, because a posting missing from one site may still be on the other. A posting that
  disappears from an uncapped extra site therefore stays open while the main site is capped
  (Julius Baer today), a conservative choice: stale open postings rather than wrongly closed.
- Capped employers (any site at 100 URLs, or an unread extra site): the lists alone close
  nothing, so each stored open posting absent from every sitemap read this run gets its own
  page requested (robots.txt check, same 1 request/second limit, sequential, at most 200 per
  employer per run, least recently seen first). It is closed only on a clear "gone" answer: HTTP
  404/410, or a redirect away from the posting URL (Workday sends removed jobs to the site
  page). A 200 page with JobPosting JSON-LD keeps it open (and refreshes `last_seen_at`); a
  200 page without it, errors, timeouts, 5xx and robots-disallowed URLs change nothing; a
  403/429 ends the checks. The run notes say "checked N postings missing from the capped list,
  closed M".
- "Remote" is added for `jobLocationType = TELECOMMUTE`, unless nearly every posting of the
  employer has it (several tenants flag everything).
