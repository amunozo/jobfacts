"""Spot-check the Workday URL pre-filter: do skipped URLs hide Swiss or remote postings?

For each Workday employer, reads the sitemap, picks up to 20 random skipped URLs (fixed seed),
fetches them (rate limited, robots obeyed) and looks at the JSON-LD location.
Usage: uv run python scripts/workday_prefilter_check.py [employer name]
"""

import random
import sys
import tomllib

from jobfacts.collect.boards import workday
from jobfacts.collect.http import USER_AGENT, _get, make_client
from jobfacts.collect.store import EMPLOYERS_FILE
from jobfacts.locations import find_candidates

SAMPLE = 20


def check(entry: dict) -> None:
    base = f"https://{entry['board_id']}.{entry['wd']}.myworkdayjobs.com"
    client = workday.LimitedClient(make_client(), workday.DEFAULT_LIMITER)
    robots = workday.load_robots(client, base)
    xml = _get(client, f"{base}/{entry['site']}/siteMap.xml", 2, accept="application/xml").text
    urls = [u for u, _ in workday.parse_sitemap(xml)]
    skipped = [u for u in urls if not workday.url_is_candidate(u)]
    sample = random.Random(1).sample(skipped, min(SAMPLE, len(skipped)))
    hits, errors = [], 0
    for url in sample:
        if not robots.can_fetch(USER_AGENT, url):
            continue
        try:
            posting = workday.parse_posting(_get(client, url, 2, accept="text/html").text, url)
        except Exception:  # noqa: BLE001 - a spot check only counts what it could read
            errors += 1
            continue
        if (
            find_candidates(posting.location_raw, location_field=True)
            or "Remote" in posting.location_raw
        ):
            hits.append((workday.url_location_text(url), posting.location_raw))
    print(
        f"{entry['name']}: sitemap {len(urls)}, kept {len(urls) - len(skipped)}, skipped "
        f"{len(skipped)}, sampled {len(sample)}, Swiss/remote among skipped {len(hits)}, "
        f"unreadable {errors}"
    )
    for url_place, location in hits:
        print(f"    MISSED? url segment {url_place!r} -> JSON-LD {location!r}")


if __name__ == "__main__":
    employers = tomllib.loads(EMPLOYERS_FILE.read_text())["employer"]
    for e in employers:
        if e["ats"] == "workday" and (len(sys.argv) < 2 or sys.argv[1] == e["name"]):
            check(e)
