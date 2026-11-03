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

## Query

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
| `--limit N` | Rows shown (default 20). The header gives the total. |
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
