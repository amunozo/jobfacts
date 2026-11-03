"""Reference labels of the test set: the majority of three model labellers, plus owner review.

`combine` reads the stored answers of gpt, gemini_or and opus (see `jobfacts.silver`) and writes
`data/private/reference_labels.jsonl`, one record per posting in the shape of an owner label
(`jobfacts.labels`) with extra keys: `support`, `votes`, `low_confidence`, `splits`, `reviewed`.
A key where no two labellers agree has value null (evaluation skips null keys) until the owner
decides it. The owner reviews the split keys and a random audit sample of unanimous keys
(`review_sheet`, `review_apply`); the audit gives the error rate of the reference.
"""

import json
import random
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobfacts import locations, silver
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import FIELDS, OFFICE_CANTONS
from jobfacts.stats import wilson

PANEL = ("gpt", "gemini_or", "opus")  # in this order: the first majority labeller gives sentences
REFERENCE_FILE = "reference_labels.jsonl"
DECISIONS_FILE = "review_decisions.jsonl"
ISSUED_FILE = "review_items.json"
SHEET_FILE = "review_sheet.md"
AUDIT_SEED = 20261103
AUDIT_SIZE = 30
KEYS = silver.KEYS


def _cmp(answer: dict, key: str) -> Any:
    """Value of an answer in a comparable (hashable) form; cantons as a sorted tuple."""
    value = answer[key]["value"]
    return tuple(sorted(value)) if key == OFFICE_CANTONS else value


def _plain(value: Any) -> Any:
    """Back from the comparable form to what is stored (cantons as a list)."""
    return list(value) if isinstance(value, tuple) else value


def vote(values: dict[str, Any]) -> tuple[Any, str]:
    """(majority value or None, support) over the panel's comparable values.

    Support is "3/3" (all agree), "2/3" (a majority) or "split" (value None)."""
    top, count = Counter(values.values()).most_common(1)[0]
    if count == len(values):
        return top, f"{count}/{len(values)}"
    if count >= 2:
        return top, f"{count}/{len(values)}"
    return None, "split"


def _dir(data_dir: Path) -> Path:
    return Path(data_dir) / "silver"


def load_panel(data_dir: Path, pids: list[int]) -> tuple[dict[str, dict[int, dict]], list[int]]:
    """Stored records per panel labeller, and the posting ids that lack an answer from any."""
    answers: dict[str, dict[int, dict]] = {lab: {} for lab in PANEL}
    missing: list[int] = []
    for pid in pids:
        recs = {}
        for lab in PANEL:
            path = _dir(data_dir) / lab / f"{pid}.json"
            if path.exists():
                recs[lab] = json.loads(path.read_text(encoding="utf-8"))
        if len(recs) < len(PANEL):
            missing.append(pid)
            continue
        for lab, rec in recs.items():
            answers[lab][pid] = rec
    return answers, missing


# ----- review decisions -----------------------------------------------------------------


def load_decisions(data_dir: Path) -> dict[tuple[int, str], dict]:
    """Latest decision per (posting id, key)."""
    path = _dir(data_dir) / DECISIONS_FILE
    out: dict[tuple[int, str], dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[(rec["posting_id"], rec["key"])] = rec
    return out


def load_issued(data_dir: Path) -> list[dict]:
    """All review items handed out so far: item, posting_id, key, kind (split|audit), sheet."""
    path = _dir(data_dir) / ISSUED_FILE
    return json.loads(path.read_text(encoding="utf-8"))["items"] if path.exists() else []


def allowed_values(key: str) -> list[str]:
    if key == OFFICE_CANTONS:
        return list(locations.office_canton_values())
    if key == "relevant":
        return sorted(silver.RELEVANT)
    return list(FIELDS[key].values)


def check_value(key: str, value: Any) -> str | None:
    """Problem with a decided value, or None."""
    allowed = allowed_values(key)
    if key == OFFICE_CANTONS:
        if not isinstance(value, list) or not all(isinstance(c, str) for c in value):
            return "office_cantons needs a list of canton codes"
        bad = sorted(set(value) - set(allowed))
        if bad:
            return f"unknown canton codes {bad}"
        return None
    if value not in allowed:
        return f"{value!r} is not allowed for {key}; allowed: {allowed}"
    return None


# ----- combine --------------------------------------------------------------------------


def _chosen(key: str, value: Any, answers: dict[str, dict]) -> tuple[str | None, dict | None]:
    """Labeller whose answer gives the sentences for `value`: Opus if it agrees, else the first
    labeller (panel order) that does."""
    agreeing = [
        lab for lab in PANEL if lab in answers and _cmp(answers[lab]["answer"], key) == value
    ]
    if not agreeing:
        return None, None
    lab = "opus" if "opus" in agreeing else agreeing[0]
    return lab, answers[lab]["answer"][key]


def combine_posting(
    item: dict, recs: dict[str, dict], decisions: dict[tuple[int, str], dict]
) -> dict:
    """Reference record of one posting from the panel's stored records."""
    pid = item["posting_id"]
    pt = PostingText.build(pid, item["title"], item["location_raw"], item["description_text"])
    support: dict[str, str] = {}
    votes: dict[str, dict[str, Any]] = {}
    low: dict[str, list[str]] = {}
    reviewed: dict[str, str] = {}
    values: dict[str, Any] = {}
    sentences: dict[str, list[int]] = {}
    translations: dict[str, str] = {}
    for key in KEYS:
        answers = {lab: rec for lab, rec in recs.items()}
        cmp = {lab: _cmp(rec["answer"], key) for lab, rec in answers.items()}
        votes[key] = {lab: _plain(v) for lab, v in cmp.items()}
        value, sup = vote(cmp)
        support[key] = sup
        flagged = [
            lab
            for lab, rec in answers.items()
            if key != "relevant" and rec["answer"][key].get("confidence") == "low"
        ]
        if flagged:
            low[key] = flagged
        sents: list[int] = []
        translation = ""
        if value is not None and key != "relevant":
            _, entry = _chosen(key, value, answers)
            sents, translation = list(entry["sentences"]), entry.get("translation", "")
        decision = decisions.get((pid, key))
        if decision and (decision["verdict"] == "wrong" or value is None):
            new = decision.get("value")
            if new is not None:
                comparable = tuple(sorted(new)) if key == OFFICE_CANTONS else new
                value, sents, translation = comparable, [], ""
                if key != "relevant":
                    _, entry = _chosen(key, comparable, answers)
                    if entry:
                        sents, translation = list(entry["sentences"]), entry.get("translation", "")
                reviewed[key] = "decided" if sup == "split" else "corrected"
        elif decision and decision["verdict"] == "ok":
            reviewed[key] = "confirmed"
        values[key] = _plain(value)
        sentences[key] = sents
        translations[key] = translation
    fields = {}
    for name in FIELDS:
        v = values[name]
        fields[name] = {
            "value": v,
            "sentences": sentences[name] if v is not None else [],
            "note": "split: no majority" if v is None else "",
            "translation": translations[name],
        }
    cantons = values[OFFICE_CANTONS]
    places = [m.key for m, _ in pt.places_in(cantons)] if cantons is not None else None
    allowed = locations.office_canton_values()
    return {
        "posting_id": pid,
        "labeller": "silver",
        "models": {lab: silver.LABELLERS[lab] for lab in PANEL},
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "relevant": values["relevant"],
        "fields": fields,
        "office_cantons": [c for c in allowed if c in set(cantons)]
        if cantons is not None
        else None,
        "office_cantons_sentences": sentences[OFFICE_CANTONS] if cantons is not None else [],
        "office_places": places,
        "note": "",
        "support": support,
        "votes": votes,
        "low_confidence": low,
        "splits": [k for k in KEYS if values[k] is None],
        "reviewed": reviewed,
    }


def combine(data_dir: Path, set_name: str = "all") -> dict:
    """Write `reference_labels.jsonl` for the postings all three labellers answered.

    Returns a summary: postings written, missing, unresolved splits and support counts."""
    data_dir = Path(data_dir)
    items = silver.items_for(data_dir, set_name)
    answers, missing = load_panel(data_dir, [i["posting_id"] for i in items])
    decisions = load_decisions(data_dir)
    records = []
    for item in items:
        pid = item["posting_id"]
        if pid in missing:
            continue
        recs = {lab: answers[lab][pid] for lab in PANEL}
        records.append(combine_posting(item, recs, decisions))
    out = data_dir / REFERENCE_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    support = Counter(s for r in records for s in r["support"].values())
    return {
        "written": len(records),
        "missing": missing,
        "support": dict(support),
        "unresolved": {r["posting_id"]: r["splits"] for r in records if r["splits"]},
        "reviewed": sum(len(r["reviewed"]) for r in records),
        "path": out,
    }


def load_reference(data_dir: Path) -> dict[int, dict]:
    path = Path(data_dir) / REFERENCE_FILE
    out: dict[int, dict] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rec = json.loads(line)
                out[rec["posting_id"]] = rec
    return out


# ----- review sheet ---------------------------------------------------------------------


def _short(text: str, limit: int = 280) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def _quote(pt: PostingText, ids: list[int]) -> str:
    return " | ".join(_short(pt.text_of(i)) for i in ids)


def _show(value: Any) -> str:
    if isinstance(value, list):
        return "[" + ", ".join(value) + "]" if value else "[] (not stated)"
    return str(value)


def render_item(number: int, kind: str, item: dict, key: str, recs: dict[str, dict]) -> str:
    pt = PostingText.build(
        item["posting_id"], item["title"], item["location_raw"], item["description_text"]
    )
    groups: dict[Any, list[str]] = {}
    for lab in PANEL:
        groups.setdefault(_cmp(recs[lab]["answer"], key), []).append(lab)
    head = (
        f"### {number}. [{kind}] posting {item['posting_id']} - {item['language']} - "
        f"{_short(item['title'], 80)} - **{key}**"
    )
    lines = [head, f"allowed: {' | '.join(allowed_values(key))}"]
    for value, labs in groups.items():
        entry_of = {lab: recs[lab]["answer"][key] for lab in labs}
        who = ", ".join(labs)
        if key == "relevant":
            reasons = "; ".join(_short(entry_of[lab].get("reason", ""), 160) for lab in labs[:1])
            lines.append(f"- **{_show(_plain(value))}** ({who}): {reasons}")
            continue
        low = [lab for lab in labs if entry_of[lab].get("confidence") == "low"]
        flag = f" [low confidence: {', '.join(low)}]" if low else ""
        lines.append(f"- **{_show(_plain(value))}** ({who}){flag}")
        shown: set[tuple] = set()
        for lab in labs:
            ids = tuple(entry_of[lab]["sentences"])
            if not ids or ids in shown:
                continue
            shown.add(ids)
            lines.append(f"  - quote: {_quote(pt, list(ids))}")
            if entry_of[lab].get("translation"):
                lines.append(f"  - EN: {_short(entry_of[lab]['translation'], 400)}")
        notes = [entry_of[lab].get("note", "") for lab in labs if entry_of[lab].get("note")]
        if notes:
            lines.append(f"  - note: {_short(notes[0], 300)}")
    return "\n".join(lines)


def review_sheet(data_dir: Path, set_name: str = "all", audit: int = AUDIT_SIZE) -> dict:
    """Write `silver/review_sheet.md`: unresolved split keys plus an audit sample.

    Items already handed out and still undecided stay on the sheet with their number. A new
    audit sample (seed `AUDIT_SEED`, uniform over unanimous (posting, key) pairs not on any
    earlier sheet) is drawn only when no earlier audit item is waiting for a decision."""
    data_dir = Path(data_dir)
    items = {i["posting_id"]: i for i in silver.items_for(data_dir, set_name)}
    answers, _ = load_panel(data_dir, list(items))
    decisions = load_decisions(data_dir)
    issued = load_issued(data_dir)
    seen = {(i["posting_id"], i["key"]) for i in issued}
    sheet_no = max((i["sheet"] for i in issued), default=0) + 1
    next_no = max((i["item"] for i in issued), default=0) + 1

    def pending(entry: dict) -> bool:
        return (entry["posting_id"], entry["key"]) not in decisions

    complete = [pid for pid in items if all(pid in answers[lab] for lab in PANEL)]
    splits, unanimous = [], []
    for pid in complete:
        recs = {lab: answers[lab][pid] for lab in PANEL}
        for key in KEYS:
            _, sup = vote({lab: _cmp(r["answer"], key) for lab, r in recs.items()})
            if sup == "split":
                splits.append((pid, key))
            elif sup == "3/3":
                unanimous.append((pid, key))
    new: list[dict] = []
    for pid, key in splits:
        if (pid, key) not in seen and (pid, key) not in decisions:
            new.append({"item": next_no, "posting_id": pid, "key": key, "kind": "split",
                        "sheet": sheet_no})  # fmt: skip
            next_no += 1
    waiting_audit = [i for i in issued if i["kind"] == "audit" and pending(i)]
    if not waiting_audit and audit:
        pool = sorted(p for p in unanimous if p not in seen)
        for pid, key in random.Random(AUDIT_SEED).sample(pool, min(audit, len(pool))):
            new.append({"item": next_no, "posting_id": pid, "key": key, "kind": "audit",
                        "sheet": sheet_no})  # fmt: skip
            next_no += 1
    on_sheet = [i for i in issued if pending(i)] + new
    on_sheet.sort(key=lambda i: i["item"])
    path = _dir(data_dir) / ISSUED_FILE
    path.write_text(json.dumps({"items": issued + new}, indent=1) + "\n", encoding="utf-8")
    n_split = sum(i["kind"] == "split" for i in on_sheet)
    n_audit = len(on_sheet) - n_split
    lines = [
        f"# Review sheet {sheet_no}",
        "",
        f"{len(on_sheet)} items: {n_split} split keys (no two labellers agree: choose the value) "
        f"and {n_audit} audit keys (all three agree: say ok, or give the right value).",
        "Reply per item number, for example `3 ok`, `4 wrong stationary`, `5 hybrid`.",
        "",
    ]
    for entry in on_sheet:
        item = items[entry["posting_id"]]
        recs = {lab: answers[lab][entry["posting_id"]] for lab in PANEL}
        lines.append(render_item(entry["item"], entry["kind"], item, entry["key"], recs))
        lines.append("")
    out = _dir(data_dir) / SHEET_FILE
    out.write_text("\n".join(lines), encoding="utf-8")
    return {"path": out, "splits": n_split, "audit": n_audit, "new": len(new),
            "incomplete": len(items) - len(complete)}  # fmt: skip


# ----- review apply ---------------------------------------------------------------------


def parse_decisions(text: str) -> list[dict]:
    """JSON lines (or one JSON list) of decisions."""
    text = text.strip()
    if text.startswith("["):
        return json.loads(text)
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def review_apply(data_dir: Path, file: Path) -> dict:
    """Validate a decisions file against the issued items and store the decisions.

    All or nothing: one bad line raises ValueError and nothing is stored. Later decisions for
    the same (posting, key) replace earlier ones when `combine` runs."""
    data_dir = Path(data_dir)
    issued = {i["item"]: i for i in load_issued(data_dir)}
    out: list[dict] = []
    problems: list[str] = []
    for n, d in enumerate(parse_decisions(Path(file).read_text(encoding="utf-8")), 1):
        entry = issued.get(d.get("item"))
        if entry is None:
            problems.append(f"line {n}: item {d.get('item')!r} was never on a sheet")
            continue
        pid, key = d.get("posting_id", entry["posting_id"]), d.get("key", entry["key"])
        if (pid, key) != (entry["posting_id"], entry["key"]):
            problems.append(f"line {n}: item {entry['item']} is posting {entry['posting_id']} "
                            f"{entry['key']}, not {pid} {key}")  # fmt: skip
            continue
        verdict = d.get("verdict")
        if verdict not in ("ok", "wrong"):
            problems.append(f"line {n}: verdict must be ok or wrong")
            continue
        value = d.get("value")
        if entry["kind"] == "split" or verdict == "wrong":
            if "value" not in d:
                problems.append(f"line {n}: item {entry['item']} needs a value")
                continue
            if bad := check_value(key, value):
                problems.append(f"line {n}: {bad}")
                continue
        rec = {
            "item": entry["item"],
            "posting_id": pid,
            "key": key,
            "kind": entry["kind"],
            "verdict": verdict,
            "decided_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if "value" in d and (verdict == "wrong" or entry["kind"] == "split"):
            rec["value"] = value
        out.append(rec)
    if problems:
        raise ValueError("; ".join(problems))
    path = _dir(data_dir) / DECISIONS_FILE
    with path.open("a", encoding="utf-8") as f:
        for rec in out:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return {"stored": len(out), "path": path}


# ----- audit ----------------------------------------------------------------------------


def audit_stats(data_dir: Path) -> dict:
    """Audit result: per key and overall, [correct, audited] over decided audit items, and the
    number of split keys issued / decided."""
    issued = load_issued(data_dir)
    decisions = load_decisions(data_dir)
    per_key: dict[str, list[int]] = {}
    for entry in issued:
        d = decisions.get((entry["posting_id"], entry["key"]))
        if entry["kind"] != "audit" or d is None:
            continue
        cell = per_key.setdefault(entry["key"], [0, 0])
        cell[0] += d["verdict"] == "ok"
        cell[1] += 1
    splits = [i for i in issued if i["kind"] == "split"]
    resolved = sum((i["posting_id"], i["key"]) in decisions for i in splits)
    k = sum(c[0] for c in per_key.values())
    n = sum(c[1] for c in per_key.values())
    return {"per_key": per_key, "correct": k, "audited": n, "splits": len(splits),
            "splits_resolved": resolved}  # fmt: skip


def audit_text(stats: dict) -> str:
    """One sentence for report headers."""
    n, k = stats["audited"], stats["correct"]
    if not n:
        return "no audit yet"
    lo, hi = wilson(k, n)
    return (f"audit: {k} of {n} unanimous answers correct "
            f"({100 * (n - k) / n:.0f}% error, 95% interval for correct "
            f"{100 * lo:.0f}-{100 * hi:.0f}%)")  # fmt: skip
