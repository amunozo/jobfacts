# Labelling app: files and API

The owner labels the test set (`data/private/test_set.jsonl`, 100 postings) in a
local web page. The page talks to a small JSON API (FastAPI, `src/jobfacts/label_app/`).
Start it with `uv run jobfacts label`; it listens on `127.0.0.1:8765` only.
All data stays in `data/private/` (git-ignored). The examples below are made up.

## Files (all in `data/private/`)

- `test_set.jsonl`: written by `jobfacts select-test-set` (fixed seed). One full
  snapshot per posting: `order`, `posting_id`, `origin`, `language`, `group`,
  `employer`, `title`, `location_raw`, `description_text`, `sentences`.
- `test_set_runs.json`: extraction run ids per extractor, e.g.
  `{"jev": 7, "deepseek": 8, "mistral": 9}`; used for the pre-fill.
- `labels.jsonl`: append-only, one line per save; the latest line per posting wins.

## Modes

- **Blind**: postings with `order` < 30. No model output is sent (`prefill` is null).
- **Prefilled**: later postings. Each field is pre-filled with the value that at
  least 2 of the 3 extractors agree on, with the sentences chosen by at least 2
  of those; if all three differ, that field has no pre-fill. Model names are
  never sent. `office_locations` is pre-filled the same way (set equality).
  The mode is decided by the server and saved with the label.

Sentence ids: `0` = title, `-1` = location field, `1..n` = description sentences.

## Endpoints

### `GET /api/spec`
```json
{"fields": [{"name": "work_mode", "question": "Where is the work done...?",
             "values": [{"value": "not_stated", "definition": "The posting does not say.",
                         "needs_sentence": false}]}],
 "location_keys": ["canton:ZH", "city:Zürich", "country:CH", "outside_switzerland"],
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
 "sentences": [{"id": 1, "text": "We work in a hybrid mode."}],
 "mode": "prefilled",
 "prefill": {"fields": {"work_mode": {"value": "hybrid", "sentences": [1]}},
             "office_locations": ["city:Zürich"], "office_locations_sentences": [-1]},
 "location_candidates": [{"key": "city:Zürich", "matched": "Zürich", "where": "location field"}],
 "fields": ["... same as /api/spec fields ..."],
 "label": null}
```
`prefill` is `null` in blind mode or when no extraction exists; `prefill.fields`
omits fields without a majority. `label` is the latest saved record or `null`.
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
 "office_locations": ["city:Zürich"], "office_locations_sentences": [-1],
 "note": ""}
```
Rules: `value` must be an allowed value; values that mean "not stated / not mentioned /
not applicable" take no sentences, every other value needs at least one existing
sentence id; `relevant` is `"true"`, `"false"` or `"unsure"` (booleans accepted);
`office_locations` are keys from `/api/spec`.

Response 200: `{"saved": {<the line appended to labels.jsonl>}}`, which adds
`posting_id`, `labeller` ("owner"), `mode` and `saved_at`.
Response 422, nothing written: `{"errors": {"min_years": "unknown sentence ids [99]"}}`.

### `GET /api/progress`
```json
{"done": 12, "total": 100,
 "by_language": {"en": {"done": 7, "total": 53}, "de": {"done": 3, "total": 26},
                 "fr": {"done": 2, "total": 21}}}
```
