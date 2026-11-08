"""Decode HTML entities in the stored Workday titles and places (see
`jobfacts.collect.store.repair_html_entities`). Safe to run again: it changes only rows that
still need it and prints how many.

Usage: uv run python scripts/repair_html_entities.py
"""

from jobfacts import db
from jobfacts.collect.store import repair_html_entities

if __name__ == "__main__":
    with db.connect() as conn:
        print(f"postings repaired: {repair_html_entities(conn)}")
