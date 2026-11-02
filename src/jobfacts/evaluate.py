"""Accuracy report: model extractions against the owner's labels.

Pure functions work on plain dicts so they can be tested without a database:

- item: one line of `test_set.jsonl` (`posting_id`, `language`, `group`, `origin`, ...)
- label: one record of `labels.jsonl` (see `jobfacts.labels`)
- extraction ("ext"): `{"error", "values", "sentences", "office_cantons",
  "office_cantons_sentences", "office_places", "details", "cost_usd", "latency_ms",
  "tokens_in", "tokens_out"}`
  as returned by `load_run`; `exts` maps extractor name -> posting id -> ext.
"""

import math
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import psycopg

from jobfacts import labels as labels_mod
from jobfacts import ordertest
from jobfacts.extract.spec import FIELDS, derive_seniority
from jobfacts.label_app import prefill

RELEVANCE_THRESHOLD = 0.5
RELEVANCE_EXTRACTOR = "jev_relevance"
MAIN_EXTRACTORS = ("jev", "deepseek", "mistral")
BINS = [(0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0001)]
BIN_NAMES = ["<0.5", "0.5-0.7", "0.7-0.9", ">=0.9"]
SCORED_FIELDS = [
    "relevant",
    "work_mode",
    "remote_from_switzerland",
    "office_cantons",
    "office_places",
    "german",
    "french",
    "min_years",
    "title_level",
    "seniority",
]


# ----- statistics -------------------------------------------------------------------------


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """95% Wilson score interval for k successes out of n (0, 1 when n is 0)."""
    if n == 0:
        return 0.0, 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def cell(k: int, n: int) -> str:
    if n == 0:
        return "-"
    lo, hi = wilson(k, n)
    return f"{100 * k / n:.0f}% [{100 * lo:.0f}-{100 * hi:.0f}] n={n}"


# ----- scoring ----------------------------------------------------------------------------


def label_value(label: dict, field: str):
    """The labelled value of a scored field, in the form the model values are compared in."""
    if field == "office_cantons":
        return set(label["office_cantons"])
    if field == "office_places":
        return set(label["office_places"])
    if field == "seniority":
        f = label["fields"]
        return derive_seniority(f["min_years"]["value"], f["title_level"]["value"])
    return label["fields"][field]["value"]


def model_value(ext: dict, field: str):
    if field in ("office_cantons", "office_places"):
        return set(ext[field])
    if field == "seniority":
        return derive_seniority(ext["values"]["min_years"], ext["values"]["title_level"])
    return ext["values"][field]


def score_records(
    items: list[dict],
    labels: dict[int, dict],
    exts: dict[str, dict[int, dict]],
    relevance: dict[int, float],
) -> tuple[list[dict], dict]:
    """One record per (extractor, field, labelled posting).

    Record: extractor, field, pid, ok, error, language, group, origin.
    Returns the records and counts: `unsure` (relevance label unsure), `missing`
    (extractor, field -> labelled postings without any extraction row).
    """
    records: list[dict] = []
    counts: dict = {"unsure": 0, "missing": Counter(), "labelled": 0}
    for item in items:
        pid = item["posting_id"]
        label = labels.get(pid)
        if label is None:
            continue
        counts["labelled"] += 1
        base = {
            "pid": pid,
            "language": item["language"],
            "group": item["group"],
            "origin": item["origin"],
        }
        if label["relevant"] == "unsure":
            counts["unsure"] += 1
        elif pid in relevance:
            ok = (relevance[pid] >= RELEVANCE_THRESHOLD) == (label["relevant"] == "true")
            records.append(
                {
                    **base,
                    "extractor": RELEVANCE_EXTRACTOR,
                    "field": "relevant",
                    "ok": ok,
                    "error": False,
                }  # fmt: skip
            )
        for extractor, by_pid in exts.items():
            ext = by_pid.get(pid)
            for field in SCORED_FIELDS[1:]:
                if ext is None:
                    counts["missing"][extractor] += 1
                    break
                if (
                    not ext["error"]
                    and ext["office_cantons"] is None
                    and field.startswith("office")
                ):
                    continue  # run made before the canton redesign (office_*_v1 history)
                if ext["error"]:
                    ok, err = False, True
                else:
                    ok, err = model_value(ext, field) == label_value(label, field), False
                records.append(
                    {**base, "extractor": extractor, "field": field, "ok": ok, "error": err}
                )
    return records, counts


def summarize(records: list[dict], **where) -> tuple[int, int, int]:
    """(correct, n, errors) over records matching all `key=value` conditions."""
    k = n = e = 0
    for r in records:
        if all(r[key] == value for key, value in where.items()):
            n += 1
            k += r["ok"]
            e += r["error"]
    return k, n, e


# ----- evidence agreement -----------------------------------------------------------------


def evidence_agreement(
    items: list[dict], labels: dict[int, dict], exts: dict[str, dict[int, dict]]
) -> dict[tuple[str, str], tuple[int, int]]:
    """(field, extractor) -> (postings whose sentences overlap, postings compared).

    Compared: model value equals the label value, the value needs a sentence, the model
    returned no error. Overlap = at least one sentence id in common.
    """
    out: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for item in items:
        label = labels.get(item["posting_id"])
        if label is None:
            continue
        for extractor, by_pid in exts.items():
            ext = by_pid.get(item["posting_id"])
            if ext is None or ext["error"]:
                continue
            for name, spec in FIELDS.items():
                lab = label["fields"][name]
                if ext["values"][name] != lab["value"] or lab["value"] in spec.no_quote:
                    continue
                hit = bool(set(ext["sentences"].get(name, [])) & set(lab["sentences"]))
                out[(name, extractor)][0] += hit
                out[(name, extractor)][1] += 1
            if (
                label["office_cantons"]
                and ext["office_cantons"] is not None
                and set(ext["office_cantons"]) == set(label["office_cantons"])
            ):
                hit = bool(
                    set(ext["office_cantons_sentences"]) & set(label["office_cantons_sentences"])
                )
                out[("office_cantons", extractor)][0] += hit
                out[("office_cantons", extractor)][1] += 1
    return {k: (v[0], v[1]) for k, v in out.items()}


# ----- confusion --------------------------------------------------------------------------


def confusion(
    items: list[dict], labels: dict[int, dict], by_pid: dict[int, dict], field: str
) -> Counter:
    """(label value, model value) -> count; model value is `ERROR` when the call failed."""
    out: Counter = Counter()
    for item in items:
        label = labels.get(item["posting_id"])
        ext = by_pid.get(item["posting_id"])
        if label is None or ext is None:
            continue
        out[
            (label["fields"][field]["value"], "ERROR" if ext["error"] else ext["values"][field])
        ] += 1
    return out


# ----- Jev calibration --------------------------------------------------------------------


def _bin(x: float) -> int:
    for i, (lo, hi) in enumerate(BINS):
        if lo <= x < hi:
            return i
    return len(BINS) - 1


def calibration(
    items: list[dict], labels: dict[int, dict], jev_exts: dict[int, dict]
) -> dict[str, list[list[int]]]:
    """For `probability` (of the chosen value) and `confidence`: per bin [correct, n].

    Pooled over the six value fields. Postings with errors or without stored details are skipped.
    """
    out = {"probability": [[0, 0] for _ in BINS], "confidence": [[0, 0] for _ in BINS]}
    for item in items:
        label = labels.get(item["posting_id"])
        ext = jev_exts.get(item["posting_id"])
        if label is None or ext is None or ext["error"]:
            continue
        for name in FIELDS:
            d = ext["details"].get(name)
            value = ext["values"][name]
            if not d:
                continue
            ok = value == label["fields"][name]["value"]
            for key, x in (
                ("probability", (d.get("probabilities") or {}).get(value)),
                ("confidence", d.get("confidence")),
            ):
                if x is None:
                    continue
                b = _bin(float(x))
                out[key][b][0] += ok
                out[key][b][1] += 1
    return out


# ----- cost and speed ---------------------------------------------------------------------


def cost_summary(by_pid: dict[int, dict]) -> dict:
    rows = list(by_pid.values())
    lat = sorted(r["latency_ms"] for r in rows if r["latency_ms"])
    n = len(rows)
    return {
        "n": n,
        "errors": sum(1 for r in rows if r["error"]),
        "cost": sum(r["cost_usd"] or 0 for r in rows),
        "tokens_in": sum(r["tokens_in"] or 0 for r in rows),
        "tokens_out": sum(r["tokens_out"] or 0 for r in rows),
        "latency_mean": sum(lat) / len(lat) if lat else 0,
        "latency_p95": lat[min(len(lat) - 1, math.ceil(0.95 * len(lat)) - 1)] if lat else 0,
    }


# ----- blind vs pre-filled ----------------------------------------------------------------


def prefill_agreement(
    items: list[dict], labels: dict[int, dict], exts: dict[str, dict[int, dict]]
) -> tuple[dict[tuple[str, str], list[int]], Counter]:
    """(field, mode) -> [label equals pre-fill majority, postings with a majority].

    The majority is the one `prefill.majority` computes from the three main extractors, also
    for blind postings (computed but never shown). `none` counts fields without a majority.
    """
    out: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    none: Counter = Counter()
    main = [e for e in MAIN_EXTRACTORS if e in exts]
    for item in items:
        pid = item["posting_id"]
        label = labels.get(pid)
        if label is None:
            continue
        by_model = {e: exts[e][pid] for e in main if pid in exts[e] and not exts[e][pid]["error"]}
        pre = prefill.majority(by_model)
        mode = label["mode"]
        for name in FIELDS:
            if name not in pre["fields"]:
                none[mode] += 1
                continue
            out[(name, mode)][0] += pre["fields"][name]["value"] == label["fields"][name]["value"]
            out[(name, mode)][1] += 1
        if "office_cantons" in pre:
            out[("office_cantons", mode)][0] += set(pre["office_cantons"]) == set(
                label["office_cantons"]
            )
            out[("office_cantons", mode)][1] += 1
        else:
            none[mode] += 1
    return out, none


# ----- loading ----------------------------------------------------------------------------


def load_run(conn: psycopg.Connection, run_id: int) -> dict[int, dict]:
    """All extraction rows of one run as ext dicts (posting id -> ext)."""
    cols = ", ".join(f"{n}, {n}_sentences" for n in FIELDS)
    rows = conn.execute(
        f"SELECT posting_id, office_cantons, office_cantons_sentences, office_places, details, "
        f"cost_usd, latency_ms, tokens_in, "
        f"tokens_out, error, {cols} FROM extractions WHERE run_id = %s",
        (run_id,),
    ).fetchall()
    out: dict[int, dict] = {}
    for pid, cantons, canton_sents, places, details, cost, latency, tin, tout, error, *rest in rows:
        values = {n: rest[2 * i] for i, n in enumerate(FIELDS)} if not error else {}
        sentences = {n: rest[2 * i + 1] or [] for i, n in enumerate(FIELDS)} if not error else {}
        out[pid] = {
            "error": error,
            "values": values,
            "sentences": sentences,
            # None = run made before the canton redesign (column empty)
            "office_cantons": None if cantons is None else list(cantons),
            "office_cantons_sentences": list(canton_sents or []),
            "office_places": list(places or []),
            "details": details or {},
            "cost_usd": cost,
            "latency_ms": latency,
            "tokens_in": tin,
            "tokens_out": tout,
        }
    return out


def load_relevance(conn: psycopg.Connection, ids: list[int], model: str) -> dict[int, float]:
    rows = conn.execute(
        "SELECT posting_id, probability FROM relevance WHERE model = %s AND posting_id = ANY(%s)",
        (model, ids),
    )
    return {pid: p for pid, p in rows}


def load_run_meta(conn: psycopg.Connection, run_id: int) -> dict:
    row = conn.execute(
        "SELECT model, spec_version, notes, finished_at - started_at FROM extraction_runs "
        "WHERE id = %s",
        (run_id,),
    ).fetchone()
    if row is None:
        return {"model": "?", "spec_version": "?", "notes": "", "wall": None}
    return {"model": row[0], "spec_version": row[1], "notes": row[2] or "", "wall": row[3]}


# ----- Markdown ---------------------------------------------------------------------------


def table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out) + "\n"


def section_accuracy(records, items, extractors, counts) -> str:
    languages = [lang for lang in ("en", "de", "fr") if any(i["language"] == lang for i in items)]
    groups = sorted({i["group"] for i in items})
    origins = [o for o in ("board", "application") if any(i["origin"] == o for i in items)]
    lines = ["## 1. Value accuracy", ""]
    lines.append(
        f"Labelled postings: {counts['labelled']}. Relevance labels `unsure` (excluded from the "
        f"`relevant` rows): {counts['unsure']}. Cells show accuracy, the 95% Wilson interval "
        "in brackets, and n. Errors count as wrong; the error count is in the last column of "
        "the overall tables.\n"
    )
    for field in SCORED_FIELDS:
        names = [RELEVANCE_EXTRACTOR] if field == "relevant" else extractors
        lines += [f"### {field}", ""]
        rows = []
        for e in names:
            k, n, err = summarize(records, extractor=e, field=field)
            row = [e, cell(k, n)]
            for lang in languages:
                row.append(cell(*summarize(records, extractor=e, field=field, language=lang)[:2]))
            for o in origins:
                row.append(cell(*summarize(records, extractor=e, field=field, origin=o)[:2]))
            row.append(err)
            rows.append(row)
        lines.append(table(["extractor", "overall", *languages, *origins, "errors"], rows))
        rows = []
        for e in names:
            rows.append(
                [e]
                + [cell(*summarize(records, extractor=e, field=field, group=g)[:2]) for g in groups]
            )
        lines.append(table(["extractor", *groups], rows))
    missing = counts["missing"]
    if missing:
        lines.append(
            "Labelled postings without any extraction row (not scored): "
            + ", ".join(f"{e}: {n}" for e, n in missing.items())
            + ".\n"
        )
    return "\n".join(lines)


def section_evidence(agreement, extractors) -> str:
    lines = [
        "## 2. Evidence agreement",
        "",
        "Among postings where the model value equals the label value and the value needs a "
        "sentence: share where the model's sentences share at least one sentence id with the "
        "labelled ones.\n",
    ]
    fields = [*FIELDS, "office_cantons"]
    rows = []
    for e in extractors:
        rows.append([e] + [cell(*agreement.get((f, e), (0, 0))) for f in fields])
    lines.append(table(["extractor", *fields], rows))
    return "\n".join(lines)


def section_confusion(items, labels, exts, extractors) -> str:
    lines = ["## 3. Confusion tables", "", "Rows: label. Columns: model value.\n"]
    for field in ("german", "work_mode"):
        values = [*FIELDS[field].values, "ERROR"]
        for e in extractors:
            c = confusion(items, labels, exts[e], field)
            lines += [f"### {field} - {e}", ""]
            rows = [[lab, *[c.get((lab, m), 0) for m in values]] for lab in FIELDS[field].values]
            lines.append(table(["label \\ model", *values], rows))
    return "\n".join(lines)


def section_calibration(cal) -> str:
    lines = [
        "## 4. Jev calibration",
        "",
        "Pooled over the six value fields. Accuracy of Jev's value by the probability it "
        "gave to the chosen value, and by its `confidence`.\n",
    ]
    rows = []
    for i, name in enumerate(BIN_NAMES):
        rows.append([name, cell(*cal["probability"][i]), cell(*cal["confidence"][i])])
    lines.append(table(["bin", "by probability of chosen value", "by confidence"], rows))
    return "\n".join(lines)


def section_cost(exts, metas) -> str:
    rows = []
    for e, by_pid in exts.items():
        c = cost_summary(by_pid)
        wall = metas.get(e, {}).get("wall")
        rows.append(
            [
                e,
                metas.get(e, {}).get("model", "?"),
                c["n"],
                c["errors"],
                f"${c['cost']:.4f}",
                f"${c['cost'] / c['n']:.5f}" if c["n"] else "-",
                f"{c['tokens_in']:,} / {c['tokens_out']:,}",
                f"{c['latency_mean'] / 1000:.1f} s",
                f"{c['latency_p95'] / 1000:.1f} s",
                f"{wall.total_seconds():.0f} s" if wall else "-",
            ]
        )
    return "## 5. Cost and speed\n\n" + table(
        ["extractor", "model", "rows", "errors", "cost", "per posting", "tokens in/out",
         "latency mean", "latency p95", "run wall time"],
        rows,
    )  # fmt: skip


def section_blind(agreement, none) -> str:
    lines = [
        "## 6. Blind vs pre-filled",
        "",
        "Share of postings where the label equals the pre-fill majority (the value at least two "
        "of jev, deepseek, mistral agree on). In blind postings the pre-fill was computed but "
        "not shown. Clearly higher agreement in pre-filled postings suggests anchoring (or "
        "just that later postings are easier: the two groups hold different postings).\n",
    ]
    rows = []
    for f in [*FIELDS, "office_cantons"]:
        b, p = agreement.get((f, "blind"), [0, 0]), agreement.get((f, "prefilled"), [0, 0])
        diff = f"{100 * (p[0] / p[1] - b[0] / b[1]):+.0f} pts" if b[1] and p[1] else "-"
        rows.append([f, cell(*b), cell(*p), diff])
    lines.append(table(["field", "blind", "pre-filled", "difference"], rows))
    lines.append(
        f"Fields without a majority (not compared): blind {none.get('blind', 0)}, "
        f"pre-filled {none.get('prefilled', 0)}.\n"
    )
    return "\n".join(lines)


def section_order(comparisons) -> str:
    head = (
        "## 7. Jev option-order test\n\nHow often Jev's answer changes when the value options "
        "are listed in another order (reversed, or shuffled with a fixed seed), compared "
        "with the original order (`not_stated` / `not_mentioned` first). Needs no labels. A "
        "high share of `not_stated -> value` changes means the first-option lean hides "
        "information in the original order.\n\n"
    )
    if not comparisons:
        return head + "No order-test runs yet (`uv run jobfacts jev-order-test`).\n"
    return head + ordertest.render(comparisons)


def canton_prf(
    items: list[dict], labels: dict[int, dict], by_pid: dict[int, dict]
) -> tuple[dict[str, list[int]], int]:
    """Per canton code: [true positives, false positives, false negatives], and postings used."""
    out: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0])
    used = 0
    for item in items:
        label = labels.get(item["posting_id"])
        ext = by_pid.get(item["posting_id"])
        if label is None or ext is None or ext["error"] or ext["office_cantons"] is None:
            continue
        used += 1
        want, got = set(label["office_cantons"]), set(ext["office_cantons"])
        for c in want | got:
            out[c][0 if c in want and c in got else 1 if c in got else 2] += 1
    return dict(out), used


def _prf_cells(tp: int, fp: int, fn: int) -> list[str]:
    precision = f"{100 * tp / (tp + fp):.0f}%" if tp + fp else "-"
    recall = f"{100 * tp / (tp + fn):.0f}%" if tp + fn else "-"
    return [precision, recall, f"{tp}/{fp}/{fn}"]


def section_cantons(items, labels, exts, extractors) -> str:
    lines = [
        "## 8. Office cantons: precision and recall per canton",
        "",
        "Per canton code (and `outside_switzerland`): precision = share of the model's "
        "postings with that canton where the label has it too; recall = share of labelled "
        "postings with that canton where the model has it. TP/FP/FN in brackets. Only "
        "cantons that occur in labels or answers are shown; `all` pools every canton "
        "(micro average).\n",
    ]
    for e in extractors:
        stats, used = canton_prf(items, labels, exts[e])
        if not used:
            continue
        rows = []
        total = [0, 0, 0]
        for c in sorted(stats):
            total = [a + b for a, b in zip(total, stats[c], strict=True)]
            rows.append([c, *_prf_cells(*stats[c])])
        rows.append(["all", *_prf_cells(*total)])
        lines += [
            f"### {e} (n={used})",
            "",
            table(["canton", "precision", "recall", "TP/FP/FN"], rows),
        ]
    return "\n".join(lines)


NOTES = """## Notes on reading this report

- **n** is the number of labelled postings behind a number. Small n means wide intervals:
  with n=20, a result of 90% could really be anywhere between about 70% and 97%.
- **[low-high]** is a 95% Wilson interval: a range that would contain the true accuracy in
  about 95 of 100 repeated samples of this kind. If two intervals overlap a lot, the data does
  not show which extractor is better.
- `de_ai` are German postings with AI/data titles; `de_other` are non-AI German postings
  (mostly one utility), used to test the language requirement fields, so do not compare
  their relevance accuracy with `de_ai`. `app_*` groups come from the owner's applications;
  `origin` `board` are collected postings.
- `office_cantons` (main location metric) is the set of cantons (plus `outside_switzerland`);
  the whole set must match exactly. Section 8 gives precision and recall per canton.
  `office_places` (secondary) is the set of place keys code found inside the chosen cantons;
  it is only as good as the cantons, and labels without an explicit `office_places` hold the
  places code finds for the labelled cantons.
- `seniority` is derived from `min_years` and `title_level` with the same rule for label and
  model, so it inherits errors of both parts.
- Runs made before the canton redesign (`*_v1`) have no `office_cantons`; their office rows
  are skipped.
- Labels are the owner's judgement, not ground truth; `relevant` is compared with the Jev
  relevance probability (>= 0.5 means relevant).
"""


# ----- orchestration ----------------------------------------------------------------------


def order_comparisons(exts: dict[str, dict[int, dict]]) -> dict[str, dict]:
    if "jev" not in exts:
        return {}
    base = exts.get("jev_v1") or exts["jev"]  # the order test ran on the pre-canton jev run
    return {
        v: ordertest.compare(base, exts[f"jev_{v}"])
        for v in ordertest.VARIANTS
        if f"jev_{v}" in exts
    }


def build_report(
    items: list[dict],
    labels: dict[int, dict],
    exts: dict[str, dict[int, dict]],
    metas: dict[str, dict],
    relevance: dict[int, float],
    today: str,
) -> str:
    ids = {i["posting_id"] for i in items}
    labels = {p: label for p, label in labels.items() if p in ids}
    extractors = list(exts)
    parts = [f"# Accuracy report ({today})\n", f"Test set: {len(items)} postings.\n"]
    if labels:
        records, counts = score_records(items, labels, exts, relevance)
        parts.append(section_accuracy(records, items, extractors, counts))
        parts.append(section_evidence(evidence_agreement(items, labels, exts), extractors))
        parts.append(section_confusion(items, labels, exts, extractors))
        parts.append(section_calibration(calibration(items, labels, exts.get("jev", {}))))
    else:
        parts.append(
            "**No labels yet** (`labels.jsonl` is missing or empty): the accuracy, evidence, "
            "confusion and calibration sections need them. Label postings with "
            "`uv run jobfacts label`.\n"
        )
    parts.append(section_cost(exts, metas))
    if labels:
        parts.append(section_cantons(items, labels, exts, extractors))
        agreement, none = prefill_agreement(items, labels, exts)
        parts.append(section_blind(agreement, none))
    parts.append(section_order(order_comparisons(exts)))
    parts.append(NOTES)
    return "\n".join(parts)


def run_evaluate(
    conn: psycopg.Connection, data_dir: Path, today: str | None = None
) -> tuple[str, Path]:
    """Read the files in `data_dir`, build the report and write it to `data_dir/reports/`."""
    from jobfacts.extract.jev import MODEL
    from jobfacts.testset import load_test_set

    items = load_test_set(data_dir / "test_set.jsonl")
    labels = labels_mod.latest(data_dir / "labels.jsonl")
    run_ids = ordertest.read_runs(data_dir / "test_set_runs.json")
    exts = {name: load_run(conn, rid) for name, rid in run_ids.items()}
    metas = {name: load_run_meta(conn, rid) for name, rid in run_ids.items()}
    relevance = load_relevance(conn, [i["posting_id"] for i in items], MODEL)
    today = today or date.today().isoformat()
    text = build_report(items, labels, exts, metas, relevance, today)
    out = data_dir / "reports" / f"accuracy_{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return text, out


def summary_lines(text: str, n_labels: int) -> str:
    """The cost and order-test sections (and a labels notice) for the terminal."""
    keep = []
    for part in text.split("\n## "):
        if part.startswith(("5. Cost", "7. Jev option-order")):
            keep.append("## " + part.strip())
    head = (
        f"{n_labels} labelled postings."
        if n_labels
        else "No labels yet: accuracy sections skipped."
    )
    return head + "\n\n" + "\n\n".join(keep)
