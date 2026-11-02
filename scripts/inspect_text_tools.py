"""Print real-data statistics for the sentence splitter, language and locations tools.

Run: uv run python scripts/inspect_text_tools.py
Needs the compose database with collected postings. Output stays on screen.
"""

import statistics
from collections import Counter

from jobfacts.db import connect
from jobfacts.language import detect
from jobfacts.locations import canton_hints, mentions_switzerland
from jobfacts.sentences import split

SWISHISH = (
    "switzerland", "schweiz", "suisse", "svizzera", "swiss", "zürich", "zurich", "geneva",
    "genève", "basel", "bern", "lausanne", "lugano", "zug", "luzern", "lucerne", "winterthur",
    "fribourg", "neuchâtel", "st. gallen", "sion", "biel", "baden",
)  # fmt: skip


def main() -> None:
    with connect() as conn:
        rows = conn.execute("SELECT title, location_raw, description_text FROM postings").fetchall()
    print(f"postings: {len(rows)}")

    counts = sorted(len(split(d or "")) for _, _, d in rows)
    p95 = counts[int(0.95 * (len(counts) - 1))]
    print(
        f"\nSENTENCES per posting: median={statistics.median(counts)} p95={p95} "
        f"max={counts[-1]} over255={sum(c > 255 for c in counts)}"
    )

    langs = Counter(detect(d or "") for _, _, d in rows)
    print("\nLANGUAGE:", dict(langs.most_common()))

    with_cand = 0
    missing: Counter = Counter()
    matched: Counter = Counter()
    for _, loc, _ in rows:
        loc = loc or ""
        cands = canton_hints(loc, location_field=True)
        if cands or mentions_switzerland(loc, location_field=True):
            with_cand += 1
            matched.update(cands)
        elif any(w in loc.lower() for w in SWISHISH):
            missing[loc] += 1
    print(
        f"\nLOCATION: {with_cand}/{len(rows)} postings with a candidate in location_raw "
        f"({100 * with_cand / len(rows):.1f}%)"
    )
    print("most common cantons:", matched.most_common(15))
    print("Swiss-looking location_raw WITHOUT candidate (top 20):")
    for loc, n in missing.most_common(20):
        print(f"  {n:4d}  {loc!r}")

    # Candidates in title/description only (free-text rule), for false-positive spotting.
    free: Counter = Counter()
    for title, _, d in rows:
        free.update(canton_hints(f"{title}\n{d or ''}"))
    print("\nFree-text canton hints (top 25):", free.most_common(25))
    unlocated = Counter(
        loc for _, loc, _ in rows if loc and not mentions_switzerland(loc, location_field=True)
    )
    print("Most common location_raw with no candidate (top 25):")
    for loc, n in unlocated.most_common(25):
        print(f"  {n:4d}  {loc!r}")


if __name__ == "__main__":
    main()
