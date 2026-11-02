"""Jev (TypeSafe) relevance filter and extractor.

One request per posting: the state is the numbered posting; for each field there is a
value Choice and a sentence Choice ("which sentence states this?"), plus one Noul per
location candidate. Probabilities and confidences are kept in `details`.
"""

import time
from typing import Any

from dotenv import load_dotenv
from typesafe_sdk import Choice, Noul, TypeSafeClient

from jobfacts.extract.base import Result
from jobfacts.extract.posting import PostingText, label, parse_label
from jobfacts.extract.spec import (
    FIELDS,
    GENERAL_RULES,
    LOCATION_ID,
    LOCATION_QUESTION,
    RELEVANCE_QUESTION,
    TITLE_ID,
    spec_hash,
)

MODEL = "jev-1.14.0"
PRICE_PER_MTOK_IN = 0.042
MAX_OPTIONS = 255
NONE = "none"
LOCATION_THRESHOLD = 0.5
RELEVANCE_THRESHOLD = 0.5
RELEVANCE_SENTENCES = 5


def make_client() -> TypeSafeClient:
    load_dotenv()  # reads TYPESAFE_API_KEY from .env (never printed)
    return TypeSafeClient(model=MODEL, timeout=120.0)


def chunks(ids: list[int], size: int = MAX_OPTIONS - 1) -> list[list[int]]:
    """Split sentence ids so each Choice has at most 255 options (ids + `none`)."""
    return [ids[i : i + size] for i in range(0, len(ids), size)] or [[]]


def build_state(p: PostingText) -> str:
    return (
        "Job posting. Each line starts with an id: T = title, L = location field of the job "
        "board, S001... = sentences of the description.\n\n" + p.numbered()
    )


def build_questions(p: PostingText) -> dict[str, Any]:
    """All questions for one posting (names: v_<field>, s_<field>_<k>, loc_<i>)."""
    questions: dict[str, Any] = {}
    ids = [TITLE_ID, LOCATION_ID, *(s.id for s in p.sentences)]
    for f in FIELDS.values():
        questions[f"v_{f.name}"] = Choice(
            instructions=f"{GENERAL_RULES} {f.question}",
            criteria=dict(f.values),
        )
        for k, part in enumerate(chunks(ids)):
            questions[f"s_{f.name}_{k}"] = Choice(
                instructions=(
                    f'Which line of the posting states the answer to: "{f.question}"? '
                    f"Choose {NONE} if no line states it."
                ),
                criteria={**{label(i): None for i in part}, NONE: None},
            )
    for i, (cand, where) in enumerate(p.candidates()):
        questions[f"loc_{i}"] = Noul(
            instructions=LOCATION_QUESTION.format(matched=cand.matched, where=where)
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

    def __init__(self, client: Any | None = None) -> None:
        self.client = client or make_client()

    def spec_version(self) -> str:
        return spec_hash("jev:" + GENERAL_RULES)

    def extract(self, p: PostingText) -> Result:
        result = Result()
        questions = build_questions(p)
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
        locs = {}
        for i, (cand, where) in enumerate(p.candidates()):
            prob = answers[f"loc_{i}"].noul
            locs[cand.key] = {"matched": cand.matched, "where": where, "probability": prob}
            if prob >= LOCATION_THRESHOLD:
                result.office_locations.append(cand.key)
        details["office_locations"] = locs
        result.details = details
        return result


def relevance_probability(client: Any, p: PostingText, model: str = MODEL) -> float:
    """Probability that the role is AI / ML / data / research (title + first sentences)."""
    head = "\n".join(s.text for s in p.sentences[:RELEVANCE_SENTENCES])
    state = f"Title: {p.title}\nLocation: {p.location_raw}\n\n{head}"
    response = client.system_one(
        state=state, questions={"relevant": Noul(instructions=RELEVANCE_QUESTION)}, model=model
    )
    return float(response.answers["relevant"].noul)
