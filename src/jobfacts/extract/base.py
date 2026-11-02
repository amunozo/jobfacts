"""Result of one extraction and the interface every extractor follows."""

from dataclasses import dataclass, field
from typing import Protocol

from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import FIELDS, derive_seniority


@dataclass
class Result:
    values: dict[str, str] = field(default_factory=dict)
    sentences: dict[str, list[int]] = field(default_factory=dict)
    office_cantons: list[str] = field(default_factory=list)  # model: codes + outside_switzerland
    office_places: list[str] = field(default_factory=list)  # code: place keys in those cantons
    details: dict = field(default_factory=dict)  # probabilities, confidences, raw output
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    latency_ms: int = 0
    error: str | None = None

    @property
    def seniority(self) -> str | None:
        if self.error or not all(k in self.values for k in ("min_years", "title_level")):
            return None
        return derive_seniority(self.values["min_years"], self.values["title_level"])


class Extractor(Protocol):
    name: str  # jev | deepseek | mistral
    model: str

    def spec_version(self) -> str: ...

    def extract(self, posting: PostingText) -> Result: ...


FIELD_NAMES = list(FIELDS)
