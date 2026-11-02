# Design decisions

One entry per decision: what, why, and which alternatives were rejected.
Newest at the bottom.

## 2026-11-02: Fixed allowed values for every extracted field
- **What:** each field takes one value from a closed list (see `fields.md`).
- **Why:** answers can be compared exactly with hand labels, so accuracy is a
  number; output can be validated against a schema; search filters need
  fixed values.
- **Rejected:** free-text answers (no exact accuracy, no reliable filters).

## 2026-11-02: Every value carries an exact quote from the posting
- **What:** the model returns the sentence(s) that support each value; code
  checks the quote appears verbatim in the posting.
- **Why:** the owner can check any answer in seconds (also in German, which
  the owner reads at B1), and an invented quote is caught automatically.
- **Rejected:** values without evidence (unverifiable); paraphrased evidence
  (cannot be checked by code).

## 2026-11-02: "not stated" instead of defaults
- **What:** a missing fact is `not_stated` / `not_mentioned`, never a default.
- **Why:** the old job-search labels used "no level means mid", which mixes
  what the posting says with what was assumed. Keeping them apart lets search
  show "check first" honestly and keeps accuracy meaningful.

## 2026-11-02: Location split into three fields
- **What:** `work_mode`, `office_locations`, `remote_from_switzerland`
  instead of one `location` value.
- **Why:** each is a separate search filter and gets its own accuracy number,
  so errors are traced to the exact question the model gets wrong.
- **Rejected:** one combined value (as in the job-search logbook): mixes
  three questions, and multi-location postings do not fit one value.

## 2026-11-02: Strict remote-from-Switzerland rule
- **What:** `allowed` only when Switzerland is named or "anywhere in the
  world"; "Europe/EU/EMEA/CET" is `unclear`.
- **Why:** many "remote Europe" roles hire only through EU entities, and
  Switzerland is not in the EU. A false "allowed" wastes an application.

## 2026-11-02: Language levels, including "level not stated" and "either"
- **What:** `german` and `french` each take `required_fluent`,
  `required_intermediate`, `required_level_not_stated`, `either_required`,
  `nice_to_have` or `not_mentioned`.
- **Why:** the owner's blocker is C1 German. "German required" without a
  level could be B1 or C2, so it must not be merged with fluent.
  "German or French" is a real requirement that would disappear if both were
  marked `nice_to_have`.
- **Rejected:** a yes/no "German required" field (loses the level);
  inferring a requirement from the posting's language (that is a guess;
  posting language is stored separately, measured by code).
- **Deferred:** English and Italian (recorded under "Later").

## 2026-11-02: Seniority from years first, then title
- **What:** stated years decide (0-1 junior, 2-4 mid, 5+ senior); otherwise
  the title; otherwise `not_stated`. Staff/principal/lead/head-of titles are
  always `staff_principal`.
- **Why:** titles inflate ("Senior" with 2 years); years are the more
  concrete requirement. Staff level is about scope, which years do not show.
- **Rejected:** title only (the problem swissaijob.ch has); a separate
  `manager` value (not needed for the owner's search yet).

## 2026-11-02: Quotes as chosen sentences
- **What:** code numbers the posting's sentences; the model answers with
  sentence numbers; code copies the text.
- **Why:** quotes are verbatim by construction, and it works for models that
  cannot generate text (Jev).
- **Rejected:** model-written quotes as the main design (they can be
  invented; still accepted from LLMs, with a verbatim check).

## 2026-11-02: Fixed location list and salary brackets
- **What:** office locations come from a fixed list of cantons and cities;
  salaries (Milestone 3) will be chosen from brackets, not free numbers.
- **Why:** turns open extraction into choices, which can be scored exactly
  and suits classifier models such as Jev, which are weak with numbers.

## 2026-11-02: Seniority read as years plus title level
- **What:** the model picks `min_years` and `title_level`; code applies
  "years win" and the staff/principal exception.
- **Why:** the rule lives in tested code, not in the prompt; models are
  weaker at arithmetic and thresholds than at reading.

## 2026-11-02: Compare Jev, DeepSeek and other cheap models in Milestone 1
- **What:** run the same 100 labelled postings through Jev (TypeSafe), a
  DeepSeek model and at least one other cheap LLM; compare accuracy per
  field and language, cost and speed.
- **Why:** Jev is far cheaper and faster but weaker outside English by its
  own docs; only measurement on our labels shows whether it is good enough.
  Running all models costs cents.
- **Rejected:** choosing one model up front.

## 2026-11-02: Compare embedding models on search results (Milestone 2)
- **What:** compare at least one API model (Mistral, OpenAI) and one open
  model (e.g. BGE-M3, Qwen3-Embedding) on the labelled real searches; pick
  the best. On a tie, prefer Mistral (European provider, key available).
- **Why:** search quality is measured like extraction quality, not assumed.

## 2026-11-02: Database access with psycopg and plain SQL migrations
- **What:** psycopg 3 with hand-written SQL; schema changes are numbered SQL
  files in `migrations/`, applied in order by a small runner that records
  which ones ran.
- **Why:** the project is meant to show SQL skills; every query is visible
  and explainable. Small enough that an ORM adds little.
- **Rejected:** SQLAlchemy + Alembic (more machinery to learn and explain
  for a handful of tables; can be adopted later if the schema grows).

## 2026-11-02: A posting is closed only after a successful board read
- **What:** a posting missing from a board's response is marked closed only
  if that board was read without error in the same run.
- **Why:** a failed or empty request must not close every posting of an
  employer.
- **Also:** an empty response from a board with open postings counts as a
  failed read. Trade-off: if an employer truly closes every role, those
  postings stay open until it posts again.

## 2026-11-02: Personio and Teamtailor added; JOIN, SuccessFactors, Homerun not
- **What:** Personio (documented XML feed, no credentials) and Teamtailor
  (documented public RSS feed) are collected. JOIN (only by reading page
  HTML), SuccessFactors (official API needs credentials) and Homerun (feed
  without stated terms) are not. job-room.ch has no public reading API.
- **Why:** official feeds only; reading page HTML is scraping.

## 2026-11-02: Extraction design as built
- **What:** one field spec in code generates both Jev questions and the LLM
  prompt; every model answers with sentence ids (title = T, location = L);
  invalid LLM output gets one retry, then is stored as an error, never as a
  value; Jev options list `not_stated` first.
- **Why:** one source of truth; quotes exact for every model; Jev leans to
  the first option, so the bias goes towards "not stated", not a guess.
- **Models compared:** Jev 1.14.0, DeepSeek V4.1 Flash (non-thinking),
  Mistral Small 2603 via OpenRouter.
