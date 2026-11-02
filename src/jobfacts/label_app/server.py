"""`jobfacts label`: load the test set and extractions, serve the app on localhost only."""

import json

import uvicorn

from jobfacts import db, labels, testset
from jobfacts.label_app.app import create_app
from jobfacts.label_app.prefill import load_extractions

HOST = "127.0.0.1"
PORT = 8765
RUNS_FILE = testset.PRIVATE_DIR / "test_set_runs.json"


def build_app():
    items = testset.load_test_set()
    run_ids = json.loads(RUNS_FILE.read_text())
    with db.connect() as conn:
        extractions = load_extractions(conn, run_ids)
    return create_app(items, labels.LABELS_FILE, extractions)


def serve(port: int = PORT) -> None:
    print(f"labelling app: http://{HOST}:{port}")
    uvicorn.run(build_app(), host=HOST, port=port, log_level="warning")
