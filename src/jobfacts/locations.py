"""Swiss cantons and places: canton first, then place.

Two jobs:

1. `CANTONS` (code, names in four languages, a few major places) feeds the prompts, the
   Jev questions and the labelling page. The model decides which cantons have an office
   for a role (`office_cantons`).
2. `find_places(text, cantons)` then finds the place(s) *inside the chosen cantons* by
   matching text against the official register of localities (`config/swiss_places.csv`,
   from swisstopo, built by `scripts/build_places.py`). `canton_hints(text)` scans the
   whole register and suggests cantons (labelling hints, Workday URL pre-filter).

Place key (stable, used in labels and the database): `place:<bfs_nr>:<name>`, where
`bfs_nr` is the official commune number and `name` the locality or commune name as written
in the register (`place:261:Zürich`, `place:6458:Peseux`). A name that exists in a canton
resolves to one place: the commune of that name if there is one, else the locality with the
largest address share. Aliases (`config/swiss_locations.toml`) add other-language names
(Zurigo, Genf, Bâle, Sitten ...); umlaut spellings (Zuerich) and name forms without
canton suffix or parentheses ("Pfäffikon" for "Pfäffikon ZH", "Biel" for "Biel/Bienne")
are generated.

Matching rules
- Case-insensitive and accent-tolerant ("Neuchatel" = "Neuchâtel"); names are matched as
  runs of words, longest run first ("Muri bei Bern" beats "Bern"). Offsets refer to the
  original text.
- Free text: a name must start with a capital letter. Names that are also common words
  ("Zug" = train, "Baden" = bathing, every name of up to 4 letters except `strong`) match
  only next to a location cue: a preceding "in", "at", "bei", "Standort:", "Kanton" ... or a
  following ", Switzerland" / "(ZG)" marker. In a job board's location field
  (`location_field=True`) no capital letter or cue is needed.
- Exclusions for known false friends: Baden-Württemberg / Baden-Baden, Freiburg im
  Breisgau, "New Bern", "Basel III", and any name followed by ", Germany" and similar.
- Location field, foreign places: the string is split into places ("A / B", "A; B", "A | B");
  a place that names a foreign country (English, German, French or Italian name, or a
  two-letter ISO code as the last part: "Berlin, DE") and not Switzerland is ignored even if
  a word of it is a Swiss place ("Monte Carlo, Monaco"). ISO codes that are also canton codes
  never veto. One Swiss place keeps the whole string Swiss ("Zurich, Switzerland / Paris, France").
- Canton codes ("ZH") count only in a location field, upper-case, and only next to another
  Swiss signal ("Paris, FR" is not Switzerland). "Swiss" is never a place.
"""

import csv
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[2] / "config" / "swiss_locations.toml"
PLACES_CSV = Path(__file__).resolve().parents[2] / "config" / "swiss_places.csv"

OUTSIDE = "outside_switzerland"

_CUE_BEFORE = re.compile(
    r"(?:\b(?:in|at|near|bei|nach|aus|from|to|auf|und|and|oder|or|à|a|vers|de|di)"
    r"|standort|location|office|lieu|sede|ort|arbeitsort|kanton|canton|region|raum|"
    r"grossraum|großraum|zweigstelle|büro|bureau|campus)\W{0,3}$"
)
_CUE_AFTER = re.compile(
    r"^\s*(?:[,(/|-]\s*)(?:switzerland|schweiz|suisse|svizzera|ch\b|[a-z]{2}\))"
)
_ABROAD_AFTER = re.compile(
    r"^\s*,\s*(?:germany|deutschland|austria|osterreich|france|frankreich|italy|italien|"
    r"liechtenstein)"
)
# name (normalised, words joined by a space) -> (regex on text before, regex on text after)
# that cancels the match
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
_TOKEN = re.compile(r"[^\W_]+")
_GAP = re.compile(r"[\s\-./'’]{1,3}")
_COUNTRY_CODE = re.compile(r"(?<![^\W_])[A-Z]{2,3}(?![^\W_])")
# Places of a multi-place location field: "A / B" (Workday, Lever, Ashby ...), "A; B" (Greenhouse),
# "A | B". A slash without spaces ("Biel/Bienne") is part of a name.
_SEGMENT_SEP = re.compile(r"[;|\n]|\s+/\s+")
_CODE_PART = re.compile(r"\s*\(?([A-Z]{2})\)?\s*")
_PAREN = re.compile(r"\([^)]*\)")
_PIECE_SEP = re.compile(r"\s+(?:or|and)\s+|\s*&\s*", re.I)
# Teamtailor adds the remote status as a segment of its own: "Berlin, Germany / (fully)".
_REMOTE_STATUS = re.compile(r"\s*\(\s*(?:fully|hybrid|onsite|temporary|remote|none)\s*\)\s*", re.I)


def _norm_char(ch: str) -> str:
    base = unicodedata.normalize("NFKD", ch)[:1]
    low = base.lower()
    return low if len(low) == 1 else ch


def normalize(text: str) -> str:
    """Lower-case and strip accents, one output character per input character."""
    return "".join(_norm_char(c) for c in text)


def _tokens(name: str) -> tuple[str, ...]:
    return tuple(_TOKEN.findall(normalize(name)))


# ----- cantons ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Canton:
    code: str
    de: str
    fr: str
    it: str
    en: str
    other: tuple[str, ...]
    places: tuple[str, ...]  # a few major places, for prompts and criteria

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys([self.de, self.fr, self.it, self.en, *self.other]))


@dataclass(frozen=True)
class Place:
    key: str  # place:<bfs_nr>:<name>
    name: str  # locality or commune name as in the register
    commune: str
    bfs_nr: int
    canton: str
    postcodes: tuple[str, ...]


@dataclass(frozen=True)
class PlaceMatch:
    key: str
    name: str
    commune: str
    bfs_nr: int
    canton: str
    matched: str  # the text as written
    start: int
    end: int


@dataclass
class _Name:
    ambiguous: bool
    places: dict[str, Place] = field(default_factory=dict)  # canton -> place
    score: dict[str, tuple] = field(default_factory=dict)
    named: set[str] = field(default_factory=set)  # cantons whose name this is


@dataclass
class _Index:
    names: dict[tuple[str, ...], _Name]
    max_len: int
    places: dict[str, Place]
    countries: re.Pattern
    country_codes: set[str]
    foreign: re.Pattern
    foreign_codes: frozenset[str]
    cantons: dict[str, Canton]


def _base_name(name: str, canton: str) -> str:
    base = re.sub(r"\s*\([^)]*\)", "", name).strip()
    return base[: -len(canton)].strip() if base.endswith(f" {canton}") else base


def _variants(name: str, canton: str) -> list[str]:
    """Written forms generated from a register name (the name itself comes first)."""
    out = [name]
    base = re.sub(r"\s*\([^)]*\)", "", name).strip()
    if base != name:
        out.append(base)
    if base.endswith(f" {canton}"):
        out.append(base[: -len(canton)].strip())
    for v in list(out):
        if "/" in v:
            out += [part.strip() for part in v.split("/")]
    for v in list(out):
        t = v.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        t = t.replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
        if t != v:
            out.append(t)
    return list(dict.fromkeys(out))


@lru_cache(maxsize=1)
def _load() -> _Index:
    data = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
    ambiguous = {" ".join(_tokens(a)) for a in [*data["ambiguous"], *data["common_words"]]}
    strong = {" ".join(_tokens(a)) for a in data["strong"]}
    cantons = {
        row["code"]: Canton(
            row["code"],
            row["de"],
            row["fr"],
            row["it"],
            row["en"],
            tuple(row["other"]),
            tuple(row["places"]),
        )
        for row in data["cantons"]
    }

    def is_ambiguous(tkey: tuple[str, ...]) -> bool:
        joined = " ".join(tkey)
        if joined in strong:
            return False
        return joined in ambiguous or (len(tkey) == 1 and len(tkey[0]) <= 4)

    names: dict[tuple[str, ...], _Name] = {}

    def entry(tkey: tuple[str, ...]) -> _Name:
        if tkey not in names:
            names[tkey] = _Name(is_ambiguous(tkey))
        return names[tkey]

    # places from the register: (bfs, base name) -> data; the base name has no "(ZH)" or
    # trailing canton code, so "Pfäffikon ZH" and the commune "Pfäffikon" are one place
    raw: dict[tuple[int, str], dict] = {}
    with PLACES_CSV.open(encoding="utf-8", newline="") as f:
        rows = csv.DictReader(line for line in f if not line.startswith("#"))
        for r in rows:
            bfs = int(r["bfs_nr"])
            share = float(r["share"] or 0)
            for name, is_commune in ((r["locality"], False), (r["commune"], True)):
                p = raw.setdefault(
                    (bfs, _base_name(name, r["canton"])),
                    {
                        "commune": r["commune"],
                        "canton": r["canton"],
                        "pc": set(),
                        "share": 0.0,
                        "names": set(),
                    },
                )
                p["pc"].add(r["postcode"])
                p["names"].add(name)
                p["share"] += 1000.0 if is_commune else share
    places: dict[str, Place] = {}
    for (bfs, base), p in raw.items():
        commune = _base_name(p["commune"], p["canton"])
        place = Place(
            f"place:{bfs}:{base}", base, commune, bfs, p["canton"], tuple(sorted(p["pc"]))
        )
        places[place.key] = place
        main = base == commune
        for name in sorted(p["names"]):
            for variant in _variants(name, p["canton"]):
                tkey = _tokens(variant)
                if not tkey:
                    continue
                e = entry(tkey)
                score = (variant == base, main, p["share"])
                if place.canton not in e.score or score > e.score[place.canton]:
                    e.score[place.canton] = score
                    e.places[place.canton] = place
    # A postal locality can spill a few addresses into another canton (St. Gallen 9012
    # reaches Teufen AR with 3% of its addresses). Drop such fragments when the same name
    # is a commune or a major locality elsewhere, so "St. Gallen" does not hint AR.
    for e in names.values():
        if len(e.score) < 2:
            continue
        strong = [c for c, (_, main, share) in e.score.items() if main or share >= 50]
        if not strong:
            continue
        for c in [c for c, (_, main, share) in e.score.items() if not main and share < 10]:
            if c not in strong:
                del e.score[c], e.places[c]
    for alias, canton, target in data["aliases"]:
        place = names.get(_tokens(target), _Name(False)).places.get(canton)
        if place is None:
            raise ValueError(f"alias {alias!r}: no place {target!r} in canton {canton}")
        e = entry(_tokens(alias))
        e.ambiguous = (
            " ".join(_tokens(alias)) in ambiguous
        )  # the short-name rule is for the register
        e.places[canton] = place
        e.score[canton] = (True, True, 1e9)
    for c in cantons.values():
        for n in c.names:
            e = entry(_tokens(n))
            e.ambiguous = " ".join(_tokens(n)) in ambiguous
            e.named.add(c.code)
    country_words = sorted((" ".join(_tokens(c)) for c in data["country"]), key=len, reverse=True)
    countries = re.compile(
        r"(?<![^\W_])(?:" + "|".join(re.escape(c) for c in country_words) + r")(?![^\W_])"
    )
    foreign_words = sorted(
        {tuple(_tokens(c)) for c in data["foreign_countries"]}, key=lambda t: (-len(t), t)
    )
    foreign = re.compile(
        r"(?<![^\W_])(?:"
        + "|".join(r"[\s\-./'’]{1,3}".join(re.escape(w) for w in words) for words in foreign_words)
        + r")(?![^\W_])"
    )
    foreign_codes = frozenset(data["foreign_country_codes"]) - set(cantons) - {"CH"}
    return _Index(
        names,
        max(len(k) for k in names),
        places,
        countries,
        set(data["country_codes"]),
        foreign,
        foreign_codes,
        cantons,
    )


CANTONS: dict[str, Canton] = _load().cantons


def canton_name(code: str, lang: str = "en") -> str:
    """Name of a canton in `de`, `fr`, `it` or `en`."""
    return getattr(CANTONS[code], lang)


def office_canton_values() -> list[str]:
    """Every value `office_cantons` may contain: the 26 codes and `outside_switzerland`."""
    return [*CANTONS, OUTSIDE]


def all_place_keys() -> set[str]:
    """Every place key a label may use."""
    return set(_load().places)


def place_by_key(key: str) -> Place | None:
    return _load().places.get(key)


# ----- scanning ---------------------------------------------------------------------------


def _clean_part(part: str, index: _Index) -> str:
    """A comma part with its parenthesised decorations ("(Hybrid)", "(Remote)", "(ZH)") blanked;
    a parenthesis that names a foreign country ("Munich (Germany)") stays."""

    def keep(m: re.Match) -> str:
        return m.group() if index.foreign.search(normalize(m.group())) else " " * len(m.group())

    return _PAREN.sub(keep, part)


def _is_whole_swiss_name(piece: str) -> bool:
    text = piece.strip()
    if not text:
        return False
    hits = _scan(text, True)
    if len(hits) != 1 or hits[0].start != 0 or hits[0].end != len(text):
        return False
    return not (len(text) == 2 and text.isupper())  # "AU", "LU": a code, not a city


def _is_foreign(segment: str) -> bool:
    """True if one place of a location field names a foreign country and not Switzerland.

    The rule is narrow, because losing a Swiss job costs more than keeping a foreign one:
    - a country counts only as a comma part of its own ("Paris, France", "(Germany)") or as the
      last word(s) of a part ("Berlin Germany", "Munich (Germany)"), never inside a street or
      place name ("Route de France 100B");
    - an ISO code counts only as the last comma part, and never a canton code;
    - the place stays Swiss if any comma part is entirely a Swiss name once split on " or ",
      " and ", " & " ("Zurich or Munich, Germany", "Geneva, Singapore"). So "Rheinfelden,
      Baden-Wurttemberg, Germany" stays Swiss: a name clash with a whole Swiss name is the cost
      of never losing a Swiss job.
    """
    index = _load()
    if index.countries.search(normalize(segment)) or any(
        m.group() in index.country_codes for m in _COUNTRY_CODE.finditer(segment)
    ):
        return False
    parts = segment.split(",")
    cleaned = [_clean_part(p, index) for p in parts]
    foreign = False
    for i, part in enumerate(cleaned):
        low = normalize(part)
        # a country is the whole part or ends it, ignoring a closing parenthesis
        foreign = foreign or any(
            not low[m.end() :].strip(" )") for m in index.foreign.finditer(low)
        )
        if i == len(parts) - 1 and i > 0:
            code = _CODE_PART.fullmatch(parts[i])
            foreign = foreign or bool(code and code.group(1) in index.foreign_codes)
    if not foreign:
        return False
    return not any(
        _is_whole_swiss_name(piece) for part in cleaned for piece in _PIECE_SEP.split(part)
    )


def _swiss_part(text: str, foreign_veto: bool = True) -> str:
    """`text` with the places that name a foreign country blanked out (same length, so
    offsets stay valid). "Zurich, Switzerland / Paris, France" keeps only the Zurich place.
    A segment that is only a remote status in parentheses ("(fully)") is always blanked: it is
    not a place. `foreign_veto=False` keeps the foreign places (for text with no structure,
    like the slug of a URL)."""
    out: list[str] = []
    pos = 0
    for sep in [*_SEGMENT_SEP.finditer(text), None]:
        end = sep.start() if sep else len(text)
        segment = text[pos:end]
        drop = _REMOTE_STATUS.fullmatch(segment) or (foreign_veto and _is_foreign(segment))
        out.append(" " * len(segment) if drop else segment)
        if sep:
            out.append(sep.group())
            pos = sep.end()
    return "".join(out)


def _has_cue(norm: str, start: int, end: int) -> bool:
    return bool(_CUE_BEFORE.search(norm[max(0, start - 25) : start])) or bool(
        _CUE_AFTER.search(norm[end : end + 25])
    )


@dataclass(frozen=True)
class _Hit:
    name: _Name
    start: int
    end: int


def _scan(text: str, location_field: bool) -> list[_Hit]:
    """Register/canton-name matches in text order, non-overlapping, longest run first."""
    index = _load()
    norm = normalize(text)
    toks = [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(norm)]
    hits: list[_Hit] = []
    i = 0
    while i < len(toks):
        reach = 1  # how many words can be joined starting here
        while (
            reach < index.max_len
            and i + reach < len(toks)
            and _GAP.fullmatch(norm[toks[i + reach - 1][2] : toks[i + reach][1]])
        ):
            reach += 1
        matched = False
        for n in range(reach, 0, -1):
            tkey = tuple(t[0] for t in toks[i : i + n])
            name = index.names.get(tkey)
            if name is None:
                continue
            start, end = toks[i][1], toks[i + n - 1][2]
            rule = _EXCLUDE.get(" ".join(tkey))
            if rule:
                before, after = rule
                if before and re.search(before, norm[max(0, start - 10) : start]):
                    continue
                if after and re.search(after, norm[end : end + 25]):
                    continue
            if _ABROAD_AFTER.search(norm[end : end + 25]):
                continue
            if not location_field and (
                not text[start].isupper() or (name.ambiguous and not _has_cue(norm, start, end))
            ):
                continue
            hits.append(_Hit(name, start, end))
            i += n
            matched = True
            break
        if not matched:
            i += 1
    return hits


def find_places(
    text: str, cantons: list[str] | set[str] | tuple[str, ...], *, location_field: bool = False
) -> list[PlaceMatch]:
    """Places named in `text` that lie in one of `cantons`, in text order.

    Only register places count (canton names like "Kanton Aargau" are not places). A name
    that exists in several chosen cantons gives one match per canton. `OUTSIDE` and unknown
    codes in `cantons` are ignored. The same place may be found several times; every
    occurrence is returned.
    """
    wanted = [c for c in dict.fromkeys(cantons) if c in CANTONS]
    if not wanted:
        return []
    out: list[PlaceMatch] = []
    scanned = _swiss_part(text) if location_field else text
    for hit in _scan(scanned, location_field):
        for c in wanted:
            p = hit.name.places.get(c)
            if p:
                out.append(
                    PlaceMatch(
                        p.key,
                        p.name,
                        p.commune,
                        p.bfs_nr,
                        c,
                        text[hit.start : hit.end],
                        hit.start,
                        hit.end,
                    )  # fmt: skip
                )
    return out


def _country_signal(text: str, location_field: bool) -> bool:
    index = _load()
    for m in index.countries.finditer(normalize(text)):
        if location_field or (text[m.start()].isupper() and m.group() != "swiss"):
            return True
    return location_field and any(
        m.group() in index.country_codes for m in _COUNTRY_CODE.finditer(text)
    )


def canton_hints(
    text: str, *, location_field: bool = False, foreign_veto: bool = True
) -> list[str]:
    """Cantons suggested by names in `text` (whole register, canton names, location codes).

    Order of first appearance. A name that exists in several cantons ("Pfäffikon", "Buchs")
    suggests all of them. In a location field a two-letter canton code ("SG") counts when
    another Swiss signal (a place, a canton name, "Switzerland", "CH") is present, and a place
    of a multi-place string ("A / B", "A; B", "A | B") that names a foreign country and not
    Switzerland is ignored ("Chateauneuf sur Isere, France"); `foreign_veto=False` turns
    that off.
    """
    if location_field:
        text = _swiss_part(text, foreign_veto)
    hits = _scan(text, location_field)
    found: list[str] = []
    for h in hits:
        for c in sorted(set(h.name.places) | h.name.named):
            if c not in found:
                found.append(c)
    if location_field and (hits or _country_signal(text, True)):
        taken = [(h.start, h.end) for h in hits]
        for m in _COUNTRY_CODE.finditer(text):
            if m.group() in CANTONS and not any(s <= m.start() < e for s, e in taken):
                if m.group() not in found:
                    found.append(m.group())
    return found


def mentions_switzerland(
    text: str, *, location_field: bool = False, foreign_veto: bool = True
) -> bool:
    """True if `text` names a Swiss place, a canton, or the country.

    In a location field, a place that names a foreign country (and not Switzerland) does not
    count, even if part of its name is a Swiss place: "Chateauneuf sur Isere, France".
    `foreign_veto=False` turns that off (a URL slug has no structure to judge).
    """
    if location_field:
        text = _swiss_part(text, foreign_veto)
    return bool(
        canton_hints(text, location_field=location_field, foreign_veto=foreign_veto)
    ) or _country_signal(text, location_field)
