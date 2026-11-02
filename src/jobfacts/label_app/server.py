"""`jobfacts label`: load the test set and extractions, serve the app on localhost only."""

import json
from pathlib import Path

import uvicorn

from jobfacts import db, testset
from jobfacts.label_app.app import create_app
from jobfacts.label_app.prefill import load_extractions

HOST = "127.0.0.1"
PORT = 8765


def build_app(data_dir: Path = testset.PRIVATE_DIR, conn=None):
    """Read `test_set.jsonl` and `test_set_runs.json` from `data_dir`; labels go to
    `data_dir/labels.jsonl`. `--data-dir` points at a copy for trying the app safely."""
    items = testset.load_test_set(data_dir / "test_set.jsonl")
    run_ids = json.loads((data_dir / "test_set_runs.json").read_text())
    if conn is None:
        with db.connect() as conn:
            extractions = load_extractions(conn, run_ids)
    else:
        extractions = load_extractions(conn, run_ids)
    return create_app(items, data_dir / "labels.jsonl", extractions)


def serve(port: int = PORT, data_dir: Path = testset.PRIVATE_DIR) -> None:
    print(f"labelling app: http://{HOST}:{port} (data: {data_dir})")
    uvicorn.run(build_app(data_dir), host=HOST, port=port, log_level="warning")
