"""Pre-fill rule (decided): the MAJORITY value of the three extractors, model names hidden.

A value is pre-filled when at least 2 of the (successful) extractors give the same value; with
three different values there is no pre-fill for that field. The supporting sentences are the ones
chosen by at least 2 of the agreeing extractors; if they share none, all sentences they chose.
"""

from collections import Counter

import psycopg

from jobfacts import locations
from jobfacts.extract.spec import FIELDS

# {"values", "sentences", "office_cantons", "office_cantons_sentences"}
Extraction = dict


def _sentences(chosen: list[list[int]]) -> list[int]:
    votes = Counter(i for s in chosen for i in set(s))
    shared = sorted(i for i, n in votes.items() if n >= 2)
    return shared or sorted(votes)


def majority(by_model: dict[str, Extraction]) -> dict:
    """`by_model` maps extractor name to its extraction. Returns the pre-fill (may be empty)."""
    fields: dict[str, dict] = {}
    for name in FIELDS:
        votes = [(e["values"][name], e["sentences"].get(name, [])) for e in by_model.values()]
        count = Counter(v for v, _ in votes)
        if not count:
            continue
        value, n = count.most_common(1)[0]
        if n >= 2:
            fields[name] = {
                "value": value,
                "sentences": _sentences([s for v, s in votes if v == value]),
            }
    result: dict = {"fields": fields}
    sets = {k: frozenset(e["office_cantons"]) for k, e in by_model.items()}
    count = Counter(sets.values())
    if count:
        chosen, n = count.most_common(1)[0]
        if n >= 2:
            agreeing = [e for k, e in by_model.items() if sets[k] == chosen]
            result["office_cantons"] = [c for c in locations.office_canton_values() if c in chosen]
            result["office_cantons_sentences"] = _sentences(
                [e.get("office_cantons_sentences", []) for e in agreeing]
            )
    return result


def load_extractions(
    conn: psycopg.Connection, run_ids: dict[str, int]
) -> dict[int, dict[str, Extraction]]:
    """posting_id -> extractor -> extraction, for the given runs (errored rows are skipped)."""
    cols = ", ".join(f"{n}, {n}_sentences" for n in FIELDS)
    out: dict[int, dict[str, Extraction]] = {}
    for extractor, run_id in run_ids.items():
        rows = conn.execute(
            f"SELECT posting_id, office_cantons, office_cantons_sentences, {cols} FROM extractions "
            "WHERE run_id = %s AND error IS NULL",
            (run_id,),
        ).fetchall()
        for posting_id, cantons, canton_sentences, *rest in rows:
            values = {n: rest[2 * i] for i, n in enumerate(FIELDS)}
            sentences = {n: rest[2 * i + 1] or [] for i, n in enumerate(FIELDS)}
            out.setdefault(posting_id, {})[extractor] = {
                "values": values,
                "sentences": sentences,
                "office_cantons": list(cantons or []),
                "office_cantons_sentences": list(canton_sentences or []),
            }
    return out
