"""Command line: `jobfacts migrate` and `jobfacts collect`."""

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from jobfacts import db, embed
from jobfacts.collect.run import collect


def _csv(value: str | None) -> list[str]:
    return [v.strip() for v in value.split(",") if v.strip()] if value else []


def _search(conn, args) -> int:
    import json

    from jobfacts import search

    filters = search.Filters(
        query=args.query,
        cantons=search.canton_codes(_csv(args.canton)),
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
        result = search.run(conn, filters, args.mode, args.model, evidence=args.evidence)
    except search.SearchError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if args.json:
        print(
            json.dumps(
                {"total": result.total, "results": [h.as_dict() for h in result.hits]},
                indent=2,
                ensure_ascii=False,
            )
        )
    else:
        print(search.format_table(result.hits, result.total, args.evidence))
    return 0


def _loopback(host: str) -> bool:
    import ipaddress

    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _serve(args) -> int:
    import os

    password = os.environ.get("JOBFACTS_WEB_PASSWORD") or None
    if args.no_auth and not _loopback(args.host):
        print(
            "error: --no-auth is only allowed on a loopback host (127.0.0.1, ::1)", file=sys.stderr
        )
        return 2
    if password is None and not args.no_auth:
        print(
            "error: set JOBFACTS_WEB_PASSWORD (or pass --no-auth on a loopback host)",
            file=sys.stderr,
        )
        return 2
    import uvicorn

    from jobfacts.web.app import create_app

    uvicorn.run(
        create_app(password=password, auth=not args.no_auth), host=args.host, port=args.port
    )
    return 0


def _gate(args) -> int:
    """`jobfacts gate keygen|record|encrypt|check` (no database; see docs/gate.md)."""
    from jobfacts import gate, reference
    from jobfacts.testset import PRIVATE_DIR, load_test_set

    try:
        if args.action == "keygen":
            path = gate.write_key(force=args.force)
            print(f"new key written to {path} (mode 600, not shown)")
            return 0
        if args.action in ("record", "encrypt"):
            key = gate.key_for_writing()
            if args.action == "encrypt":
                bundle = json.loads(gate.PLAIN_BUNDLE_PATH.read_text(encoding="utf-8"))
            else:
                from jobfacts.extract.jev import make_client

                data_dir = args.data_dir or PRIVATE_DIR
                items = load_test_set(data_dir / "holdout_set.jsonl")
                bundle = gate.record(
                    items, reference.load_reference(data_dir, "holdout"), make_client()
                )
            gate.write_bundle(bundle, key)
            meta = bundle["meta"]
            print(
                f"{args.action}: {meta['n']} postings, model {meta['model']}, "
                f"${meta.get('cost_usd', 0):.4f}\nplain: {gate.PLAIN_BUNDLE_PATH}\n"
                f"encrypted: {gate.BUNDLE_PATH} ({gate.BUNDLE_PATH.stat().st_size:,} bytes)"
            )
            return 0
        key = gate.load_key()
        if key is None:
            if args.skip_without_key:
                print(f"gate skipped: {gate.KEY_ENV} is not set (a fork or a local run).")
                return 0
            print(f"{gate.KEY_ENV} is not set; pass --skip-without-key to skip the gate.")
            return 2
        bundle = gate.load_bundle(key)
        gate.check_spec(bundle)
        scored = gate.replay(bundle)
        if args.write_baseline:
            doc = gate.baseline_document(bundle["meta"], scored["fields"])
            gate.write_baseline(doc)
            print(f"wrote {gate.BASELINE_PATH}")
            baseline = doc["fields"]
        else:
            baseline = gate.load_baseline()["fields"]
        rows = gate.compare(baseline, scored["fields"])
        print(gate.render_table(rows))
        ns = {f: v["n"] for f, v in scored["fields"].items()}
        print(f"\nn per field: {ns}")
        moved = {f: c for f, c in scored["changed"].items() if c}
        print(f"answers that differ from the recording (count only): {moved or 'none'}")
        if scored["split_changed"]:
            print(
                f"warning: today's sentence splitter cuts {scored['split_changed']} postings "
                "differently from the recording; the gate uses the recorded sentences."
            )
        failed = [r[0] for r in rows if not r[4]]
        if failed:
            print(f"\nGATE FAILED: {', '.join(failed)}")
            return 1
        print("\ngate passed")
        return 0
    except (gate.GateError, FileNotFoundError) as error:
        print(error)
        return 2


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
        "daily", help="database up, migrate, collect, relevance, Jev extraction, embeddings"
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
    sea.add_argument(
        "--mode",
        choices=["words", "meaning", "hybrid"],
        help="hybrid (default: full-text and embeddings fused with RRF; words only if the "
        "query cannot be embedded), words = full-text only, meaning = embeddings only",
    )
    sea.add_argument(
        "--model",
        choices=sorted(embed.MODEL_IDS),
        default=embed.DEFAULT_MODEL,
        help="embedding model for meaning and hybrid (default %(default)s)",
    )
    sea.add_argument("--evidence", action="store_true", help="print supporting sentences")
    sea.add_argument("--json", action="store_true", help="print JSON for other programs")
    srv = sub.add_parser("serve", help="the HTTP API and the private search page (docs/api.md)")
    srv.add_argument("--host", default="127.0.0.1")
    srv.add_argument("--port", type=int, default=8000)
    srv.add_argument(
        "--no-auth",
        action="store_true",
        help="no password (only on a loopback host); otherwise JOBFACTS_WEB_PASSWORD is needed",
    )
    see = sub.add_parser(
        "search-eval", help="recall check: would the search have shown past applications?"
    )
    see.add_argument("--applications", type=Path, required=True, help="folder of applications")
    see.add_argument("--dates", type=Path, required=True, help="JSON: folder key -> YYYY-MM-DD")
    see.add_argument("--since", type=date.fromisoformat, help="only applications from YYYY-MM-DD")
    see.add_argument("--out", type=Path, help="report path (default data/private/reports)")
    emb = sub.add_parser("embed", help="embed the posting pool for search by meaning")
    emb.add_argument("--model", choices=sorted(embed.MODEL_IDS), required=True)
    emb.add_argument("--limit", type=int, help="only this many postings (for timing)")
    emb.add_argument(
        "--char-budget",
        type=int,
        help="characters embedded per posting (default per model: "
        + ", ".join(f"{m} {n}" for m, n in embed.CHAR_BUDGETS.items())
        + ")",
    )
    men = sub.add_parser(
        "meaning-eval", help="which embedding model finds the jobs the owner applied to?"
    )
    men.add_argument("--phrases", type=Path, required=True, help="one query per line, # comments")
    men.add_argument("--applications", type=Path, required=True, help="folder of applications")
    men.add_argument("--dates", type=Path, required=True, help="JSON: folder key -> YYYY-MM-DD")
    men.add_argument("--models", default=",".join(embed.MODEL_IDS), help="comma-separated aliases")
    men.add_argument("--out", type=Path, help="report path (default data/private/reports)")
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
    gat = sub.add_parser(
        "gate", help="accuracy gate for CI: record model answers, replay them through the rules"
    )
    gat.add_argument("action", choices=["keygen", "record", "encrypt", "check"])
    gat.add_argument("--force", action="store_true", help="keygen: replace an existing key")
    gat.add_argument("--data-dir", type=Path, help="record: folder with the held-out set")
    gat.add_argument(
        "--skip-without-key",
        action="store_true",
        help="check: exit 0 with a notice when JOBFACTS_GATE_KEY is not set (forks)",
    )
    gat.add_argument(
        "--write-baseline", action="store_true", help="check: write ci/accuracy_baseline.json"
    )
    args = parser.parse_args(argv)

    if args.command == "gate":
        return _gate(args)
    if args.command == "silver":
        return _silver(args)
    if args.command == "serve":
        return _serve(args)
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
        if args.command == "search-eval":
            from jobfacts import search_eval

            try:
                result = search_eval.run(conn, args.applications, args.dates, args.since, args.out)
            except (OSError, ValueError) as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            print(result.summary)
            print(f"\nreport: {result.path}")
            return 0
        if args.command == "embed":
            try:
                embedder = embed.make_embedder(args.model)
                summary = embed.run_embed(
                    conn,
                    embedder,
                    limit=args.limit,
                    budget=args.char_budget,
                    progress=embed.print_progress,
                )
            except embed.CostError as error:
                print(f"refusing to start: {error}", file=sys.stderr)
                return 2
            except (RuntimeError, KeyError) as error:  # missing optional group or API key
                print(f"error: {error!r}", file=sys.stderr)
                return 2
            print(
                f"{summary.model} at {summary.budget} characters: embedded {summary.embedded}, "
                f"unchanged {summary.skipped}, "
                f"tokens {summary.tokens}, cost ${summary.cost:.4f}, {summary.seconds:.0f}s "
                f"(pool {embed.pool_size(conn)})"
            )
            return 0
        if args.command == "meaning-eval":
            from jobfacts import meaning_eval

            models = _csv(args.models)
            unknown = [m for m in models if m not in embed.MODEL_IDS]
            if unknown:
                print(f"error: unknown models {unknown}", file=sys.stderr)
                return 2
            try:
                result = meaning_eval.run(
                    conn, args.phrases, args.applications, args.dates, models, args.out
                )
            except (OSError, ValueError, RuntimeError, KeyError) as e:
                print(f"error: {e!r}", file=sys.stderr)
                return 2
            print(result.report)
            print(f"\nreport: {result.path}")
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
            from jobfacts.extract.jev_backup import backup_calls, backup_line
            from jobfacts.extract.run import run_relevance

            client = jev.make_client()
            r = run_relevance(conn, client)
            threshold = jev.RELEVANCE_THRESHOLD
            print(f"relevance flags updated for threshold {threshold}: {r.flags_changed}")
            print(f"scored {r.scored}, relevant {r.relevant}, errors {r.errors}")
            if line := backup_line(backup_calls(client)):
                print(line)
            return 0
        if args.command == "extract":
            from jobfacts.extract.jev_backup import backup_line_for_results
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
                        record_partial=args.run_key is not None,
                    )
                except FileExistsError as e:
                    print(e)
                    return 2
                except FileNotFoundError as e:
                    print(f"error: {e}", file=sys.stderr)
                    return 2
                failed = sum(1 for x in results if x.error)
                cost = sum(x.cost_usd for x in results)
                if args.limit and args.run_key is None:
                    print("partial run (--limit) not recorded; pass --run-key to record it")
                print(
                    f"holdout run {run_id}: {len(results)} postings, {failed} errors, ${cost:.4f}"
                )
                if line := backup_line_for_results(results):
                    print(line)
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
            if line := backup_line_for_results(results):
                print(line)
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
