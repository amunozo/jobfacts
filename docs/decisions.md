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

## 2026-11-02: Test set of 100, with non-AI German postings reported apart
- **What:** ~50 application descriptions + ~50 collected postings; German
  = all ~7 German AI postings + ~20 other Swiss German postings (max 3 per
  employer), reported as a separate group. Full text snapshot kept in
  `data/private/` so results stay reproducible.
- **Why:** language wording does not depend on the role type; 7 postings
  alone give a meaningless German accuracy (each one moves it 14 points).
- **Rejected:** waiting weeks for German AI postings to accumulate.

## 2026-11-02: Pre-fill labels with the majority of three models
- **What:** first 30 postings blind; after that, each field is pre-filled
  with the value 2 or 3 models agree on, without saying which model; no
  pre-fill when all three differ. Labels also mark relevance.
- **Why:** pre-filling from one model would anchor the owner towards it and
  inflate that model's measured accuracy. The blind 30 measure how much
  pre-filling shifts labels at all.
- **Rejected:** pre-fill from Jev only (cheapest, but biased comparison).

## 2026-11-02: Workday read through sitemap + JSON-LD only
- **What:** Workday career sites are read through their sitemap and the
  schema.org `JobPosting` data on each posting page, which they publish for
  search engines. Opt-in per employer, `robots.txt` obeyed, at most 1
  request per second per host, only new or changed pages, and a URL
  pre-filter for Swiss or remote postings.
- **Why:** big Swiss pharma and bank employers are only on Workday; this is
  the one route meant for machines. Evidence in `notes/workday_terms.md`.
- **Rejected:** the `/wday/cxs/` JSON endpoint (undocumented, no
  permission); stays in the owner's private tool only.

## 2026-11-02: Keep `not_stated` first in Jev options
- **What:** measured on the 100 test postings: reversing or shuffling the
  option order changes 0-3% of Jev answers per field, with no direction
  towards or away from `not_stated`.
- **Why:** the documented first-option bias is not measurable here, so the
  original order stays.

## 2026-11-02: Office location as canton first, place second
- **What:** the model picks the canton(s) with an office (26 cantons +
  outside Switzerland; none = not stated) with evidence; code then finds the
  places inside those cantons in swisstopo's official locality register
  (`config/swiss_places.csv`, ~5,900 places, (c) swisstopo, open data that
  may be redistributed with attribution).
- **Why:** 26 options always fit one Jev question set; the canton removes
  most name ambiguity (Baden AG, Rapperswil SG/BE); search filters are
  regional; the official register finds villages a hand-made list missed
  (Ibach, Netstal, Grancia). Accuracy is measured at canton level first.
- **Rejected:** a model choice over communes (Bern and Vaud have ~300 each,
  over Jev's 255-option limit); the hand-made list of ~255 places.

## 2026-11-02: Command-line search
- **What:** PostgreSQL full-text search (`simple` configuration, title
  weighted above description, GIN index) over open, relevant board
  postings, using each posting's latest Jev extraction. Language filters
  are "the most I accept" (`--german-max none|intermediate|fluent`);
  "required, level not stated" is hidden below fluent unless asked.
  `--canton` and `--remote` combine with OR ("here, or remote from
  Switzerland"); all other filters combine with AND. `--json` for other
  programs.
- **Why:** `simple` works the same for EN/DE/FR (no stemming, so "model"
  does not match "models"; meaning-based search comes in Milestone 2).
  OR between canton and remote matches how the owner looks for roles.
- **Rejected:** one language-specific stemmer (would mangle the other two).

## 2026-11-03: New watchlist employers: API boards only
- **What:** of 23 employers added to the private watchlist, only Exa and
  Deepgram (Ashby) are collected now. prospective.ch, Oracle Recruiting and
  HiBob are listed under README "Later", each pending a terms check. ITU
  (SuccessFactors, API needs credentials) and the sources read as page HTML
  (universities, research institutes, SRG SSR, small company sites) are not
  collected.
- **Why:** the "official feeds only, no page HTML" rule stays; Milestone 1
  scope does not grow.
- **Rejected:** relaxing the rule for public institutions with permissive
  robots.txt (harder to defend once the repository is public).

## 2026-11-03: Labelling page shows postings as a document
- **What:** the labelling page rebuilds paragraphs, headings and bullet
  lists from the stored plain text and the sentence offsets. Sentences stay
  clickable spans, and their numbers show on hover or when marked. The old
  view with one row per sentence stays available behind a key.
- **Why:** the line view was hard to read. Sentence ids, the unit of
  evidence shared with the extractors, don't change, so labels are unaffected.
- **Rejected for now:** keeping the original HTML for real formatting. It
  needs a new column and a re-collect, and it would change the text the test
  set is built on (listed under README "Later").

## 2026-11-03: German postings labelled by three models, checked from quotes
- **What:** the owner labels English and French on the labelling page (it
  now serves only `en,fr`; the 30 blind postings are the first 30 served).
  The 26 German test postings are labelled independently by Opus 5.5 (Claude
  Code subagent), GPT-6 Astra (pi) and Gemini 4 Argon
  (pi, opencode-go). Each gives value, sentence ids and an English
  translation of those sentences. All agree: accepted, ~20% spot-checked.
  Two agree: owner reviews from quote + translation. All differ: owner
  decides or the field is "uncertain" and not scored.
- **Why:** the owner's German is not enough to label full postings, but is
  enough to check a short quote with a translation. Three model families
  catch errors that two agreeing models would share. None of the three is
  an extractor under test (Jev, DeepSeek, Mistral), so no model grades
  itself. Labels record the labellers and whether the owner checked them;
  German accuracy is reported separately as model-labelled ("silver").
- **Rejected:** Opus alone (no check on its mistakes); two models with a
  third only as tie-breaker (misses errors the two share; at 26 postings
  the full third run costs almost nothing); owner labelling German alone.

## 2026-11-03: Add contract_type
- **What:** a new extraction field `contract_type` with `permanent`,
  `fixed_term` and `not_stated` (nothing assumed; an internship is not taken
  to be fixed-term). Same rules as the other fields: one allowed value, a
  supporting sentence unless `not_stated`.
- **Why:** the owner filters out fixed-term roles. It is cheapest to add now,
  before labelling starts: the labelling page, the extractors and the report
  all read the field list from the spec. The one early label (posting order
  0) has no value; it counts as unlabelled for this field.
- **Rejected:** adding it later (the 100 test postings would have to be
  labelled again, and the three extractors rerun after labels exist).

## 2026-11-03: Reference labels by three-model majority
- **What:** the reference labels of the 100-posting test set are the
  majority of three model labellers: GPT-6 Astra (pi), Gemini 4 Argon
  (OpenRouter) and Claude Opus 5.5. The owner reviews only the keys where
  all three differ, plus a random audit of 30 unanimous answers (fixed
  seed) to measure the error rate of the reference. The owner's own labels
  (about 7-10 postings) stay as a human comparison, and `evaluate` scores
  against the model reference by default (`--reference owner` keeps the old
  mode). Rules were tightened first (worldwide beats a country list;
  "Founding" is not a level; ranges take the lower end; planned offices
  do not count; "very good" = fluent; BI roles not relevant).
- **Why:** in a pilot of 12 postings the three models agreed on 97% of the
  answers and matched the owner's labels on 95-96%; the two misses they
  shared were an owner mislabel and an open rule question. Owner time is
  the scarce resource, and the audit shows how much the reference can be
  trusted instead of assuming it.
- **Rejected:** the owner labels all 100 by hand (slow, and error-prone:
  one of the pilot disagreements was the owner's own mistake); one model
  only (no error signal at all); two models (more owner review for little
  gain: every disagreement would need the owner, and two models cannot
  outvote each other).

## 2026-11-03: "Manager" is a level only when it is a rank
- **What:** `title_level = staff_principal` for "Manager" used as a rank
  (plain "Manager", "Manager, X", "Engineering Manager"). A function title
  (Sales, Key Account, Application, Project, Programmatic Advertising
  Manager) is `not_stated`. Added after the labelling runs, so the five
  affected test postings are fixed in the review decisions.
- **Why:** in sales and project roles "Manager" names the job, not a
  position above senior; treating it as a rank would let those roles pass a
  seniority filter they do not belong to.
- **Rejected:** every "Manager" title as `staff_principal` (the earlier rule).

## 2026-11-03: Jev extracts postings for search
- **What:** Jev (jev-1.14.0) is the extraction model for search.
- **Why:** against the three-model reference (100 postings) Jev and DeepSeek
  tie on average (~95%; German 93.8 vs 93.6, French 97.5 vs 97.0). Jev is
  better on work mode (German 26/26 vs 21/26) and remote; DeepSeek on
  title level (mostly English: 98% vs 85%) and office cantons. Jev costs a
  third as much ($0.0004 vs $0.0013 per posting), is about 6x faster, and
  its quotes are chosen sentences by construction, so they cannot be
  invented.
- **Rejected:** DeepSeek (no clear accuracy gain for 3x the cost); a mix
  per field (best numbers, but two extractors to run and maintain);
  Mistral (clearly worse: ~80% on German postings).

## 2026-11-03: Page look inspired by SBB, built only from openly licensed parts
- **What:** web pages take their style from the SBB design system (clean
  Swiss layout, one red accent, strict grid) but use a free font. Allowed
  as-is: Lyne components (MIT), Lyne design tokens (ISC), SBB icons and
  pictograms (Apache-2.0), keeping their licence notices. Not used: the SBB
  font (served from SBB's CDN with no licence), the SBB logo, the station
  clock, or any icon that shows SBB's own brand marks.
  Applies to the Milestone 2 search page (the labelling page stays as it
  is). Plain HTML and CSS with the tokens and icons; Lyne components not
  used.
- **Why:** the repository may become public; a page that looks like an SBB
  product could read as an official SBB tool, and code licences do not
  grant trademark rights (Apache-2.0 section 6 says so explicitly).
- **Rejected:** the full SBB look (font and brand not licensed for others);
  Lyne components (more polished, but add a JavaScript dependency and are
  harder to explain than plain HTML and CSS); restyling the labelling page
  now (not in the current milestone).

## 2026-11-03: Connect to the owner's job_watch as an extra source
- **What:** the owner's `job_watch.py` (a separate private tool) calls
  `jobfacts search "" --json` in this folder as a subprocess and treats the
  results as one more source. Both sources stay on; each output row says who
  found it (watch / jobfacts / both), and a comparison section lists what
  only one of them found and what jobfacts' filters dropped. job_watch
  applies the filters itself (German fluent and fixed-term dropped; location
  by job_watch's own region rules) so the comparison is fair.
- **Why:** jobfacts covers only the employer job-board APIs; job_watch also
  covers career sites, Swiss boards and aggregators, and owns the "seen"
  ledger and the applied-roles filter. Running both for a week measures
  whether jobfacts finds the same roles and filters them better (the
  Milestone 1 test). No jobfacts code moves into that tool.
- **Rejected:** replacing job_watch's board polling now (loses the
  comparison); copying jobfacts code into the vault; filtering German and
  location inside jobfacts (only-watch rows would look like misses).

## 2026-11-03: Extraction rule fixes (wording plus three code rules)
- **What:** (1) Jev wording: a remote-worker country or region ("Remote - United
  States", "Remote (Europe)") is not an office; office cities next to "remote"
  are no region for `remote_from_switzerland`, and a Swiss office or employer
  alone does not make it `allowed`; `title_level` reads the title line only and
  names the non-levels (Postdoc, Member of Technical/Product Staff, Research
  Staff, plain Researcher, Expert, Resident, "Experienced ...", Group Product
  Manager); Partner is `staff_principal`; a bracketed level counts;
  `min_years` 0 only when no experience at all is needed. (2) Code rules after
  Jev (`extract/rules.py`): `title_level` is kept only if the title contains a
  keyword for that level (`seniority` follows from the corrected level); a canton
  the model picked is dropped unless `canton_hints()` finds a place in it
  (hinted cantons are never added); `remote_from_switzerland` is
  `not_applicable` unless `work_mode` is `remote`.
- **Why:** errors found in the 100-posting test set were mostly the model
  reading the description for the title, or inventing a canton from a country
  line; code can check both exactly, which wording cannot.
- **Rejected:** tuning the canton probability threshold (fits the test set, no
  new information); adding hinted cantons the model missed (hints include
  customers and history); a title-level keyword list that also fills in levels
  (would guess where the title is silent).

## 2026-11-03: Relevance wording, threshold 0.4 and a title list
- **What:** the relevance question (wording "W1") judges the role's own tasks, not
  the employer's business: sales, customer success, support, recruiting,
  marketing, design, operations, finance, billing, legal and data-centre roles do
  not count even at an AI company; web scraping and analytics engineering count
  as data engineering. Owner decisions: product managers of AI/ML products and
  solutions architects / forward-deployed engineers on AI products are
  relevant; SQL and statistical data analysts count, market-insight,
  revenue-planning and BI analysts do not. Threshold 0.5 -> 0.4. A title list
  (`extract/rules.py: title_excluded`) scores plainly non-technical titles 0
  without a model call: always for sales, recruiting/talent acquisition,
  designer, technician, customer success, "not seeing the right opportunity";
  only when the title names no engineer/scientist/researcher/developer/architect/SWE/ML/AI
  for insights, billing, payroll and partnerships. Title only, never the
  description (a "payroll partner" contract note hit a Data Engineer). Each
  relevance row stores a version (question, model, threshold, title rules), so a
  change re-scores everything once.
- **Why:** the old question accepted about 1,700 postings, many of them
  non-technical roles at AI companies (support, design, data-centre technicians,
  insight analysts). The wording removes most; the list removes what is still left
  by plain title words.
- **Rejected:** only raising the threshold (it drops good roles first, since the
  false positives score 0.5-0.7 like true ones); copying job_watch's title regexes
  (they also exclude product managers and solutions roles, which the owner wants).

## 2026-11-04: Held-out rule questions (Swisscom home office, rank titles, language check)
- **What:** (1) "in one of our offices in Switzerland or in home office" is
  `remote` with `remote_from_switzerland` = `allowed`: the worker chooses. (2) A
  German or French value is kept only if the posting names that language
  somewhere (in English, German or French, or "national languages"); otherwise
  code sets `not_mentioned`. (3) Co-founder, CEO and "Senior Manager" are rank
  words for `staff_principal`, and code now applies the existing rule "a
  staff/principal/lead/head-of title always gives `staff_principal`" even when
  the model chose a lower level or none. Gender tags such as "(m/w/d)" are
  removed first, because their slash looked like a range ("Senior Manager ...
  (m/w/d)" became `senior`). (4) Apprenticeships stay under the general rule:
  `fixed_term` only when a length is stated.
- **Why:** on the 50-posting held-out set, the 3 Swisscom postings
  (the sentence appears in about half of Swisscom's ~58 open postings)
  split GPT and Gemini (hybrid) from Opus and Jev (remote). Jev invented
  `nice_to_have` for a language the posting never names in 3 postings, once
  quoting "Home office: up to two days per week". The code before this change only vetoed
  levels, so it could not correct a missed rank.
- **Rejected:** checking only the quoted sentence for the language name
  (correct answers sometimes come without a quote, e.g. "Maîtrise du
  français"); reading the posting's own language as a requirement (against
  "no guessing"; the posting language is already a separate value); filling in
  any level the title names when the model says `not_stated` (only the rank level
  is forced, since the rules already say a rank title always wins).

## 2026-11-04: Group duplicate postings in search
- **What:** search shows one row per job: same employer + same title after
  normalising (gender tags, case, spaces). The best-ranked matching posting is
  shown; the others are listed as `duplicates`. Relevance and extraction stay per
  posting; no schema change.
- **Why:** 857 of ~5,600 open postings repeated another posting of the same
  employer (one copy per city), and one board's repeated ads were most of the
  noise when testing a lower relevance threshold.
- **Rejected:** requiring identical text too (catches 544 of 857; per-city
  variants stay); extracting one posting per group (location facts differ per
  city); marking duplicates in the database (more moving parts for a display
  problem).

## 2026-11-04: Relevance threshold 0.4 -> 0.2 (recall first); threshold changes are free
- **What:** postings with relevance probability >= 0.2 count as relevant. The
  threshold is no longer part of the relevance version: the stored probability does
  not depend on it, so a new threshold only recomputes the `relevant` flag in the
  database (`store.refresh_relevance_flags`, run by `jobfacts relevance` and
  `daily`) instead of re-scoring every posting (~5,600 calls, ~$1.10).
- **Why:** owner priority is recall: a wrong row costs seconds, a missed job is
  never seen. On 149 labelled postings recall goes 95/98 -> 97/98; search grows
  from 417 to 506 jobs (~90 more, about 12-16 plausibly relevant by title), then
  roughly 1-2 extra jobs a day. The three labelled misses share a pattern
  (consulting / methodology / compliance roles with data and AI content, scored
  0.16-0.28), so a lower threshold fixes a consistent blind spot. An independent
  second opinion (Fable subagent) reached the same choice.
- **Rejected:** 0.3 (adds 30 jobs, recovers no labelled miss); 0.22 (same labelled
  recall with 11 fewer jobs, but fitted to one posting; three misses cannot
  separate 0.2 from 0.22); 0.15 (48 more jobs for one more labelled hit; can be
  tried later for free). Main risk: noise makes real roles easier to skim past.

## 2026-11-04: Jev through OpenRouter as a backup
- **What:** when a call to TypeSafe's Jev API fails for any reason, the same call
  goes to OpenRouter's decisions endpoint (`typesafe/jev-1.14`, served by
  TypeSafe). After 3 consecutive TypeSafe failures, the rest of the run uses
  OpenRouter directly. Answers are stored as normal Jev results; the extraction
  details record `route: openrouter`, and `daily` reports how many calls the
  backup served.
- **Why:** a quota or outage at TypeSafe would otherwise leave every new posting
  with an error row. Checked 4 Nov: OpenRouter takes the SDK's exact request body
  and returns the same answers, token counts and price for the same posting, so
  no second model has to be evaluated.
- **Rejected:** DeepSeek or Gemini as backup (a different model needs its own
  accuracy check, and search would mix models); switching only on quota errors
  (TypeSafe's quota signal is not documented: 402, 403 or 429).

## 2026-11-05: SwissDevJobs rejected; only data a site openly allows
- **What:** SwissDevJobs (swissdevjobs.ch) is not a source. It was built and run
  once on 5 Nov, then removed with its data. Sources stay limited to official,
  documented job-board APIs and feeds whose terms allow the use.
- **Why:** SwissDevJobs has no official API; its website's own JSON
  (`/api/jobsLight`) is undocumented. Its robots.txt allows bots to read it, but
  robots.txt grants no right to reuse the data, and no terms of service were
  found. If the project ever turned commercial, it would compete with the site's
  business (paid job posts), which Swiss unfair-competition law (UWG Art. 5 lit. c,
  taking over another's work by technical reproduction) makes risky. A private-only
  exception would have to be removed again before publishing.
- **Rejected:** private use until the site answers an email (the owner preferred
  a clean rule over an exception); leaving it to the owner's private `job_watch`
  only is the owner's own choice outside this project.
