# Extraction fields (Milestone 1)

The single reference for what is extracted from each posting. The extraction
prompt, the output schema, the labelling app and the accuracy report all use
these names and values. Change a field here first, then everywhere else.

## Rules for every field

- **One value from the allowed list.** Nothing else is accepted; output that
  does not match the schema is retried or flagged, never stored.
- **Supporting quote.** Every value except `not_stated` / `not_mentioned`
  comes with the posting sentence(s) that support it. Code splits the posting
  into numbered sentences; the model returns sentence numbers, and code
  copies the text. Quotes are therefore always verbatim; whether the chosen
  sentence really supports the value is checked during labelling. (Generative
  LLMs that return quote text instead are checked by code: the quote must
  appear in the posting.)
- **No guessing.** When the posting does not state a fact, the value is
  `not_stated` (or `not_mentioned` for languages), with no quote. Defaults
  such as "no level means mid" are not allowed.
- **Measured by code, not the LLM:** posting language (`en`, `de`, `fr`,
  `other`). It is stored next to the fields and used to report accuracy per
  language, but it is not an extraction field.

## 1. Where the work happens

### `work_mode`
| Value | Meaning |
|---|---|
| `onsite` | Work at the office every day. |
| `hybrid` | Part office, part home. |
| `remote` | Fully remote (occasional travel or offsites still count as remote). |
| `not_stated` | The posting does not say. |

An office location with nothing said about home or remote working is
`not_stated`, not `onsite`: `onsite` needs a statement.

Decided cases (owner, 2026-11-03):
- Both options offered ("Zurich / Remote", "hybrid in Nuremberg or fully
  remote") → `remote`.
- Home working mentioned only as a benefit (home-office allowance, télétravail,
  "Möglichkeit für Home-Office") → `hybrid`.
- "Primarily on site", "grundsätzlich vom Office aus", "office-first" → `onsite`.
- Several offices on fixed days ("4 days Lausanne, 1 day Fribourg") → `onsite`.
- A tag such as #LI-HYBRID counts as a statement.
- When the job board's field and the description disagree, the description wins.

### `office_cantons`
A set of values: any of the 26 Swiss canton codes (`ZH`, `BE`, ... `JU`) and
`outside_switzerland` (an office outside Switzerland is named). The model decides which
cantons have an office or work location for this role, from the title, the job board's
location field and the description; a city, town or village counts for its canton. A
place mentioned only for other reasons (company history, customers, other roles, travel)
does not count, and neither does a planned future office ("a Zurich office in 2027"). **Empty set = not stated.** A non-empty set needs supporting
sentence(s); an empty one carries none. Accuracy is the exact match of the whole set
(main location metric), plus precision and recall per canton.

Decided cases: a remote role that only lists countries where the person
may live, with no office named, is an empty set. Code rule (2026-11-03): a canton the
model picked is dropped unless the posting names a place in that canton
(`PostingText.canton_hints()`); cantons are never added by code. An ambiguous place name
(Reinach is in BL and AG) that nothing in the posting resolves gets both
cantons.

### `office_places`
Not extracted by a model. Code looks for place names in the posting (location field,
title, description) and matches them against the official register of Swiss localities
(swisstopo, `config/swiss_places.csv`), **restricted to the cantons in `office_cantons`**.
Value: a set of place keys `place:<bfs_nr>:<name>` (`bfs_nr` = official commune number,
e.g. `place:261:Zürich`). Shown to the owner for confirmation while labelling and used
for search; secondary metric only (it is as good as the cantons).

### `remote_from_switzerland`
| Value | Meaning |
|---|---|
| `allowed` | Remote, and the posting names Switzerland as allowed, or says anywhere in the world or "globally". A worldwide statement wins over a list of countries. |
| `unclear` | Remote, but the region is only "Europe", "EU", "EMEA", a time zone (e.g. CET), or similar, without naming Switzerland. |
| `not_allowed` | Remote, but the posting lists countries without Switzerland, or allows only one other country. |
| `not_stated` | Remote, but the posting names no region at all. |
| `not_applicable` | The role is not remote (`work_mode` is `onsite`, `hybrid` or `not_stated`). |

Decided cases: a remote country list without Switzerland that includes
"EMEA" → `unclear`; "choose where you live" → `unclear`.

Decided cases (owner, 2026-11-03): "Remote (Europe)" plus "work from anywhere"
in the perks → `unclear` (a named region wins over perk wording). Office cities
of the home country next to "remote" ("Zürich / Remote", "Hamburg oder
Nürnberg, oder remote") name no region → `not_stated`. A line that only says
where a remote worker may live ("Remote - United States") is not an office.
Code rule: if `work_mode` is not `remote`, the value is `not_applicable`.

## 2. Language requirement

Two fields with the same allowed values: `german` and `french`. English and
Italian are not extracted in Milestone 1.

| Value | Meaning | Typical wording |
|---|---|---|
| `required_fluent` | Required at a high level. | C1, C2, native, fluent, excellent, very good, verhandlungssicher, fliessend, courant |
| `required_intermediate` | Required below fluent. | A2-B2, good, working knowledge, gute Kenntnisse, bonnes connaissances |
| `required_level_not_stated` | Required, no level given. | "German required", "Deutsch ist ein Muss" |
| `either_required` | One of several languages is required, any of them is enough. | "fluent in German or French" (both fields get this value) |
| `nice_to_have` | Wanted but not required. | plus, advantage, preferred, von Vorteil, un atout |
| `not_mentioned` | The posting says nothing about this language. | |

A posting written in German does not by itself state a German requirement:
the field stays `not_mentioned`. The posting language (measured by code) is
available as a separate filter.

Decided cases: "English and/or French" → `french` is `nice_to_have` (English
alone is enough); a school-grade prerequisite ("good grades in German") →
`required_intermediate`; "maîtrise du français" → `required_fluent`.

## 3. `seniority`

| Value | Meaning |
|---|---|
| `internship` | Internship, apprenticeship (Lehrstelle), working student, thesis. |
| `junior` | 0-1 years required, or a junior / graduate / entry-level title. |
| `mid` | 2-4 years required, or a mid-level title (e.g. "Engineer II"). |
| `senior` | 5+ years required, or a senior title. |
| `staff_principal` | Staff, principal, lead or head-of title. Never from years alone. |
| `not_stated` | No years, no level in the title ("Founding" is not a level; only co-founder would be). |

Extracted as two parts, combined by code:
- `min_years`: `0` to `15`, `15_plus`, or `not_stated` (minimum years of
  experience required).
- `title_level`: `internship`, `junior`, `mid`, `senior`, `staff_principal`,
  `not_stated` (from the title only).

Order of evidence:

1. **Years of experience**, when stated. "PhD or 3 years" uses the 3. A range
   ("3-5 years") uses the minimum. A degree is never converted into years:
   "MSc or PhD, or equivalent experience" is `not_stated`.
   Any range or set of alternatives gives the lower end; this also holds for a
   level range in the title ("Confirmé à Sénior" gives `mid`).
2. **Otherwise the title.**
3. **Otherwise `not_stated`.**

When years and title disagree ("Senior ML Engineer, 2+ years"), years win,
except that a staff/principal/lead/head-of title always gives
`staff_principal`, and an internship title always gives `internship`.

Decided cases (title level): a team or unit lead (Teamleiter, Leiter
Abteilung) and "Manager" as a rank (plain "Manager", "Manager, X",
"Engineering Manager") → `staff_principal`; a function title (Sales, Key
Account, Application, Project or Programmatic Advertising Manager),
Projektleiter and Testleiter (roles, not ranks) → `not_stated`; "Postdoctoral Researcher" →
`not_stated`; "Associate" as the lower end of a range → `junior`.

Decided cases (owner, 2026-11-03, title level): a level in brackets counts
("(Senior) Consultant" → `senior`, "(Junior-) Projektleiter" → `junior`);
"Member of Technical Staff", "Member of Product Staff" and "Research Staff" →
`not_stated` (job names, not staff level); "Partner" → `staff_principal`;
"Expert", "Resident", "Experienced ..." and "Group Product Manager" →
`not_stated` (provisional, owner unsure). Code rule: `title_level` is kept only
if the title contains a keyword for that level (multilingual list in
`extract/rules.py`; "Manager" only as a rank), otherwise `not_stated`;
`seniority` is recomputed from the corrected level.

Decided cases (years, 2026-11-03): `0` only when the posting says no experience
at all is needed; waiving only some experience ("no prior AI experience
required", "Erfahrung im Finanzsektor keine Voraussetzung") → `not_stated`.

Decided cases (years): years listed as preferred or nice to have still count;
separate requirements that all apply ("3+ years ML and 2+ years Python") take
the highest; a range or alternatives take the lowest.

## 4. `contract_type`

| Value | Meaning | Typical wording |
|---|---|---|
| `permanent` | Open-ended position. | unbefristet, CDI, permanent position |
| `fixed_term` | An end date or limited duration is stated. | befristet, CDD, 12-month contract, maternity cover, temporary staffing |
| `not_stated` | The posting does not say. | |

Nothing is assumed: an internship is **not** taken to be `fixed_term`, and a
missing statement is `not_stated` (no quote). Added 2026-11-03 so that fixed-term
roles can be filtered out of the search. "Employment type: Contract" with no duration → `fixed_term`. "Full time" is about hours, not
contract length, so it does not make a post `permanent`. **Search** treats
`not_stated` as permanent: a "no fixed-term" filter drops only `fixed_term`.
