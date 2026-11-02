"""The labelling API (JSON). The UI in `static/` only talks to these endpoints.

Documented in docs/labelling.md. Blind mode (the first BLIND_COUNT postings by `order`) shows no
model output; later postings come pre-filled with the three-extractor majority (model names are
never sent).
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from jobfacts import labels, locations
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

    def get_item(posting_id: int) -> dict:
        if posting_id not in by_id:
            raise HTTPException(404, "unknown posting")
        return by_id[posting_id]

    @app.get("/api/spec")
    def spec():
        return {
            "fields": field_spec(),
            "location_keys": sorted(locations.all_keys()),
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
        pt = PostingText.build(posting_id, it["title"], it["location_raw"], it["description_text"])
        return {
            "posting_id": posting_id,
            "order": it["order"],
            "language": it["language"],
            "group": it["group"],
            "employer": it["employer"],
            "title": it["title"],
            "location_raw": it["location_raw"],
            "sentences": it["sentences"],
            "mode": mode,
            "prefill": pre,
            "location_candidates": [
                {"key": c.key, "matched": c.matched, "where": where} for c, where in pt.candidates()
            ],
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
            record = labels.save(
                labels_path, posting_id, payload, labels.snapshot_sentence_ids(it), mode_of(it)
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
