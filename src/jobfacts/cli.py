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
        no_fixed_term=args.no_fixed_term,
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


def _reference(args, data_dir) -> int:
    from jobfacts import reference

    if args.action == "combine":
        s = reference.combine(data_dir, args.set)
        print(f"wrote {s['written']} postings to {s['path']}; support {s['support']}")
        if s["missing"]:
            print(f"not yet answered by all three labellers: {len(s['missing'])} postings")
        print(f"keys decided by review: {s['reviewed']}; unresolved splits: {s['unresolved']}")
    elif args.action == "review-sheet":
        s = reference.review_sheet(data_dir, args.set, args.audit)
        print(
            f"{s['path']}: {s['splits']} split keys, {s['audit']} audit keys "
            f"({s['new']} new); postings not answered by all three: {s['incomplete']}"
        )
    else:
        if not args.file:
            print("review-apply needs the decisions file")
            return 2
        try:
            s = reference.review_apply(data_dir, args.file, args.set)
        except ValueError as e:
            print(f"nothing stored: {e}")
            return 2
        print(f"stored {s['stored']} decisions in {s['path']}; run `silver combine` to apply")
    return 0


def _silver(args) -> int:
    from jobfacts import silver
    from jobfacts.testset import PRIVATE_DIR

    data_dir = args.data_dir or PRIVATE_DIR
    if args.action == "pilot-select":
        ids = silver.pilot_select(data_dir)
        print(f"pilot set: {len(ids)} postings {ids} -> {data_dir / 'silver' / silver.PILOT_FILE}")
        return 0
    try:
        silver.items_for(data_dir, args.set)  # fails early when the pilot is not selected
    except ValueError as e:
        print(e)
        return 2
    if args.action == "render":
        n = silver.render_all(data_dir, args.set)
        print(f"wrote {n} prompts to {data_dir / 'silver' / 'prompts'}")
        return 0
    if args.action == "status":
        counts = silver.status(data_dir, args.set)
        total = counts.pop("total")
        for lab, n in counts.items():
            print(f"{lab}: {n}/{total} valid answers")
        return 0
    if args.action in ("combine", "review-sheet", "review-apply"):
        return _reference(args, data_dir)
    if args.action == "report":
        from jobfacts import silver_report

        path, _text, summary = silver_report.write_report(data_dir, args.set)
        print(f"set {summary['set']}: {summary['n']} postings; compared: {summary['labellers']}")
        if summary["missing"]:
            print(f"missing: {summary['missing']}")
        if "all_agree" in summary:
            print(f"all labellers agree on {100 * summary['all_agree']:.0f}% of the answers")
            for pair, share in summary["pairwise"].items():
                print(f"  {pair}: {100 * share:.0f}%")
        print(f"report: {path}")
        return 0
    if not args.labeller:
        print("--labeller is required for run and check")
        return 2
    if args.action == "run":
        s = silver.run_labeller(
            args.labeller,
            data_dir,
            limit=args.limit,
            posting_id=args.posting,
            force=args.force,
            set_name=args.set,
        )
    else:
        s = silver.check_labeller(args.labeller, data_dir, args.set)
    print(", ".join(f"{k} {len(v)}" for k, v in s.items()))
    for pid, reason in s["failed"].items():
        print(f"  failed {pid}: {reason[:300]}")
    return 1 if s["failed"] else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="jobfacts")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("migrate", help="apply database migrations")
    day = sub.add_parser(
        "daily", help="database up, migrate, collect, relevance, Jev extraction for search"
    )
    day.add_argument("--skip-collect", action="store_true", help="skip the collect step")
    col = sub.add_parser("collect", help="read all employer boards and store postings")
    col.add_argument("--only", help="only this employer name or ATS name (for testing)")
    sub.add_parser("relevance", help="score all open postings with the Jev relevance filter")
    ext = sub.add_parser("extract", help="extract the fields of eligible postings")
    ext.add_argument("--extractor", choices=["jev", "deepseek", "mistral"], required=True)
    ext.add_argument("--limit", type=int)
    ext.add_argument("--data-dir", type=Path, help="holdout folder (default data/private)")
    ext.add_argument("--posting-ids", type=int, nargs="+")
    ext.add_argument(
        "--set",
        choices=["holdout"],
        help="run on the held-out set and record the run in holdout_runs.json",
    )
    ext.add_argument("--run-key", help="key in holdout_runs.json (default: the extractor name)")
    ext.add_argument("--force", action="store_true", help="replace an existing holdout run key")
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
    sea.add_argument(
        "--no-fixed-term", action="store_true", help="drop postings with a fixed-term contract"
    )
    sea.add_argument("--seniority", help="comma-separated, e.g. junior,mid")
    sea.add_argument("--new-days", type=int, help="first seen in the last N days")
    sea.add_argument("--limit", type=int, default=20)
    sea.add_argument("--evidence", action="store_true", help="print supporting sentences")
    sea.add_argument("--json", action="store_true", help="print JSON for other programs")
    imp = sub.add_parser("import-applications", help="import application job descriptions")
    imp.add_argument("path", type=Path)
    sub.add_parser("select-test-set", help="write data/private/test_set.jsonl")
    hol = sub.add_parser(
        "select-holdout", help="write data/private/holdout_set.jsonl (see docs/labelling.md)"
    )
    hol.add_argument("--n", type=int, default=50)
    hol.add_argument("--force", action="store_true", help="replace an existing holdout file")
    lab = sub.add_parser("label", help="start the labelling app on localhost")
    lab.add_argument("--port", type=int, default=8765)
    lab.add_argument(
        "--data-dir", type=Path, help="folder with test_set.jsonl (default data/private)"
    )
    lab.add_argument(
        "--languages", default="en,fr", help="posting languages to label (default en,fr)"
    )
    ev = sub.add_parser("evaluate", help="write the accuracy report (Markdown) from the labels")
    ev.add_argument("--data-dir", type=Path, help="folder with the test set (default data/private)")
    ev.add_argument(
        "--set",
        choices=["all", "holdout"],
        default="all",
        help="all = the 100-posting test set (default); holdout = the held-out set",
    )
    ev.add_argument(
        "--reference",
        choices=["silver", "owner"],
        default="silver",
        help="labels to score against: silver = model majority (default), owner = own labels",
    )
    oo = sub.add_parser(
        "jev-order-test", help="run Jev with reversed and shuffled options on the test set"
    )
    oo.add_argument("--data-dir", type=Path, help="folder with the test set (default data/private)")
    oo.add_argument("--force", action="store_true", help="rerun variants that already have a run")
    sil = sub.add_parser("silver", help="silver labels by several models (no database)")
    sil.add_argument(
        "action",
        choices=[
            "pilot-select",
            "render",
            "run",
            "check",
            "status",
            "report",
            "combine",
            "review-sheet",
            "review-apply",
        ],
    )
    sil.add_argument("file", nargs="?", type=Path, help="decisions file (review-apply)")
    sil.add_argument("--audit", type=int, default=30, help="audit sample size (review-sheet)")
    sil.add_argument("--set", choices=["pilot", "de", "all", "holdout"], default="pilot")
    sil.add_argument(
        "--data-dir", type=Path, help="folder with the test set (default data/private)"
    )
    sil.add_argument("--labeller", choices=["gpt", "gemini", "gemini_or", "opus"])
    sil.add_argument("--limit", type=int)
    sil.add_argument("--posting", type=int, help="only this posting id")
    sil.add_argument("--force", action="store_true", help="relabel postings that have an answer")
    args = parser.parse_args(argv)

    if args.command == "silver":
        return _silver(args)
    if args.command == "daily":
        from jobfacts.daily import run_daily

        return run_daily(skip_collect=args.skip_collect)

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
        if args.command == "select-holdout":
            from jobfacts.testset import select_holdout

            try:
                select_holdout(conn, args.n, force=args.force)
            except FileExistsError as e:
                print(e)
                return 2
            return 0
        if args.command == "label":
            from jobfacts.label_app.server import serve
            from jobfacts.testset import PRIVATE_DIR

            langs = tuple(x.strip() for x in args.languages.split(",") if x.strip())
            serve(args.port, args.data_dir or PRIVATE_DIR, langs)
            return 0
        if args.command == "evaluate":
            from jobfacts import labels, reference
            from jobfacts.evaluate import run_evaluate, summary_lines
            from jobfacts.testset import PRIVATE_DIR

            data_dir = args.data_dir or PRIVATE_DIR
            try:
                text, path = run_evaluate(
                    conn, data_dir, reference=args.reference, set_name=args.set
                )
            except (FileNotFoundError, ValueError) as e:
                print(e)
                return 2
            n_labels = len(
                reference.load_reference(data_dir, args.set)
                if args.reference == "silver"
                else labels.latest(data_dir / "labels.jsonl")
            )
            print(summary_lines(text, n_labels))
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

            if args.set == "holdout":
                from jobfacts import holdout
                from jobfacts.testset import PRIVATE_DIR

                try:
                    run_id, results = holdout.run_holdout(
                        conn,
                        make_extractor(args.extractor),
                        args.data_dir or PRIVATE_DIR,
                        key=args.run_key or args.extractor,
                        force=args.force,
                        limit=args.limit,
                    )
                except FileExistsError as e:
                    print(e)
                    return 2
                failed = sum(1 for x in results if x.error)
                cost = sum(x.cost_usd for x in results)
                print(
                    f"holdout run {run_id}: {len(results)} postings, {failed} errors, ${cost:.4f}"
                )
                return 0
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
