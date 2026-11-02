CREATE TABLE employers (
    id        bigserial PRIMARY KEY,
    name      text NOT NULL,
    ats       text NOT NULL,
    board_id  text NOT NULL,
    extra     jsonb NOT NULL DEFAULT '{}',
    active    boolean NOT NULL DEFAULT true,
    UNIQUE (ats, board_id)
);

CREATE TABLE postings (
    id               bigserial PRIMARY KEY,
    employer_id      bigint NOT NULL REFERENCES employers (id),
    source_job_id    text NOT NULL,
    url              text NOT NULL,
    title            text NOT NULL,
    location_raw     text NOT NULL DEFAULT '',
    description_text text NOT NULL DEFAULT '',
    posted_at        timestamptz,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    closed_at        timestamptz,
    content_hash     text NOT NULL,
    UNIQUE (employer_id, source_job_id)
);
CREATE INDEX postings_closed_at_idx ON postings (closed_at);
CREATE INDEX postings_open_idx ON postings (employer_id) WHERE closed_at IS NULL;

CREATE TABLE collection_runs (
    id               bigserial PRIMARY KEY,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    boards_ok        int NOT NULL DEFAULT 0,
    boards_failed    int NOT NULL DEFAULT 0,
    postings_new     int NOT NULL DEFAULT 0,
    postings_updated int NOT NULL DEFAULT 0,
    postings_closed  int NOT NULL DEFAULT 0
);

CREATE TABLE board_reads (
    run_id        bigint NOT NULL REFERENCES collection_runs (id),
    employer_id   bigint NOT NULL REFERENCES employers (id),
    ok            boolean NOT NULL,
    error         text,
    postings_seen int NOT NULL DEFAULT 0,
    PRIMARY KEY (run_id, employer_id)
);
