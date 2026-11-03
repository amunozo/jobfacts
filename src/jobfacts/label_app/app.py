"""The labelling API (JSON). The UI in `static/` only talks to these endpoints.

Documented in docs/labelling.md. Blind mode (the first BLIND_COUNT postings by `order`) shows no
model output; later postings come pre-filled with the three-extractor majority (model names are
never sent).
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from jobfacts import labels, locations, sentences
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import FIELDS
from jobfacts.label_app import prefill

BLIND_COUNT = 30
STATIC_DIR = Path(__file__).parent / "static"


def field_spec() -> list[dict]:
    return [
        {
            "name": f.name,
            "question": f.question,
            "values": [
                {"value": v, "definition": d, "needs_sentence": v not in f.no_quote}
                for v, d in f.values.items()
            ],
        }
        for f in FIELDS.values()
    ]


def canton_spec() -> list[dict]:
    return [
        {
            "code": c.code,
            "name_en": c.en,
            "name_de": c.de,
            "name_fr": c.fr,
            "name_it": c.it,
            "places": list(c.places),
        }
        for c in locations.CANTONS.values()
    ]


def sentence_offsets(item: dict) -> list[list[int]] | None:
    """[start, end] in `description_text` for each stored sentence, for the document view.

    The text is split again with the same deterministic splitter; when the ids or texts do not
    match the stored sentences (an older snapshot, a changed splitter), returns None and the page
    falls back to the line view.
    """
    split = sentences.split(item.get("description_text") or "")
    stored = [(s["id"], s["text"]) for s in item["sentences"]]
    if [(s.id, s.text) for s in split] != stored:
        return None
    return [[s.start, s.end] for s in split]


def create_app(
    items: list[dict],
    labels_path: Path,
    extractions: dict[int, dict[str, dict]] | None = None,
    blind_count: int = BLIND_COUNT,
) -> FastAPI:
    app = FastAPI(title="jobfacts labelling")
    by_id = {it["posting_id"]: it for it in items}
    extractions = extractions or {}

    def mode_of(item: dict) -> str:
        return "blind" if item["order"] < blind_count else "prefilled"

    def posting_text(item: dict) -> PostingText:
        return PostingText.build(
            item["posting_id"], item["title"], item["location_raw"], item["description_text"]
        )

    def get_item(posting_id: int) -> dict:
        if posting_id not in by_id:
            raise HTTPException(404, "unknown posting")
        return by_id[posting_id]

    @app.get("/api/spec")
    def spec():
        return {
            "fields": field_spec(),
            "cantons": canton_spec(),
            "outside_value": locations.OUTSIDE,
            "relevant_values": list(labels.RELEVANT_VALUES),
        }

    @app.get("/api/items")
    def list_items():
        done = labels.latest(labels_path)
        return [
            {
                "posting_id": it["posting_id"],
                "order": it["order"],
                "language": it["language"],
                "group": it["group"],
                "employer": it["employer"],
                "title": it["title"],
                "mode": mode_of(it),
                "labelled": it["posting_id"] in done,
            }
            for it in sorted(items, key=lambda x: x["order"])
        ]

    @app.get("/api/items/{posting_id}")
    def get_posting(posting_id: int):
        it = get_item(posting_id)
        mode = mode_of(it)
        pre = None
        if mode == "prefilled":
            by_model = extractions.get(posting_id, {})
            pre = prefill.majority(by_model) if by_model else None
        pt = posting_text(it)
        hints = pt.canton_hints()
        by_canton: dict[str, list[dict]] = {c: [] for c in hints}
        for m, where in pt.places_in(hints):
            by_canton.setdefault(m.canton, []).append(
                {"key": m.key, "name": m.name, "commune": m.commune, "matched": m.matched,
                 "where": where}
            )  # fmt: skip
        if pre and "office_cantons" in pre:
            pre["office_places"] = [m.key for m, _ in pt.places_in(pre["office_cantons"])]
        return {
            "posting_id": posting_id,
            "order": it["order"],
            "language": it["language"],
            "group": it["group"],
            "employer": it["employer"],
            "title": it["title"],
            "location_raw": it["location_raw"],
            "description_text": it.get("description_text") or "",
            "sentences": it["sentences"],
            "offsets": sentence_offsets(it),
            "mode": mode,
            "prefill": pre,
            "canton_hints": hints,
            "place_candidates": by_canton,
            "fields": field_spec(),
            "label": labels.latest(labels_path).get(posting_id),
        }

    @app.put("/api/items/{posting_id}/label")
    async def put_label(posting_id: int, request: Request):
        it = get_item(posting_id)
        try:
            payload = await request.json()
        except ValueError:
            return JSONResponse({"errors": {"body": "not valid JSON"}}, status_code=422)
        if not isinstance(payload, dict):
            return JSONResponse({"errors": {"body": "must be a JSON object"}}, status_code=422)
        try:
            pt = posting_text(it)
            record = labels.save(
                labels_path,
                posting_id,
                payload,
                labels.snapshot_sentence_ids(it),
                mode_of(it),
                default_places=lambda cantons: [m.key for m, _ in pt.places_in(cantons)],
            )
        except labels.ValidationError as error:
            return JSONResponse({"errors": error.errors}, status_code=422)
        return {"saved": record}

    @app.get("/api/progress")
    def progress():
        done = labels.latest(labels_path)
        by_lang: dict[str, dict[str, int]] = {}
        for it in items:
            row = by_lang.setdefault(it["language"], {"done": 0, "total": 0})
            row["total"] += 1
            row["done"] += it["posting_id"] in done
        return {
            "done": sum(r["done"] for r in by_lang.values()),
            "total": len(items),
            "by_language": by_lang,
        }

    if STATIC_DIR.exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
