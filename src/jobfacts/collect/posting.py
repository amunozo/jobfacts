"""The common shape every board module produces."""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True)
class Posting:
    source_job_id: str
    url: str
    title: str
    location_raw: str = ""
    description_text: str = ""
    posted_at: datetime | None = None
    source_version: str | None = None  # not part of the content hash

    @property
    def content_hash(self) -> str:
        """Hash of the fields whose change counts as an edit."""
        raw = "\x1f".join([self.url, self.title, self.location_raw, self.description_text])
        return hashlib.sha256(raw.encode()).hexdigest()


def parse_time(value: object) -> datetime | None:
    """ISO string or epoch milliseconds to an aware datetime; None if unusable."""
    if value in (None, ""):
        return None
    try:
        if isinstance(value, int | float):
            return datetime.fromtimestamp(value / 1000, tz=UTC)
        parsed = datetime.fromisoformat(str(value).replace(" UTC", "+00:00").replace("Z", "+00:00"))
    except (ValueError, OverflowError, OSError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


@dataclass
class BoardRead:
    """Result of an incremental board read (Workday).

    `postings` holds only the postings actually fetched this time. `seen_ids` holds the id of
    every posting the board listed, fetched or not; stored postings missing from it get closed.
    `close_missing` is False when the board's list is known to be partial.
    `notes` is a one-line report for the run summary.
    """

    postings: list[Posting] = field(default_factory=list)
    seen_ids: set[str] = field(default_factory=set)
    notes: str = ""
    close_missing: bool = True  # False when the list is known to be incomplete
