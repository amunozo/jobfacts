"""Jev (TypeSafe) relevance filter and extractor.

One request per posting: the state is the numbered posting; for each field there is a
value Choice and a sentence Choice ("which sentence states this?"). Office cantons: one
Noul per canton plus one for "outside Switzerland", and one sentence Choice for the
evidence. Probabilities and confidences are kept in `details`.
"""

import hashlib
import time
from typing import Any

from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, TypeSafeClient

from jobfacts import locations
from jobfacts.extract.base import Result
from jobfacts.extract.posting import PostingText, label, parse_label
from jobfacts.extract.rules import TITLE_RULES, apply_extraction_rules, title_excluded
from jobfacts.extract.spec import (
    FIELDS,
    GENERAL_RULES,
    LOCATION_ID,
    OFFICE_CANTONS,
    OFFICE_EVIDENCE_QUESTION,
    OUTSIDE_QUESTION,
    RELEVANCE_QUESTION,
    TITLE_ID,
    canton_question,
    order_values,
    spec_hash,
)

MODEL = "jev-1.14.0"
PRICE_PER_MTOK_IN = 0.042
MAX_OPTIONS = 255
NONE = "none"
CANTON_THRESHOLD = 0.5
RELEVANCE_THRESHOLD = 0.4
RELEVANCE_SENTENCES = 5

# Bump when code that changes Jev's input or the stored output changes without changing any
# constant below or in spec.py (sentence splitting, numbering, post-processing, places,
# seniority). Code cannot be hashed reliably; this number is part of the spec version.
EXTRACTION_CODE_VERSION = 2  # 2: title/canton/remote rules (extract.rules)

STATE_HEADER = (
    "Job posting. Each line starts with an id: T = title, L = location field of the job "
    "board, S001... = sentences of the description.\n\n"
)
SENTENCE_QUESTION = (
    'Which line of the posting states the answer to: "{question}"? '
    "Choose {none} if no line states it."
)
OFFICE_EVIDENCE_SUFFIX = " Choices are line ids."


def make_client() -> TypeSafeClient:
    load_dotenv()  # reads TYPESAFE_API_KEY from .env (never printed)
    return TypeSafeClient(model=MODEL, timeout=120.0)


def chunks(ids: list[int], size: int = MAX_OPTIONS - 1) -> list[list[int]]:
    """Split sentence ids so each Choice has at most 255 options (ids + `none`)."""
    return [ids[i : i + size] for i in range(0, len(ids), size)] or [[]]


def build_state(p: PostingText) -> str:
    return STATE_HEADER + p.numbered()


def build_questions(p: PostingText, ordering: str = "original") -> dict[str, Any]:
    """All questions for one posting (names: v_<field>, s_<field>_<k>, c_<code>, c_outside)."""
    questions: dict[str, Any] = {}
    ids = [TITLE_ID, LOCATION_ID, *(s.id for s in p.sentences)]
    for f in FIELDS.values():
        questions[f"v_{f.name}"] = Choice(
            instructions=f"{GENERAL_RULES} {f.question}",
            criteria=order_values(f.name, f.values, ordering),
        )
        for k, part in enumerate(chunks(ids)):
            questions[f"s_{f.name}_{k}"] = Choice(
                instructions=SENTENCE_QUESTION.format(question=f.question, none=NONE),
                criteria={**{label(i): None for i in part}, NONE: None},
            )
    for code in locations.CANTONS:
        questions[f"c_{code}"] = Noul(instructions=canton_question(code))
    questions["c_outside"] = Noul(instructions=OUTSIDE_QUESTION)
    for k, part in enumerate(chunks(ids)):
        questions[f"s_{OFFICE_CANTONS}_{k}"] = Choice(
            instructions=OFFICE_EVIDENCE_QUESTION + OFFICE_EVIDENCE_SUFFIX,
            criteria={**{label(i): None for i in part}, NONE: None},
        )
    return questions


def best_sentence(
    answers: dict[str, Any], field_name: str, n_chunks: int
) -> tuple[list[int], dict]:
    """Pick the best line over all chunks: highest probability, `none` means no quote."""
    best_label, best_p = NONE, -1.0
    top: dict[str, float] = {}
    for k in range(n_chunks):
        probs = answers[f"s_{field_name}_{k}"].probabilities
        top.update(sorted(probs.items(), key=lambda kv: -kv[1])[:3])
        name, p = max(probs.items(), key=lambda kv: kv[1])
        if p > best_p:
            best_label, best_p = name, p
    parsed = None if best_label == NONE else parse_label(best_label)
    return ([parsed] if parsed is not None else []), {
        "top": dict(sorted(top.items(), key=lambda kv: -kv[1])[:5])
    }


class JevExtractor:
    name = "jev"
    model = MODEL

    def __init__(self, client: Any | None = None, ordering: str = "original") -> None:
        self.client = client or make_client()
        order_values("x", {}, ordering)  # validates the name
        self.ordering = ordering

    def spec_version(self) -> str:
        extra = "|".join(
            [
                "jev",
                GENERAL_RULES,
                MODEL,
                f"threshold:{CANTON_THRESHOLD}",
                f"none:{NONE}",
                f"max_options:{MAX_OPTIONS}",
                STATE_HEADER,
                SENTENCE_QUESTION,
                OFFICE_EVIDENCE_SUFFIX,
                f"code:{EXTRACTION_CODE_VERSION}",
            ]
        )
        if self.ordering != "original":
            extra += f"|order:{self.ordering}"  # default hash stays unchanged
        return spec_hash(extra)

    def extract(self, p: PostingText) -> Result:
        result = Result()
        questions = build_questions(p, self.ordering)
        started = time.monotonic()
        try:
            response = self.client.system_one(
                state=build_state(p), questions=questions, model=self.model
            )
        except Exception as error:  # network/API failure: stored as an error, not raised
            result.error = f"{type(error).__name__}: {error}"
            result.latency_ms = int((time.monotonic() - started) * 1000)
            return result
        result.latency_ms = int((time.monotonic() - started) * 1000)
        result.tokens_in = response.usage.input_tokens
        result.tokens_out = response.usage.output_tokens
        result.cost_usd = result.tokens_in * PRICE_PER_MTOK_IN / 1e6
        answers = response.answers
        n_chunks = len(chunks([TITLE_ID, LOCATION_ID, *(s.id for s in p.sentences)]))
        details: dict[str, Any] = {"model_returned": getattr(response, "model", None)}
        if self.ordering != "original":
            details["ordering"] = self.ordering
        for f in FIELDS.values():
            a = answers[f"v_{f.name}"]
            value = a.choice
            result.values[f.name] = value
            sent, sdetail = best_sentence(answers, f.name, n_chunks)
            result.sentences[f.name] = [] if value in f.no_quote else sent
            details[f.name] = {
                "probabilities": a.probabilities,
                "confidence": a.confidence,
                "sentence": sdetail,
            }
        probs = {code: answers[f"c_{code}"].noul for code in locations.CANTONS}
        probs[locations.OUTSIDE] = answers["c_outside"].noul
        result.office_cantons = [c for c, pr in probs.items() if pr >= CANTON_THRESHOLD]
        if result.office_cantons:
            result.sentences[OFFICE_CANTONS], sdetail = best_sentence(
                answers, OFFICE_CANTONS, n_chunks
            )
        else:
            sdetail = {}
        details[OFFICE_CANTONS] = {
            "probabilities": {c: round(pr, 4) for c, pr in probs.items() if pr >= 0.05},
            "sentence": sdetail,
        }
        result.details = details
        apply_extraction_rules(p, result)
        return result


def relevance_version() -> str:
    """Version of everything that decides a relevance score; stored with each score, so a
    change makes `jobfacts relevance` re-score all postings once."""
    parts = [
        RELEVANCE_QUESTION,
        MODEL,
        f"threshold:{RELEVANCE_THRESHOLD}",
        f"sentences:{RELEVANCE_SENTENCES}",
        TITLE_RULES,
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]


def relevance_probability(client: Any, p: PostingText, model: str = MODEL) -> float:
    """Probability that the role is AI / ML / data / research (title + first sentences).
    A title on the exclusion list (rules.title_excluded) scores 0 without a model call."""
    if title_excluded(p.title):
        return 0.0
    head = "\n".join(s.text for s in p.sentences[:RELEVANCE_SENTENCES])
    state = f"Title: {p.title}\nLocation: {p.location_raw}\n\n{head}"
    response = client.system_one(
        state=state, questions={"relevant": Noul(instructions=RELEVANCE_QUESTION)}, model=model
    )
    return float(response.answers["relevant"].noul)
