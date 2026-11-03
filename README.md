# jobfacts (working name)

**The facts a job posting hides.** jobfacts collects AI and data job postings
every day, straight from employers' public job boards, and finds the roles
someone based in Switzerland can actually take. For each posting it extracts
the facts that decide this, such as the language requirement, whether the role
is on-site, hybrid or remote, whether remote work from Switzerland is allowed,
and the real seniority. Each fact comes with the sentence from the posting that
supports it, so any answer can be checked in seconds.

Status: planning. Nothing is built yet. See `HANDOFF.md` for the current state.

## Why it exists

Swiss AI job boards such as swissaijob.ch (about 550 roles from 144 companies
in November 2026) list roles with skill tags, a seniority level and an
estimated salary. They do not say whether German is required, whether a remote
role can be done from Switzerland, or how long the commute is. Their seniority
and salary estimates are often wrong. For international candidates in
Switzerland, the language requirement is usually the first thing to check, and
today it means reading every posting.

The project has a second purpose: it is a portfolio project. It is built to
show, with measured results, the skills that AI-engineering job ads ask for:
a data pipeline and SQL, an LLM application whose accuracy is measured, search,
an API, automated tests, cloud deployment and monitoring.

## What it does (first version: search)

The first version is only about **finding roles**. A user can search open AI
and data roles by text and filter them by:

- **Where the work happens:** a Swiss city, or remote.
- **Remote from Switzerland:** remote roles are included only when the posting
  explicitly allows working from Switzerland (see "Remote rules" below).
- **Language requirement:** German or French required, and at what level
  ("fluent", "C1", "nice to have", "not mentioned").
- **Seniority:** judged from what the posting asks for (years of experience,
  responsibilities), not only from the title.

Every extracted fact is stored with the exact quote that supports it, or as
"not stated" when the posting says nothing. The system never guesses a fact
the posting does not contain.

### Remote rules

A remote role counts as "allowed from Switzerland" only when the posting:

- names Switzerland as an allowed country (or lists Swiss remote), or
- says the role can be done from anywhere in the world.

Postings that say "remote in Europe", "EU", "EMEA" or "CET time zone" without
naming Switzerland are marked **unclear**, not allowed. Many such roles hire
only through EU entities, and Switzerland is not in the EU. Unclear roles can
be shown on request, labelled "check first". Postings that list countries
without Switzerland, or only allow one other country, are excluded.

## How it works

1. **Collect.** A scheduled job reads each employer's public job board through
   its official API (Greenhouse, Lever, Ashby, SmartRecruiters, Workable,
   Recruitee, Workday and similar). The starting employer list comes from the
   private job-search watchlist (about 130 employers), plus remote-first
   employers outside Switzerland. New postings go into PostgreSQL. Duplicates
   are merged, and postings that disappear are marked closed so search only
   shows open roles.
2. **Keep only relevant roles.** A first filter keeps AI, ML, data and
   research roles and drops the rest.
3. **Extract facts.** An LLM reads each new posting and fills a fixed set of
   fields, each with a supporting quote. The output is checked against a schema;
   broken output is retried or flagged, never silently stored.
4. **Search.** Keyword search first (PostgreSQL full-text search), then search
   by meaning (embeddings stored with pgvector), always combined with the
   filters above.
5. **Serve.** A FastAPI service answers searches for a simple web page and for
   other programs.
6. **Run and watch.** The service runs in Docker on one cloud provider.
   Monitoring tracks failed collections, extraction errors, cost per posting
   and response time.

## How we know it works

The evaluation is the core of the project, not an extra.

- **Test set:** about 150 postings, labelled by hand by the person who uses
  the tool every day. Each label is a field value plus the supporting
  sentence. Postings in German, French and English are all included.
- **Labelling is mostly one-time per field:** the same labels are reused when
  the model or prompt changes. New fields (Milestones 3 and 4) need their own
  small rounds of about 50 postings, and a monthly spot check of 10-20 new
  postings catches drift (employers changing formats, model updates).
- **Extraction accuracy:** measured per field and per posting language.
- **Model choice:** at least two LLMs compared on accuracy and on cost per
  posting.
- **Search quality:** a small set of real searches with the roles that should
  come back, checked against what the system returns.
- **Automatic checks on every change:** unit tests plus the extraction test
  set run in GitHub Actions; a change that lowers accuracy is blocked.
- **Daily health:** each run records how many boards were read, how many
  failed, how many postings were new, closed or extracted, and the cost.

## Scope rules

- Public data only. Postings are summarised with a link back to the employer,
  never copied in full.
- No personal data. Nothing from the private job-search repository
  (applications, CVs, notes, outcomes) goes into this repository.
- No accounts, no payments, no application tracking in the first version.
- Each source's terms of use are checked before it is added.

## Milestones

**Milestone 1 (about two weeks): collection and the first facts.**
- Collector for the main job-board APIs, writing to PostgreSQL once a day.
- Relevance filter for AI and data roles.
- Extraction of three fields: work mode and remote eligibility, language
  requirement, seniority. Each with a quote.
- A small local labelling web app: it shows a posting next to the fields,
  the owner picks each value and marks the supporting sentence, and labels
  are saved to `data/private/`. It has a blind mode (model answers hidden)
  for the first 30 postings, then shows the model's pre-filled answers to
  confirm or correct.
- 100 labelled postings and an accuracy table per field.
- A command-line search with these filters.
- Done when the daily run has worked for a week and the search is used instead
  of reading postings by hand.

**Milestone 2: a usable service.**
- Search by meaning (pgvector) next to keyword search.
- FastAPI service and a simple web page. The API is designed to be called
  by other programs too, first the owner's private job-search tools.
- GitHub Actions with the accuracy gate; Docker; deployment on one cloud
  provider; monitoring.
- Load test with a stated result (response time at a given request rate).

**Milestone 3: more facts and salary.**
- New fields, each with a quote: minimum years of experience, PhD required or
  preferred, salary when stated (amount, currency, workload percentage).
- A salary estimate when the posting states none, built from stated salaries
  of similar postings (same role family, seniority and region) and from public
  Swiss pay scales where their terms allow it. Each estimate shows how many
  comparable postings support it; with too few, it says "not enough data"
  instead of guessing.
- The estimate is measured by hiding the stated salary of known postings and
  comparing; the error is published.
- Risk: few Swiss postings state a salary, so the comparison set starts small.
  The posting history grows every day.

**Milestone 4: commute and relisted roles.**
- Office location from the posting or the employer's site, otherwise the city.
- Door-to-door public transport time from a home address, using Swiss open
  timetable data; checked against the SBB app on 30 postings.
- Relisted roles: link a new posting to an earlier one at the same employer
  when the duties and team match, even if the title changed. Measured on
  pairs of postings labelled by hand.

Milestones 3 and 4 start only after Milestones 1 and 2 are finished.

**Later (not planned yet):**
- More feed types, each only after a terms check: prospective.ch JSON
  (Swiss federal administration, several universities), Oracle Recruiting
  (Canton of Vaud) and HiBob. Career sites readable only as page HTML stay
  out (see `docs/decisions.md`).
- Keep each posting's original HTML (new column, re-collect) so the
  labelling page can show real formatting such as bold, links and heading
  levels. Today only the plain text is stored.
- An MCP server (the standard way for LLM agents to call tools) wrapping the
  search API, so assistants such as Claude can search postings directly.
- Language requirements for English and Italian (Milestone 1 extracts
  German and French only).
- Private mode: fit against a CV, and links to the private job-search
  repository (for example, `job_watch` reading from this database). This
  stays out of the public code.
- **Decision Apertus trigger.** decision_apertus (a separate project: an
  open decision model on Apertus) starts early if the accuracy report shows
  Jev clearly weaker in German or French than in English on some field
  (about 10+ points, beyond the 95% intervals) and sending those postings
  to another extractor does not fix it. Italian cannot be judged until the
  test set has Italian postings.

## Publishing and licence

- The repository stays private during Milestone 1.
- When Milestone 1 works, the code is published under the AGPL-3.0 licence,
  with a write-up of the results (accuracy table, architecture, costs).
  AGPL lets anyone use and change the code, but anyone running a modified
  version as a public service must publish their changes.
- The database and the labelled postings stay private: the labels contain
  quotes from postings, and the data is the valuable part.
- If a paid service ever exists, it charges for hosted features (alerts,
  saved searches), never for the code. No decision on charging is made now.

## Stack

Python 3.12, PostgreSQL with pgvector, FastAPI, Docker, pytest and GitHub
Actions. The LLM is called through an API; the provider is chosen by
measured accuracy and cost. The cloud provider and monthly budget are still
open (see `HANDOFF.md`).

## Earlier attempts (for reference)

- `~/projects/personal/code/swiss_remote_job_radar/` (March 2026): a paid
  daily feed. Its filter presets (`config/presets/`) may be reused; its paid
  product plan is not part of this project.
- This folder held a July 2026 plan for a CV-matching tool on a generic
  European job board. It was removed on 2 November 2026 (moved to the system
  trash).
