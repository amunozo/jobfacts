# Monitoring

Three light checks, enough for one small server and one owner. None of them needs software
on the server beyond what already runs.

| Question | Answer from |
|---|---|
| Is the web server up and the database reachable? | an outside uptime monitor that calls `GET /health` |
| Did the daily run happen and finish well? | a dead-man switch that `jobfacts daily` pings |
| What did the recent runs do? | the table at `/runs` |

## The daily run is recorded

`jobfacts daily` writes one row to the table `daily_runs` (migration `010_daily_runs.sql`):
it inserts a row with status `running` once the database is up and migrated, and updates it at
the end to `ok`, or to `failed` with the name of the step that failed. This holds for every
way out once the run has started: an error in a step, an unexpected error in the code after
the steps (step `summary`), a lost database connection (the row is then closed through a fresh
connection; if that fails too, only a warning is printed and the next run marks the row
`interrupted`), Ctrl-C, and SIGTERM (`docker stop`, `systemctl stop`, the unit's timeout; the
daily job handles it, so it needs the container's `init: true` to receive it at all). A
database that cannot be connected to after the "database" step is reported as step `connect`. The row holds the numbers of the printed summary: boards read ok and
failed, new and closed postings, newly scored relevant, extracted, embedded, errors, cost in
USD, and a short warnings text (for example "embedding skipped"). A number is empty when its
step did not run (`--skip-collect` leaves the collection numbers empty). If the database
cannot be reached there is no row; the dead-man ping covers that case. A row left `running`
by a run that was killed is marked `failed` with the step `interrupted` by the next run, once
it is more than 6 hours old.

## Dead-man switch (healthchecks.io)

A **dead-man switch** is an outside service that expects a message ("ping") at a fixed time
every day and sends you an alert when it does not arrive. It catches what the machine cannot
report about itself: the server is down, cron did not start the job, the job hangs, or the job
crashed before it could write anything. This is why it lives outside the server.

Set up (free plan; check the current limits on the site):

1. Create an account at https://healthchecks.io and add a check.
2. Schedule: choose the cron form, `30 6 * * *`, time zone `Europe/Zurich`. Grace time: 2
   hours (the alert comes if no ping arrives within 2 hours after the expected time).
3. Add your e-mail (or another channel the site offers) as the way to be alerted.
4. Copy the check's ping URL (`https://hc-ping.com/<uuid>`) and put it in the `.env` file next
   to `docker-compose.yml`, on the server and, if you want to try it, locally:

   ```
   JOBFACTS_HEALTHCHECK_URL=https://hc-ping.com/<your-uuid>
   ```

   The URL is a secret: anyone who has it can send pings. Do not commit it.

What `jobfacts daily` sends when the variable is set:

| When | Request | Body |
|---|---|---|
| at the start | `GET <url>/start` | none |
| all steps done | `POST <url>` | the one-line summary, for example `daily: 4,812 open postings (+9 new, 3 closed), ...` |
| a step failed | `POST <url>/fail` | `daily failed at step 'collect'` |

The "start" ping lets the service measure how long a run takes and alert if a run started and
never finished. Every request has a 5-second timeout. A ping that fails only prints a warning
and never fails the run; the warning names the error class and never the URL. The body holds
the step name and the summary only, no keys and no error text. Without the variable no network
call is made.

## Uptime monitor for `/health`

The dead-man switch says nothing when the web server is down between two runs. For that, add
a free outside uptime monitor, for example UptimeRobot (https://uptimerobot.com): create an
HTTP(S) monitor for `https://<domain>/health`, check interval as short as the plan allows,
alert by e-mail. `/health` needs no password, answers 200 when the database is reachable and
503 otherwise, and holds nothing sensitive. Look up the current free-plan terms yourself
(interval, number of monitors, and whether commercial or non-commercial use is allowed) before
relying on it; they change.

`/health` also shows `last_daily_run` (finished time and status of the newest run), so a
person can see at a glance when the last run ended, but its status code does not depend on it:
a failed run is the dead-man switch's job.

## The `/runs` page

`https://<domain>/runs` (same password as the search page; linked as "Daily runs" in the page
header) shows the last 30 runs, newest first: start time in the Europe/Zurich zone, duration,
status (`ok`, `failed` in red, `running`), failed step, the numbers above, the cost, and the
warnings. A run with failed boards is still `ok` (a few of the employers' sites are often down); the
count of failed boards is in the table and in the warnings column.

## Files

- `src/jobfacts/monitor.py`: the ping and the run row.
- `src/jobfacts/daily.py`: where both are called.
- `src/jobfacts/web/templates/runs.html`, `src/jobfacts/web/app.py`: the page and `/health`.
- `tests/test_monitor.py`, `tests/test_web.py`.
