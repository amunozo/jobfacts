"""Silver labels for test postings: several LLMs label each posting independently.

gpt and gemini are run through the `pi` CLI; gemini_or calls Gemini directly through OpenRouter;
opus answers are written by another process as raw files and only validated here. Which postings
are labelled is an item set (`pilot`, `de`, `all`). Combining the answers into final labels is
not done here; `jobfacts.silver_report` compares them.
"""

import hashlib
import json
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobfacts import locations
from jobfacts.extract import llm
from jobfacts.extract.posting import PostingText, label, parse_label
from jobfacts.extract.spec import FIELDS, OFFICE_CANTONS
from jobfacts.testset import HOLDOUT_FILE, TEST_SET_FILE, load_test_set

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "prompts" / "label_posting.md"
FIELDS_MD = ROOT / "docs" / "fields.md"
LABELLERS = {
    "gpt": "openai-codex/gpt-6-astra",
    "gemini": "opencode-go/gemini-4-argon-contributor",
    "gemini_or": "google/gemini-4-argon",
    "opus": "claude-opus-5-5 (Claude Code subagent)",
}
RUNNABLE = ("gpt", "gemini", "gemini_or")
SETS = ("pilot", "de", "all", "holdout")
PILOT_FILE = "pilot.json"
PILOT_PER_LANGUAGE = 4
PILOT_LANGUAGES = ("en", "de", "fr")
OR_MAX_TOKENS = 16000
KEYS = [*FIELDS, OFFICE_CANTONS, "relevant"]
CONFIDENCE = {"high", "low"}
RELEVANT = {"true", "false", "unsure"}
SYSTEM_PROMPT = "You are a careful annotator. Reply with one JSON object only."
MAX_ARG_BYTES = 120_000
TIMEOUT = 600


def _posting(item: dict) -> PostingText:
    return PostingText.build(
        item["posting_id"], item["title"], item["location_raw"], item["description_text"]
    )


def render_prompt(item: dict) -> str:
    text = TEMPLATE.read_text(encoding="utf-8")
    text = text.replace("{fields_md}", FIELDS_MD.read_text(encoding="utf-8"))
    text = text.replace("{cantons}", llm.render_cantons())
    text = text.replace("{posting}", _posting(item).numbered())
    for placeholder in ("{fields_md}", "{cantons}", "{posting}"):
        assert placeholder not in text, f"placeholder left in prompt: {placeholder}"
    return text


def pilot_select(data_dir: Path) -> list[int]:
    """Pick the pilot postings (first 4 by `order` of each language; the English ones must
    include order 0, which the owner labelled) and write `silver/pilot.json`."""
    items = sorted(load_test_set(Path(data_dir) / TEST_SET_FILE.name), key=lambda i: i["order"])
    ids: list[int] = []
    for lang in PILOT_LANGUAGES:
        chosen = [i for i in items if i["language"] == lang][:PILOT_PER_LANGUAGE]
        first = next((i for i in items if i["order"] == 0 and i["language"] == lang), None)
        if first and first not in chosen:
            chosen = [first, *chosen[: PILOT_PER_LANGUAGE - 1]]
        ids += [i["posting_id"] for i in chosen]
    out = Path(data_dir) / "silver" / PILOT_FILE
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"posting_ids": ids}, indent=2) + "\n", encoding="utf-8")
    return ids


def items_for(data_dir: Path, set_name: str = "pilot") -> list[dict]:
    """Items of an item set, sorted by `order` (`holdout` reads holdout_set.jsonl)."""
    if set_name not in SETS:
        raise ValueError(f"unknown set {set_name!r}; use one of {SETS}")
    if set_name == "holdout":
        path = Path(data_dir) / HOLDOUT_FILE.name
        if not path.exists():
            raise ValueError("no holdout set yet: run `jobfacts select-holdout`")
        return sorted(load_test_set(path), key=lambda i: i["order"])
    items = load_test_set(Path(data_dir) / TEST_SET_FILE.name)
    if set_name == "de":
        items = [i for i in items if i["language"] == "de"]
    elif set_name == "pilot":
        path = Path(data_dir) / "silver" / PILOT_FILE
        if not path.exists():
            raise ValueError("no pilot set yet: run `jobfacts silver pilot-select`")
        wanted = set(json.loads(path.read_text(encoding="utf-8"))["posting_ids"])
        items = [i for i in items if i["posting_id"] in wanted]
    return sorted(items, key=lambda i: i["order"])


def render_all(data_dir: Path, set_name: str = "pilot") -> int:
    out = Path(data_dir) / "silver" / "prompts"
    out.mkdir(parents=True, exist_ok=True)
    items = items_for(data_dir, set_name)
    for item in items:
        (out / f"{item['posting_id']}.md").write_text(render_prompt(item), encoding="utf-8")
    return len(items)


def extract_json(text: str) -> Any:
    """Parse the outermost {...} of a reply (fences or surrounding text allowed)."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found in the reply")
    try:
        return json.loads(text[start : end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"the reply is not valid JSON: {e}") from e


def _ids(raw: Any, p: PostingText, name: str, problems: list[str]) -> list[int]:
    if not isinstance(raw, list):
        problems.append(f"{name}.sentences must be a list")
        return []
    ids: list[int] = []
    for x in raw:
        sid = parse_label(x) if isinstance(x, str) else None
        if sid is None or sid not in p.known_ids():
            problems.append(f"{name}: unknown sentence id {x!r}")
        elif sid not in ids:
            ids.append(sid)
    return ids


def validate(obj: Any, item: dict) -> dict:
    """Check an answer against the spec; return it normalised (int sentence ids)."""
    if not isinstance(obj, dict):
        raise ValueError("the reply must be a JSON object")
    problems: list[str] = []
    missing = [k for k in KEYS if k not in obj]
    extra = [k for k in obj if k not in KEYS]
    if missing:
        problems.append(f"missing keys: {', '.join(missing)}")
    if extra:
        problems.append(f"unexpected keys: {', '.join(extra)}")
    p = _posting(item)
    out: dict[str, Any] = {}
    for name in [*FIELDS, OFFICE_CANTONS]:
        entry = obj.get(name)
        if name not in obj:
            continue
        if not isinstance(entry, dict) or "value" not in entry:
            problems.append(f"{name} must be an object with a value")
            continue
        value = entry["value"]
        n_before = len(problems)
        if name == OFFICE_CANTONS:
            allowed = locations.office_canton_values()
            if not isinstance(value, list):
                problems.append(f"{name}.value must be a list")
            else:
                bad = [c for c in value if c not in allowed]
                if bad or not all(isinstance(c, str) for c in value):
                    problems.append(f"{name}: not a canton code or outside_switzerland: {bad!r}")
                if len(set(map(str, value))) != len(value):
                    problems.append(f"{name}: duplicate codes")
            no_quote = not isinstance(value, list) or not value
        else:
            if not isinstance(value, str) or value not in FIELDS[name].values:
                problems.append(
                    f"{name}: {value!r} is not allowed; allowed: {list(FIELDS[name].values)}"
                )
            no_quote = value in FIELDS[name].no_quote
        ids = _ids(entry.get("sentences", []), p, name, problems)
        if len(problems) == n_before:
            if no_quote and ids:
                problems.append(f"{name}: value {value!r} must have no sentences")
            if not no_quote and not ids:
                problems.append(f"{name}: value {value!r} needs at least one sentence")
        translation = entry.get("translation", "")
        if not isinstance(translation, str):
            problems.append(f"{name}.translation must be a string")
        elif bool(translation.strip()) != bool(ids):
            problems.append(
                f"{name}: translation must be non-empty exactly when there are sentences"
            )
        if entry.get("confidence") not in CONFIDENCE:
            problems.append(f'{name}.confidence must be "high" or "low"')
        note = entry.get("note", "")
        if not isinstance(note, str):
            problems.append(f"{name}.note must be a string")
            note = ""
        out[name] = {
            "value": value,
            "sentences": ids,
            "translation": translation if isinstance(translation, str) else "",
            "confidence": entry.get("confidence"),
            "note": note,
        }
    rel = obj.get("relevant")
    if "relevant" in obj:
        if not isinstance(rel, dict):
            problems.append("relevant must be an object with value and reason")
        else:
            if rel.get("value") not in RELEVANT:
                problems.append(f"relevant.value must be one of {sorted(RELEVANT)}")
            if not isinstance(rel.get("reason"), str):
                problems.append("relevant.reason must be a string")
            out["relevant"] = {"value": rel.get("value"), "reason": rel.get("reason")}
    if problems:
        raise ValueError("; ".join(problems))
    return {k: out[k] for k in KEYS}


def to_label_form(answer: dict) -> dict:
    """Copy of a normalised answer with sentence ids written as T / L / S004."""
    return {
        k: {**v, "sentences": [label(i) for i in v["sentences"]]} if "sentences" in v else v
        for k, v in answer.items()
    }


@dataclass
class Reply:
    text: str
    usage: dict = field(default_factory=dict)  # input, output, reasoning, cost_usd, duration_ms


class CallError(RuntimeError):
    """A failed model call; `usage` holds what is known about the attempt."""

    def __init__(self, message: str, usage: dict | None = None):
        super().__init__(message)
        self.usage = usage or {}


def make_usage(input=0, output=0, reasoning=0, cost_usd=0.0, duration_ms=0) -> dict:  # noqa: A002
    return {
        "input": int(input or 0),
        "output": int(output or 0),
        "reasoning": int(reasoning or 0),
        "cost_usd": float(cost_usd or 0.0),
        "duration_ms": int(duration_ms or 0),
    }


def parse_pi_events(stdout: str) -> Reply:
    """Reply and usage from `pi --mode json` output (JSON lines).

    The last `message_end` event with an assistant message holds the answer. pi exits 0 even
    when the model call failed, so `stopReason == "error"` raises a CallError.
    """
    last = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        msg = event.get("message") if isinstance(event, dict) else None
        if event.get("type") == "message_end" and isinstance(msg, dict):
            if msg.get("role") == "assistant":
                last = msg
    if last is None:
        raise CallError("pi output has no assistant message")
    u = last.get("usage") or {}
    usage = make_usage(
        u.get("input"),
        u.get("output"),
        u.get("reasoning"),
        (u.get("cost") or {}).get("total"),
        last.get("durationMs"),
    )
    if last.get("stopReason") == "error":
        raise CallError(f"pi model error: {last.get('errorMessage') or 'unknown'}", usage)
    parts = last.get("content") or []
    if isinstance(parts, str):
        text = parts
    else:
        text = "".join(p.get("text", "") for p in parts if p.get("type") == "text")
    return Reply(text, usage)


def pi_call(model: str, prompt: str) -> Reply:
    """One model call through the pi CLI (no tools, no session)."""
    cmd = [
        "pi", "-p", "--no-session", "--no-extensions", "--no-tools", "--thinking", "high",
        "--mode", "json", "--model", model, "--system-prompt", SYSTEM_PROMPT, "--",
    ]  # fmt: skip
    tmp = None
    started = time.monotonic()
    try:
        if len(prompt.encode("utf-8")) > MAX_ARG_BYTES:
            tmp = tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8")
            tmp.write(prompt)
            tmp.close()
            cmd += [f"@{tmp.name}", "Follow the instructions in the attached file."]
        else:
            cmd.append(prompt)
        r = subprocess.run(
            cmd,
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise CallError(
            f"pi timed out after {TIMEOUT} s", make_usage(duration_ms=TIMEOUT * 1000)
        ) from None
    finally:
        if tmp:
            Path(tmp.name).unlink(missing_ok=True)
    if r.returncode != 0:
        raise CallError(
            f"pi exited with {r.returncode}: {r.stderr.strip()[-500:]}",
            make_usage(duration_ms=(time.monotonic() - started) * 1000),
        )
    return parse_pi_events(r.stdout)


def openrouter_call(model: str, prompt: str, client=None) -> Reply:
    """One direct call through OpenRouter (same system and user prompt as the pi labellers)."""
    client = client or llm.openrouter_client(timeout=TIMEOUT)
    started = time.monotonic()
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            max_tokens=OR_MAX_TOKENS,
            extra_body={"usage": {"include": True}, "reasoning": {"effort": "high"}},
        )
    except Exception as e:
        raise CallError(
            f"{type(e).__name__}: {e}", make_usage(duration_ms=(time.monotonic() - started) * 1000)
        ) from e
    duration = (time.monotonic() - started) * 1000
    u = response.usage
    extra = getattr(u, "model_extra", None) or {} if u is not None else {}
    details = getattr(u, "completion_tokens_details", None) if u is not None else None
    usage = make_usage(
        getattr(u, "prompt_tokens", 0),
        getattr(u, "completion_tokens", 0),
        getattr(details, "reasoning_tokens", 0),
        extra.get("cost", getattr(u, "cost", 0.0)),
        duration,
    )
    choice = response.choices[0]
    text = choice.message.content or ""
    if not text:
        raise CallError(
            f"empty reply (finish_reason {getattr(choice, 'finish_reason', None)!r})", usage
        )
    return Reply(text, usage)


def default_call(labeller: str) -> Callable[[str, str], Reply]:
    return openrouter_call if labeller == "gemini_or" else pi_call


def _store(
    data_dir: Path,
    labeller: str,
    item: dict,
    answer: dict,
    prompt_sha: str,
    usage: list[dict] | None = None,
) -> None:
    out = Path(data_dir) / "silver" / labeller
    out.mkdir(parents=True, exist_ok=True)
    record = {
        "posting_id": item["posting_id"],
        "labeller": labeller,
        "model": LABELLERS[labeller],
        "saved_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "prompt_sha256": prompt_sha,
        "answer": answer,
    }
    if usage is not None:
        record["usage"] = usage  # one entry per attempt, failed attempts included
    path = out / f"{item['posting_id']}.json"
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _stored(data_dir: Path, labeller: str, pid: int) -> bool:
    return (Path(data_dir) / "silver" / labeller / f"{pid}.json").exists()


def run_labeller(
    labeller: str,
    data_dir: Path,
    *,
    limit: int | None = None,
    posting_id: int | None = None,
    force: bool = False,
    set_name: str = "pilot",
    call: Callable[[str, str], Reply | str] | None = None,
) -> dict:
    """Label the postings of a set with one model. `call(model, prompt) -> Reply` is injectable
    (a plain string reply is accepted too, without usage)."""
    if labeller not in RUNNABLE:
        raise ValueError(f"{labeller} is not run by code; use one of {RUNNABLE}")
    call = call or default_call(labeller)
    model = LABELLERS[labeller]
    data_dir = Path(data_dir)
    raw_dir = data_dir / "silver" / "raw" / labeller
    raw_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"done": [], "skipped": [], "failed": {}}
    items = items_for(data_dir, set_name)
    if posting_id is not None:
        items = [i for i in items if i["posting_id"] == posting_id]
    for item in items:
        pid = item["posting_id"]
        if _stored(data_dir, labeller, pid) and not force:
            summary["skipped"].append(pid)
            continue
        if limit is not None and len(summary["done"]) + len(summary["failed"]) >= limit:
            break
        prompt = render_prompt(item)
        sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
        current = prompt
        error = ""
        usage: list[dict] = []
        for _ in range(2):
            try:
                try:
                    reply = call(model, current)
                except CallError as e:
                    usage.append(e.usage or make_usage())
                    raise
                if isinstance(reply, str):
                    reply = Reply(reply, make_usage())
                usage.append(reply.usage)
                (raw_dir / f"{pid}.txt").write_text(reply.text, encoding="utf-8")
                answer = validate(extract_json(reply.text), item)
            except Exception as e:  # one bad posting must not stop the run
                error = str(e)
                current = (
                    f"{prompt}\n\nYour previous answer was invalid: {error}\n"
                    "Reply again with the corrected JSON only."
                )
                continue
            _store(data_dir, labeller, item, answer, sha, usage)
            summary["done"].append(pid)
            break
        else:
            summary["failed"][pid] = error
    return summary


def check_labeller(labeller: str, data_dir: Path, set_name: str = "pilot") -> dict:
    """Validate and store answers another process wrote to silver/raw/<labeller>/<pid>.json|txt."""
    data_dir = Path(data_dir)
    raw_dir = data_dir / "silver" / "raw" / labeller
    summary: dict[str, Any] = {"done": [], "missing": [], "failed": {}}
    for item in items_for(data_dir, set_name):
        pid = item["posting_id"]
        path = next(
            (p for p in (raw_dir / f"{pid}.json", raw_dir / f"{pid}.txt") if p.exists()), None
        )
        if path is None:
            summary["missing"].append(pid)
            continue
        try:
            answer = validate(extract_json(path.read_text(encoding="utf-8")), item)
        except Exception as e:
            summary["failed"][pid] = str(e)
            continue
        sha = hashlib.sha256(render_prompt(item).encode("utf-8")).hexdigest()
        _store(data_dir, labeller, item, answer, sha)
        summary["done"].append(pid)
    return summary


def status(data_dir: Path, set_name: str = "pilot") -> dict[str, int]:
    pids = [i["posting_id"] for i in items_for(data_dir, set_name)]
    return {lab: sum(_stored(Path(data_dir), lab, pid) for pid in pids) for lab in LABELLERS} | {
        "total": len(pids)
    }
