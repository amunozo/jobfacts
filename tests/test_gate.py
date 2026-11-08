"""The CI accuracy gate: encrypted bundle, replay of recorded answers, thresholds, CLI.

All data here is invented; nothing calls a model or the database.
"""

import json
from pathlib import Path

import pytest

from fakes import FakeJev
from jobfacts import cli, gate
from jobfacts.extract import jev, rules
from jobfacts.extract.spec import FIELDS

DESC_A = (
    "We build machine learning systems in Zürich. You will train models. "
    "We need 3 years of experience. Fluent German is required. This is a hybrid role."
)
DESC_B = (
    "We build machine learning systems in Zürich. You will train models. "
    "We need 3 years of experience. This is a hybrid role."
)  # names no language: the rule must turn a guessed German level into not_mentioned


def item(pid: int, description: str) -> dict:
    return {
        "posting_id": pid,
        "language": "en",
        "group": "en_ai",
        "origin": "board",
        "title": "Machine Learning Engineer",
        "location_raw": "Zürich, Switzerland",
        "description_text": description,
    }


def label(german: str) -> dict:
    fields = {name: {"value": next(iter(FIELDS[name].values))} for name in FIELDS}
    fields.update(
        work_mode={"value": "hybrid"},
        german={"value": german},
        french={"value": "not_mentioned"},
        min_years={"value": "3"},
        title_level={"value": "not_stated"},
    )
    return {"fields": fields, "office_cantons": ["ZH"], "office_places": ["place:261:Zürich"]}


ITEMS = [item(1, DESC_A), item(2, DESC_B)]
REFERENCE = {1: label("required_fluent"), 2: label("not_mentioned")}
# The fake model guesses the same answers for both postings (German required, hybrid, 3 years).
PICK = {
    "v_work_mode": "hybrid",
    "v_german": "required_fluent",
    "v_french": "not_mentioned",
    "v_min_years": "3",
    "v_title_level": "not_stated",
}


def fake_client() -> FakeJev:
    return FakeJev(PICK, noul=0.0, noul_pick={"c_ZH": 0.9})


@pytest.fixture
def bundle() -> dict:
    return gate.record(ITEMS, REFERENCE, fake_client(), today="2026-11-08", workers=1)


@pytest.fixture
def paths(tmp_path, monkeypatch) -> Path:
    """The gate's files in a temporary folder, and a key in the environment."""
    monkeypatch.setattr(gate, "BUNDLE_PATH", tmp_path / "ci" / "holdout_bundle.enc")
    monkeypatch.setattr(gate, "BASELINE_PATH", tmp_path / "ci" / "accuracy_baseline.json")
    monkeypatch.setattr(gate, "PLAIN_BUNDLE_PATH", tmp_path / "private" / "gate_bundle.json")
    monkeypatch.setenv(gate.KEY_ENV, gate.new_key())
    return tmp_path


# ----- encryption -------------------------------------------------------------------------


def test_encrypt_round_trip_and_wrong_key():
    key = gate.new_key()
    token = gate.encrypt(b'{"title": "secret title"}', key)
    assert b"secret title" not in token
    assert gate.decrypt(token, key) == b'{"title": "secret title"}'
    with pytest.raises(gate.GateError, match="wrong key"):
        gate.decrypt(token, gate.new_key())
    with pytest.raises(gate.GateError, match="wrong key"):
        gate.decrypt(token[:-4] + b"AAAA", key)  # a damaged file is refused, not misread
    with pytest.raises(gate.GateError, match="not a valid"):
        gate.decrypt(token, "not-a-key")


def test_keygen_writes_mode_600_and_does_not_overwrite(tmp_path):
    path = gate.write_key(tmp_path / "key.txt")
    assert path.stat().st_mode & 0o777 == 0o600
    first = path.read_text()
    with pytest.raises(gate.GateError, match="--force"):
        gate.write_key(path)
    assert path.read_text() == first
    gate.write_key(path, force=True)
    assert path.read_text() != first


def test_bundle_file_round_trip(paths, bundle):
    key = gate.load_key()
    gate.write_bundle(bundle, key)
    assert gate.PLAIN_BUNDLE_PATH.stat().st_mode & 0o777 == 0o600
    assert gate.load_bundle(key) == bundle
    assert b"Machine Learning Engineer" not in gate.BUNDLE_PATH.read_bytes()


# ----- record and replay ------------------------------------------------------------------


def test_record_keeps_input_answers_and_reference(bundle):
    assert bundle["meta"]["n"] == 2 and bundle["meta"]["model"] == jev.MODEL
    assert bundle["meta"]["question_version"] == gate.current_question_version()
    rec = bundle["postings"][0]
    assert rec["input"]["title"] == "Machine Learning Engineer"
    assert rec["input"]["sentences"][0]["id"] == 1
    answers = rec["response"]["answers"]
    assert answers["v_german"]["choice"] == "required_fluent"
    assert "probabilities" in answers["s_german_0"] and answers["c_ZH"] == {"noul": 0.9}
    assert rec["response"]["usage"] == {"input_tokens": 1000, "output_tokens": 5}
    assert rec["reference"]["fields"]["german"] == {"value": "required_fluent"}
    # the text of the posting is in the input only; the reference holds values, no quotes
    assert "note" not in json.dumps(rec["reference"])


def test_record_refuses_without_reference_and_on_failed_call():
    with pytest.raises(gate.GateError, match="no reference label"):
        gate.record(ITEMS, {1: REFERENCE[1]}, fake_client(), workers=1)

    class Broken:
        def system_one(self, **kwargs):
            raise RuntimeError("down")

    with pytest.raises(gate.GateError, match="2 of 2 calls failed"):
        gate.record(ITEMS, REFERENCE, Broken(), workers=1)


def test_replay_applies_the_rules_and_scores(bundle):
    scored = gate.replay(bundle)
    f = scored["fields"]
    # posting 2 guessed German, but its text names no language: rule 4 corrects it
    assert f["german"] == {"accuracy": 100.0, "n": 2, "correct": 2}
    assert f["work_mode"]["accuracy"] == 100.0
    assert f["min_years"]["accuracy"] == 100.0
    assert f["office_cantons"] == {"accuracy": 100.0, "n": 2, "correct": 2}
    assert f["office_places"]["accuracy"] == 100.0
    assert f["seniority"]["accuracy"] == 100.0
    # the label of contract_type is the first value, which the fake model also picks first
    assert f["contract_type"]["accuracy"] == 100.0
    assert scored["split_changed"] == 0
    assert not any(scored["changed"].values())  # same code as at recording time


def test_replay_scores_a_wrong_answer(bundle):
    bundle["postings"][0]["reference"]["fields"]["work_mode"] = {"value": "remote"}
    f = gate.replay(bundle)["fields"]
    assert f["work_mode"] == {"accuracy": 50.0, "n": 2, "correct": 1}


def test_replay_reports_answers_that_changed_since_recording(bundle, monkeypatch):
    monkeypatch.setattr(rules, "_require_language_named", lambda p, result: None)
    scored = gate.replay(bundle)
    assert scored["changed"]["german"] == 1
    assert scored["fields"]["german"]["accuracy"] == 50.0


def test_replay_uses_recorded_sentences(bundle):
    bundle["postings"][0]["input"]["sentences"][0]["text"] = "Another sentence."
    assert gate.replay(bundle)["split_changed"] == 1


# ----- thresholds -------------------------------------------------------------------------


def test_threshold_two_points_passes_and_two_point_one_fails():
    base = {"german": {"accuracy": 96.0, "n": 50}}
    row = gate.compare(base, {"german": {"accuracy": 94.0}})[0]
    assert row[3] == -2.0 and row[4] is True
    row = gate.compare(base, {"german": {"accuracy": 93.9}})[0]
    assert row[3] == -2.1 and row[4] is False
    assert gate.compare(base, {"german": {"accuracy": 99.0}})[0][4] is True  # improvement
    assert gate.compare(base, {"german": {"accuracy": None}})[0][4] is False  # cannot be scored
    assert gate.compare(base, {})[0][4] is False  # field gone


def test_render_table_marks_failures():
    rows = gate.compare({"german": {"accuracy": 96.0}}, {"german": {"accuracy": 90.0}})
    text = gate.render_table(rows)
    assert "german" in text and "96.00" in text and "90.00" in text and "FAIL" in text


# ----- spec hash --------------------------------------------------------------------------


def test_spec_mismatch_refuses_with_rerecord_message(bundle, monkeypatch):
    gate.check_spec(bundle)  # same questions: fine
    monkeypatch.setattr(jev, "GENERAL_RULES", jev.GENERAL_RULES + " Changed question.")
    with pytest.raises(gate.GateError, match="Re-record"):
        gate.check_spec(bundle)


def test_code_version_is_not_part_of_the_question_version(bundle, monkeypatch):
    monkeypatch.setattr(jev, "EXTRACTION_CODE_VERSION", jev.EXTRACTION_CODE_VERSION + 1)
    gate.check_spec(bundle)  # the gate exists to test exactly this kind of change


# ----- command line -----------------------------------------------------------------------


def test_cli_missing_key(monkeypatch, capsys):
    monkeypatch.delenv(gate.KEY_ENV, raising=False)
    assert cli.main(["gate", "check", "--skip-without-key"]) == 0
    assert "skipped" in capsys.readouterr().out
    assert cli.main(["gate", "check"]) == 2
    assert "--skip-without-key" in capsys.readouterr().out


def test_cli_check_passes_then_fails_when_a_rule_is_broken(paths, bundle, monkeypatch, capsys):
    gate.write_bundle(bundle, gate.load_key())
    assert cli.main(["gate", "check"]) == 2  # no baseline yet
    assert "no " in capsys.readouterr().out
    assert cli.main(["gate", "check", "--write-baseline"]) == 0
    baseline = json.loads(gate.BASELINE_PATH.read_text())
    assert baseline["fields"]["german"] == {"accuracy": 100.0, "n": 2}
    assert cli.main(["gate", "check"]) == 0
    assert "gate passed" in capsys.readouterr().out
    # break a rule (in this test only): the guessed German level is no longer corrected
    monkeypatch.setattr(rules, "_require_language_named", lambda p, result: None)
    assert cli.main(["gate", "check"]) == 1
    out = capsys.readouterr().out
    assert "GATE FAILED: german" in out and "-50.00" in out


def test_cli_check_refuses_other_questions_wrong_key_and_missing_bundle(
    paths, bundle, monkeypatch, capsys
):
    assert cli.main(["gate", "check"]) == 2  # no bundle
    assert "gate record" in capsys.readouterr().out
    gate.write_bundle(bundle, gate.load_key())
    monkeypatch.setenv(gate.KEY_ENV, gate.new_key())
    assert cli.main(["gate", "check"]) == 2
    assert "wrong key" in capsys.readouterr().out


def test_cli_check_with_changed_questions(paths, bundle, monkeypatch, capsys):
    gate.write_bundle(bundle, gate.load_key())
    monkeypatch.setattr(jev, "GENERAL_RULES", jev.GENERAL_RULES + " Changed question.")
    assert cli.main(["gate", "check"]) == 2
    assert "Re-record" in capsys.readouterr().out


# ----- what is in the repository ----------------------------------------------------------


def test_plain_bundle_and_key_live_under_the_ignored_private_folder():
    private = gate.REPO_ROOT / "data" / "private"
    assert gate.PLAIN_BUNDLE_PATH.parent == private and gate.KEY_PATH.parent == private
    ignored = (gate.REPO_ROOT / ".gitignore").read_text().splitlines()
    assert "data/private/" in ignored


def test_ci_folder_holds_only_the_encrypted_bundle_and_the_baseline():
    ci = gate.REPO_ROOT / "ci"
    if not ci.exists():
        pytest.skip("no ci/ folder yet")
    assert {p.name for p in ci.iterdir()} <= {"holdout_bundle.enc", "accuracy_baseline.json"}
    enc = ci / "holdout_bundle.enc"
    if enc.exists():
        assert enc.read_bytes().startswith(b"gAAAA")  # a Fernet token, not readable JSON
    baseline = ci / "accuracy_baseline.json"
    if baseline.exists():
        doc = json.loads(baseline.read_text())
        assert set(doc) == {
            "date", "model", "code_version", "question_version", "n_postings", "fields",
        }  # fmt: skip
        for name, entry in doc["fields"].items():
            assert name in gate.GATE_FIELDS
            assert set(entry) == {"accuracy", "n"}
            assert isinstance(entry["accuracy"], int | float) and isinstance(entry["n"], int)
