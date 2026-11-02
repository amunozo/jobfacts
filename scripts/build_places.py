"""Build `config/swiss_places.csv` from swisstopo's official locality register.

Source: "Amtliches Ortschaftenverzeichnis" (official index of cities and towns including
postal codes and perimeter), swisstopo open government data (OGD).
  Dataset:  https://opendata.swiss/en/dataset/amtliches-ortschaftenverzeichnis-mit-plz-und-perimeter
  Download: https://data.geo.admin.ch/ch.swisstopo-vd.ortschaftenverzeichnis_plz/
            ortschaftenverzeichnis_plz/ortschaftenverzeichnis_plz_2056.csv.zip
  Terms:    https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices
            (checked 2 Nov 2026): the data "may be used, distributed and made accessible",
            also enriched, processed and used commercially; a source reference is mandatory
            ("(c) swisstopo" or "Bundesamt fuer Landestopografie swisstopo").
  So the derived CSV may be committed, with the attribution line in its header.

Output columns: locality, postcode, commune, bfs_nr, canton, share
- one row per (locality, postcode, commune); "Lausanne 25" style numbers are removed from
  locality names and the rows merged; `share` is the sum of the register's address shares
  (%) and is only used to pick the main commune when a locality spans several;
- rows without a canton (Liechtenstein and other non-Swiss rows) are dropped.

Usage: uv run python scripts/build_places.py
"""

import csv
import io
import re
import urllib.request
import zipfile
from collections import defaultdict
from datetime import date
from pathlib import Path

URL = (
    "https://data.geo.admin.ch/ch.swisstopo-vd.ortschaftenverzeichnis_plz/"
    "ortschaftenverzeichnis_plz/ortschaftenverzeichnis_plz_2056.csv.zip"
)
OUT = Path(__file__).resolve().parents[1] / "config" / "swiss_places.csv"
HEADER = """\
# Swiss localities: Amtliches Ortschaftenverzeichnis (official locality register).
# Source: Bundesamt fuer Landestopografie swisstopo (c) swisstopo,
#   https://data.geo.admin.ch/ch.swisstopo-vd.ortschaftenverzeichnis_plz/
# Terms: swisstopo OGD, use/distribution/processing allowed, source reference mandatory:
#   https://www.swisstopo.admin.ch/en/terms-of-use-free-geodata-and-geoservices
# Processed by scripts/build_places.py on {today}: trailing house-zone numbers removed,
# rows merged, rows without a canton dropped. Do not edit by hand; rerun the script.
"""


def clean_locality(name: str) -> str:
    return re.sub(r"\s+[A-Z]?\d+$", "", name.strip())


def build(raw: bytes) -> tuple[list[list[str]], int]:
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        member = next(n for n in z.namelist() if n.endswith(".csv"))
        text = z.read(member).decode("utf-8-sig")
    merged: dict[tuple, float] = defaultdict(float)
    dropped = 0
    for row in csv.DictReader(io.StringIO(text), delimiter=";"):
        canton = row["Kantonskürzel"].strip()
        if not canton:
            dropped += 1
            continue
        share = float(row["Adressenanteil"].replace("%", "").strip() or 0)
        key = (
            clean_locality(row["Ortschaftsname"]),
            row["PLZ4"].strip(),
            row["Gemeindename"].strip(),
            row["BFS-Nr"].strip(),
            canton,
        )
        merged[key] += share
    rows = [[*k, f"{v:.0f}"] for k, v in sorted(merged.items(), key=lambda kv: (kv[0][4], kv[0]))]
    return rows, dropped


def main() -> None:
    with urllib.request.urlopen(URL, timeout=120) as response:  # noqa: S310 - fixed https URL
        raw = response.read()
    rows, dropped = build(raw)
    with OUT.open("w", encoding="utf-8", newline="") as f:
        f.write(HEADER.format(today=date.today().isoformat()))
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["locality", "postcode", "commune", "bfs_nr", "canton", "share"])
        w.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT} (dropped {dropped} rows without canton)")


if __name__ == "__main__":
    main()
