"""`jobfacts label`: load the test set and extractions, serve the app on localhost only."""

import json
from pathlib import Path

import uvicorn

from jobfacts import db, testset
from jobfacts.label_app.app import create_app
from jobfacts.label_app.prefill import load_extractions

HOST = "127.0.0.1"
PORT = 8765


PREFILL_EXTRACTORS = ("jev", "deepseek", "mistral")
# German postings are labelled by three models (Opus, GPT, Gemini) and checked by the owner
# from quotes, not on this page; see docs/decisions.md (2026-11-03).
LABEL_LANGUAGES = ("en", "fr")


def build_app(
    data_dir: Path = testset.PRIVATE_DIR, conn=None, languages: tuple[str, ...] = LABEL_LANGUAGES
):
    """Read `test_set.jsonl` and `test_set_runs.json` from `data_dir`; labels go to
    `data_dir/labels.jsonl`. `--data-dir` points at a copy for trying the app safely.
    Only postings in `languages` are served."""
    items = [
        it
        for it in testset.load_test_set(data_dir / "test_set.jsonl")
        if it["language"] in languages
    ]
    all_runs = json.loads((data_dir / "test_set_runs.json").read_text())
    # Pre-fill uses only the three current extractors; the file also holds older runs
    # (`*_v1`) and order-test variants, which must not vote.
    run_ids = {k: all_runs[k] for k in PREFILL_EXTRACTORS if k in all_runs}
    if conn is None:
        with db.connect() as conn:
            extractions = load_extractions(conn, run_ids)
    else:
        extractions = load_extractions(conn, run_ids)
    return create_app(items, data_dir / "labels.jsonl", extractions)


def serve(
    port: int = PORT,
    data_dir: Path = testset.PRIVATE_DIR,
    languages: tuple[str, ...] = LABEL_LANGUAGES,
) -> None:
    print(
        f"labelling app: http://{HOST}:{port} (data: {data_dir}, languages: {','.join(languages)})"
    )
    uvicorn.run(build_app(data_dir, languages=languages), host=HOST, port=port, log_level="warning")
