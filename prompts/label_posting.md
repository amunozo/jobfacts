# Reference labels for a job posting

You are labelling one job posting to build a reference test set. The labels you
give are the answer key against which automatic extraction systems will be
scored, so accuracy matters far more than speed. Read the whole posting before
answering. The posting may be in English, German or French. A person who may not
read the posting's language well will check your labels using only the sentences
you cite and your English translations of them, so cite the sentences that decide
each value and translate them faithfully (for English sentences, repeat them).

Other annotators label the same posting independently. Do not try to guess
what is "usual" for such postings: label only what this posting states.

## Rules

Follow the field guide below exactly. The most important rules:

- **No guessing.** If the posting does not state a fact, use `not_stated` (or
  `not_mentioned` for languages, `not_applicable` where the guide says so), with
  no sentences. The language a posting is written in does not by itself mean
  that language is required.
- **Years of experience only when a number is stated.** A degree (MSc, PhD) is
  not converted into years. "PhD or 3 years of experience" gives 3. "MSc or PhD,
  or equivalent industry experience" gives `not_stated`. A range gives its
  minimum. `0` only when the posting says no experience at all is needed;
  waiving only some experience ("no prior AI experience required") is
  `not_stated`.
- **`title_level` comes from the title line (T) only.** An apprenticeship
  (Lehrstelle, apprentissage) counts as `internship`.
  A level in brackets counts ("(Senior) Consultant" is `senior`). "Partner" is
  `staff_principal`. Member of Technical/Product Staff, Research Staff, Postdoc,
  plain Researcher, Expert, Resident, "Experienced ..." and Group Product
  Manager state no level: `not_stated`.
- **Work mode needs a statement.** An office location with nothing said about
  home or remote working is `not_stated`, not `onsite`.
- **Office cantons:** only places where this role works. Company history,
  customers, other sites mentioned in passing and travel do not count. The
  location line (L) counts.
- **Remote regions:** a country or region where a remote worker may live
  ("Remote - United States", "Remote (Europe)") is not an office. Office cities
  of the home country next to "remote" ("Zürich / Remote") name no region:
  `remote_from_switzerland` is `not_stated`. A named region ("Remote (Europe)")
  wins over perk wording such as "work from anywhere": `unclear`. A Swiss office
  or employer alone does not make it `allowed`.

## Field guide

{fields_md}

## Swiss cantons (code: names; some places)

{cantons}

## Relevance

`relevant` is `"true"` if the main day-to-day work of the role itself is AI,
machine learning, data science, data engineering (including analytics engineering
and web scraping / data acquisition), SQL or statistical data analysis, or AI/ML
or computational research; or software or infrastructure engineering on ML/AI
systems (including model-training infrastructure); or product management of an
ML/AI product; or solutions architecture or forward-deployed / deployed
engineering on an AI product; or building AI evaluations. Judge the role's own
tasks, not the employer's business: sales, account, customer-success, support,
recruiting, marketing, design, operations, finance, billing, legal and
data-centre roles are `"false"` even at an AI company. Business-intelligence,
reporting, market-insight and revenue-planning analyst roles (dashboards, Power
BI) are `"false"`. `"unsure"` only if the posting is too vague to tell.

## The posting

The posting is shown as numbered lines: T = title, L = location field of the job
board, S001, S002, ... = sentences of the description. Cite lines by these ids.

{posting}

## Answer format

Reply with one JSON object and nothing else:

```json
{
  "work_mode":               {"value": "...", "sentences": ["S004"], "translation": "...", "confidence": "high", "note": ""},
  "remote_from_switzerland": {"value": "...", "sentences": [], "translation": "", "confidence": "high", "note": ""},
  "german":                  {"value": "...", "sentences": ["S031"], "translation": "...", "confidence": "high", "note": ""},
  "french":                  {"value": "...", "sentences": [], "translation": "", "confidence": "high", "note": ""},
  "min_years":               {"value": "...", "sentences": ["S027"], "translation": "...", "confidence": "low", "note": "..."},
  "title_level":             {"value": "...", "sentences": ["T"], "translation": "...", "confidence": "high", "note": ""},
  "contract_type":           {"value": "...", "sentences": [], "translation": "", "confidence": "high", "note": ""},
  "office_cantons":          {"value": ["BL"], "sentences": ["L"], "translation": "...", "confidence": "high", "note": ""},
  "relevant":                {"value": "false", "reason": "one short sentence in English"}
}
```

- `value`: exactly one allowed value from the field guide (a list of canton codes
  for `office_cantons`; empty list = not stated).
- `sentences`: the ids of the line(s) that decide the value; empty exactly when
  the value means not stated / not mentioned / not applicable.
- `translation`: a faithful English translation of the cited lines, in order,
  separated by " / ". Empty when there are no sentences.
- `confidence`: `"high"` or `"low"`. Use `"low"` when the wording is ambiguous or
  the rules do not clearly decide the case.
- `note`: empty, or one short English sentence explaining a `"low"` confidence
  or a judgement call.
