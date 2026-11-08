# HTTP API and search page

`jobfacts serve` starts a small web server with a JSON API and a search page. It reads the same
database and runs the same search code as `jobfacts search` (`search.prepare` and
`search.execute`, which `search.run` chains for the command line), so the two cannot give
different results for the same filters.

## Run it

```
export JOBFACTS_WEB_PASSWORD='a long password'      # not stored anywhere in the repository
uv run jobfacts serve                                # http://127.0.0.1:8000
uv run jobfacts serve --host 0.0.0.0 --port 8080     # reachable from the network
uv run jobfacts serve --no-auth                      # no password, loopback address only
```

The database is `DATABASE_URL` (default: the compose database on port 5433). The Mistral key
for the query embedding is read like in `jobfacts search`.

Safety rules of the command:

- Without `JOBFACTS_WEB_PASSWORD` it refuses to start, unless `--no-auth` is given.
- `--no-auth` is only accepted for a loopback host (`127.0.0.1`, `::1`, `localhost`), also
  when a password is set. A server on `0.0.0.0` always has a password.
- Basic authentication sends the password with every request, in clear text. On a network,
  put the server behind HTTPS (a reverse proxy such as Caddy or nginx); do not expose plain
  HTTP to the internet. There is no rate limit on password guesses, so use a long password.

## Endpoints

An **endpoint** is one URL the server answers, with the kind of answer it gives.

| Endpoint | Auth | Answer |
|---|---|---|
| `GET /health` | none | JSON, 200 or 503 |
| `GET /api/search` | password | JSON |
| `GET /` | password | HTML page (no JavaScript) |
| `GET /runs` | password | HTML page: the last 30 daily runs |
| `GET /static/style.css` | password | the stylesheet |

### `GET /health`

Meant for a monitor or a load balancer. Nothing sensitive:

```json
{"status": "ok", "database": true, "open_postings": 123,
 "last_collection_finished": "2026-11-08T08:40:37+00:00",
 "last_daily_run": {"finished_at": "2026-11-08T08:41:02+00:00", "status": "ok"},
 "embedding_model": "mistral-embed"}
```

`last_collection_finished` is `null` before the first finished collection run.
`last_daily_run` is the newest row of the `daily_runs` table (`finished_at` is `null` while it
is `running`), or `null` if there is none. It does not change the status code: a failed daily
run is reported by the dead-man ping (see `docs/monitoring.md`), not by `/health`. If the database
cannot be reached the answer is status 503 with `{"status": "unavailable", "database": false,
"embedding_model": ...}` and no error text.

### `GET /api/search`

The filters of `jobfacts search` as URL parameters (see `docs/search.md` for what each means):

| Parameter | Meaning |
|---|---|
| `query` | words; at most 500 characters. Empty = the plain filter list, newest first |
| `canton` | codes, repeated or with commas: `canton=ZH&canton=VD` or `canton=ZH,VD` |
| `remote`, `remote_unclear` | `1`/`true`/`on` or `0`/`false`/empty |
| `work_mode`, `seniority` | lists like `canton` |
| `german_max`, `french_max` | `none`, `intermediate` or `fluent` |
| `show_unclear_language`, `no_fixed_term` | flags |
| `new_days` | first seen in the last N days |
| `limit` | 1 to 100 (default 20) |
| `mode` | `words`, `meaning` or `hybrid` (default: hybrid) |
| `evidence` | flag: add the supporting sentences to each result |

The answer is the object of `jobfacts search --json` with two more keys:

```json
{"total": 154, "results": [ ...same objects as the command line... ],
 "mode_used": "hybrid", "warnings": []}
```

`mode_used` is the mode that really ran: the default hybrid becomes `words` when there is no
query, or when the query could not be embedded (then a warning says so; the warning does not
contain the error text). `warnings` also say when embeddings or relevance scores are out of date.

Errors are `{"error": "message"}`: status 400 for a value the spec does not allow, an unknown
parameter (so a typo cannot silently drop a filter) or a bad number; 502 when `mode=meaning`
was asked for and the query cannot be embedded; 503 when the database is not reachable.

### `GET /`

The search page: the same filters as a form, and the results with title, company, place,
work mode, remote status, German and French requirement, seniority, contract, first-seen date,
a link to the original posting and the supporting quotes of each fact (in a closed `<details>`
element). It shows the mode used and every warning. The form is a plain HTML form that sends
a `GET` request, so the page works without JavaScript and every search has a URL that can be
bookmarked.

### `GET /runs`

The table of the last 30 runs of `jobfacts daily`, newest first: start time (Europe/Zurich),
duration, status, failed step, the numbers of the run's summary and the cost. See
`docs/monitoring.md`. The header of every page links to it.

## Terms

- **HTTP Basic authentication:** the browser asks for a user name and password and sends them
  in the `Authorization` header of each request. The server accepts any user name and checks
  only the password, comparing it with `secrets.compare_digest`, which takes the same time
  whether the first or the last character is wrong (so timing reveals nothing).
- **Connection pool:** opening a database connection takes time, so the server opens a few
  (1 to 4, `psycopg_pool.ConnectionPool`) and lends them to requests. A request waits at most
  5 seconds for a free one. The query embedding is made before a connection is borrowed, so a
  slow embedding API does not use up the pool.
- **Content-Security-Policy (CSP):** a response header that tells the browser which sources
  a page may load. Ours allows only the server itself: no CDN, no web fonts, no scripts from
  elsewhere. With `X-Content-Type-Options: nosniff` the browser does not guess content types.
- **Autoescaping:** Jinja2 (the template engine) turns `<` into `&lt;` in every value it
  prints, so a posting title with a `<script>` tag is shown as text. Links to postings are only
  made for absolute `http` and `https` URLs, with `rel="noopener noreferrer"`, so a stored
  `javascript:` URL is shown as plain text.
- **App factory:** `jobfacts.web.app.create_app(connection_source, embedder_factory, password,
  auth, model)` builds the app from its parts; tests pass a throwaway database schema and a
  fake embedder. The embedder (which needs an API key) is made once, on the first search that
  needs it.

## Files

- `src/jobfacts/web/app.py`: the app, authentication, headers, routes.
- `src/jobfacts/web/params.py`: URL parameters to `search.Filters`.
- `src/jobfacts/web/templates/` (`base.html`, `index.html`, `runs.html`) and
  `src/jobfacts/web/static/style.css`: the pages.
- `NOTICE`: the licence of the design tokens used in the stylesheet.
- `tests/test_web.py`.
