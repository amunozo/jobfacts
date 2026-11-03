-- contract_type (3 Nov 2026): permanent | fixed_term | not_stated. Old rows stay NULL.
ALTER TABLE extractions
    ADD COLUMN contract_type text,
    ADD COLUMN contract_type_sentences int[];
