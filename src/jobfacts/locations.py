"""Find Swiss place names (cities, cantons, country words) in text.

Matching rules
- Case-insensitive and accent-tolerant ("Zurich" = "Zürich"), whole words only.
  Offsets always refer to the original text.
- Longest name wins when names overlap ("Muri bei Bern" beats "Bern").
- Exclusions for known false friends: Baden-Württemberg / Baden-Baden,
  Freiburg im Breisgau, "New Bern", "Basel III" (banking rules).
- Names that are also common words (config `ambiguous`: German "Zug" = train,
  "Baden" = bathing, "Thun" = to do, ...) match in free text only when written
  with a capital letter AND next to a location cue: a preceding "in", "at",
  "bei", "near", "Standort:", "Kanton" ... or a following ", Switzerland" /
  "(ZG)" style marker. With `location_field=True` (the job board's location
  string, e.g. "Zug, Switzerland") the cue is not needed.
- Free text: a place name must start with a capital letter.
- Canton codes ("ZH") and "CH" are recognised only as upper-case tokens when
  `location_field=True` ("AG" in free text means a company form). "CH" counts
  alone; canton codes only when another Swiss candidate is present, because
  SG, FR, GE, AR, BE ... are also other countries' codes ("Paris, FR").
"""

import re
import tomllib
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "config" / "swiss_locations.toml"

_CUE_BEFORE = re.compile(
    r"(?:\b(?:in|at|near|bei|nach|aus|from|to|auf|und|and|oder|or|à|a|vers|de|di)"
    r"|standort|location|office|lieu|sede|ort|arbeitsort|kanton|canton|region|raum|"
    r"grossraum|großraum|zweigstelle|büro|bureau|campus)\W{0,3}$"
)
_CUE_AFTER = re.compile(
    r"^\s*(?:[,(/|-]\s*)(?:switzerland|schweiz|suisse|svizzera|ch\b|[a-z]{2}\))"
)
_CODE_CUE_AFTER = re.compile(r"^\s*\(?(?:[A-Z]{2})\)")
# name (normalised) -> (regex on text before, regex on text after) that cancels the match
_EXCLUDE: dict[str, tuple[str | None, str | None]] = {
    "baden": (
        r"(?:new|north|south)\s+$",
        r"^\s*[-–]\s*(?:w[uü]rttemberg|baden)|^\s*w[uü]rttemberg",
    ),
    "freiburg": (
        None,
        r"^\s*(?:im\s+breisgau|i\.\s*br|\(\s*(?:breisgau|germany|deutschland)|,\s*(?:germany|deutschland))",
    ),
    "bern": (r"(?:new|north|south)\s+$", None),
    "basel": (None, r"^\s*(?:iii|iv|3|ii|2)\b"),
}


@dataclass(frozen=True)
class Candidate:
    key: str  # "city:Zürich", "canton:ZH" or "country:CH"
    kind: str  # "city" | "canton" | "country"
    canton: str | None
    matched: str
    start: int
    end: int


def _norm_char(ch: str) -> str:
    base = unicodedata.normalize("NFKD", ch)[:1]
    low = base.lower()
    return low if len(low) == 1 else ch


def normalize(text: str) -> str:
    """Lower-case and strip accents, one output character per input character."""
    return "".join(_norm_char(c) for c in text)


@dataclass(frozen=True)
class _Entry:
    key: str
    kind: str
    canton: str | None
    ambiguous: bool


@lru_cache(maxsize=1)
def _load(path: Path = CONFIG):
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    ambiguous = {normalize(a) for a in data["ambiguous"]}
    names: dict[str, _Entry] = {}

    def add(name: str, key: str, kind: str, canton: str | None) -> None:
        n = normalize(name)
        names[n] = _Entry(key, kind, canton, n in ambiguous)

    for code, name, others in data["cantons"]:
        for n in [name, *others]:
            add(n, f"canton:{code}", "canton", code)
    for city, code, variants in data["places"]:  # cities override same-named cantons
        for n in [city, *variants]:
            add(n, f"city:{city}", "city", code)
    for c in data["country"]:
        add(c, "country:CH", "country", None)
    # non-ambiguous canton names double as cities when a city entry exists; fine as is.
    ordered = sorted(names, key=len, reverse=True)
    pattern = "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in ordered)
    regex = re.compile(rf"(?<![^\W_])(?:{pattern})(?![^\W_])")
    codes = {c for c, *_ in data["cantons"]}
    return regex, names, codes, set(data["country_codes"])


def _has_cue(norm: str, start: int, end: int) -> bool:
    return bool(_CUE_BEFORE.search(norm[max(0, start - 25) : start])) or bool(
        _CUE_AFTER.search(norm[end : end + 25])
    )


def find_candidates(text: str, *, location_field: bool = False) -> list[Candidate]:
    """Return Swiss place candidates in `text`, in text order, non-overlapping."""
    regex, names, codes, country_codes = _load()
    norm = normalize(text)
    out: list[Candidate] = []
    for m in regex.finditer(norm):
        name = m.group()
        entry = names[re.sub(r"\s+", " ", name)]
        start, end = m.span()
        rule = _EXCLUDE.get(name)
        if rule:
            before, after = rule
            if before and re.search(before, norm[max(0, start - 10) : start]):
                continue
            if after and re.search(after, norm[end : end + 25]):
                continue
        if not location_field:
            if not text[start].isupper():
                continue
            if entry.ambiguous and not _has_cue(norm, start, end):
                continue
            if name == "swiss":  # an adjective ("Swiss banks", "Swiss Re"), not a place
                continue
        out.append(Candidate(entry.key, entry.kind, entry.canton, text[start:end], start, end))
    if (
        location_field
    ):  # codes count only next to another Swiss signal (SG, FR, GE = other countries)
        codes_found: list[Candidate] = []
        taken = [(c.start, c.end) for c in out]
        for m in re.finditer(r"(?<![^\W_])[A-Z]{2,3}(?![^\W_])", text):
            if any(s <= m.start() < e for s, e in taken):
                continue
            tok = m.group()
            if tok in country_codes:
                out.append(Candidate("country:CH", "country", None, tok, *m.span()))
            elif tok in codes and len(tok) == 2:
                codes_found.append(Candidate(f"canton:{tok}", "canton", tok, tok, *m.span()))
        if out:
            out.extend(codes_found)
        out.sort(key=lambda c: c.start)
    return out


def all_keys() -> set[str]:
    """Every location key a label may use: cities, cantons, the country, outside_switzerland."""
    _, names, _, _ = _load()
    return {e.key for e in names.values()} | {"outside_switzerland"}
