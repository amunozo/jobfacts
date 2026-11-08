# Search (Milestone 1)

```
uv run jobfacts extract --extractor jev --for-search   # after each collect: only new/changed postings
# or: uv run jobfacts daily   # database, migrate, collect, relevance and this extract in one go
uv run jobfacts search [QUERY] [filters]
```

## What is searched

Open board postings (`closed_at IS NULL`, `origin = 'board'`) that the relevance filter
kept, using each posting's latest successful Jev extraction from a search run whose `content_hash` equals the posting's current one (latest run first).
Application postings, closed postings and postings without a successful extraction never appear.

`extract --for-search` starts a run marked `purpose = 'search'`. It skips postings that
already have a successful search-run extraction for their current `content_hash`, so
only new, changed or previously failed postings are read. It also redoes postings extracted
under an older `spec_version` (changed extraction rules). When several extractions exist,
search shows the latest one. Experiment runs and extractions of an older text are never shown: a posting whose text changed disappears from search until `jobfacts daily` re-extracts it (an older spec version with the current text is still shown until then). Each hit also carries `location_raw`, the board's raw location text.

## Duplicates are grouped

Many employers post one job once per city (EWOR: the same text in 20 cities; Tether: one
remote role per country). Search shows each job once: postings of the same employer whose
titles are equal after `grouping.title_key` (gender tags such as "(m/w/d)" removed, case,
spaces and `&nbsp;` ignored; other brackets such as "(100 % remote)" kept) form one group.
Grouping happens only here; relevance and extraction still run per posting, because
location facts differ by city. A group appears if any of its postings matches the filters;
the best-ranked matching posting is shown with its facts, and the other matching postings
are listed under it (`+ N more locations: ...`; in JSON `duplicates: [{id, url,
location_raw}]`, always present). `total` and `--limit` count groups. Two different jobs
with the same generic title at one employer are shown as one group with both links
(accepted, 4 Nov 2026). On 4 Nov: 463 postings, 417 groups.

## Query

The command line and the web API (`docs/api.md`) share one code path: `search.prepare` (validate, pick the mode, embed the query) and `search.execute` (run, add warnings); `search.run` chains them.

By default (`--mode hybrid`, `--model mistral`) the query is also embedded and the full-text and
meaning rankings are fused (see "Search by meaning" below); if the query cannot be embedded the
search falls back to words only, with a warning. `--mode words` is the plain full-text search
described here, with no API call.

PostgreSQL full-text search over title + description (`search_tsv`, a generated
column with a GIN index, migration `006_search.sql`). It uses the `simple`
configuration (no stemming): postings are in English, German and French, and one
language's stemmer would damage the other two. Consequence: exact word forms
only ("model" does not match "models"). The query uses web-search syntax: several words
mean AND, `"quoted phrase"`, `OR`, and `-word` to exclude. Title matches weigh more than
description matches. Results are ordered by `ts_rank`; with no query, newest first
(`first_seen_at`).

## Filters (all optional, combined with AND)

| Option | Meaning |
|---|---|
| `--canton ZH,VD` | `office_cantons` overlaps the list (also `outside_switzerland`). |
| `--remote` | `work_mode = remote` and `remote_from_switzerland = allowed`. |
| `--remote-unclear` | Like `--remote`, but also `unclear` and `not_stated` regions. |
| `--work-mode onsite,hybrid,remote` | Work mode is one of these. |
| `--german-max none\|intermediate\|fluent` | Highest German requirement you accept (below). |
| `--french-max ...` | Same for French. |
| `--show-unclear-language` | Keep `required_level_not_stated` although the max is below `fluent`. |
| `--no-fixed-term` | Drop `contract_type = fixed_term`; `permanent`, `not_stated` (counted as permanent) and rows extracted before the field existed stay. |
| `--seniority junior,mid` | Derived seniority is one of these. |
| `--new-days N` | First seen in the last N days. |
| `--limit N` | Jobs (groups) shown (default 20). The header gives the total number of groups. |
| `--mode hybrid\|words\|meaning` | Ranking: `hybrid` (default), `words`, `meaning` (below). |
| `--model mistral\|openai\|qwen` | Embedding model for `hybrid` and `meaning` (default `mistral`). |
| `--evidence` | Print the supporting sentence of each fact under the row. |
| `--json` | JSON (`{"total": n, "results": [...]}`) for other programs. |

Special rule: when `--canton` and `--remote`/`--remote-unclear` are both given, they are
OR-ed ("in these cantons, or remote from Switzerland"); every other filter is AND.

Language maximum (values of the `german` / `french` field):

| Max | Kept values |
|---|---|
| `none` | `not_mentioned`, `nice_to_have` |
| `intermediate` | those + `required_intermediate`, `either_required` |
| `fluent` | those + `required_fluent` and `required_level_not_stated` |

`required_level_not_stated` is excluded when the max is below `fluent`, unless
`--show-unclear-language` is set; such rows are marked `[check language first]`.
Without `--german-max`, German is not filtered. Values outside the spec
(`docs/fields.md`) are rejected with an error (exit code 2).

## Examples (made-up output)

```
$ jobfacts search "machine learning" --canton VD,GE --german-max intermediate --limit 2
14 matches, showing 2

1. Machine Learning Engineer  (Example AG)
   Lausanne | hybrid | de: not_mentioned | fr: nice_to_have | mid | seen 2026-11-01
   https://example.com/jobs/1

2. Data Scientist NLP  (Beispiel SA)
   Genève | onsite | de: required_intermediate | fr: required_fluent | senior | seen 2026-10-28
   https://example.com/jobs/2
```

```
$ jobfacts search "NLP" --remote --evidence --limit 1
$ jobfacts search --canton ZH --seniority junior,mid --new-days 7
$ jobfacts search "data" --french-max none --json
```

## Recall check (search-eval)

```
uv run jobfacts search-eval --applications DIR --dates FILE [--since YYYY-MM-DD] [--out PATH]
```

A replay of the search on the past: the jobs the owner applied to are the jobs they wanted, so
for each application the command decides whether the search would have shown it and, if not,
at which step it was lost.

Inputs:
- `DIR` holds `<Company>/<Role>/job_description.md` (Role may be nested). The key of an
  application is its folder path relative to `DIR`, for example `Acme/ML Engineer`. The first
  line of the file is often the link of the posting.
- `FILE` is a JSON object from that key to the application date (`YYYY-MM-DD`). Applications
  without a date are listed as "no date" and skipped. `--since` keeps applications on or after
  that date.
- Postings imported from the application files (`origin = 'application'`) are ignored; only
  board postings count, open or closed.
- Output: a Markdown report (default `data/private/reports/search_eval_<today>.md`, which is
  git-ignored) and the summary table on stdout.

Matching an application to a board posting:
1. By link: a stable job id is taken from the application link and from each posting's URL and
   `source_job_id` (Ashby, Greenhouse including `gh_jid=`, Lever, SmartRecruiters, Workable,
   Recruitee, Personio, Teamtailor, Workday). Equal ids match. Recruitee slugs and Workday
   requisitions are only unique within a board or tenant, so those keys carry it. Links on
   custom domains are recognised only for stored postings, so they fall through to step 2.
2. Otherwise by company and title. The company folder is matched to the name of a board
   employer after casefolding and removing accents, punctuation and legal suffixes (AG, SA,
   GmbH, Inc, ...); one name inside the other as whole words also counts (`name_contained`),
   if the shorter one has two words or five characters. A link on a known job board also names
   the employer through its board id (`url_board`). Among that employer's postings the title
   must be equal after `grouping.title_key`, or similar: `difflib` ratio of at least 0.9
   (`TITLE_RATIO`) and the same level words (`extract.rules.level_words`: intern, junior,
   senior, staff, principal, lead, head, founding, ...), so "Senior X" never matches "Staff X"
   or "X". The posting first seen closest to the application date wins.
   Only postings that could be the opening applied to are considered: not closed before the
   application date, and, when the employer's board had been read successfully on or before
   that date, not first seen more than 30 days after it (`REPOST_DAYS`; later ones are reposts).
   Dates are Europe/Zurich dates.

The posting that decides the outcome is the one search shows. Search groups postings of the same
employer with the same `title_key`, orders them for an empty query by `first_seen_at DESC, id`,
and the owner-view filters look at the shown posting only. So the matched posting's whole group
is checked, and the group is judged by its first posting (in that order) that reaches the filters
(relevant, extracted); a group-mate with other facts can decide the result. If no group member
gets that far, the furthest failure is reported. When title matching finds postings with
different titles, the group with the furthest outcome wins.

Outcome per application, the first failing step:

| Outcome | Meaning |
|---|---|
| `not_covered` | No board employer matches: a source gap, not a search error. |
| `unknown_before_collection` | Employer covered, no matching posting, and the application predates our first successful read of that board (or the board was never read): the posting may have closed before we started. |
| `not_collected` | Same, but the board had been read before the application: a collector or matching miss. |
| `no_relevance` / `not_relevant` | Posting found; no Jev relevance row, or the row says not relevant (the score is given). |
| `not_eligible` | Relevant and without an extraction, but extraction skips it by design (decision 7a, `PostingText.is_extraction_eligible`): no Swiss location and not remote. The detail is the posting's location. A posting that does have an extraction is judged by the extraction instead. |
| `not_extracted` | Relevant and eligible, but no successful Jev search extraction for the current text. For an open posting an extraction made for older text counts as none (search does not show it; detail "extraction is for older text"); for a closed posting the latest extraction is used and noted. |
| `filtered` | Extracted but dropped by the owner-view filters (`search_eval.filter_reason`): German `required_fluent`; fixed-term contract; remote but not allowed from Switzerland with no Swiss office; not remote and no Swiss office. |
| `found` | Would have been shown. The report adds whether the posting was first seen before, on or after the application date. |

Two recall figures, each with a 95% Wilson interval:
- **Recall within scope (Swiss or remote postings)** = found / (found + not_collected +
  no_relevance + not_relevant + not_extracted + filtered): how many of the postings the search
  is built to cover it would have shown.
- **Recall of all wanted jobs at covered employers** = found / (the same base + not_eligible):
  the same, but counting relevant postings outside Switzerland that are not remote, which
  extraction skips by design.

`not_covered` and `unknown_before_collection` are left out of both bases and reported with their
counts. When the application predates the employer's first successful read, a `found` row says
"collected from <date> (after the application; collection started then)" instead of comparing
the first-seen date. The report also counts applications per link
source (linkedin, hiring.cafe, ats-direct, other, none), has one table per non-found outcome,
and lists the `not_covered` companies so name-matching misses can be spotted by eye.

## Search by meaning (Milestone 2, step 1)

```
uv run jobfacts embed --model mistral|openai|qwen [--limit N] [--char-budget N]
uv run jobfacts search "query" [--mode hybrid|words|meaning] [--model mistral|openai|qwen]
uv run jobfacts meaning-eval --phrases FILE --applications DIR --dates FILE [--models a,b] [--out PATH]
```

An embedding is a list of numbers (a vector) that a model makes from a text so that texts with
a similar meaning get vectors that point the same way. "ML engineer" and "engineer for machine
learning" share almost no words but land close together. Search by meaning embeds the query,
then ranks postings by cosine similarity to it (`<=>` in pgvector is the cosine distance).

**Pool.** `jobfacts embed` embeds every board posting, open or closed, whose relevance score of
the current Jev model is at or above `jev.RELEVANCE_THRESHOLD` (closed ones are needed for the
evaluation). `embed.POOL_FROM` defines it once. Application postings are never embedded.

**Text and budget.** `embed.embedding_text`: title, blank line, description, cut to a character
budget that is set per model in `embed.CHAR_BUDGETS`: Mistral 6000, OpenAI 6000, Qwen 800 (the
local model is too slow on a CPU for long texts, the API models cost cents; the first comparison
ran at 800 for all three, see docs/decisions.md). `--char-budget N` overrides it. Each vector
stores the SHA-256 of its text (`text_hash`), the budget (`char_budget`) and the posting's
`content_hash` at embed time. A rerun skips a posting whose text at this budget is unchanged
(it only refreshes the stored content hash if the posting changed after the cut), re-embeds
one whose text changed, and re-embeds everything when run with another budget. Vectors are stored per (posting, model), so the
models sit side by side; the `vector` column has no fixed dimension (1024, 1536, 1024).
Queries are embedded when asked (one API call or one local forward pass); the web server remembers the last 1,024 query vectors (`docs/api.md`, "Query cache"). Qwen3-Embedding gets
its instruction line (`Instruct: <job-search task>\nQuery: <q>`) on queries only.

**Stale and missing vectors.** `search.stale_embedding_count(conn, model_id)` counts pool
postings with no vector for the model or whose vector's `content_hash` differs from the
posting's current one. `jobfacts search` in `meaning` and `hybrid` mode prints a warning on
stderr when it is above 0 (like the stale relevance rows), and `meaning-eval` does the same
in the report and on stderr, and also warns when a model's vectors have mixed budgets. The
report states each model's budget(s) as read from the stored rows. `jobfacts daily` runs
`embed` for the default model (`embed.DEFAULT_MODEL`, Mistral) after relevance and extraction,
with the cost guard; a missing key, an API error or the guard only prints a warning and the
daily run continues (its summary line reports the number embedded).

**Retries.** API calls retry on rate limits (429), server errors (5xx), timeouts and connection
errors: up to 6 tries for a batch of documents (wait 2, 4, 8 ... seconds, or the server's
`Retry-After`, at most 120 s), 2 tries for a query so that a search does not hang. Other
errors (bad key, bad request) are raised at once. A run that still fails keeps the chunks
already stored; running it again continues.

**Cost guard.** Before any call `embed` estimates the tokens as characters / 3 and refuses to
start when the estimate costs more than $2. It then prints the tokens the provider counted and
the cost (Mistral $0.10, OpenAI $0.02 per million tokens; Qwen is local and free).

**Local model.** Qwen3-Embedding-0.6B needs `sentence-transformers` and torch (CPU wheels),
in the optional uv group `embed-local`, which the server and CI do not install. Run it with
`uv run --group embed-local jobfacts embed --model qwen`. It runs in float32 with 8 threads
(the bfloat16 default of the library was 2-4 times slower on this CPU).

**Search modes.** `--mode hybrid` is the default, with `--model mistral`: it embeds the query,
runs the full-text and the meaning queries with the same filters, takes the best 200 postings of
each and fuses them with reciprocal rank fusion: a posting scores the sum of 1 / (60 + position)
over the lists it is in. RRF uses positions only, so it needs no scaling between a text rank and
a similarity. A posting in only one list still counts. Only these (at most 400) postings are
candidates, so in hybrid `total` counts the jobs among them, not all matches. `--mode meaning`
ranks by similarity alone: every posting that has a vector for `--model` and passes the filters
is a candidate, so a weak match is still listed. `--mode words` is the full-text search above,
unchanged, and never calls an API. Every filter applies in all modes; grouping of duplicates and
`--json` work as before (`rank` is the similarity, the RRF score or the text rank).

**Fallback.** If the query cannot be embedded (no API key, network error, API error after the
retries), hybrid falls back to `words` and prints one warning on stderr; stdout stays valid
(`--json` is still JSON), so other programs that call `jobfacts search --json` keep working
offline. Hybrid without a query (only filters) is the plain filter list: no API call and no
warning. An explicit `--mode meaning` has no words to fall back to and exits with an error;
an explicit `--mode hybrid` or `meaning` with an empty query is an error too. Filter errors
(unknown canton, ...) are reported before any API call.

**Index.** Exact search (a scan of the stored vectors) is used: the pool is a few thousand
rows. No HNSW index: with filters an approximate index can drop matching postings; one is
added only if the load test shows the vector scan as the bottleneck (decision of 2026-11-08).

### Which model? (meaning-eval)

A replay like `search-eval`: the postings matched to the owner's past applications (the same
matching code) are the wanted jobs, restricted to the pool; those with outcome `found` or
`filtered` are "within scope". Every phrase of the phrases file is run as a query with each
method over the whole pool, with no canton or eligibility filter (ranking only). Rankings are
of jobs (duplicates grouped as in search). For a wanted job the best rank over all phrases is
kept, as if the owner had run all phrases; a job matched to several postings counts its best
posting. Reported: recall@10, @20, @50 (share of wanted jobs within that rank for at least one
phrase), MRR of the best rank (0 when no phrase ranks the job), Wilson 95% interval for
recall@20, the wanted jobs each phrase puts in its top 20, and every wanted job's best rank per
method. Methods: `words`, `meaning:<model>`, `hybrid:<model>`. The report goes to
`data/private/reports/meaning_eval_<date>.md` (git-ignored).
