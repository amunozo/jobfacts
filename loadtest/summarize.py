"""Markdown table of a load-test results folder (the Locust CSV files written by run.sh).
Usage: python loadtest/summarize.py loadtest/results/2026-11-08"""

import csv
import sys
from pathlib import Path

folder = Path(sys.argv[1])
files = sorted(folder.glob("users_*_stats.csv"), key=lambda p: int(p.stem.split("_")[1]))
print("| Users | Endpoint | Requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failed |")
print("|---:|---|---:|---:|---:|---:|---:|---:|---:|")
for path in files:
    users = path.stem.split("_")[1]
    rows = list(csv.DictReader(path.open()))
    rows.sort(key=lambda r: r["Name"] != "Aggregated")  # the overall line first
    for r in rows:
        n, failed = int(r["Request Count"]), int(r["Failure Count"])
        name = "**all**" if r["Name"] == "Aggregated" else r["Name"]
        pct = f"{100 * failed / n:.1f}%" if n else "-"
        print(
            f"| {users} | {name} | {n} | {float(r['Requests/s']):.1f} | {r['50%']} | {r['95%']} "
            f"| {r['99%']} | {float(r['Max Response Time']):.0f} | {failed} ({pct}) |"
        )


# Memory and CPU of the containers, sampled every few seconds during each step (docker stats).
UNITS = {"KiB": 1 / 1024, "MiB": 1, "GiB": 1024}


def mib(text: str) -> float:
    for unit, factor in UNITS.items():
        if text.endswith(unit):
            return float(text[: -len(unit)]) * factor
    return float(text.rstrip("B")) / 2**20


print()
print("| Users | Container | Memory max (MiB) | Memory limit | CPU avg | CPU max |")
print("|---:|---|---:|---:|---:|---:|")
for path in sorted(folder.glob("docker_stats_*.txt"), key=lambda p: int(p.stem.split("_")[2])):
    users = path.stem.split("_")[2]
    samples: dict[str, list[tuple[float, float]]] = {}
    limits: dict[str, str] = {}
    for line in path.read_text().splitlines():
        name, used, _, limit, cpu = line.split()
        samples.setdefault(name, []).append((mib(used), float(cpu.rstrip("%"))))
        limits[name] = limit
    for name, rows in sorted(samples.items()):
        cpus = [c for _, c in rows]
        short = name.removeprefix("jobfacts-loadtest-")
        peak = max(m for m, _ in rows)
        print(
            f"| {users} | {short} | {peak:.0f} | {limits[name]} "
            f"| {sum(cpus) / len(cpus):.0f}% | {max(cpus):.0f}% |"
        )
