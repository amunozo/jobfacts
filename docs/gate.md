# Accuracy gate in CI

## What a regression gate is

A regression gate is an automatic check that fails the build when a change makes
the product worse. Here "worse" means: one of the extracted fields (work mode,
German requirement, office cantons, ...) is less accurate on a fixed set of
postings than it was before. The gate runs in GitHub Actions after the tests,
so a change to the code rules that quietly breaks a field is caught before it
is merged.

## What it measures, and what it does not

Extraction has two parts: the model (Jev) answers the questions, and code rules
then correct or complete those answers (the title level, the "language must be
named" rule, the canton hints, the places, the seniority). The gate tests the
second part. It stores the model's raw answers once, and on every run it
re-reads them with the current code (`JevExtractor._read`, which applies the
rules, then the place step) and scores the result against the reference labels
with the same functions as the held-out report (`jobfacts.evaluate`).

- It measures changes to the code rules on fixed answers.
- It does not measure model drift: the model is never called in CI, so a change
  in how Jev answers cannot show up here. That needs a new recording, or the
  normal held-out report.
- Fields covered (10): `work_mode`, `remote_from_switzerland`, `office_cantons`,
  `office_places`, `german`, `french`, `min_years`, `title_level`,
  `contract_type`, `seniority`. `relevant` is not covered: it comes from a
  separate relevance call, not from the extraction answers.
- On 50 postings one posting is 2 points, so one changed answer passes (a drop
  of exactly 2.0 passes) and two fail. A rule change that moves one posting is
  noise level; the gate is meant for real breakage.

## What is encrypted, and why

The repository is public, but the held-out postings and their labels are
private (`data/private/`, git-ignored). The gate needs both in CI, so they are
stored encrypted in `ci/holdout_bundle.enc`:

- per posting: the text the model saw (title, location, description, sentences),
  the raw System One response (every choice, probability, confidence, Noul value,
  sentence-chunk answer, usage, model), and the reference label values;
- metadata: date, model, the hash of the questions, the code version at recording.

The encryption is Fernet from the `cryptography` package (AES-128-CBC with an
HMAC, so a wrong key or a changed file is refused, never misread). The key is the
GitHub secret `JOBFACTS_GATE_KEY`; the local copy is `data/private/gate_key.txt`
(mode 600, git-ignored). The plain bundle is written only to
`data/private/gate_bundle.json`. `ci/` holds nothing else but the encrypted
bundle and `ci/accuracy_baseline.json`, which has only per-field accuracy
numbers, the number of postings, the date, the model and the code version (a
test checks this). The check prints no posting ids, texts or labels, because
CI logs of a public repository are public; it prints counts only.

## Commands

```bash
uv run jobfacts gate keygen                  # new key -> data/private/gate_key.txt (not printed)
uv run jobfacts gate record                  # one Jev call per held-out posting (~$0.02)
JOBFACTS_GATE_KEY=$(cat data/private/gate_key.txt) uv run jobfacts gate check --write-baseline
JOBFACTS_GATE_KEY=$(cat data/private/gate_key.txt) uv run jobfacts gate check
gh secret set JOBFACTS_GATE_KEY < data/private/gate_key.txt    # once
```

`check` exit codes: 0 passed (or skipped, see below), 1 a field dropped by more
than 2.0 points, 2 it cannot run (no key, no bundle or baseline, wrong key, other
questions). Without `JOBFACTS_GATE_KEY` it exits 2, unless `--skip-without-key`
is given: then it prints a notice and exits 0. CI uses that flag because GitHub
does not give secrets to pull requests from forks. Besides the table (field,
baseline, now, delta) it prints how many answers differ from the ones at
recording time, per field: that is the quickest way to see which rule moved.

## Re-recording after changing the questions

The bundle is only valid for the questions it was recorded with. `check` compares
the hash of the questions (`JevExtractor.question_version()`: the general rules,
every field question and value, the canton questions, the model name, the prompt
templates) with the one in the bundle and refuses with a "re-record" message if
they differ. `EXTRACTION_CODE_VERSION` is left out of that hash on purpose: it is
bumped when the code rules change, which is exactly what the gate must test.

To re-record: change the questions, run `gate record`, then `gate check
--write-baseline`, look at the table, and commit `ci/holdout_bundle.enc` and
`ci/accuracy_baseline.json`. Do the same when a drop is a deliberate trade-off
(a rule that fixes one field and costs another): write the new baseline in the
same commit and say why in the message. The sentence splitter is not part of the
hash; the gate replays the recorded sentences and warns if today's splitter
would cut a posting differently.

## Rotating the key

```bash
cp data/private/gate_key.txt data/private/gate_key.old
uv run jobfacts gate keygen --force
uv run jobfacts gate encrypt          # re-encrypts data/private/gate_bundle.json, no model call
gh secret set JOBFACTS_GATE_KEY < data/private/gate_key.txt
```

Commit the new `ci/holdout_bundle.enc`. Old commits still hold bundles encrypted
with the old key, so rotating does not hide what that key could already read; it
only protects bundles recorded afterwards. If the key leaked, the postings in the
bundle (public job ads) and their labels are what is exposed; no API keys are in it.
