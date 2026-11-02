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

### `office_locations`
A set of values from a fixed list kept in code (`locations` list): the 26
Swiss cantons, the Swiss cities and towns that host employers (each mapped to
its canton), plus `outside_switzerland`. The job-board API location field and
a name match on the posting text give the candidates; the model confirms
which ones are offices for this role. Empty when no office is named.
Accuracy is measured as an exact match of the set, and also at canton level.

### `remote_from_switzerland`
| Value | Meaning |
|---|---|
| `allowed` | Remote, and the posting names Switzerland as allowed, or says anywhere in the world. |
| `unclear` | Remote, but the region is only "Europe", "EU", "EMEA", a time zone (e.g. CET), or similar, without naming Switzerland. |
| `not_allowed` | Remote, but the posting lists countries without Switzerland, or allows only one other country. |
| `not_stated` | Remote, but the posting names no region at all. |
| `not_applicable` | The role is not remote (`work_mode` is `onsite`, `hybrid` or `not_stated`). |

## 2. Language requirement

Two fields with the same allowed values: `german` and `french`. English and
Italian are not extracted in Milestone 1.

| Value | Meaning | Typical wording |
|---|---|---|
| `required_fluent` | Required at a high level. | C1, C2, native, fluent, excellent, verhandlungssicher, fliessend, courant |
| `required_intermediate` | Required below fluent. | A2-B2, good, working knowledge, gute Kenntnisse, bonnes connaissances |
| `required_level_not_stated` | Required, no level given. | "German required", "Deutsch ist ein Muss" |
| `either_required` | One of several languages is required, any of them is enough. | "fluent in German or French" (both fields get this value) |
| `nice_to_have` | Wanted but not required. | plus, advantage, preferred, von Vorteil, un atout |
| `not_mentioned` | The posting says nothing about this language. | |

A posting written in German does not by itself state a German requirement:
the field stays `not_mentioned`. The posting language (measured by code) is
available as a separate filter.

## 3. `seniority`

| Value | Meaning |
|---|---|
| `internship` | Internship, working student, thesis. |
| `junior` | 0-1 years required, or a junior / graduate / entry-level title. |
| `mid` | 2-4 years required, or a mid-level title (e.g. "Engineer II"). |
| `senior` | 5+ years required, or a senior title. |
| `staff_principal` | Staff, principal, lead or head-of title. Never from years alone. |
| `not_stated` | No years, no level in the title. |

Extracted as two parts, combined by code:
- `min_years`: `0` to `15`, `15_plus`, or `not_stated` (minimum years of
  experience required).
- `title_level`: `internship`, `junior`, `mid`, `senior`, `staff_principal`,
  `not_stated` (from the title only).

Order of evidence:

1. **Years of experience**, when stated. "PhD or 3 years" uses the 3. A range
   ("3-5 years") uses the minimum.
2. **Otherwise the title.**
3. **Otherwise `not_stated`.**

When years and title disagree ("Senior ML Engineer, 2+ years"), years win,
except that a staff/principal/lead/head-of title always gives
`staff_principal`.
