"""Owner labels: append-only JSON lines in `data/private/labels.jsonl` (git-ignored).

One line per save; the latest line per posting wins. A record:

    {"posting_id": 1, "labeller": "owner", "mode": "blind"|"prefilled", "saved_at": "...",
     "relevant": "true"|"false"|"unsure",
     "fields": {"work_mode": {"value": "hybrid", "sentences": [4], "note": ""}, ...},
     "office_cantons": ["ZH"], "office_cantons_sentences": [-1],
     "office_places": ["place:261:Zürich"], "note": ""}

`office_cantons` are canton codes plus "outside_switzerland" (empty = not stated); `office_places`
are place keys inside those cantons (see `jobfacts.locations`); when the payload omits them, the
server fills in the places code finds in the posting for the labelled cantons.

Sentence ids follow the extraction convention: 0 = title, -1 = location field, 1..n = sentences.
"""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from jobfacts import locations
from jobfacts.extract.spec import FIELDS, LOCATION_ID, TITLE_ID

PRIVATE_DIR = Path(__file__).resolve().parents[2] / "data" / "private"
LABELS_FILE = PRIVATE_DIR / "labels.jsonl"
RELEVANT_VALUES = ("true", "false", "unsure")


class ValidationError(Exception):
    def __init__(self, errors: dict[str, str]):
        super().__init__(errors)
        self.errors = errors


def load_all(path: Path = LABELS_FILE) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def latest(path: Path = LABELS_FILE) -> dict[int, dict]:
    """The most recent saved label of each posting."""
    return {rec["posting_id"]: rec for rec in load_all(path)}


def _sentence_ids(value, known: set[int]) -> str | None:
    if not isinstance(value, list) or not all(
        isinstance(i, int) and not isinstance(i, bool) for i in value
    ):
        return "sentences must be a list of integers"
    unknown = sorted(set(value) - known)
    if unknown:
        return f"unknown sentence ids {unknown}"
    return None


def validate(
    payload: dict,
    sentence_ids: set[int],
    default_places: Callable[[list[str]], list[str]] | None = None,
) -> dict:
    """Check a label payload; return the cleaned record parts or raise ValidationError.

    `sentence_ids` are the ids that exist in the posting snapshot (including 0 and -1).
    `default_places(cantons)` gives the places to store when the payload has no `office_places`.
    """
    errors: dict[str, str] = {}
    fields_in = payload.get("fields") or {}
    clean_fields: dict[str, dict] = {}
    for name, spec in FIELDS.items():
        entry = fields_in.get(name)
        if not isinstance(entry, dict):
            errors[name] = "missing"
            continue
        value = entry.get("value")
        sentences = entry.get("sentences", [])
        note = entry.get("note", "") or ""
        if value not in spec.values:
            errors[name] = f"value {value!r} is not allowed; allowed: {', '.join(spec.values)}"
            continue
        bad = _sentence_ids(sentences, sentence_ids)
        if bad:
            errors[name] = bad
        elif value in spec.no_quote and sentences:
            errors[name] = f"{value!r} carries no supporting sentence"
        elif value not in spec.no_quote and not sentences:
            errors[name] = f"{value!r} needs at least one supporting sentence"
        elif not isinstance(note, str):
            errors[name] = "note must be text"
        else:
            clean_fields[name] = {"value": value, "sentences": sorted(set(sentences)), "note": note}
    for extra in set(fields_in) - set(FIELDS):
        errors[extra] = "unknown field"

    relevant = payload.get("relevant")
    if isinstance(relevant, bool):
        relevant = "true" if relevant else "false"
    if relevant not in RELEVANT_VALUES:
        errors["relevant"] = "must be true, false or unsure"

    cantons = payload.get("office_cantons")
    allowed = locations.office_canton_values()
    if not isinstance(cantons, list) or not all(isinstance(k, str) for k in cantons):
        errors["office_cantons"] = "must be a list of canton codes and/or outside_switzerland"
        cantons = []
    elif bad_keys := sorted(set(cantons) - set(allowed)):
        errors["office_cantons"] = f"unknown canton codes {bad_keys}"
        cantons = []
    loc_sentences = payload.get("office_cantons_sentences", [])
    bad = _sentence_ids(loc_sentences, sentence_ids)
    if bad:
        errors["office_cantons_sentences"] = bad
    elif "office_cantons" not in errors and cantons and not loc_sentences:
        errors["office_cantons_sentences"] = "office cantons need at least one supporting sentence"
    elif "office_cantons" not in errors and not cantons and loc_sentences:
        errors["office_cantons_sentences"] = "no office cantons (not stated) carries no sentence"
    places = payload.get("office_places")
    if places is None:
        places = default_places(cantons) if default_places else []
    if not isinstance(places, list) or not all(isinstance(k, str) for k in places):
        errors["office_places"] = "must be a list of place keys"
        places = []
    else:
        known = locations.all_place_keys()
        wrong = [k for k in places if k not in known]
        outside = [
            k for k in places if k in known and locations.place_by_key(k).canton not in cantons
        ]
        if wrong:
            errors["office_places"] = f"unknown place keys {sorted(wrong)}"
        elif outside:
            errors["office_places"] = f"places outside the chosen cantons {sorted(outside)}"
    if errors:
        raise ValidationError(errors)
    return {
        "relevant": relevant,
        "fields": clean_fields,
        "office_cantons": [c for c in allowed if c in set(cantons)],
        "office_cantons_sentences": sorted(set(loc_sentences)),
        "office_places": sorted(set(places)),
        "note": str(payload.get("note", "") or ""),
    }


def save(
    path: Path,
    posting_id: int,
    payload: dict,
    sentence_ids: set[int],
    mode: str,
    default_places: Callable[[list[str]], list[str]] | None = None,
) -> dict:
    """Validate and append one label line. Raises ValidationError; nothing is written then."""
    record = {
        "posting_id": posting_id,
        "labeller": "owner",
        "mode": mode,
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **validate(payload, sentence_ids, default_places),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def snapshot_sentence_ids(item: dict) -> set[int]:
    return {TITLE_ID, LOCATION_ID, *(s["id"] for s in item["sentences"])}
