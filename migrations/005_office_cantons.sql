-- Office location redesign (2 Nov 2026): the model chooses cantons, code finds the places.
-- office_cantons: canton codes plus 'outside_switzerland' (empty = not stated).
-- office_places: place keys 'place:<bfs_nr>:<name>' found by code inside those cantons.
-- The old office_locations column stays for history.
ALTER TABLE extractions
    ADD COLUMN office_cantons text[],
    ADD COLUMN office_cantons_sentences int[],
    ADD COLUMN office_places text[];
