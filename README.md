# jobfacts

**The facts a job posting hides.** jobfacts collects AI and data job postings
straight from employers' public job boards and finds the roles someone based in
Switzerland can actually take. For each posting it extracts the facts that
decide this: the German or French requirement, whether the role is on-site,
hybrid or remote, whether remote work from Switzerland is allowed, where the
office is, and the real seniority. Each fact points to the sentence in the
posting that supports it, so any answer can be checked in seconds. When a
posting does not state a fact, jobfacts stores "not stated" and never guesses.

**Status (8 November 2026): Milestone 1 of 4 built (its last check, a week of
daily runs, is under way); Milestone 2 started.** The collector, the
extraction and a command-line search (full text plus search by meaning) work,
and the author uses them for their own job search. It is a personal project, not a hosted service. See
"Not built yet" below.

## Why

Swiss AI job boards list skills, a seniority level and an estimated salary.
They do not say whether German is required or whether a "remote" role can be
done from Switzerland, and their seniority estimates are often wrong. For
international candidates in Switzerland the language requirement is usually
the first thing to check, and today that means reading every posting.

## What works today

- **Collection.** Reads the official public job-board APIs and feeds of about
  100 employers (about 140 boards): Greenhouse, Lever, Ashby, SmartRecruiters,
  Recruitee, Workable, Personio, Teamtailor and Workday (sitemap and
  structured data only). Postings go into PostgreSQL; postings that disappear
  are marked closed. A run on 4 November 2026 read 141 boards with 0 failures
  (5,583 open postings). Each board is committed as it is read, so a crash
  loses at most one board. See `docs/collector.md`.
- **Relevance filter.** A cheap classifier model scores whether a posting is
  an AI, ML, data or research role from its title and first sentences. The
  threshold (0.2) favours recall: a wrong row costs seconds, a missed job
  costs more.
- **Fact extraction.** Each field has a fixed list of allowed values
  (`docs/fields.md`). The posting is split into numbered sentences and the
  model picks values and supporting sentence numbers, so a quote cannot be
  invented. Deterministic code rules handle what models do badly (year
  thresholds, place names from the official swisstopo register). The
  extractor is Jev (TypeSafe), chosen over DeepSeek and Mistral after
  measuring all three on the same postings: similar accuracy to DeepSeek at
  about a third of the cost.
- **Search.** `jobfacts search` combines PostgreSQL full-text search with
  filters (canton, remote from Switzerland, maximum German level, no
  fixed-term) and groups duplicate postings. See `docs/search.md`.
- **Search by meaning (first step of Milestone 2).** Relevant postings are
  embedded with Mistral `mistral-embed` (first 6,000 characters) into
  pgvector. `jobfacts search` by default fuses the full-text ranking and the
  similarity ranking with reciprocal rank fusion, keeping every filter; it
  falls back to full text when the query cannot be embedded. `jobfacts daily`
  embeds new and changed postings. See `docs/search.md`.
- **HTTP API and search page (Milestone 2).** `jobfacts serve` starts a
  FastAPI service: `/api/search` returns the same results as `jobfacts search
  --json`, `/` is a server-rendered search page (filters, facts and their
  supporting quotes, no JavaScript), `/health` reports the database state.
  Everything except `/health` sits behind a password (HTTP Basic). It runs on
  the author's machine; it is not deployed. See `docs/api.md`.
- **Light monitoring (Milestone 2).** `jobfacts daily` records each run in a
  `daily_runs` table (status, failed step, the summary numbers, cost), shown at
  `/runs`; `/health` reports the last run; with `JOBFACTS_HEALTHCHECK_URL` set
  it pings an outside dead-man service (start, success, failure). Tested with
  fake HTTP only: no real check has been set up yet. See `docs/monitoring.md`.
- **Accuracy gate for CI (Milestone 2).** The held-out labels and the model's
  recorded answers for those 50 postings are stored encrypted in `ci/`; a CI
  step decrypts them with a secret key, re-applies the code rules and fails if
  any of 10 fields loses more than 2 points against `ci/accuracy_baseline.json`.
  It measures changes to the code rules on fixed answers, not model drift, and
  skips when the key is not set (forks). See `docs/gate.md`.
- **Evaluation.** A local labelling web app, reference labels, and an
  accuracy report per field and per posting language (`docs/labelling.md`).
- **Search recall check.** `jobfacts search-eval` replays past job
  applications against the database and reports, for each wanted job, the step
  that would have lost it (employer not collected, not relevant, filtered, ...)
  and the recall. The applications themselves stay private
  (`docs/search.md`).
- **Daily update.** `jobfacts daily` runs the whole update in one command
  (database, migrations, collection, relevance, extraction). It is started by
  hand for now.

## Measured accuracy

Held-out set of 50 postings (25 English, 13 German, 12 French) that were not
used while writing the prompts and rules (those were tuned on a separate
100-posting test set). Run of 4 November 2026.

| field | overall | en | de | fr |
|---|---|---|---|---|
| relevant (AI/data role) | 88% | 88% | 85% | 91% |
| work mode | 96% | 100% | 92% | 92% |
| remote from Switzerland | 96% | 92% | 100% | 100% |
| office canton(s) | 92% | 88% | 92% | 100% |
| office place(s) | 96% | 96% | 92% | 100% |
| German requirement | 96% | 100% | 100% | 83% |
| French requirement | 98% | 100% | 100% | 92% |
| minimum years | 100% | 100% | 100% | 100% |
| title level | 98% | 96% | 100% | 100% |
| contract type | 98% | 100% | 100% | 92% |
| seniority | 100% | 100% | 100% | 100% |

How to read it:

- **The reference labels are model labels checked by a person, not labels
  written from scratch by hand.** Three models (GPT-6 Astra, Gemini 4 Argon,
  Claude Opus 5.5) label each posting independently; where they disagree, the
  author decides. In a 12-posting pilot, this reference matched the author's
  own hand labels on 95-96% of answers. No independent audit yet.
- **The set is small.** On 50 postings, 96% has a 95% confidence interval of
  about 87-99%; per language the intervals are wider still.
- **Cost:** about $0.0005 per posting for extraction (Jev).

## Which embedding model? (measured 8 November 2026)

`jobfacts meaning-eval` replays the author's past applications as wanted jobs
against the 2,504 relevant postings, with 14 search phrases fixed before any
model was scored, and counts how many wanted jobs reach the top 20 of at least
one phrase. Three models were compared: Mistral `mistral-embed` and OpenAI
`text-embedding-3-small` (APIs) and Qwen3-Embedding-0.6B (open, run on a CPU).
Mistral with 6,000 characters per posting placed the most wanted jobs, ahead
of full-text search and the other two models; with only the first 800
characters its lead was much smaller. The sample is small (44 jobs), so the
ranking between models is an indication, not a decisive result. Embedding the
pool costs about $0.17 with Mistral at 6,000 characters; the local model was
free but took 55 minutes for 800 characters per posting, so it was not run on
longer texts.

## Not built yet

- A running deployment (Milestone 2). The files and a step-by-step guide for
  one AWS Lightsail server with Docker Compose exist (`docs/deploy.md`) and
  were tested locally, but nothing is deployed, so there is no scheduled daily
  run on a server and no outside alert configured yet.
- A load test (Milestone 2).
- PhD and stated-salary fields, salary estimates (Milestone 3).
- Commute time and relisted roles (Milestone 4).

## Run it

Needs Python 3.12, [uv](https://docs.astral.sh/uv/) and Docker.

```bash
docker compose up -d          # PostgreSQL 16 with pgvector on port 5433
uv sync
cp .env.example .env          # then add your API keys (see the file)
uv run jobfacts migrate
uv run jobfacts collect       # about 1.5 minutes
uv run jobfacts relevance
uv run jobfacts extract --extractor jev --for-search
uv run jobfacts search "machine learning" --canton VD --german-max intermediate
```

`uv run jobfacts daily` runs the update steps in order. Collection needs no
API key; relevance and extraction need `TYPESAFE_API_KEY`.

## Tests and CI

```bash
uv run ruff check . && uv run ruff format --check . && uv run pytest
```

About 500 tests; the database tests use the compose database and are skipped
locally when it is not running. GitHub Actions runs the same checks, with a
PostgreSQL service, on every push and pull request; there the database tests
fail instead of being skipped. A last step runs the accuracy gate
(`jobfacts gate check`, `docs/gate.md`).

## Design rules

- **Remote from Switzerland** counts only when the posting names Switzerland
  (or Swiss remote) or allows work from anywhere. "Remote in Europe", "EU",
  "EMEA" or "CET time zone" without Switzerland is **unclear**, not allowed:
  many such roles hire only through EU entities.
- **Sources:** only official, documented job-board APIs and feeds whose terms
  allow the use; each source is checked before it is added. No scraping of
  sites that forbid it.
- **No guessing:** a fact the posting does not state is stored as "not stated".
- **Evaluation first:** every field has allowed values, a supporting quote and
  labelled test cases before search uses it.

Each design decision, with the alternatives rejected, is in
`docs/decisions.md`.

## Milestones

1. **Collection and the first facts** (in progress): collector, relevance
   filter, extraction with quotes, labelled postings with an accuracy table,
   command-line search. Done when the daily run has worked for a week and the
   search replaces reading postings by hand.
2. **A usable service:** search by meaning (pgvector), a FastAPI service and a
   simple web page, Docker deployment on one cloud provider, monitoring, an
   accuracy gate in CI, a load test with a stated result.
3. **More facts and salary:** PhD, stated salary, and a
   salary estimate from comparable postings that says "not enough data"
   instead of guessing, with its error measured and published.
4. **Commute and relisted roles:** door-to-door public transport time from
   Swiss open timetable data; linking a new posting to an earlier one for the
   same role.

**Later (not planned yet):**
- More feed types, each only after a terms check: SAP SuccessFactors (many
  large Swiss employers: insurers, industry, EPFL), JOIN, prospective.ch JSON
  (Swiss federal administration, several universities), Oracle Recruiting
  and HiBob. Career sites readable only as page HTML stay out.
- Keep each posting's original HTML so the labelling page can show real
  formatting. Today only the plain text is stored.
- An MCP server (the standard way for LLM agents to call tools) wrapping the
  search API.
- Language requirements for English and Italian (Milestone 1 extracts German
  and French only).
- Posting language as a search filter or a result column.
- Record wanted jobs that scored below the relevance threshold, as free labels
  for relevance misses.

## Data and licence

- Code: [GNU AGPL-3.0](LICENSE). Anyone may use and change it; anyone running
  a modified version as a public service must publish their changes.
- The posting database and the labelled postings are not published: the
  labels contain quotes from postings.
- `config/swiss_places.csv` is built from the official swisstopo locality
  register, (c) swisstopo, open data redistributed with attribution
  (`scripts/build_places.py` rebuilds it).
- Postings belong to their employers; jobfacts links back to each one.

## Stack

Python 3.12, PostgreSQL 16 with pgvector, psycopg with plain SQL migrations,
httpx, pytest, ruff, uv, Docker Compose and GitHub Actions. LLM providers:
TypeSafe (Jev), OpenRouter (comparison models and the Jev backup route).
