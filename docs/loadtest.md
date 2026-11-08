# Load test

**Result (8 November 2026, on a laptop, not on the real server): p95 310 ms at 25 simulated
users with 0% errors. At 50 users the p95 was about 1.0 s (also 0% errors), so 25 is the largest
step that met the target "under 1 s".** Details, method and limits below.

## What a load test is

A load test sends many requests at a server at the same time, the way many people would, and
measures how it answers. The question here is: how many people can use the search page at once
before it gets slow or starts failing?

- **Simulated user:** a small program that behaves like one person at the search page: it
  searches, sometimes with filters, sometimes opens the page, and waits 1 to 3 seconds before the
  next action. Locust (a Python load-testing tool) runs many of them.
- **Response time and percentiles:** the response time is how long one request takes. The
  **p50** (median) is the time that half of the requests beat; the **p95** is the time that 95%
  of the requests beat, so 1 request in 20 is slower. Averages hide the slow ones; p95 and p99
  show what an unlucky user sees. Locust rounds p50 to p99 to two significant digits (for
  example 1,042 ms is shown as 1,000 ms); the maximum is exact.
- **Ramp-up:** users are started 5 per second, not all at once; the statistics are reset when the
  last user has started, so each step below is 60 seconds at the full user count.
- **Closed loop:** a simulated user sends its next request only after the answer to the last one
  and its pause. When the server slows down, the users send fewer requests, so the numbers show
  the throughput the server can sustain, not an arrival rate that it cannot keep up with.

## Setup

Everything is reproducible with one command, `loadtest/run.sh` (it needs the development
database running and `MISTRAL_API_KEY` in `.env`; it takes about 7 minutes and removes
everything afterwards):

- **Machine:** this computer, Intel Core i7-1365U (12 threads: 2 performance cores with two
  threads each, 8 efficiency cores), 31 GB RAM, Docker 29.9.3. The whole stack (database, app,
  Caddy) is pinned to two threads, the two separate efficiency cores 4 and 5, to stand in for the
  2 vCPUs of the Lightsail 1 GB plan (`loadtest/compose.loadtest.yml`, `cpuset`; `cpus: 2.0` on each
  service would have allowed each container 2 CPUs, six in all). Locust runs on the performance
  cores 0 to 3, so it does not compete with the stack. The memory limits are the ones of
  `deploy/compose.prod.yml` (database 384 MB, app 256 MB, Caddy 96 MB).
- **The stack is the production one:** a scratch copy of the repository, started with
  `deploy/compose.prod.yml` (image built from the `Dockerfile`), a fake `.env` with temporary
  passwords, and requests that go through Caddy over HTTPS (`https://localhost:18443`, Caddy's
  own test certificate; Locust skips the certificate check, the traffic is still encrypted).
  `jobfacts serve` runs one uvicorn worker (one process) with the database pool at its default
  of 1 to 4 connections.
- **Data:** a copy of the real development database: 6,058 open board postings, 2,508 postings
  with a Mistral vector (the posting pool for search by meaning).
- **Cache warm:** before each run, one request per phrase (20 phrases) fills the query-embedding
  cache, so hybrid searches measure the app and the database, not the Mistral API. The cache
  is described in `docs/api.md`.
- **What each user does** (`loadtest/locustfile.py`; weights out of 15): hybrid search with one
  of 20 invented generic phrases (6); the same with one to three filters among canton, maximum
  German level and remote (3); filters only, no query, the plain "words" list (2); the HTML page
  `/` with a phrase (2); the HTML page `/` without a query (1); `/health` (1). A hybrid search
  that silently fell back to words (for example because Mistral failed) counts as a failure.

## Results

Two complete runs were made with the same script; they agree within rounding. The run below is
the second (all files are in `loadtest/results/2026-11-08/`; `table.md` is this table).
Overall lines first; "ms" columns are milliseconds.

| Users | Requests/s | p50 | p95 | p99 | max | Failed requests |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.5 | 120 | 150 | 160 | 162 | 0 of 30 |
| 10 | 4.6 | 120 | 170 | 340 | 383 | 0 of 275 |
| 25 | 11.5 | 130 | 310 | 400 | 499 | 0 of 691 |
| 50 | 18.4 | 710 | 1,000 | 1,100 | 1,283 | 0 of 1,104 |

The first run gave p95 170, 180, 320 and 1,000 ms at 1, 10, 25 and 50 users, throughput 0.5,
4.8, 11.6 and 18.5 requests/s, and 0 failures. One step is about a minute, so the 1-user step has
only 30 requests and its p95 is not stable; the 25- and 50-user steps have 700 to 1,100.

Per endpoint, p50 / p95 in ms (requests in the step):

| Endpoint | 1 user | 10 users | 25 users | 50 users |
|---|---:|---:|---:|---:|
| `/api/search` hybrid | 120 / 130 (13) | 120 / 160 (103) | 130 / 310 (271) | 740 / 1,000 (448) |
| `/api/search` hybrid + filters | 110 / 120 (5) | 110 / 160 (62) | 120 / 270 (114) | 680 / 1,000 (227) |
| `/api/search` filters only (words) | 97 / 160 (5) | 98 / 140 (28) | 110 / 230 (95) | 700 / 970 (152) |
| `/` page with query | 140 / 150 (4) | 140 / 210 (43) | 170 / 340 (103) | 780 / 1,100 (144) |
| `/` page, no query | 140 / 140 (3) | 140 / 170 (19) | 160 / 330 (64) | 790 / 1,000 (72) |
| `/health` | no request | 14 / 30 (20) | 12 / 100 (44) | 490 / 780 (61) |

(The 1-user step made no `/health` request by chance.)

### Memory and CPU

`docker stats` sampled every 3 seconds during each step. At the 50-user step (the busiest):

| Container | Memory, highest sample | Memory limit | CPU, average of samples |
|---|---:|---:|---:|
| `app` | 112 MiB | 256 MiB | 24% |
| `db` | 186 MiB | 384 MiB | 161% |
| `caddy` | 24 MiB | 96 MiB | 2% |

CPU is in the `docker stats` unit: 100% is one CPU thread, so the stack's ceiling is 200%.
All three stay far below their memory limits (about 320 MiB in all at the highest samples); the
other steps are in `table.md`.

### Where the time goes

- **The database is the limit, not the app or the proxy.** At 50 users the database container
  used 161% of its 200% CPU while the app used 24% and Caddy 2%. At 25 users the database was at
  97%. Throughput stops growing near 18 requests/s and the extra users only wait in line,
  which is why p50 jumps from 130 ms (25 users) to 710 ms (50 users).
- **Meaning search is cheap next to the rest.** With one user, the plain filter list (no
  query, no vectors) has a p50 of 97 ms and the hybrid search 120 ms, so the vector half adds
  about 20 ms. The page `/` costs about 20 ms more than the API (HTML rendering and the
  supporting quotes). `/health`, which only runs one small query, takes 12 to 14 ms, so Caddy,
  TLS, the framework and the connection pool cost little.
- **Most of a search is the SQL query that lists the filtered postings.** One measurement made
  outside the load test, on the same data on a performance core of this computer (so about 20
  ms rather than 100 ms): the plain filter list took 19 ms of SQL, the same query with a word
  query 2 ms (the full-text index narrows it first), the vector query for the top 200 24 ms. No
  index on the vectors exists (about 2,500 vectors are compared one by one). `pg_stat_statements`
  was not set up, since it needs a database restart with an extra module; this was enough to
  see where the load goes.
- **No setting was changed.** There were no errors, the pool (at most 4 connections) never ran out
  (no `503` or timeouts), and the app process used about a quarter of a CPU. More uvicorn
  workers or a bigger pool would only let more queries compete for the same two saturated
  CPUs, and cost memory. If more capacity is ever needed, the cheaper search query is the
  lever (an idea, not built: see "Later" in `README.md`).

## Limits of this result

- **Not the real server.** This is a laptop, with two efficiency-core threads standing in for the
  Lightsail plan's 2 vCPUs. Lightsail's CPUs are shared and burstable (it allows a burst of
  full speed, then limits to a baseline share), so the real server can be slower, and slow down
  further in a long test. The speed of an efficiency core against a Lightsail vCPU is
  not measured. Treat the numbers as the shape of the curve (the knee is between 25 and 50 users),
  not as a promise. A test against the real server has not been made.
- **Cached embeddings.** The 20 phrases were embedded before the test. A new query pays one
  Mistral API call: in the warm-up, the first request of each phrase took 0.4 to 1.7 s, against
  about 0.12 s when the phrase was cached. People type new queries all the time, so a real hybrid
  search is slower than the numbers above by about that much on a cache miss.
- **Simulated users are not people.** A simulated user acts every 1 to 3 seconds plus the
  response time, all day; a real person pauses far longer, so 25 simulated users stand for many
  more real ones. With 20 phrases and 8 cantons the database sees a narrow mix of queries.
- **One minute per step, two runs, one machine.** Percentiles from 30 to 1,100 requests; the
  load generator ran on the same computer; other programs on it were not controlled; the
  laptop's CPU speed changes with temperature and power state.
- **One worker.** The app is one uvicorn process; searches run in its thread pool and wait
  for the database. It was never the bottleneck here, but it is the configuration measured.
- **Read-only traffic.** The daily collection, embedding and backups (which run on the server at
  night) did not run during the test.

## Run it again

```
loadtest/run.sh                              # 1, 10, 25 and 50 users
USERS="25 35 50" STEP_SECONDS=120 loadtest/run.sh
```

Environment: `STACK_CPUS` and `LOCUST_CPUS` (CPU threads of the stack and of Locust, see
`lscpu -e`), `HTTPS_PORT`, `RESULTS_DIR`. Against another server (for example the real one, with
`JOBFACTS_WEB_PASSWORD` set; no `JOBFACTS_INSECURE_TLS`):
`uv run locust -f loadtest/locustfile.py --host https://HOST --headless -u 10 -r 5 --run-time 90s`.
Do that only against your own server.

## Files

- `loadtest/locustfile.py`: the simulated user and the 20 phrases.
- `loadtest/run.sh`: the scratch stack, the data, the warm-up, the steps and the clean-up.
- `loadtest/compose.loadtest.yml`: the CPU limit (test only; production file untouched).
- `loadtest/warmup.py`, `loadtest/summarize.py`: the cache warm-up and the table.
- `loadtest/results/2026-11-08/`: Locust CSV files per step, container samples, `table.md`.
