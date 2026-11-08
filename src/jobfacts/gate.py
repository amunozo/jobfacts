"""Accuracy gate for CI: replay recorded model answers through today's code rules.

`jobfacts gate record` asks Jev once per held-out posting and stores the raw answers next to
the reference labels. The bundle is written in plain text under `data/private/` (git-ignored)
and encrypted to `ci/holdout_bundle.enc` (Fernet; the key is a GitHub secret). `jobfacts gate
check` decrypts it, re-reads every recorded answer with the current code (`JevExtractor._read`,
which applies the code rules, then the place step), scores it with the same functions as the
held-out report and compares each field with `ci/accuracy_baseline.json`. No model is called, so
the check is free and deterministic: it measures changes to the code rules on fixed answers, not
drift of the model. See docs/gate.md.
"""

import json
import os
import zlib
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from jobfacts import sentences as splitter
from jobfacts.evaluate import SCORED_FIELDS, score_records, summarize
from jobfacts.extract import jev
from jobfacts.extract.base import Result
from jobfacts.extract.posting import PostingText
from jobfacts.extract.run import add_places
from jobfacts.testset import PRIVATE_DIR

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_DIR = REPO_ROOT / "ci"
BUNDLE_PATH = CI_DIR / "holdout_bundle.enc"
BASELINE_PATH = CI_DIR / "accuracy_baseline.json"
PLAIN_BUNDLE_PATH = PRIVATE_DIR / "gate_bundle.json"
KEY_PATH = PRIVATE_DIR / "gate_key.txt"
KEY_ENV = "JOBFACTS_GATE_KEY"
FORMAT = 1
MAX_DROP = 2.0  # points of accuracy; a drop of exactly this much still passes
# Everything the held-out report scores except `relevant`: that one comes from a separate
# relevance call (`jev.relevance_probability`), not from the extraction answers `_read` reads.
GATE_FIELDS = [f for f in SCORED_FIELDS if f != "relevant"]
RECORDED = ("values", "office_cantons", "office_places")  # what replay should reproduce


class GateError(Exception):
    """A gate problem the user can fix (message is printed as is)."""


# ----- key and encryption ----------------------------------------------------------------


def new_key() -> str:
    from cryptography.fernet import Fernet

    return Fernet.generate_key().decode()


def write_key(path: Path = KEY_PATH, force: bool = False) -> Path:
    """Write a new key to `path` with mode 600 and return the path. The key is never returned
    or printed. Refuses to replace an existing key unless `force`."""
    path = Path(path)
    if path.exists() and not force:
        raise GateError(f"{path} already exists; use --force to replace it (rotating the key)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(new_key() + "\n")
    path.chmod(0o600)
    return path


def encrypt(data: bytes, key: str) -> bytes:
    from cryptography.fernet import Fernet

    try:
        return Fernet(key.strip().encode()).encrypt(zlib.compress(data, 9))
    except ValueError as error:
        raise GateError(
            "the gate key is not a valid Fernet key; run `jobfacts gate keygen`"
        ) from error


def decrypt(token: bytes, key: str) -> bytes:
    from cryptography.fernet import Fernet, InvalidToken

    try:
        return zlib.decompress(Fernet(key.strip().encode()).decrypt(token))
    except InvalidToken as error:
        raise GateError("cannot decrypt the bundle: wrong key or damaged file") from error
    except ValueError as error:
        raise GateError("the gate key is not a valid Fernet key") from error


def load_key(env: dict[str, str] | None = None) -> str | None:
    key = (os.environ if env is None else env).get(KEY_ENV, "").strip()
    return key or None


def key_for_writing() -> str:
    """The key for `record` / `encrypt`: the environment variable, else the local key file."""
    key = load_key()
    if key:
        return key
    if KEY_PATH.exists():
        return KEY_PATH.read_text(encoding="utf-8").strip()
    raise GateError(f"no key: set {KEY_ENV} or run `jobfacts gate keygen`")


# ----- responses: serialise and replay ---------------------------------------------------


def serialize_response(response: Any) -> dict:
    """A System One response as plain JSON, with everything `JevExtractor._read` uses."""
    answers: dict[str, dict] = {}
    for name, a in response.answers.items():
        if hasattr(a, "choice"):
            answers[name] = {
                "choice": a.choice,
                "probabilities": dict(a.probabilities),
                "confidence": a.confidence,
            }
        else:
            answers[name] = {"noul": a.noul}
    out: dict[str, Any] = {
        "answers": answers,
        "usage": {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
        },
        "model": getattr(response, "model", None),
    }
    if route := getattr(response, "route", None):
        out["route"] = route
    return out


def deserialize_response(data: dict) -> SimpleNamespace:
    answers = {name: SimpleNamespace(**a) for name, a in data["answers"].items()}
    return SimpleNamespace(
        answers=answers,
        usage=SimpleNamespace(**data["usage"]),
        model=data.get("model"),
        route=data.get("route"),
    )


class RecordingClient:
    """Wraps a client and keeps the last response, so the raw answers can be stored."""

    def __init__(self, client: Any) -> None:
        self.client, self.response = client, None

    def system_one(self, **kwargs: Any) -> Any:
        self.response = self.client.system_one(**kwargs)
        return self.response


def _input_of(p: PostingText) -> dict:
    return {
        "title": p.title,
        "location_raw": p.location_raw,
        "description": p.description,
        "sentences": [
            {"id": s.id, "start": s.start, "end": s.end, "text": s.text} for s in p.sentences
        ],
    }


def posting_from_input(posting_id: int, data: dict) -> tuple[PostingText, bool]:
    """The PostingText the answers were given for. The sentences are the recorded ones (what
    the model saw: the answers name them by number); the second value tells whether today's
    sentence splitter would split the description differently."""
    p = PostingText.build(posting_id, data["title"], data["location_raw"], data["description"])
    recorded = [splitter.Sentence(**s) for s in data["sentences"]]
    changed = [(s.id, s.text) for s in p.sentences] != [(s.id, s.text) for s in recorded]
    p.sentences = recorded
    return p, changed


def read_answers(p: PostingText, response: dict, extractor: Any = None) -> dict:
    """Replay: read one recorded response with the current code (rules and places included).
    Returns the extraction in the form the accuracy report scores. A crash of the code is an
    error row (counted as wrong), as a failed call is in the real run."""
    extractor = extractor or jev.JevExtractor(client=object())
    try:
        result = extractor._read(p, deserialize_response(response), Result())
        add_places(p, result)
    except (KeyError, TypeError, AttributeError, ValueError) as error:
        return {"error": f"{type(error).__name__}: {error}", "values": {}, "office_cantons": []}
    return {
        "error": result.error,
        "values": result.values,
        "office_cantons": result.office_cantons,
        "office_places": result.office_places,
    }


# ----- record ----------------------------------------------------------------------------


def reference_entry(label: dict) -> dict:
    """The part of a reference label the scoring needs (no notes, quotes or translations)."""
    return {
        "fields": {k: {"value": v["value"]} for k, v in label["fields"].items()},
        "office_cantons": label.get("office_cantons"),
        "office_places": label.get("office_places", []),
    }


def record(
    items: list[dict],
    reference: dict[int, dict],
    client: Any,
    today: str | None = None,
    workers: int = 6,
) -> dict:
    """Ask Jev once per held-out posting and return the bundle (a plain dict).

    `items`: lines of `holdout_set.jsonl`; `reference`: `reference.load_reference(dir,
    "holdout")` (silver labels with the owner's decisions applied). Raises GateError, and
    stores nothing, if a call fails or a posting has no reference label."""
    missing = [i["posting_id"] for i in items if i["posting_id"] not in reference]
    if missing:
        raise GateError(f"no reference label for {len(missing)} held-out postings")

    def one(item: dict) -> tuple[dict, Result, Any, PostingText]:
        p = PostingText.build(
            item["posting_id"], item["title"], item["location_raw"], item["description_text"]
        )
        recorder = RecordingClient(client)
        result = jev.JevExtractor(recorder).extract(p)
        add_places(p, result)
        return item, result, recorder.response, p

    postings, failed, cost = [], 0, 0.0
    with ThreadPoolExecutor(workers) as pool:
        for item, result, response, p in pool.map(one, items):
            if result.error or response is None:
                failed += 1
                continue
            cost += result.cost_usd
            postings.append(
                {
                    "posting_id": item["posting_id"],
                    "language": item["language"],
                    "group": item["group"],
                    "origin": item["origin"],
                    "input": _input_of(p),
                    "response": serialize_response(response),
                    "reference": reference_entry(reference[item["posting_id"]]),
                    "at_record": {
                        "values": result.values,
                        "office_cantons": result.office_cantons,
                        "office_places": result.office_places,
                    },
                }
            )
    if failed:
        raise GateError(f"{failed} of {len(items)} calls failed; nothing stored")
    return {
        "format": FORMAT,
        "meta": {
            "recorded_on": today or date.today().isoformat(),
            "model": jev.MODEL,
            "question_version": jev.JevExtractor(client=object()).question_version(),
            "code_version": jev.EXTRACTION_CODE_VERSION,
            "n": len(postings),
            "cost_usd": round(cost, 6),
        },
        "postings": postings,
    }


def write_bundle(
    bundle: dict, key: str, plain_path: Path | None = None, enc_path: Path | None = None
) -> None:
    """Plain bundle under data/private/ (git-ignored), encrypted copy for the repository."""
    plain_path, enc_path = plain_path or PLAIN_BUNDLE_PATH, enc_path or BUNDLE_PATH
    data = json.dumps(bundle, ensure_ascii=False, separators=(",", ":")).encode()
    plain_path.parent.mkdir(parents=True, exist_ok=True)
    plain_path.write_bytes(data)
    plain_path.chmod(0o600)
    enc_path.parent.mkdir(parents=True, exist_ok=True)
    enc_path.write_bytes(encrypt(data, key))


def load_bundle(key: str, enc_path: Path | None = None) -> dict:
    enc_path = enc_path or BUNDLE_PATH
    if not enc_path.exists():
        raise GateError(f"no {enc_path}: run `jobfacts gate record`")
    bundle = json.loads(decrypt(enc_path.read_bytes(), key))
    if bundle.get("format") != FORMAT:
        raise GateError(f"bundle format {bundle.get('format')} is not {FORMAT}; re-record")
    return bundle


# ----- check -----------------------------------------------------------------------------


def current_question_version() -> str:
    return jev.JevExtractor(client=object()).question_version()


def check_spec(bundle: dict) -> None:
    """Refuse a bundle recorded for other questions: its answers would mean nothing."""
    recorded, now = bundle["meta"]["question_version"], current_question_version()
    if recorded != now:
        raise GateError(
            f"the questions changed since the bundle was recorded ({recorded} -> {now}): the "
            "recorded answers no longer answer today's questions. Re-record with `jobfacts "
            "gate record`, then `jobfacts gate check --write-baseline`, and commit "
            "ci/holdout_bundle.enc and ci/accuracy_baseline.json."
        )


def replay(bundle: dict) -> dict:
    """Score the recorded answers read with the current code. Returns `fields` (field ->
    {accuracy, n, correct}), `changed` (field -> number of postings whose answer differs from
    the one at recording time) and `split_changed` (postings the sentence splitter now cuts
    differently)."""
    items, labels, exts = [], {}, {}
    changed = dict.fromkeys(GATE_FIELDS, 0)
    split_changed = 0
    extractor = jev.JevExtractor(client=object())
    for rec in bundle["postings"]:
        pid = rec["posting_id"]
        p, split = posting_from_input(pid, rec["input"])
        split_changed += split
        ext = read_answers(p, rec["response"], extractor)
        items.append(
            {
                "posting_id": pid,
                "language": rec["language"],
                "group": rec["group"],
                "origin": rec["origin"],
            }
        )
        labels[pid] = {**rec["reference"], "relevant": None}
        exts[pid] = ext
        before = rec["at_record"]
        for f in GATE_FIELDS:
            if f == "seniority":
                continue  # follows min_years and title_level
            if f in ("office_cantons", "office_places"):
                same = set(ext.get(f, [])) == set(before[f])
            else:
                same = ext["values"].get(f) == before["values"].get(f)
            changed[f] += not same
    records, _ = score_records(items, labels, {"jev": exts}, {})
    fields = {}
    for f in GATE_FIELDS:
        k, n, _ = summarize(records, extractor="jev", field=f)
        fields[f] = {"accuracy": round(100 * k / n, 2) if n else None, "n": n, "correct": k}
    return {"fields": fields, "changed": changed, "split_changed": split_changed}


def compare(baseline: dict[str, dict], now: dict[str, dict], max_drop: float = MAX_DROP) -> list:
    """Rows (field, baseline, now, delta, ok). A field fails when it dropped by more than
    `max_drop` points, or when it is in the baseline but cannot be scored now."""
    rows = []
    for field, base in baseline.items():
        base_acc = base["accuracy"]
        now_acc = (now.get(field) or {}).get("accuracy")
        if now_acc is None:
            rows.append((field, base_acc, None, None, False))
            continue
        delta = round(now_acc - base_acc, 6)
        rows.append((field, base_acc, now_acc, delta, delta >= -max_drop))
    return rows


def baseline_document(meta: dict, scored: dict, today: str | None = None) -> dict:
    """The committed baseline: accuracy numbers and counts only, no labels or text."""
    return {
        "date": today or date.today().isoformat(),
        "model": meta["model"],
        "code_version": jev.EXTRACTION_CODE_VERSION,
        "question_version": meta["question_version"],
        "n_postings": meta["n"],
        "fields": {f: {"accuracy": v["accuracy"], "n": v["n"]} for f, v in scored.items()},
    }


def write_baseline(doc: dict, path: Path | None = None) -> None:
    path = path or BASELINE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")


def load_baseline(path: Path | None = None) -> dict:
    path = path or BASELINE_PATH
    if not path.exists():
        raise GateError(f"no {path}: run `jobfacts gate check --write-baseline` and commit it")
    return json.loads(path.read_text(encoding="utf-8"))


def render_table(rows: list, max_drop: float = MAX_DROP) -> str:
    def fmt(x: float | None) -> str:
        return "-" if x is None else f"{x:.2f}"

    lines = [
        f"{'field':<26}{'baseline':>10}{'now':>10}{'delta':>9}  status",
        "-" * 62,
    ]
    for field, base, now, delta, ok in rows:
        d = "-" if delta is None else f"{delta:+.2f}"
        status = "ok" if ok else f"FAIL (more than {max_drop} points below)"
        lines.append(f"{field:<26}{fmt(base):>10}{fmt(now):>10}{d:>9}  {status}")
    return "\n".join(lines)
