"""Posting language detection (offline).

Library: `lingua-language-detector`, because it is accurate on short and mixed
texts (better than langdetect/fastText-lite for 1-2 sentence input), fully
offline, pure wheels and actively maintained. It is given a few European
languages so that Spanish or Italian text is not forced into EN/DE/FR.
"""

from functools import lru_cache
from typing import Literal

from lingua import Language, LanguageDetectorBuilder

Lang = Literal["en", "de", "fr", "other"]
_MAX_CHARS = 5000
_MAP = {Language.ENGLISH: "en", Language.GERMAN: "de", Language.FRENCH: "fr"}


@lru_cache(maxsize=1)
def _detector():
    languages = [
        Language.ENGLISH, Language.GERMAN, Language.FRENCH, Language.ITALIAN,
        Language.SPANISH, Language.PORTUGUESE, Language.DUTCH, Language.POLISH,
    ]  # fmt: skip
    return LanguageDetectorBuilder.from_languages(*languages).build()


def detect(text: str) -> Lang:
    """Return "en", "de", "fr", or "other" (also for empty text)."""
    text = text.strip()[:_MAX_CHARS]
    if not text:
        return "other"
    language = _detector().detect_language_of(text)
    return _MAP.get(language, "other")  # type: ignore[return-value]
