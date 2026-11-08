"""HTML to plain text that keeps paragraph breaks."""

import html
import re
from html.parser import HTMLParser

_BLOCK = {"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"}


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK and tag != "li":
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(value: str | None) -> str:
    """Convert HTML (also entity-escaped HTML, as Greenhouse sends it) to plain text."""
    if not value:
        return ""
    if "&lt;" in value and "<" not in value:
        value = html.unescape(value)
    parser = _Extractor()
    parser.feed(value)
    text = "".join(parser.parts).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def unescape_field(value: str | None) -> str:
    """A one-line field (title, place) with HTML entities decoded, also when escaped twice
    ("R&amp;amp;I"), non-breaking spaces turned into spaces and whitespace collapsed."""
    text = value or ""
    for _ in range(3):  # stops at the first pass that changes nothing
        decoded = html.unescape(text)
        if decoded == text:
            break
        text = decoded
    return re.sub(r"\s+", " ", text).strip()
