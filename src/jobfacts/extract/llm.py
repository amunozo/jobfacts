"""Chat-model extractors (DeepSeek, Mistral via OpenRouter) with strict JSON validation.

The model gets the same numbered posting as Jev and returns
`{field: {"value": ..., "sentences": ["S012", ...]}}`. Output is validated against the
spec; on invalid output the model is asked once more, then the result is an error.
Invalid values are never stored.
"""

import json
import os
import re
import time
from typing import Any

from dotenv import load_dotenv
from openai import OpenAI

from jobfacts.extract.base import Result
from jobfacts.extract.jev import build_state
from jobfacts.extract.posting import PostingText, parse_label
from jobfacts.extract.spec import FIELDS, GENERAL_RULES, spec_hash

SYSTEM_TEMPLATE = """You extract facts from one job posting. {rules}

The posting is shown as numbered lines: T = title, L = location field of the job board, \
S001, S002, ... = sentences of the description. For every field below choose exactly one \
allowed value and list the ids of the line(s) that support it. A value that means "not \
stated / not mentioned / not applicable" has an empty list of ids. Otherwise give at least \
one id, using only ids that exist.

Fields and allowed values:
{fields}

office_locations: below the posting you get a list of candidate places (key: matched \
text). Return the keys of the candidates that are an office location for this role, \
with supporting ids. Return an empty list if none is.

Reply with one JSON object and nothing else, in this shape:
{{"work_mode": {{"value": "...", "sentences": ["S004"]}}, ..., \
"office_locations": {{"value": ["city:Zürich"], "sentences": ["L"]}}}}
The object must have exactly these keys: {keys}."""


def render_fields() -> str:
    out = []
    for f in FIELDS.values():
        out.append(f"### {f.name}\n{f.question}")
        out += [f'- "{v}": {d}' for v, d in f.values.items()]
    return "\n".join(out)


def system_prompt() -> str:
    keys = ", ".join([*FIELDS, "office_locations"])
    return SYSTEM_TEMPLATE.format(rules=GENERAL_RULES, fields=render_fields(), keys=keys)


def user_prompt(p: PostingText) -> str:
    cands = p.candidates()
    listing = "\n".join(f"- {c.key}: {c.matched} (in {where})" for c, where in cands)
    return f"{build_state(p)}\n\nCandidate places:\n{listing or '(none)'}"


def _parse_ids(raw: Any, p: PostingText, name: str) -> list[int]:
    if not isinstance(raw, list):
        raise ValueError(f"{name}.sentences must be a list")
    ids: list[int] = []
    for item in raw:
        sid = parse_label(item) if isinstance(item, str | int) else None
        if sid is None or sid not in p.known_ids():
            raise ValueError(f"{name}: unknown sentence id {item!r}")
        if sid not in ids:
            ids.append(sid)
    return ids


def validate(obj: Any, p: PostingText, candidate_keys: set[str]) -> Result:
    """Check model output against the spec. Raises ValueError with a readable message."""
    if not isinstance(obj, dict):
        raise ValueError("the reply must be a JSON object")
    missing = [k for k in [*FIELDS, "office_locations"] if k not in obj]
    if missing:
        raise ValueError(f"missing keys: {', '.join(missing)}")
    result = Result()
    for f in FIELDS.values():
        entry = obj[f.name]
        if not isinstance(entry, dict) or "value" not in entry:
            raise ValueError(f'{f.name} must be {{"value": ..., "sentences": [...]}}')
        value = str(entry["value"])
        if value not in f.values:
            raise ValueError(f"{f.name}: {value!r} is not allowed; allowed: {list(f.values)}")
        ids = _parse_ids(entry.get("sentences", []), p, f.name)
        result.values[f.name] = value
        result.sentences[f.name] = [] if value in f.no_quote else ids
        if value not in f.no_quote and not ids:
            result.details.setdefault("missing_quote", []).append(f.name)
    entry = obj["office_locations"]
    if not isinstance(entry, dict) or not isinstance(entry.get("value"), list):
        raise ValueError('office_locations must be {"value": [keys], "sentences": [...]}')
    for key in entry["value"]:
        if key not in candidate_keys:
            raise ValueError(f"office_locations: {key!r} is not one of the candidates")
    result.office_locations = list(dict.fromkeys(entry["value"]))
    result.sentences["office_locations"] = _parse_ids(entry.get("sentences", []), p, "office")
    return result


def parse_json(text: str) -> Any:
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    return json.loads(fence.group(1) if fence else text)


class LLMExtractor:
    def __init__(
        self,
        name: str,
        model: str,
        client: Any,
        price_in: float,
        price_out: float,
        extra_body: dict | None = None,
    ) -> None:
        self.name, self.model, self.client = name, model, client
        self.price_in, self.price_out = price_in, price_out  # USD per million tokens
        self.extra_body = extra_body or {}

    def spec_version(self) -> str:
        return spec_hash("llm:" + SYSTEM_TEMPLATE)

    def _chat(self, messages: list[dict]) -> Any:
        return self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=0,
            response_format={"type": "json_object"},
            extra_body=self.extra_body,
        )

    def extract(self, p: PostingText) -> Result:
        keys = {c.key for c, _ in p.candidates()}
        messages = [
            {"role": "system", "content": system_prompt()},
            {"role": "user", "content": user_prompt(p)},
        ]
        tokens_in = tokens_out = 0
        started = time.monotonic()
        last_error = "no attempt"
        raw = ""
        result = Result()
        for attempt in range(2):
            try:
                response = self._chat(messages)
            except Exception as error:  # API failure (after the SDK's own retries)
                last_error = f"{type(error).__name__}: {error}"
                break
            if response.usage:
                tokens_in += response.usage.prompt_tokens
                tokens_out += response.usage.completion_tokens
            raw = response.choices[0].message.content or ""
            try:
                result = validate(parse_json(raw), p, keys)
                last_error = ""
                result.details["attempts"] = attempt + 1
                break
            except ValueError as error:  # includes json.JSONDecodeError
                last_error = f"invalid output: {error}"
                messages = [
                    *messages,
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": f"That reply was invalid: {error}. Reply again with only "
                        "the corrected JSON object.",
                    },
                ]
        if last_error:
            result = Result(details={"attempts": attempt + 1, "raw": raw[:4000]})
            result.error = last_error
        result.tokens_in, result.tokens_out = tokens_in, tokens_out
        result.cost_usd = (tokens_in * self.price_in + tokens_out * self.price_out) / 1e6
        result.latency_ms = int((time.monotonic() - started) * 1000)
        return result


# Prices in USD per million tokens, read on 2026-11-02:
# DeepSeek peak-hour cache-miss rate (off-peak is half); OpenRouter listing price.
DEEPSEEK_MODEL = "deepseek-flash"
MISTRAL_MODEL = "mistralai/mistral-small-2603"


def deepseek_extractor() -> LLMExtractor:
    load_dotenv()
    client = OpenAI(
        api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com", timeout=180
    )
    return LLMExtractor(
        "deepseek", DEEPSEEK_MODEL, client, 0.30, 1.20, {"thinking": {"type": "disabled"}}
    )


def mistral_extractor() -> LLMExtractor:
    load_dotenv()
    client = OpenAI(
        api_key=os.environ["OPENROUTER_API_KEY"],
        base_url="https://openrouter.ai/api/v1",
        timeout=180,
    )
    return LLMExtractor("mistral", MISTRAL_MODEL, client, 0.15, 0.60)
