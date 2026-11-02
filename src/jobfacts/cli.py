"""Command line: `jobfacts migrate` and `jobfacts collect`."""

import argparse

from jobfacts import db
from jobfacts.collect.run import collect


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfacts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    col = sub.add_parser("collect", help="read all employer boards and store postings")
    col.add_argument("--only", help="only this employer name or ATS name (for testing)")
    args = parser.parse_args(argv)

    with db.connect() as conn:
        if args.command == "migrate":
            applied = db.migrate(conn)
            print("applied: " + ", ".join(applied) if applied else "nothing to apply")
            return 0
        summary = collect(conn, only=args.only)
    c = summary.counts
    print(
        f"run {summary.run_id}: boards ok {summary.boards_ok}, failed {summary.boards_failed}; "
        f"new {c.new}, updated {c.updated}, closed {c.closed}; {summary.seconds:.0f}s"
    )
    for name, error in summary.failures:
        print(f"  FAILED {name}: {error}")
    return 0
