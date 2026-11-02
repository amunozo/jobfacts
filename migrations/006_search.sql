-- Search (Milestone 1).
-- 1. Full-text column over title + description. The `simple` configuration (no stemming,
--    no stop words) because postings are English, German and French; one language's
--    stemmer would mangle the other two. Generated, so it follows every content update.
ALTER TABLE postings ADD COLUMN search_tsv tsvector
    GENERATED ALWAYS AS (
        setweight(to_tsvector('simple', title), 'A')
        || setweight(to_tsvector('simple', description_text), 'B')
    ) STORED;
CREATE INDEX postings_search_idx ON postings USING gin (search_tsv);

-- 2. Incremental extraction. A run made for search ('search') is told apart from test-set
--    and comparison runs ('experiment'); each extraction remembers the posting version
--    (content_hash) it read, so only new or changed postings are extracted again.
ALTER TABLE extraction_runs ADD COLUMN purpose text NOT NULL DEFAULT 'experiment'
    CHECK (purpose IN ('experiment', 'search'));
ALTER TABLE extractions ADD COLUMN content_hash text;
