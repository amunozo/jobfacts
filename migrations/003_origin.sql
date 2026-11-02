-- Postings can also come from the owner's own application files (private test material).
-- They hang on one employer row per company (ats = 'application'), which no collector reads,
-- so the collector never closes them. Search later filters on origin = 'board'.
ALTER TABLE postings ADD COLUMN origin text NOT NULL DEFAULT 'board'
    CHECK (origin IN ('board', 'application'));
CREATE INDEX postings_origin_idx ON postings (origin);
