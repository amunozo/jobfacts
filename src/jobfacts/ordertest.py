"""Jev option-order bias test: does the order of the value options change Jev's answers?

Jev's documentation says it leans toward the first Choice option, and our spec puts
`not_stated` / `not_mentioned` first on purpose. This module reruns Jev on the test set with
the options reversed and shuffled (fixed seed), stores the runs as normal extraction runs and
compares the answers with the original Jev run.

The comparison works on "extraction dicts" (see `jobfacts.evaluate.load_run`): keys `error`,
`values`, `office_cantons`.
"""

import json
from collections import Counter
from pathlib import Path

import psycopg

from jobfacts.extract.spec import FIELDS

NEUTRAL = {"not_stated", "not_mentioned"}
VARIANTS = ("reversed", "shuffled")  # run id keys in test_set_runs.json: jev_<variant>


def _direction(old: str, new: str) -> str:
    if old in NEUTRAL and new not in NEUTRAL:
        return "neutral_to_value"
    if old not in NEUTRAL and new in NEUTRAL:
        return "value_to_neutral"
    return "value_to_value"


def compare(original: dict[int, dict], variant: dict[int, dict]) -> dict[str, dict]:
    """Per field: postings compared, how many changed, and the direction of each change.

    Only postings with a successful extraction in both runs are compared. Directions:
    `neutral_to_value` (not_stated -> something), `value_to_neutral`, `value_to_value`.
    `office_cantons` counts a changed set; direction = cantons added / removed / replaced.
    """
    out: dict[str, dict] = {}
    pids = [
        p
        for p in sorted(original)
        if p in variant and not original[p]["error"] and not variant[p]["error"]
    ]
    for name in [*FIELDS, "office_cantons"]:
        n = changed = 0
        directions: Counter = Counter()
        moves: Counter = Counter()
        for p in pids:
            n += 1
            if name == "office_cantons":
                if original[p]["office_cantons"] is None or variant[p]["office_cantons"] is None:
                    n -= 1  # a run from before the canton redesign: nothing to compare
                    continue
                a, b = set(original[p]["office_cantons"]), set(variant[p]["office_cantons"])
                if a == b:
                    continue
                changed += 1
                directions["added" if a < b else "removed" if b < a else "replaced"] += 1
                continue
            old, new = original[p]["values"][name], variant[p]["values"][name]
            if old == new:
                continue
            changed += 1
            directions[_direction(old, new)] += 1
            moves[f"{old} -> {new}"] += 1
        out[name] = {"n": n, "changed": changed, "directions": dict(directions), "moves": moves}
    return out


def render(comparisons: dict[str, dict[str, dict]]) -> str:
    """Markdown tables, one per variant. `comparisons`: variant name -> result of `compare`."""
    lines: list[str] = []
    for variant, result in comparisons.items():
        lines += [f"### Original order vs {variant}", ""]
        lines += [
            "| field | n | changed | share | not_stated -> value | value -> not_stated "
            "| value -> value | most common moves |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for name, r in result.items():
            d = r["directions"]
            share = f"{100 * r['changed'] / r['n']:.0f}%" if r["n"] else "-"
            moves = "; ".join(f"{m} ({c})" for m, c in r["moves"].most_common(3))
            if name == "office_cantons":
                cells = ["-", "-", "-"]
                moves = ", ".join(f"{k} {v}" for k, v in sorted(d.items()))
            else:
                cells = [
                    str(d.get("neutral_to_value", 0)),
                    str(d.get("value_to_neutral", 0)),
                    str(d.get("value_to_value", 0)),
                ]
            lines.append(
                f"| {name} | {r['n']} | {r['changed']} | {share} | "
                + " | ".join(cells)
                + f" | {moves} |"
            )
        lines.append("")
    return "\n".join(lines)


def read_runs(path: Path) -> dict[str, int]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def run_order_test(
    conn: psycopg.Connection, data_dir: Path, client=None, force: bool = False
) -> dict[str, int]:
    """Run Jev with reversed and shuffled options on the test set; returns the new run ids."""
    from jobfacts.extract.jev import JevExtractor
    from jobfacts.extract.run import run_extraction
    from jobfacts.testset import load_test_set

    runs_file = data_dir / "test_set_runs.json"
    runs = read_runs(runs_file)
    ids = [item["posting_id"] for item in load_test_set(data_dir / "test_set.jsonl")]
    new: dict[str, int] = {}
    for variant in VARIANTS:
        key = f"jev_{variant}"
        if key in runs and not force:
            continue
        extractor = JevExtractor(client, ordering=variant)
        run_id, _ = run_extraction(conn, extractor, ids, notes=f"jev option order test: {variant}")
        new[key] = run_id
        runs[key] = run_id
        runs_file.write_text(json.dumps(runs) + "\n", encoding="utf-8")  # keep progress
    return new
