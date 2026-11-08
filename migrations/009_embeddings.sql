-- Search by meaning (Milestone 2, step 1): one embedding per posting and embedding model.
-- The `vector` column has no fixed dimension because the models differ (1024, 1536, ...);
-- every query filters on `model` first. Exact search (a scan with <=>) is enough for a pool of
-- a few thousand rows; an HNSW index comes once one model is chosen (an index needs one
-- fixed dimension, so then the column is cast to it).
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE posting_embeddings (
    posting_id   bigint      NOT NULL REFERENCES postings (id),
    model        text        NOT NULL,  -- model id, e.g. mistral-embed
    text_hash    text        NOT NULL,  -- sha256 of the embedded text: a changed text is re-embedded
    char_budget  integer     NOT NULL,  -- characters of title + description that were embedded
    content_hash text        NOT NULL,  -- postings.content_hash at embed time; a different one = stale
    embedding    vector      NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (posting_id, model)
);
