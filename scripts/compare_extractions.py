"""Compare extraction runs: agreement per field, per language, cost, latency, examples.

Usage: uv run python scripts/compare_extractions.py RUN_ID_A RUN_ID_B RUN_ID_C
No labels exist yet, so this reports agreement between models, not accuracy.
"""

import itertools
import sys
from collections import defaultdict

from jobfacts import db
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import FIELDS

FIELD_NAMES = [*FIELDS, "seniority"]


def main(run_ids: list[int]) -> None:
    conn = db.connect()
    names = {}
    rows: dict[int, dict[int, dict]] = {}
    for rid in run_ids:
        extractor, model = conn.execute(
            "SELECT extractor, model FROM extraction_runs WHERE id = %s", (rid,)
        ).fetchone()
        names[rid] = extractor
        cols = ", ".join([*FIELD_NAMES, *(f"{f}_sentences" for f in FIELDS)])
        cur = conn.execute(
            f"SELECT posting_id, {cols}, office_cantons, error, cost_usd, latency_ms, "
            "tokens_in, tokens_out FROM extractions WHERE run_id = %s",
            (rid,),
        )
        keys = [d.name for d in cur.description]
        rows[rid] = {r[0]: dict(zip(keys, r, strict=True)) for r in cur.fetchall()}
        n = len(rows[rid])
        cost = sum(r["cost_usd"] or 0 for r in rows[rid].values())
        lat = sum(r["latency_ms"] or 0 for r in rows[rid].values()) / n
        err = sum(1 for r in rows[rid].values() if r["error"])
        print(
            f"{extractor:9} {model:30} n={n} errors={err} cost=${cost:.4f} mean_latency={lat:.0f}ms"
        )
    ids = sorted(set.intersection(*(set(r) for r in rows.values())))
    post = {
        r[0]: PostingText.build(*r)
        for r in conn.execute(
            "SELECT id, title, location_raw, description_text FROM postings WHERE id = ANY(%s)",
            (ids,),
        )
    }
    langs = defaultdict(list)
    for i in ids:
        langs[post[i].language].append(i)
    counts = {k: len(v) for k, v in langs.items()}
    print(f"\nPostings in all runs: {len(ids)}; by language: {counts}")

    def same(a, b, f, i):
        ra, rb = rows[a][i], rows[b][i]
        if ra["error"] or rb["error"]:
            return None
        if f == "office_cantons":
            return set(ra[f] or []) == set(rb[f] or [])
        return ra[f] == rb[f]

    for a, b in itertools.combinations(run_ids, 2):
        print(f"\n== {names[a]} vs {names[b]} (share of postings with identical value)")
        print(f"{'field':26}{'all':>8}" + "".join(f"{k:>8}" for k in langs))
        for f in [*FIELD_NAMES, "office_cantons"]:
            cells = []
            for group in [ids, *langs.values()]:
                r = [same(a, b, f, i) for i in group]
                r = [x for x in r if x is not None]
                cells.append(f"{sum(r) / len(r):8.2f}" if r else f"{'-':>8}")
            print(f"{f:26}" + "".join(cells))

    print("\n== Disagreement examples (all three differ or any differ), sentence quoted")
    shown = 0
    for f in FIELD_NAMES[:-1]:
        for i in ids:
            vals = {rid: rows[rid][i][f] for rid in run_ids if not rows[rid][i]["error"]}
            if len(set(vals.values())) < 2 or shown >= 5:
                continue
            shown += 1
            print(f"\nposting {i} field {f} | {post[i].title[:60]} ({post[i].language})")
            for rid, v in vals.items():
                ids_ = rows[rid][i][f"{f}_sentences"] or []
                quote = "; ".join(post[i].text_of(s)[:110] for s in ids_[:1])
                print(f"  {names[rid]:9} {v:22} {ids_} {quote!r}")
            break


if __name__ == "__main__":
    main([int(x) for x in sys.argv[1:]])
