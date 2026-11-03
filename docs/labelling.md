# Labelling app: files and API

The owner labels the test set (`data/private/test_set.jsonl`, 100 postings) in a
local web page. The page talks to a small JSON API (FastAPI, `src/jobfacts/label_app/`).
Start it with `uv run jobfacts label`; it listens on `127.0.0.1:8765` only.
All data stays in `data/private/` (git-ignored). The examples below are made up.
`--data-dir DIR` reads `test_set.jsonl` and `test_set_runs.json` from `DIR` and writes
`DIR/labels.jsonl` instead (for trying the app on a copy); `--port` changes the port.

The page (`static/`: `index.html`, `app.css`, `app.js`, plain files, no build step) shows
the posting on the left and the label form on the right. Press `?` in the page for the
keyboard shortcuts.

The posting has two views; `Shift+V` or the Document/Lines button above the title switches
between them, and the page remembers the choice (browser storage, per browser):

- **Document view** (default): the posting reads like a rendered note. Sentence numbers are
  hidden, shown on hover and always shown for sentences marked as evidence, with the field
  marks (`MODE`, `DE`, ...) as small badges after the sentence. The layout is rebuilt from the
  plain text between sentences (the stored text has no HTML): a blank line starts a new
  paragraph; a line starting with a bullet marker (`-`, `*`, `•`, `1.`, ..., or a line holding
  only `-` followed by the item text) is a list item, and consecutive items form one list even
  with blank lines between them; a short standalone line (one sentence, at most 10 words and 70
  characters) without ending punctuation, or ending in `:` with at most 6 words, is a heading;
  a plain line right after another plain line continues the paragraph after a line break.
- **Line view**: one numbered row per sentence.

Both views make one clickable element per sentence id, so clicking a sentence toggles it as
evidence the same way. The title (`T`) and location (`L`) rows stay at the top in both.
If the stored sentences do not match a fresh split of `description_text` (`offsets` is
`null`), the page shows the line view for that posting.

## Files (all in `data/private/`)

- `test_set.jsonl`: written by `jobfacts select-test-set` (fixed seed). One full
  snapshot per posting: `order`, `posting_id`, `origin`, `language`, `group`,
  `employer`, `title`, `location_raw`, `description_text`, `sentences`.
- `test_set_runs.json`: extraction run ids per extractor, e.g.
  `{"jev": 7, "deepseek": 8, "mistral": 9}`; used for the pre-fill.
- `labels.jsonl`: append-only, one line per save; the latest line per posting wins.

## Modes

- **Blind**: the first 30 served postings by `order`. No model output is sent (`prefill` is null).
- **Relevance**: `true` for AI, ML, data-science, data-engineering and research
  roles; software or infrastructure roles with real ML/AI components (including
  training infrastructure); product roles on ML/AI products; roles building AI
  evaluations; web-scraping and analytics engineering. `false` otherwise
  (business-intelligence and reporting roles, e.g. Power BI, are `false`); `unsure` only when the posting is too vague to tell.
- **Languages**: only English and French postings are served (`--languages`, default `en,fr`).
  German postings are labelled by three models and checked by the owner from quotes
  (see `docs/decisions.md`, 2026-11-03).
- **Prefilled**: later postings. Each field is pre-filled with the value that at
  least 2 of the 3 extractors agree on, with the sentences chosen by at least 2
  of those; if all three differ, that field has no pre-fill. Model names are
  never sent. `office_cantons` is pre-filled the same way (set equality of the canton sets);
  `office_places` is then the places code finds in the posting inside the pre-filled cantons.
  The mode is decided by the server and saved with the label.

Offices are labelled canton first, then place: the owner confirms the canton set
(`office_cantons`: canton codes and/or `outside_switzerland`; empty = not stated), and the
places inside those cantons (`office_places`, keys `place:<bfs_nr>:<name>`, from the
official swisstopo register, found by code). `canton_hints` and `place_candidates` are
suggestions from code.

Sentence ids: `0` = title, `-1` = location field, `1..n` = description sentences.

## Endpoints

### `GET /api/spec`
```json
{"fields": [{"name": "work_mode", "question": "Where is the work done...?",
             "values": [{"value": "not_stated", "definition": "The posting does not say.",
                         "needs_sentence": false}]}],
 "cantons": [{"code": "ZH", "name_en": "Zurich", "name_de": "Zürich", "name_fr": "Zurich",
              "name_it": "Zurigo", "places": ["Zürich", "Winterthur", "Dübendorf"]}],
 "outside_value": "outside_switzerland",
 "relevant_values": ["true", "false", "unsure"]}
```

### `GET /api/items`
List sorted by `order`. The language and group are shown on purpose.
```json
[{"posting_id": 1234, "order": 0, "language": "en", "group": "app_en",
  "employer": "Acme AG", "title": "ML Engineer", "mode": "blind", "labelled": false}]
```

### `GET /api/items/{posting_id}`
```json
{"posting_id": 1234, "order": 31, "language": "en", "group": "en_ai", "employer": "Acme AG",
 "title": "ML Engineer", "location_raw": "Zürich, Switzerland",
 "description_text": "We work in a hybrid mode.",
 "sentences": [{"id": 1, "text": "We work in a hybrid mode."}],
 "offsets": [[0, 25]],
 "mode": "prefilled",
 "prefill": {"fields": {"work_mode": {"value": "hybrid", "sentences": [1]}},
             "office_cantons": ["ZH"], "office_cantons_sentences": [-1],
             "office_places": ["place:261:Zürich"]},
 "canton_hints": ["ZH", "SZ"],
 "place_candidates": {"ZH": [{"key": "place:261:Zürich", "name": "Zürich", "commune": "Zürich",
                              "matched": "Zürich", "where": "location field"}],
                      "SZ": []},
 "fields": ["... same as /api/spec fields ..."],
 "label": null}
```
`offsets` holds `[start, end]` in `description_text` for each sentence (same order), from a
fresh split with `jobfacts.sentences.split`; it is `null` when that split does not match the
stored ids and texts. `prefill` is `null` in blind mode or when no extraction exists; `prefill.fields`
omits fields without a majority; `office_cantons` / `office_cantons_sentences` /
`office_places` are present only when at least two extractors agree on the canton set.
`canton_hints` are the cantons code finds anywhere in the posting (whole register); 
`place_candidates` maps each hinted canton to the places found in it (`where`: `location field`,
`title` or `posting text`). Both are shown in blind mode too (they come from code, not a model). `label` is the latest saved record or `null`.
404 for an unknown posting.

### `PUT /api/items/{posting_id}/label`
Request (every field of the spec is required):
```json
{"relevant": "true",
 "fields": {"work_mode": {"value": "hybrid", "sentences": [1], "note": ""},
            "remote_from_switzerland": {"value": "not_applicable", "sentences": []},
            "german": {"value": "not_mentioned", "sentences": []},
            "french": {"value": "not_mentioned", "sentences": []},
            "min_years": {"value": "3", "sentences": [2]},
            "title_level": {"value": "not_stated", "sentences": []}},
 "office_cantons": ["ZH"], "office_cantons_sentences": [-1],
 "office_places": ["place:261:Zürich"],
 "note": ""}
```
Rules: `value` must be an allowed value; values that mean "not stated / not mentioned /
not applicable" take no sentences, every other value needs at least one existing
sentence id; `relevant` is `"true"`, `"false"` or `"unsure"` (booleans accepted);
`office_cantons` are codes from `/api/spec` `cantons` plus `outside_value`: a non-empty list
needs at least one existing sentence id in `office_cantons_sentences`, an empty list (not
stated) takes none. `office_places` is optional: when omitted the server stores the places code
finds for the labelled cantons (`[]` is accepted and means none); every key must exist and lie
in a labelled canton. The saved record has the cantons in spec order (`outside_switzerland` last).

Response 200: `{"saved": {<the line appended to labels.jsonl>}}`, which adds
`posting_id`, `labeller` ("owner"), `mode` and `saved_at`.
Response 422, nothing written: `{"errors": {"min_years": "unknown sentence ids [99]"}}`.

### `GET /api/progress`
```json
{"done": 12, "total": 100,
 "by_language": {"en": {"done": 7, "total": 53}, "de": {"done": 3, "total": 26},
                 "fr": {"done": 2, "total": 21}}}
```

## Silver labels by several models

Silver labels for test-set postings: several models label each posting independently with
the prompt template `prompts/label_posting.md` (language-neutral; the field guide in it is
`docs/fields.md`). Combining the answers into final labels is a separate step. No command
here needs the database. Labellers: `gpt` (GPT-6 Astra, pi), `gemini` (Gemini 4 Argon via
opencode-go, pi), `gemini_or` (Gemini 4 Argon called directly through OpenRouter, max 16000
output tokens, thinking effort high) and `opus` (answers written by a Claude Code subagent).

Item sets (`--set`, default `pilot`): `pilot` = 12 postings (the first 4 by `order` of each of
en, de, fr; the English ones include order 0, which the owner labelled), written by
`pilot-select` to `silver/pilot.json`; `de` = the 26 German postings; `all` = the 100.

```
uv run jobfacts silver pilot-select                 # write silver/pilot.json
uv run jobfacts silver render [--set pilot|de|all]  # write the prompts
uv run jobfacts silver run --labeller gpt [--set S] [--limit N] [--posting ID] [--force]
uv run jobfacts silver run --labeller gemini_or
uv run jobfacts silver check --labeller opus        # validate answers written by an agent
uv run jobfacts silver status                       # valid answers per labeller
uv run jobfacts silver report [--set S]             # compare labellers, tokens, cost, time
```

All files are under `data/private/silver/` (git-ignored):

- `pilot.json`: the pilot posting ids.
- `prompts/<posting_id>.md`: the rendered prompt.
- `raw/<labeller>/<posting_id>.txt`: raw model text (for opus, the answer is written
  as `.json` or `.txt` by the other process).
- `<labeller>/<posting_id>.json`: the valid answer, as `{posting_id, labeller, model,
  saved_at, prompt_sha256, answer, usage}`; sentence ids are integers (`T` = 0, `L` = -1).
  `usage` is a list with one entry per attempt (failed ones included):
  `{input, output, reasoning, cost_usd, duration_ms}`. Opus has none; its usage is given in
  `usage_opus.json` as `{"total_tokens": N, "duration_ms": N, "postings": N, "note": "..."}`.

`run` calls the model (pi with `--mode json`, no tools, no session, thinking high; or the
OpenRouter API for `gemini_or`), validates the answer against `jobfacts.extract.spec`
(all fields including `contract_type` are required), and retries once with the error message
appended. pi exits 0 when the model call fails, so the JSON events are read: `stopReason ==
"error"` counts as a failed attempt. Postings that fail twice are listed in the summary and
left without a stored answer.

`report` writes `data/private/reports/silver_<set>_<date>.md`: per field (and `office_cantons`
as an exact set, and `relevant`) the share of postings where all labellers agree / a group of
at least two agrees / all differ, overall and per language; pairwise agreement; low-confidence
counts; agreement with the owner's labels (latest per posting; fields the owner has not
labelled are skipped); tokens, cost and time per labeller; retries. It uses the labellers that
have a valid answer for every posting of the set and lists the others as missing.

### Reference labels: three-model majority and owner review

The reference for scoring the extractors is the majority of three labellers: `gpt`,
`gemini_or` and `opus` (the panel; plain `gemini` is not in it). The owner's own labels
(`data/private/labels.jsonl`) are kept as a human comparison.

```
uv run jobfacts silver combine --set all        # -> data/private/reference_labels.jsonl
uv run jobfacts silver review-sheet [--audit N] # -> data/private/silver/review_sheet.md
uv run jobfacts silver review-apply FILE        # store the owner's decisions
uv run jobfacts evaluate [--reference silver|owner]   # default silver
```

`combine` writes one record per posting that all three labellers answered, in the shape of
an owner label (`fields: {name: {value, sentences}}`, `office_cantons`,
`office_cantons_sentences`, `office_places` filled by code for the cantons, `relevant`),
`labeller: "silver"` and `models`, plus per key:

- `support`: `3/3`, `2/3` or `split`. Majority = at least two equal answers
  (`office_cantons` compared as a set).
- `votes`: each labeller's value; `low_confidence`: the labellers that marked the key low.
- Sentences come from Opus when Opus is in the majority, otherwise from the first majority
  labeller (order gpt, gemini_or). A split key has value `null` (and `splits` lists it).
  Evaluation skips null keys, so `n` differs per field until the owner has decided them.
- `reviewed`: keys decided or checked by the owner (`decided`, `corrected`, `confirmed`).

Owner review is done in chat, without a terminal UI. `review-sheet` writes numbered items:
every unresolved split key, plus an audit sample of 30 unanimous (posting, key) pairs,
drawn uniformly with the fixed seed 20261103 and never repeating a key that was on an
earlier sheet (`silver/review_items.json` records every item handed out, with its number).
Items still undecided stay on the next sheet with the same number, and a new sample is
drawn only when no earlier audit item is waiting. Each item shows the posting id, language,
title, the candidate values with who said what, the allowed values, and the cited sentences
in the original language with the labellers' English translation.

The decisions file (JSON lines or one JSON list) has one line per answered item:
`{"item": 7, "posting_id": 1234, "key": "german", "verdict": "ok"|"wrong", "value": ...}`.
`posting_id` and `key` are optional (taken from the item and checked if present). `value`
is required for split items and for `wrong`, and is checked against the allowed values
(`office_cantons`: a list). `review-apply` is all or nothing and appends to
`silver/review_decisions.jsonl`; the latest decision per key wins. Run `combine` again to
apply them: a decided value overrides the majority, and takes its sentences from a labeller
that gave the same value (none otherwise).

The audit estimates the error rate of the reference: `silver report` has an "Audit" section
with k of n audited unanimous answers correct and a 95% Wilson interval (overall and per
key, where n allows) and the number of split keys resolved. `evaluate` in silver mode names
the reference and its audited error rate in the header, skips blind-vs-prefilled (an
owner-only comparison), and adds a section comparing the reference with the owner's own
labels per key. The silver report is written to `accuracy_silver_<date>.md`, the owner one to
`accuracy_<date>.md`.
