-- Light monitoring (Milestone 2, step 4): one row per `jobfacts daily` run, shown on /runs.
-- The numbers are those of the run's printed summary. A column is NULL when its step did
-- not run (for example new_postings with --skip-collect).
CREATE TABLE daily_runs (
    id             bigserial   PRIMARY KEY,
    started_at     timestamptz NOT NULL DEFAULT now(),
    finished_at    timestamptz,
    status         text        NOT NULL DEFAULT 'running'
                   CHECK (status IN ('running', 'ok', 'failed')),
    failed_step    text,        -- step name when status = 'failed'
    boards_ok      integer,
    boards_failed  integer,
    new_postings   integer,
    closed         integer,
    relevant       integer,     -- newly scored as relevant
    extracted      integer,
    embedded       integer,
    errors         integer,
    cost_usd       numeric(10, 4),
    warnings       text         -- short text, for example "embedding skipped"
);
CREATE INDEX daily_runs_started_idx ON daily_runs (started_at DESC);
