-- Relevance rows remember the version of the question, threshold and title list that made them
-- (3 Nov 2026), so `jobfacts relevance` re-scores everything once when those change.
-- Old rows stay NULL, which never matches a current version.
ALTER TABLE relevance ADD COLUMN spec_version text;
