"""Split a posting into numbered sentences with exact character offsets.

The extraction step shows the model a numbered list of sentences and lets it
pick ids as quotes. Every sentence keeps `start`/`end` so that
`text == source[start:end]` always holds: whitespace and bullet markers are
removed by moving the offsets, never by changing characters.
"""

import re
from dataclasses import dataclass

MAX_PIECE = 600

_LINE = re.compile(r"[^\n\r  ]+")
_BULLET = re.compile(r"^(?:[-*+•·▪◦‣–—]|\d{1,2}[.)])\s+")
# A sentence end: . ! ? or … (with optional closing quote/bracket) then whitespace.
_END = re.compile(r"[.!?…]+[\"'”’)\]]*(?=\s)")
_NEXT_OK = re.compile(r"[A-ZÀ-Þ0-9\"'“‘(\[•\-*]")
_ABBREVIATIONS = {
    "e.g", "i.e", "etc", "z.b", "bzw", "u.a", "d.h", "ca", "dr", "prof", "nr", "p. ex", "env",
    "mr", "mrs", "ms", "vs", "approx", "ggf", "inkl", "evtl", "usw", "u.v.m", "o.ä", "z.t",
    "v.a", "s.o", "sog", "resp", "max", "min", "tel", "st", "hr", "no", "inc", "ltd", "co",
    "cf", "ex", "p.ex", "c.-à-d", "cà-d", "m.a", "dipl", "ing", "lic", "mag", "abs", "ev",
}  # fmt: skip
_MONTHS = {
    "januar", "februar", "märz", "maerz", "april", "mai", "juni", "juli", "august",
    "september", "oktober", "november", "dezember", "jan", "feb", "mär", "apr", "jun", "jul",
    "aug", "sep", "sept", "okt", "nov", "dez",
}  # fmt: skip


@dataclass(frozen=True)
class Sentence:
    id: int
    start: int
    end: int
    text: str


def _is_break(line: str, end: int, nxt: int) -> bool:
    """Decide whether the sentence end matched at `end` (exclusive) is a real break."""
    if nxt >= len(line) or not _NEXT_OK.match(line[nxt]):
        return False
    punct = line[:end].rstrip("\"'”’)]")
    if not punct.endswith("."):
        return True  # ! ? and … always end a sentence if an uppercase word follows
    token = re.split(r"\s", punct[:-1])[-1].lower().lstrip("(\"'“")
    if token in _ABBREVIATIONS:
        return False
    if len(token) == 1 and token.isalpha() and token != "a":  # initials: "J. Smith"
        return False
    if re.fullmatch(r"\d{1,2}", token):  # German ordinal: "1. Januar"
        word = re.match(r"[^\W\d_]+", line[nxt:])
        if word and word.group().lower() in _MONTHS:
            return False
    # "z.B." style: token like "z.b" already handled above; dotted tokens of
    # single letters ("u.a", "d.h", "i.e") are abbreviations.
    return not re.fullmatch(r"(?:[^\W\d_]\.)+[^\W\d_]", token)


def _split_line(line: str, base: int) -> list[tuple[int, int]]:
    spans = []
    start = 0
    for m in _END.finditer(line):
        nxt = m.end()
        while nxt < len(line) and line[nxt].isspace():
            nxt += 1
        if _is_break(line, m.end(), nxt):
            spans.append((start, m.end()))
            start = nxt
    spans.append((start, len(line)))
    out = []
    for s, e in spans:
        out.extend(_split_long(line, s, e))
    return [(base + s, base + e) for s, e in out]


def _split_long(line: str, s: int, e: int) -> list[tuple[int, int]]:
    if e - s <= MAX_PIECE or "; " not in line[s:e]:
        return [(s, e)]
    out = []
    pos = s
    for m in re.finditer(r";\s+", line[s:e]):
        cut = s + m.end()
        if s + m.start() + 1 - pos >= 1 and cut < e:
            out.append((pos, s + m.start() + 1))
            pos = cut
    out.append((pos, e))
    return out


def split(text: str) -> list[Sentence]:
    sentences: list[Sentence] = []
    for line in _LINE.finditer(text):
        raw = line.group()
        lead = len(raw) - len(raw.lstrip())
        body = raw.strip()
        if not body:
            continue
        bullet = _BULLET.match(body)
        if bullet:
            lead += bullet.end()
            body = body[bullet.end() :].strip()
        for s, e in _split_line(body, line.start() + lead):
            piece = text[s:e]
            stripped = piece.strip()
            if not stripped:
                continue
            s += len(piece) - len(piece.lstrip())
            e -= len(piece) - len(piece.rstrip())
            sentences.append(Sentence(len(sentences) + 1, s, e, text[s:e]))
    return sentences
