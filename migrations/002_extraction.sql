-- Relevance filter and extraction results (Milestone 1).

CREATE TABLE relevance (
    posting_id  bigint NOT NULL REFERENCES postings (id),
    model       text NOT NULL,
    probability double precision NOT NULL,
    relevant    boolean NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (posting_id, model)
);

CREATE TABLE extraction_runs (
    id           bigserial PRIMARY KEY,
    extractor    text NOT NULL,          -- jev | deepseek | mistral
    model        text NOT NULL,
    spec_version text NOT NULL,          -- hash of field spec + prompt template
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    notes        text
);

-- One row per posting per run. A column per field value and a <field>_sentences
-- array (ids from jobfacts.sentences; 0 = title, -1 = location field).
CREATE TABLE extractions (
    id                           bigserial PRIMARY KEY,
    run_id                       bigint NOT NULL REFERENCES extraction_runs (id),
    posting_id                   bigint NOT NULL REFERENCES postings (id),
    work_mode                    text,
    work_mode_sentences          int[],
    remote_from_switzerland      text,
    remote_from_switzerland_sentences int[],
    german                       text,
    german_sentences             int[],
    french                       text,
    french_sentences             int[],
    min_years                    text,
    min_years_sentences          int[],
    title_level                  text,
    title_level_sentences        int[],
    office_locations             text[],
    seniority                    text,   -- derived in code from min_years + title_level
    details                      jsonb NOT NULL DEFAULT '{}',
    tokens_in                    int,
    tokens_out                   int,
    cost_usd                     double precision,
    latency_ms                   int,
    error                        text,
    created_at                   timestamptz NOT NULL DEFAULT now(),
    UNIQUE (run_id, posting_id)
);
CREATE INDEX extractions_posting_idx ON extractions (posting_id);
