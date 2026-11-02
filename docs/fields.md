# Extraction fields (Milestone 1)

The single reference for what is extracted from each posting. The extraction
prompt, the output schema, the labelling app and the accuracy report all use
these names and values. Change a field here first, then everywhere else.

## Rules for every field

- **One value from the allowed list.** Nothing else is accepted; output that
  does not match the schema is retried or flagged, never stored.
- **Supporting quote.** Every value except `not_stated` / `not_mentioned`
  comes with one or more quotes copied exactly from the posting. Code checks
  that each quote appears in the posting text; a quote that does not appear
  marks the answer as invalid.
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
A list of `{city, country}` as written in the posting (for example
`{"city": "Zürich", "country": "Switzerland"}`). Empty list when no office is
named. Code, not the LLM, maps cities to Swiss regions for the search filter.
Accuracy is measured as an exact match of the set of locations.

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

Order of evidence:

1. **Years of experience**, when stated. "PhD or 3 years" uses the 3. A range
   ("3-5 years") uses the minimum.
2. **Otherwise the title.**
3. **Otherwise `not_stated`.**

When years and title disagree ("Senior ML Engineer, 2+ years"), years win,
except that a staff/principal/lead/head-of title always gives
`staff_principal`.
