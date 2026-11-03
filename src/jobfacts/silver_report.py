"""Report on silver labels: how much the model labellers agree, and what they cost.

Used to decide how many model labellers are needed. Works with any subset of labellers: those
with a valid answer for every posting of the set are compared, the others are listed as missing.
"""

import json
from collections import Counter
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any

from jobfacts import labels as labels_mod
from jobfacts import silver
from jobfacts.extract.spec import FIELDS, OFFICE_CANTONS
from jobfacts.stats import wilson

KEYS = silver.KEYS
LANGUAGES = ("en", "de", "fr")


def load_answers(data_dir: Path, labeller: str, pids: list[int]) -> dict[int, dict] | None:
    """Stored records of a labeller, or None unless every posting of the set has one."""
    out: dict[int, dict] = {}
    for pid in pids:
        path = Path(data_dir) / "silver" / labeller / f"{pid}.json"
        if not path.exists():
            return None
        out[pid] = json.loads(path.read_text(encoding="utf-8"))
    return out


def comparable(answer: dict, key: str) -> Any:
    entry = answer[key]
    if key == OFFICE_CANTONS:
        return tuple(sorted(entry["value"]))
    return entry["value"]


def owner_value(label: dict, key: str) -> Any:
    """The owner's value in the same form as `comparable`, or None if not labelled."""
    if key == "relevant":
        return label.get("relevant")
    if key == OFFICE_CANTONS:
        return tuple(sorted(label.get("office_cantons", [])))
    entry = (label.get("fields") or {}).get(key)
    return entry["value"] if entry else None  # e.g. contract_type on early labels


def agreement_class(values: list[Any]) -> str:
    """`all` agree, `partial` (a group of at least 2 but not everyone), or `none` agree."""
    top = Counter(values).most_common(1)[0][1]
    if top == len(values):
        return "all"
    return "partial" if top >= 2 else "none"


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.0f}%" if n else "-"


def table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def agreement_counts(
    answers: dict[str, dict[int, dict]], items: list[dict]
) -> dict[tuple[str, str], Counter]:
    """(key, language or "all") -> Counter of agreement classes over the postings."""
    out: dict[tuple[str, str], Counter] = {}
    for key in KEYS:
        for item in items:
            values = [comparable(a[item["posting_id"]]["answer"], key) for a in answers.values()]
            cls = agreement_class(values)
            for lang in ("all", item["language"]):
                out.setdefault((key, lang), Counter())[cls] += 1
    return out


def pairwise(
    answers: dict[str, dict[int, dict]], items: list[dict]
) -> dict[tuple[str, str], dict[str, list[int]]]:
    """(labeller a, labeller b) -> key -> [agree, compared]."""
    out: dict = {}
    for a, b in combinations(answers, 2):
        per_key: dict[str, list[int]] = {k: [0, 0] for k in KEYS}
        for key in KEYS:
            for item in items:
                pid = item["posting_id"]
                same = comparable(answers[a][pid]["answer"], key) == comparable(
                    answers[b][pid]["answer"], key
                )
                per_key[key][0] += same
                per_key[key][1] += 1
        out[(a, b)] = per_key
    return out


def low_confidence(records: dict[int, dict]) -> tuple[int, int]:
    """(answers marked low, answers with a confidence) over all postings."""
    low = total = 0
    for rec in records.values():
        for key in [*FIELDS, OFFICE_CANTONS]:
            total += 1
            low += rec["answer"][key]["confidence"] == "low"
    return low, total


def owner_agreement(
    answers: dict[str, dict[int, dict]], owner: dict[int, dict], items: list[dict]
) -> dict[str, dict[str, list[int]]]:
    """labeller -> key -> [agree, compared] over owner-labelled postings of the set."""
    out: dict[str, dict[str, list[int]]] = {}
    for lab, recs in answers.items():
        per_key: dict[str, list[int]] = {k: [0, 0] for k in KEYS}
        for item in items:
            label = owner.get(item["posting_id"])
            if label is None:
                continue
            for key in KEYS:
                mine = owner_value(label, key)
                if mine is None:
                    continue
                per_key[key][0] += comparable(recs[item["posting_id"]]["answer"], key) == mine
                per_key[key][1] += 1
        out[lab] = per_key
    return out


def usage_totals(records: dict[int, dict]) -> dict:
    t = {"input": 0, "output": 0, "reasoning": 0, "cost_usd": 0.0, "duration_ms": 0}
    attempts = retried = 0
    for rec in records.values():
        usage = rec.get("usage") or []
        attempts += len(usage)
        retried += len(usage) > 1
        for u in usage:
            for k in t:
                t[k] += u.get(k, 0)
    return {**t, "attempts": attempts, "retried": retried, "has_usage": any(
        r.get("usage") for r in records.values()
    )}  # fmt: skip


def build_report(
    data_dir: Path, set_name: str, labels_path: Path | None = None
) -> tuple[str, dict]:
    """Markdown report and a small summary dict (labellers, missing, headline numbers)."""
    data_dir = Path(data_dir)
    items = silver.items_for(data_dir, set_name)
    pids = [i["posting_id"] for i in items]
    answers: dict[str, dict[int, dict]] = {}
    missing: list[str] = []
    for lab in silver.LABELLERS:
        recs = load_answers(data_dir, lab, pids)
        if recs is None:
            missing.append(lab)
        else:
            answers[lab] = recs
    langs = [lang for lang in LANGUAGES if any(i["language"] == lang for i in items)]
    n = len(items)
    mix = ", ".join(f"{sum(i['language'] == lg for i in items)} {lg}" for lg in langs)
    lines = [
        f"# Silver labels: {set_name} set ({date.today().isoformat()})",
        "",
        f"{n} postings ({mix}). "
        f"Labellers compared: {', '.join(answers) or 'none'}. "
        f"Missing (no valid answer for every posting): {', '.join(missing) or 'none'}.",
        "",
    ]
    summary: dict[str, Any] = {
        "set": set_name,
        "n": n,
        "labellers": list(answers),
        "missing": missing,
    }
    if len(answers) < 2:
        lines.append(
            "Fewer than two labellers have answers for the whole set: no agreement tables.\n"
        )
    else:
        counts = agreement_counts(answers, items)
        lines += [
            "## 1. Agreement per field",
            "",
            "Share of postings where all labellers agree / a group of at least two agrees "
            "(not everyone) / all differ. `office_cantons` is the exact set; `relevant` is "
            "included.",
            "",
        ]
        header = ["field", *[f"{lg}" for lg in ["all", *langs]]]
        rows = []
        for key in KEYS:
            row = [key]
            for lg in ["all", *langs]:
                c = counts[(key, lg)]
                tot = sum(c.values())
                row.append(
                    f"{pct(c['all'], tot)} / {pct(c['partial'], tot)} / {pct(c['none'], tot)}"
                )
            rows.append(row)
        total_all = {lg: Counter() for lg in ["all", *langs]}
        for (_key, lg), c in counts.items():
            total_all[lg].update(c)
        row = ["**all fields**"]
        for lg in ["all", *langs]:
            c, tot = total_all[lg], sum(total_all[lg].values())
            row.append(f"{pct(c['all'], tot)} / {pct(c['partial'], tot)} / {pct(c['none'], tot)}")
        rows.append(row)
        lines.append(table(header, rows))
        c = total_all["all"]
        tot = sum(c.values())
        summary["all_agree"] = c["all"] / tot if tot else 0.0
        lines += ["## 2. Pairwise agreement", ""]
        pw = pairwise(answers, items)
        rows = []
        for (a, b), per_key in pw.items():
            k = sum(v[0] for v in per_key.values())
            t = sum(v[1] for v in per_key.values())
            rows.append([f"{a} - {b}", pct(k, t), *[pct(*per_key[key]) for key in KEYS]])
        lines.append(table(["pair", "all keys", *KEYS], rows))
        summary["pairwise"] = {
            f"{a}-{b}": sum(v[0] for v in pk.values()) / max(1, sum(v[1] for v in pk.values()))
            for (a, b), pk in pw.items()
        }
    lines += ["## 3. Low-confidence answers", ""]
    rows = []
    for lab, recs in answers.items():
        low, total = low_confidence(recs)
        rows.append([lab, f"{low} of {total}", pct(low, total)])
    lines.append(table(["labeller", "low-confidence answers", "share"], rows))

    owner = labels_mod.latest(labels_path or data_dir / "labels.jsonl")
    labelled = [i for i in items if i["posting_id"] in owner]
    lines += ["## 4. Agreement with the owner", ""]
    if not labelled or not answers:
        lines.append("No owner-labelled postings in this set.\n")
    else:
        lines.append(
            f"Owner-labelled postings in the set: {len(labelled)}. A field the owner has not "
            "labelled (for example `contract_type` on early labels) is not compared.\n"
        )
        oa = owner_agreement(answers, owner, items)
        rows = []
        for lab, per_key in oa.items():
            k = sum(v[0] for v in per_key.values())
            t = sum(v[1] for v in per_key.values())
            rows.append(
                [lab, f"{k}/{t} ({pct(k, t)})", *[f"{v[0]}/{v[1]}" for v in per_key.values()]]
            )
        lines.append(table(["labeller", "all keys", *KEYS], rows))

    lines += ["## 5. Tokens, cost and time", ""]
    rows = []
    grand = {"input": 0, "output": 0, "reasoning": 0, "cost_usd": 0.0, "duration_ms": 0}
    for lab, recs in answers.items():
        t = usage_totals(recs)
        if not t["has_usage"] and lab == "opus":
            rows.append(_opus_row(data_dir, n))
            continue
        if not t["has_usage"]:
            rows.append([lab, "no usage recorded"] + [""] * 7)
            continue
        for k in grand:
            grand[k] += t[k]
        rows.append(_usage_row(lab, t, n))
    rows.append(_usage_row("total (pi/API labellers)", {**grand, "attempts": "", "retried": ""}, n))
    lines.append(
        table(
            [
                "labeller",
                "input tokens",
                "output tokens",
                "reasoning tokens",
                "cost",
                "time",
                "cost / posting",
                "time / posting",
                "retried postings",
            ],
            rows,
        )  # fmt: skip
    )
    lines.append(
        "Output tokens may already include reasoning tokens (depends on the provider). "
        "Failed attempts are included. Opus usage comes from `usage_opus.json` (total tokens "
        "only).\n"
    )
    lines += ["## 6. Validation failures and retries", ""]
    rows = []
    for lab in silver.LABELLERS:
        recs = load_answers(data_dir, lab, pids) if lab in answers else None
        if recs is None:
            have = sum((data_dir / "silver" / lab / f"{p}.json").exists() for p in pids)
            rows.append([lab, f"{have}/{n} answers", "-", "-"])
            continue
        t = usage_totals(recs)
        rows.append([lab, f"{n}/{n} answers", t["retried"] if t["has_usage"] else "-",
                     t["attempts"] - n if t["has_usage"] else "-"])  # fmt: skip
    lines.append(
        table(["labeller", "valid answers", "postings needing a retry", "extra attempts"], rows)
    )
    lines += _audit_section(data_dir, summary)
    return "\n".join(lines), summary


def _audit_section(data_dir: Path, summary: dict) -> list[str]:
    """Owner audit of unanimous answers (error rate of the reference) and split resolution."""
    from jobfacts import reference

    stats = reference.audit_stats(data_dir, summary.get("set", "all"))
    lines = ["## 7. Audit of the reference", ""]
    k, n = stats["correct"], stats["audited"]
    summary["audit"] = {"correct": k, "audited": n, **{
        x: stats[x] for x in ("splits", "splits_resolved")}}  # fmt: skip
    lines.append(
        f"Split keys (no two labellers agree) handed to the owner: {stats['splits']}, "
        f"resolved: {stats['splits_resolved']}.\n"
    )
    if not n:
        lines.append("No audited unanimous answers yet (`jobfacts silver review-sheet`).\n")
        return lines
    rows = []
    for key in [*KEYS, "all keys"]:
        cell_ = [k, n] if key == "all keys" else stats["per_key"].get(key)
        if cell_ is None:
            continue
        lo, hi = wilson(*cell_)
        rows.append([key, f"{cell_[0]} of {cell_[1]}", pct(*cell_),
                     f"{100 * lo:.0f}-{100 * hi:.0f}%"])  # fmt: skip
    lines.append(
        "Unanimous answers (all three labellers agree) drawn at random and checked by the "
        "owner. The interval is a 95% Wilson interval for the share correct; per-field rows "
        "have small n.\n"
    )
    lines.append(table(["key", "correct", "share", "95% interval"], rows))
    return lines


def _usage_row(lab: str, t: dict, n: int) -> list[Any]:
    sec = t["duration_ms"] / 1000
    return [
        lab,
        f"{t['input']:,}",
        f"{t['output']:,}",
        f"{t['reasoning']:,}",
        f"${t['cost_usd']:.4f}",
        f"{sec:.0f} s",
        f"${t['cost_usd'] / n:.4f}" if n else "-",
        f"{sec / n:.0f} s" if n else "-",
        t["retried"],
    ]


def _opus_row(data_dir: Path, n: int) -> list[Any]:
    path = data_dir / "silver" / "usage_opus.json"
    if not path.exists():
        return ["opus", "no usage_opus.json", "", "", "", "", "", "", ""]
    u = json.loads(path.read_text(encoding="utf-8"))
    sec = u.get("duration_ms", 0) / 1000
    return [
        "opus",
        "-",
        "-",
        "-",
        f"{u.get('total_tokens', 0):,} tokens in total (no cost)",
        f"{sec:.0f} s",
        "-",
        f"{sec / n:.0f} s" if n else "-",
        "-",
    ]


def write_report(data_dir: Path, set_name: str) -> tuple[Path, str, dict]:
    text, summary = build_report(data_dir, set_name)
    out = Path(data_dir) / "reports"
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"silver_{set_name}_{date.today().isoformat()}.md"
    path.write_text(text, encoding="utf-8")
    return path, text, summary
