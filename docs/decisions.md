# Design decisions

One entry per decision: what, why, and which alternatives were rejected.
Newest at the bottom.

## 2026-11-02: Fixed allowed values for every extracted field
- **What:** each field takes one value from a closed list (see `fields.md`).
- **Why:** answers can be compared exactly with hand labels, so accuracy is a
  number; output can be validated against a schema; search filters need
  fixed values.
- **Rejected:** free-text answers (no exact accuracy, no reliable filters).

## 2026-11-02: Every value carries an exact quote from the posting
- **What:** the model returns the sentence(s) that support each value; code
  checks the quote appears verbatim in the posting.
- **Why:** the owner can check any answer in seconds (also in German, which
  the owner reads at B1), and an invented quote is caught automatically.
- **Rejected:** values without evidence (unverifiable); paraphrased evidence
  (cannot be checked by code).

## 2026-11-02: "not stated" instead of defaults
- **What:** a missing fact is `not_stated` / `not_mentioned`, never a default.
- **Why:** the old job-search labels used "no level means mid", which mixes
  what the posting says with what was assumed. Keeping them apart lets search
  show "check first" honestly and keeps accuracy meaningful.

## 2026-11-02: Location split into three fields
- **What:** `work_mode`, `office_locations`, `remote_from_switzerland`
  instead of one `location` value.
- **Why:** each is a separate search filter and gets its own accuracy number,
  so errors are traced to the exact question the model gets wrong.
- **Rejected:** one combined value (as in the job-search logbook): mixes
  three questions, and multi-location postings do not fit one value.

## 2026-11-02: Strict remote-from-Switzerland rule
- **What:** `allowed` only when Switzerland is named or "anywhere in the
  world"; "Europe/EU/EMEA/CET" is `unclear`.
- **Why:** many "remote Europe" roles hire only through EU entities, and
  Switzerland is not in the EU. A false "allowed" wastes an application.

## 2026-11-02: Language levels, including "level not stated" and "either"
- **What:** `german` and `french` each take `required_fluent`,
  `required_intermediate`, `required_level_not_stated`, `either_required`,
  `nice_to_have` or `not_mentioned`.
- **Why:** the owner's blocker is C1 German. "German required" without a
  level could be B1 or C2, so it must not be merged with fluent.
  "German or French" is a real requirement that would disappear if both were
  marked `nice_to_have`.
- **Rejected:** a yes/no "German required" field (loses the level);
  inferring a requirement from the posting's language (that is a guess;
  posting language is stored separately, measured by code).
- **Deferred:** English and Italian (recorded under "Later").

## 2026-11-02: Seniority from years first, then title
- **What:** stated years decide (0-1 junior, 2-4 mid, 5+ senior); otherwise
  the title; otherwise `not_stated`. Staff/principal/lead/head-of titles are
  always `staff_principal`.
- **Why:** titles inflate ("Senior" with 2 years); years are the more
  concrete requirement. Staff level is about scope, which years do not show.
- **Rejected:** title only (the problem swissaijob.ch has); a separate
  `manager` value (not needed for the owner's search yet).

## 2026-11-02: Quotes as chosen sentences
- **What:** code numbers the posting's sentences; the model answers with
  sentence numbers; code copies the text.
- **Why:** quotes are verbatim by construction, and it works for models that
  cannot generate text (Jev).
- **Rejected:** model-written quotes as the main design (they can be
  invented; still accepted from LLMs, with a verbatim check).

## 2026-11-02: Fixed location list and salary brackets
- **What:** office locations come from a fixed list of cantons and cities;
  salaries (Milestone 3) will be chosen from brackets, not free numbers.
- **Why:** turns open extraction into choices, which can be scored exactly
  and suits classifier models such as Jev, which are weak with numbers.

## 2026-11-02: Seniority read as years plus title level
- **What:** the model picks `min_years` and `title_level`; code applies
  "years win" and the staff/principal exception.
- **Why:** the rule lives in tested code, not in the prompt; models are
  weaker at arithmetic and thresholds than at reading.

## 2026-11-02: Compare Jev, DeepSeek and other cheap models in Milestone 1
- **What:** run the same 100 labelled postings through Jev (TypeSafe), a
  DeepSeek model and at least one other cheap LLM; compare accuracy per
  field and language, cost and speed.
- **Why:** Jev is far cheaper and faster but weaker outside English by its
  own docs; only measurement on our labels shows whether it is good enough.
  Running all models costs cents.
- **Rejected:** choosing one model up front.
