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


def build_app(data_dir: Path = testset.PRIVATE_DIR, conn=None):
    """Read `test_set.jsonl` and `test_set_runs.json` from `data_dir`; labels go to
    `data_dir/labels.jsonl`. `--data-dir` points at a copy for trying the app safely."""
    items = testset.load_test_set(data_dir / "test_set.jsonl")
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


def serve(port: int = PORT, data_dir: Path = testset.PRIVATE_DIR) -> None:
    print(f"labelling app: http://{HOST}:{port} (data: {data_dir})")
    uvicorn.run(build_app(data_dir), host=HOST, port=port, log_level="warning")
