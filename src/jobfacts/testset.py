"""Select the 100-posting test set (deterministic) and write a full snapshot.

Output: `data/private/test_set.jsonl` (git-ignored). Each line is a self-contained snapshot so
that evaluation stays reproducible when a posting later changes or closes.
"""

import json
import random
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import psycopg

from jobfacts.extract import jev
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import FIELDS

SEED = 20261102
PRIVATE_DIR = Path(__file__).resolve().parents[2] / "data" / "private"
TEST_SET_FILE = PRIVATE_DIR / "test_set.jsonl"
HOLDOUT_FILE = PRIVATE_DIR / "holdout_set.jsonl"
HOLDOUT_SEED = 20261104
BORDERLINE_RANGE = (0.3, 0.6)
TOTAL = 100
BOARD_TARGET = 50
N_DE_OTHER = 20
N_FR_AI = 8
N_BORDERLINE = 5
DE_AI_MIN_PROB = 0.35
LANG_ORDER = ("en", "de", "fr")


def _spread(items: list[dict], key: str, per_key: int, n: int, rng: random.Random) -> list[dict]:
    """Take up to n items, round-robin over the values of `key`, at most per_key each."""
    buckets: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        buckets[it[key]].append(it)
    for b in buckets.values():
        rng.shuffle(b)
    names = sorted(buckets)
    rng.shuffle(names)
    out: list[dict] = []
    for round_ in range(per_key):
        for name in names:
            if round_ < len(buckets[name]) and len(out) < n:
                out.append(buckets[name][round_])
    return out


def _disagreement(conn: psycopg.Connection) -> dict[int, int]:
    """Per posting: number of fields on which the extractors in existing runs differ."""
    names = list(FIELDS)
    cols = ", ".join(f"e.{n}" for n in names)
    rows = conn.execute(
        f"SELECT e.posting_id, r.extractor, {cols} FROM extractions e "
        "JOIN extraction_runs r ON r.id = e.run_id WHERE e.error IS NULL ORDER BY e.run_id"
    ).fetchall()
    latest: dict[int, dict[str, tuple]] = defaultdict(dict)
    for row in rows:
        latest[row[0]][row[1]] = row[2:]
    score = {}
    for pid, by_model in latest.items():
        if len(by_model) < 2:
            continue
        score[pid] = sum(
            len({vals[i] for vals in by_model.values()}) > 1 for i in range(len(names))
        )
    return score


def _load(conn: psycopg.Connection, origin: str) -> list[dict]:
    rows = conn.execute(
        "SELECT p.id, e.name, p.title, p.location_raw, p.description_text, r.probability "
        "FROM postings p JOIN employers e ON e.id = p.employer_id "
        "LEFT JOIN relevance r ON r.posting_id = p.id AND r.model = %s "
        "WHERE p.origin = %s AND p.description_text <> '' "
        + ("AND p.closed_at IS NULL " if origin == "board" else "")
        + "ORDER BY p.id",
        (jev.MODEL, origin),
    ).fetchall()
    out = []
    for pid, employer, title, loc, desc, prob in rows:
        pt = PostingText.build(pid, title, loc, desc)
        out.append(
            {
                "posting_id": pid,
                "origin": origin,
                "language": pt.language,
                "employer": employer,
                "title": title,
                "location_raw": loc,
                "description_text": desc,
                "prob": prob,
                "eligible": pt.is_extraction_eligible(),
                "sentences": [{"id": s.id, "text": s.text} for s in pt.sentences],
            }
        )
    return out


def _interleave(items: list[dict], rng: random.Random) -> list[dict]:
    """Order so that every prefix has languages in proportion (smooth weighted round-robin)."""
    queues = {lang: [i for i in items if i["language"] == lang] for lang in LANG_ORDER}
    for q in queues.values():
        rng.shuffle(q)
    total = sum(len(q) for q in queues.values())
    credit = dict.fromkeys(LANG_ORDER, 0.0)
    out = []
    for _ in range(total):
        for lang, q in queues.items():
            credit[lang] += len(q) / total
        lang = max((lang for lang in LANG_ORDER if queues[lang]), key=lambda x: credit[x])
        credit[lang] -= 1
        out.append(queues[lang].pop())
    return out


def select(conn: psycopg.Connection) -> list[dict]:
    rng = random.Random(SEED)
    apps = _load(conn, "application")
    board = [b for b in _load(conn, "board") if b["eligible"] and b["language"] in LANG_ORDER]
    relevant = [b for b in board if (b["prob"] or 0) >= jev.RELEVANCE_THRESHOLD]
    chosen: list[dict] = []

    # Board postings
    # Only 1 Swiss German posting passes 0.5, so German AI/data uses a looser cut-off (0.35).
    de_ai = [b for b in board if b["language"] == "de" and (b["prob"] or 0) >= DE_AI_MIN_PROB]
    de_other = [b for b in board if b["language"] == "de" and (b["prob"] or 0) < DE_AI_MIN_PROB]
    fr_ai = [b for b in relevant if b["language"] == "fr"]
    for b in de_ai:
        b["group"] = "de_ai"
    sel_de_other = _spread(de_other, "employer", 3, N_DE_OTHER, rng)
    for b in sel_de_other:
        b["group"] = "de_other"
    sel_fr = _spread(fr_ai, "employer", 4, N_FR_AI, rng)
    for b in sel_fr:
        b["group"] = "fr_ai"
    chosen += de_ai + sel_de_other + sel_fr

    taken = {b["posting_id"] for b in chosen}
    en = [b for b in board if b["language"] == "en" and b["posting_id"] not in taken]
    borderline = [b for b in en if b["prob"] is not None and 0.35 <= b["prob"] <= 0.65]
    sel_border = _spread(borderline, "employer", 1, N_BORDERLINE, rng)
    for b in sel_border:
        b["group"] = "en_borderline"
    n_en_ai = BOARD_TARGET - len(chosen) - len(sel_border)
    dis = _disagreement(conn)
    taken_border = {b["posting_id"] for b in sel_border}
    en_ai = [b for b in en if b in relevant and b["posting_id"] not in taken_border]
    rng.shuffle(en_ai)
    en_ai.sort(key=lambda b: -dis.get(b["posting_id"], -1))  # stable: ties stay shuffled
    # prefer disagreement but keep employers varied (max 2 each)
    per_employer: Counter = Counter()
    sel_en_ai = []
    for b in en_ai:
        if len(sel_en_ai) >= n_en_ai:
            break
        if per_employer[b["employer"]] < 2:
            per_employer[b["employer"]] += 1
            sel_en_ai.append(b)
    for b in sel_en_ai:
        b["group"] = "en_ai"
    chosen += sel_border + sel_en_ai

    # Application postings: all French and German, English spread over companies (max 2 each)
    n_app = TOTAL - len(chosen)
    app_fr = [a for a in apps if a["language"] == "fr"]
    app_de = [a for a in apps if a["language"] == "de"]
    for a in app_fr:
        a["group"] = "app_fr"
    for a in app_de:
        a["group"] = "app_de"
    app_en = [a for a in apps if a["language"] == "en"]
    sel_app_en = _spread(app_en, "employer", 2, n_app - len(app_fr) - len(app_de), rng)
    for a in sel_app_en:
        a["group"] = "app_en"
    chosen += app_fr + app_de + sel_app_en

    ordered = _interleave(chosen, rng)
    for i, item in enumerate(ordered):
        item["order"] = i
    return ordered


def select_test_set(conn: psycopg.Connection, path: Path = TEST_SET_FILE) -> list[dict]:
    items = select(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    keys = [
        "order", "posting_id", "origin", "language", "group", "employer", "title",
        "location_raw", "description_text", "sentences",
    ]  # fmt: skip
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps({k: item[k] for k in keys}, ensure_ascii=False) + "\n")
    print_summary(items)
    return items


def print_summary(items: list[dict]) -> None:
    counts = Counter((i["origin"], i["language"], i["group"]) for i in items)
    print(f"{'origin':<12}{'lang':<6}{'group':<15}{'n':>3}")
    for (origin, lang, group), n in sorted(counts.items()):
        print(f"{origin:<12}{lang:<6}{group:<15}{n:>3}")
    first = Counter(i["language"] for i in items if i["order"] < 30)
    print(f"total {len(items)}; first 30 by language: {dict(first)}")


def load_test_set(path: Path = TEST_SET_FILE) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


# ----- held-out set -------------------------------------------------------------------------
# A second set of board postings that the rules were never tuned on. It is chosen only from
# postings outside the test set and outside the owner's labels, with a fixed seed and without
# looking at extractor disagreement (the test set used that), so accuracy on it is a fair check.

ITEM_KEYS = [
    "order", "posting_id", "origin", "language", "group", "stratum", "employer", "title",
    "location_raw", "description_text", "sentences",
]  # fmt: skip


def holdout_targets(n: int) -> dict[str, dict[str, int]]:
    """Per language: total, borderline and (nominal) other counts. At n=50: en 25, de 13, fr 12;
    borderline 5/3/2. Relevant postings (`*_ai`) take the rest; `*_other` (low relevance) takes
    what a stratum could not fill."""
    de, fr = round(0.26 * n), round(0.24 * n)
    return {
        "en": {"total": n - de - fr, "borderline": round(0.1 * n)},
        "de": {"total": de, "borderline": round(0.06 * n)},
        "fr": {"total": fr, "borderline": round(0.04 * n)},
    }


def choose_holdout(
    board: list[dict], excluded_ids: set[int], excluded_titles: set[tuple], n: int, seed: int
) -> list[dict]:
    """Pick the held-out items from `board` items (as returned by `_load`). Pure and
    deterministic for a given input and seed."""
    rng = random.Random(seed)
    seen_titles = set(excluded_titles)
    pool = [
        b
        for b in sorted(board, key=lambda b: b["posting_id"])
        if b["origin"] == "board"
        and b["eligible"]
        and b["language"] in LANG_ORDER
        and b["posting_id"] not in excluded_ids
        and (b["employer"], b["title"]) not in excluded_titles
    ]
    chosen: list[dict] = []

    def take(cands: list[dict], per_employer: int, count: int, stratum: str) -> list[dict]:
        cands = [c for c in cands if (c["employer"], c["title"]) not in seen_titles]
        out = []
        for c in _spread(cands, "employer", per_employer, count, rng):
            if (c["employer"], c["title"]) in seen_titles:
                continue
            seen_titles.add((c["employer"], c["title"]))
            c = {**c, "group": stratum, "stratum": stratum}
            out.append(c)
        return out

    lo, hi = BORDERLINE_RANGE
    for lang, t in holdout_targets(n).items():
        mine = [b for b in pool if b["language"] == lang]
        border = take(
            [b for b in mine if b["prob"] is not None and lo <= b["prob"] <= hi],
            2,
            t["borderline"],
            f"{lang}_borderline",
        )
        used = {b["posting_id"] for b in border}
        # German: one posting passes 0.5, so the "relevant" cut-off is the looser 0.35 as in the
        # test set.
        cut = DE_AI_MIN_PROB if lang == "de" else jev.RELEVANCE_THRESHOLD
        rel = [b for b in mine if b["posting_id"] not in used and (b["prob"] or 0) >= cut]
        want_ai = t["total"] - t["borderline"] - (2 if lang != "en" else 0)
        ai = take(rel, 2, want_ai, f"{lang}_ai")
        if len(ai) < want_ai:  # few relevant postings: allow more per employer before "other"
            have = {b["posting_id"] for b in ai}
            ai += take(
                [b for b in rel if b["posting_id"] not in have], 6, want_ai - len(ai), f"{lang}_ai"
            )
        used |= {b["posting_id"] for b in ai}
        rest = [b for b in mine if b["posting_id"] not in used]
        other = take(rest, 2, t["total"] - len(border) - len(ai), f"{lang}_other")
        chosen += border + ai + other

    ordered = _interleave(chosen, rng)
    for i, item in enumerate(ordered):
        item["order"] = i
    return ordered


def select_holdout(
    conn: psycopg.Connection,
    n: int = 50,
    path: Path = HOLDOUT_FILE,
    force: bool = False,
    test_set_path: Path = TEST_SET_FILE,
    labels_path: Path | None = None,
    seed: int = HOLDOUT_SEED,
) -> list[dict]:
    """Write `holdout_set.jsonl` (+ `holdout_set.meta.json`). Refuses to overwrite without
    `force`, because labels and runs refer to it."""
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(f"{path} exists; use --force to replace it")
    test_items = load_test_set(test_set_path) if Path(test_set_path).exists() else []
    labels_path = labels_path or Path(test_set_path).parent / "labels.jsonl"
    owner_ids: set[int] = set()
    if Path(labels_path).exists():
        with Path(labels_path).open(encoding="utf-8") as f:
            owner_ids = {json.loads(x)["posting_id"] for x in f if x.strip()}
    excluded = {i["posting_id"] for i in test_items} | owner_ids
    titles = {(i["employer"], i["title"]) for i in test_items if "employer" in i}
    items = choose_holdout(_load(conn, "board"), excluded, titles, n, seed)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps({k: item[k] for k in ITEM_KEYS}, ensure_ascii=False) + "\n")
    meta = {
        "seed": seed,
        "n": len(items),
        "requested": n,
        "targets": holdout_targets(n),
        "strata": dict(Counter(i["stratum"] for i in items)),
        "languages": dict(Counter(i["language"] for i in items)),
        "excluded_test_set": len(test_items),
        "excluded_owner_labels": len(owner_ids),
        "selected_at": date.today().isoformat(),
    }
    path.with_suffix(".meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    print_summary(items)
    return items


def load_holdout(path: Path = HOLDOUT_FILE) -> list[dict]:
    return load_test_set(path)
