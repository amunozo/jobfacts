-- A board may tell us a posting's version without us reading it (Workday sitemap lastmod).
-- The collector compares it with the stored value to fetch only new or changed postings.
ALTER TABLE postings ADD COLUMN source_version text;
