"""One request per phrase of the load test, so the app's query-embedding cache is warm (the load
test then measures the app, not the Mistral API), plus one of each other kind of request.
Usage: JOBFACTS_WEB_PASSWORD=... python loadtest/warmup.py https://localhost:18443"""

import ast
import os
import sys
import time
from pathlib import Path

import httpx


def phrases() -> list[str]:
    """PHRASES of locustfile.py, read without importing it (importing locust here patches ssl
    and breaks the http client)."""
    tree = ast.parse((Path(__file__).parent / "locustfile.py").read_text())
    for node in tree.body:
        if isinstance(node, ast.Assign) and node.targets[0].id == "PHRASES":
            return ast.literal_eval(node.value)
    raise SystemExit("PHRASES not found in locustfile.py")


base = sys.argv[1]
auth = ("loadtest", os.environ["JOBFACTS_WEB_PASSWORD"])
with httpx.Client(base_url=base, auth=auth, verify=False, timeout=60) as client:
    for phrase in phrases():
        start = time.perf_counter()
        r = client.get("/api/search", params={"query": phrase})
        body = r.json()
        seconds = time.perf_counter() - start
        print(f"{seconds:6.2f} s  {r.status_code}  {body['mode_used']:7s}  {phrase}")
        if r.status_code != 200 or body["mode_used"] != "hybrid":
            sys.exit(f"warm-up failed: {r.status_code} {body}")
    for path in ["/", "/health", "/api/search?canton=ZH"]:
        r = client.get(path)
        print(f"{r.status_code}  {path}")
        r.raise_for_status()
