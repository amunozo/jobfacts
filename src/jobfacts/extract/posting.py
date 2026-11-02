"""What a model sees of one posting: title, location field and numbered sentences."""

import re
from dataclasses import dataclass, field

from jobfacts import language as langdetect
from jobfacts import locations
from jobfacts import sentences as splitter
from jobfacts.extract.spec import LOCATION_ID, TITLE_ID

_REMOTE = re.compile(
    r"remote|anywhere|work from home|home\s?office|homeoffice|télétravail|teletravail|"
    r"travail à distance|fernarbeit|von zuhause|mobiles arbeiten|distributed",
    re.IGNORECASE,
)


@dataclass
class PostingText:
    id: int
    title: str
    location_raw: str
    description: str
    sentences: list[splitter.Sentence] = field(default_factory=list)
    language: str = "other"

    @classmethod
    def build(cls, id: int, title: str, location_raw: str, description: str) -> "PostingText":
        return cls(
            id,
            title,
            location_raw,
            description,
            splitter.split(description),
            langdetect.detect(f"{title}. {description}"),
        )

    def text_of(self, sentence_id: int) -> str:
        if sentence_id == TITLE_ID:
            return self.title
        if sentence_id == LOCATION_ID:
            return self.location_raw
        return self.sentences[sentence_id - 1].text

    def known_ids(self) -> set[int]:
        return {TITLE_ID, LOCATION_ID, *(s.id for s in self.sentences)}

    def numbered(self) -> str:
        """The posting as shown to the models (ids: T = title, L = location, S001...)."""
        lines = [f"{label(TITLE_ID)}| {self.title}", f"{label(LOCATION_ID)}| {self.location_raw}"]
        lines += [f"{label(s.id)}| {s.text}" for s in self.sentences]
        return "\n".join(lines)

    def candidates(self) -> list[tuple[locations.Candidate, str]]:
        """Swiss place candidates (one per key), with where they were found."""
        found: dict[str, tuple[locations.Candidate, str]] = {}
        for c in locations.find_candidates(self.location_raw, location_field=True):
            found.setdefault(c.key, (c, "location field"))
        for c in locations.find_candidates(self.description):
            found.setdefault(c.key, (c, "posting text"))
        return list(found.values())

    def is_extraction_eligible(self) -> bool:
        """Decision 7a: a Swiss location in the location field, or remote/anywhere/home office."""
        swiss = bool(locations.find_candidates(self.location_raw, location_field=True))
        return swiss or bool(_REMOTE.search(f"{self.location_raw} {self.title}"))


def label(sentence_id: int) -> str:
    """Id as written in prompts: T (title), L (location field), S001..."""
    if sentence_id == TITLE_ID:
        return "T"
    if sentence_id == LOCATION_ID:
        return "L"
    return f"S{sentence_id:03d}"


def parse_label(text: str) -> int | None:
    text = str(text).strip().upper()
    if text == "T":
        return TITLE_ID
    if text == "L":
        return LOCATION_ID
    m = re.fullmatch(r"S?0*(\d+)", text)
    return int(m.group(1)) if m and int(m.group(1)) > 0 else None
