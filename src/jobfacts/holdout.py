"""Run an extractor on the held-out set and record the run id in `holdout_runs.json`.

Same idea as `test_set_runs.json` (key -> extraction run id), kept in a separate file so the
two sets never mix. `jobfacts evaluate --set holdout` reads it.
"""

import json
from pathlib import Path

import psycopg

from jobfacts.extract.run import run_extraction
from jobfacts.ordertest import read_runs
from jobfacts.testset import HOLDOUT_FILE, load_test_set

RUNS_FILE = "holdout_runs.json"


def run_holdout(
    conn: psycopg.Connection,
    extractor,
    data_dir: Path,
    key: str,
    force: bool = False,
    limit: int | None = None,
):
    """Extract every held-out posting with `extractor`; store the run id under `key`.

    Raises FileExistsError if the key already has a run (unless `force`) so that paid runs are
    not repeated by accident."""
    data_dir = Path(data_dir)
    path = data_dir / HOLDOUT_FILE.name
    if not path.exists():
        raise FileNotFoundError(f"no {path.name}: run `jobfacts select-holdout`")
    runs_file = data_dir / RUNS_FILE
    runs = read_runs(runs_file)
    if key in runs and not force:
        raise FileExistsError(f"{RUNS_FILE} already has run {runs[key]} for {key!r}; use --force")
    ids = [i["posting_id"] for i in load_test_set(path)]
    run_id, results = run_extraction(conn, extractor, ids, limit, notes=f"holdout set: {key}")
    runs[key] = run_id
    runs_file.write_text(json.dumps(runs) + "\n", encoding="utf-8")
    return run_id, results
