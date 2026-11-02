"""The common shape every board module produces."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Posting:
    source_job_id: str
    url: str
    title: str
    location_raw: str = ""
    description_text: str = ""
    posted_at: datetime | None = None

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
