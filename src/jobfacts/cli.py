"""Command line: `jobfacts migrate` and `jobfacts collect`."""

import argparse
from pathlib import Path

from jobfacts import db
from jobfacts.collect.run import collect


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def _search(conn, args) -> int:
    import json
    import sys

    from jobfacts import search

    filters = search.Filters(
        query=args.query,
        cantons=[
            c.upper() if c.lower() != "outside_switzerland" else c.lower()
            for c in _csv(args.canton)
        ],
        remote=args.remote,
        remote_unclear=args.remote_unclear,
        work_modes=_csv(args.work_mode),
        german_max=args.german_max,
        french_max=args.french_max,
        show_unclear_language=args.show_unclear_language,
        seniority=_csv(args.seniority),
        new_days=args.new_days,
        limit=args.limit,
    )
    try:
        hits, total = search.search(conn, filters, evidence=args.evidence)
    except search.SearchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(
            json.dumps(
                {"total": total, "results": [h.as_dict() for h in hits]},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(search.format_table(hits, total, args.evidence))
    return 0


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
    ext.add_argument(
        "--for-search",
        action="store_true",
        help="incremental run for `jobfacts search`: only new or changed open board postings",
    )
    sea = sub.add_parser("search", help="search open board postings (see docs/search.md)")
    sea.add_argument("query", nargs="?", default="", help="full-text query (title + description)")
    sea.add_argument("--canton", help="comma-separated canton codes, e.g. ZH,VD")
    sea.add_argument("--remote", action="store_true", help="remote and allowed from Switzerland")
    sea.add_argument(
        "--remote-unclear", action="store_true", help="remote, also if region unclear/not stated"
    )
    sea.add_argument("--work-mode", help="comma-separated: onsite, hybrid, remote")
    sea.add_argument("--german-max", choices=["none", "intermediate", "fluent"])
    sea.add_argument("--french-max", choices=["none", "intermediate", "fluent"])
    sea.add_argument(
        "--show-unclear-language",
        action="store_true",
        help="keep 'required, level not stated' when a max below fluent is set",
    )
    sea.add_argument("--seniority", help="comma-separated, e.g. junior,mid")
    sea.add_argument("--new-days", type=int, help="first seen in the last N days")
    sea.add_argument("--limit", type=int, default=20)
    sea.add_argument("--evidence", action="store_true", help="print supporting sentences")
    sea.add_argument("--json", action="store_true", help="print JSON for other programs")
    imp = sub.add_parser("import-applications", help="import application job descriptions")
    imp.add_argument("path", type=Path)
    sub.add_parser("select-test-set", help="write data/private/test_set.jsonl")
    lab = sub.add_parser("label", help="start the labelling app on localhost")
    lab.add_argument("--port", type=int, default=8765)
    lab.add_argument(
        "--data-dir", type=Path, help="folder with test_set.jsonl (default data/private)"
    )
    ev = sub.add_parser("evaluate", help="write the accuracy report (Markdown) from the labels")
    ev.add_argument("--data-dir", type=Path, help="folder with the test set (default data/private)")
    oo = sub.add_parser(
        "jev-order-test", help="run Jev with reversed and shuffled options on the test set"
    )
    oo.add_argument("--data-dir", type=Path, help="folder with the test set (default data/private)")
    oo.add_argument("--force", action="store_true", help="rerun variants that already have a run")
    args = parser.parse_args(argv)

    with db.connect() as conn:
        if args.command == "migrate":
            applied = db.migrate(conn)
            print("applied: " + ", ".join(applied) if applied else "nothing to apply")
            return 0
        if args.command == "search":
            return _search(conn, args)
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
        if args.command == "evaluate":
            from jobfacts import labels
            from jobfacts.evaluate import run_evaluate, summary_lines
            from jobfacts.testset import PRIVATE_DIR

            data_dir = args.data_dir or PRIVATE_DIR
            text, path = run_evaluate(conn, data_dir)
            print(summary_lines(text, len(labels.latest(data_dir / "labels.jsonl"))))
            print(f"\nreport: {path}")
            return 0
        if args.command == "jev-order-test":
            from jobfacts import evaluate, ordertest
            from jobfacts.testset import PRIVATE_DIR

            data_dir = args.data_dir or PRIVATE_DIR
            new = ordertest.run_order_test(conn, data_dir, force=args.force)
            print(f"new runs: {new or 'none (already present; use --force)'}")
            runs = ordertest.read_runs(data_dir / "test_set_runs.json")
            exts = {k: evaluate.load_run(conn, runs[k]) for k in runs if k.startswith("jev")}
            for k in exts:
                if k != "jev":
                    cost = evaluate.cost_summary(exts[k])
                    print(f"{k}: run {runs[k]}, ${cost['cost']:.4f}, errors {cost['errors']}")
            print(ordertest.render(evaluate.order_comparisons(exts)))
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
                conn,
                make_extractor(args.extractor),
                args.posting_ids,
                args.limit,
                for_search=args.for_search,
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
    for name, note in summary.notes:
        print(f"  {name}: {note}")
    for name, error in summary.failures:
        print(f"  FAILED {name}: {error}")
    return 0
