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
    sub.add_parser("relevance", help="score all open postings with the Jev relevance filter")
    ext = sub.add_parser("extract", help="extract the fields of eligible postings")
    ext.add_argument("--extractor", choices=["jev", "deepseek", "mistral"], required=True)
    ext.add_argument("--limit", type=int)
    ext.add_argument("--posting-ids", type=int, nargs="+")
    args = parser.parse_args(argv)

    with db.connect() as conn:
        if args.command == "migrate":
            applied = db.migrate(conn)
            print("applied: " + ", ".join(applied) if applied else "nothing to apply")
            return 0
        if args.command == "relevance":
            from jobfacts.extract import jev
            from jobfacts.extract.run import run_relevance

            r = run_relevance(conn, jev.make_client())
            print(f"scored {r.scored}, relevant {r.relevant}, errors {r.errors}")
            return 0
        if args.command == "extract":
            from jobfacts.extract.run import make_extractor, run_extraction

            run_id, results = run_extraction(
                conn, make_extractor(args.extractor), args.posting_ids, args.limit
            )
            failed = sum(1 for x in results if x.error)
            cost = sum(x.cost_usd for x in results)
            print(f"run {run_id}: {len(results)} postings, {failed} errors, ${cost:.4f}")
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
