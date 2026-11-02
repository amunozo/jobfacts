"""Command line: `jobfacts migrate` and `jobfacts collect`."""

import argparse
from pathlib import Path

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
    imp = sub.add_parser("import-applications", help="import application job descriptions")
    imp.add_argument("path", type=Path)
    sub.add_parser("select-test-set", help="write data/private/test_set.jsonl")
    lab = sub.add_parser("label", help="start the labelling app on localhost")
    lab.add_argument("--port", type=int, default=8765)
    lab.add_argument(
        "--data-dir", type=Path, help="folder with test_set.jsonl (default data/private)"
    )
    args = parser.parse_args(argv)

    with db.connect() as conn:
        if args.command == "migrate":
            applied = db.migrate(conn)
            print("applied: " + ", ".join(applied) if applied else "nothing to apply")
            return 0
        if args.command == "import-applications":
            from jobfacts.applications import import_applications

            c = import_applications(conn, args.path)
            print(f"new {c.new}, updated {c.updated}, unchanged {c.unchanged}")
            return 0
        if args.command == "select-test-set":
            from jobfacts.testset import select_test_set

            select_test_set(conn)
            return 0
        if args.command == "label":
            from jobfacts.label_app.server import serve
            from jobfacts.testset import PRIVATE_DIR

            serve(args.port, args.data_dir or PRIVATE_DIR)
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
