# Labelling app: files and API

The owner labels the test set (`data/private/test_set.jsonl`, 100 postings) in a
local web page. The page talks to a small JSON API (FastAPI, `src/jobfacts/label_app/`).
Start it with `uv run jobfacts label`; it listens on `127.0.0.1:8765` only.
All data stays in `data/private/` (git-ignored). The examples below are made up.
`--data-dir DIR` reads `test_set.jsonl` and `test_set_runs.json` from `DIR` and writes
`DIR/labels.jsonl` instead (for trying the app on a copy); `--port` changes the port.

The page (`static/`: `index.html`, `app.css`, `app.js`, plain files, no build step) shows
the posting as numbered sentences on the left and the label form on the right. Press `?`
in the page for the keyboard shortcuts.

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
 "sentences": [{"id": 1, "text": "We work in a hybrid mode."}],
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
`prefill` is `null` in blind mode or when no extraction exists; `prefill.fields`
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
