"""Load test of the jobfacts web app with Locust (see docs/loadtest.md and loadtest/run.sh).

Each simulated user behaves like a person at the search page: it searches, sometimes with
filters, sometimes opens the page, and waits 1 to 3 seconds between actions. HTTP Basic
password: environment variable JOBFACTS_WEB_PASSWORD. Run it through `loadtest/run.sh`, which
starts a throwaway copy of the production stack; to run it by hand against any server:

    JOBFACTS_WEB_PASSWORD=... uv run locust -f loadtest/locustfile.py --host https://HOST
"""

import os
import random

import urllib3
from locust import HttpUser, between, task

# Invented generic job-search phrases (no real search or posting text), in English, German and
# French like the postings. Fixed, so that the query-embedding cache of the app can be warmed
# with one request per phrase.
PHRASES = [
    "software engineer python backend",
    "data analyst with sql and dashboards",
    "machine learning engineer for computer vision",
    "devops engineer kubernetes and cloud infrastructure",
    "frontend developer react typescript",
    "product manager for a software platform",
    "cybersecurity analyst",
    "data engineer building pipelines with spark",
    "mobile app developer ios android",
    "quality assurance and test automation engineer",
    "backend developer java microservices",
    "bioinformatics scientist",
    "project manager in the pharmaceutical industry",
    "robotics software engineer",
    "business intelligence consultant",
    "Softwareentwickler Cloud",
    "Datenanalyst Statistik",
    "ingénieur logiciel backend",
    "chef de projet informatique",
    "site reliability engineer",
]
CANTONS = ["ZH", "VD", "GE", "BE", "BS", "ZG", "LU", "SG"]
GERMAN_MAX = ["none", "intermediate", "fluent"]
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
PASSWORD = os.environ.get("JOBFACTS_WEB_PASSWORD", "")
# Set to 1 for a server with a self-made certificate (Caddy on `localhost`): the connection is
# still encrypted, but the certificate is not checked. Never use it against a real address.
INSECURE_TLS = os.environ.get("JOBFACTS_INSECURE_TLS") == "1"


def random_filters() -> dict:
    """One to three filters, like someone narrowing a search."""
    options = {
        "canton": random.choice(CANTONS),
        "german_max": random.choice(GERMAN_MAX),
        "remote": "true",
    }
    chosen = random.sample(sorted(options), k=random.randint(1, 3))
    return {name: options[name] for name in chosen}


class Searcher(HttpUser):
    wait_time = between(1, 3)

    def on_start(self):
        if not PASSWORD:
            raise SystemExit("set JOBFACTS_WEB_PASSWORD")
        self.client.auth = ("loadtest", PASSWORD)
        if INSECURE_TLS:
            self.client.verify = False

    def search_api(self, name: str, params: dict, mode: str):
        """GET /api/search; a hybrid search that silently fell back to words is a failure."""
        with self.client.get("/api/search", params=params, name=name, catch_response=True) as r:
            if r.status_code != 200:
                r.failure(f"status {r.status_code}")
                return
            body = r.json()
            if body["mode_used"] != mode:
                r.failure(f"mode_used {body['mode_used']!r}, wanted {mode!r}: {body['warnings']}")

    @task(6)
    def api_hybrid(self):
        self.search_api("/api/search hybrid", {"query": random.choice(PHRASES)}, "hybrid")

    @task(3)
    def api_hybrid_filtered(self):
        params = {"query": random.choice(PHRASES), **random_filters()}
        self.search_api("/api/search hybrid + filters", params, "hybrid")

    @task(2)
    def api_words_filters_only(self):
        self.search_api("/api/search filters only (words)", random_filters(), "words")

    @task(2)
    def page_with_query(self):
        self.client.get("/", params={"query": random.choice(PHRASES)}, name="/ page with query")

    @task(1)
    def page_plain(self):
        self.client.get("/", name="/ page, no query")

    @task(1)
    def health(self):
        self.client.get("/health", name="/health")
