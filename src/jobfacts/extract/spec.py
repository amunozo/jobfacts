"""The single field specification: names, allowed values and what each value means.

Mirrors `docs/fields.md` (a test checks the value lists). Both the Jev questions and the
LLM prompt are generated from `FIELDS`; never copy values anywhere else.

Value order matters for Jev, which leans toward the first option: "not stated" comes
first, so the model has to find evidence before choosing anything else.
"""

import hashlib
import json
import random
from dataclasses import dataclass

from jobfacts import locations

# Sentence ids shown to the models. Posting sentences are 1..n (jobfacts.sentences.split).
TITLE_ID = 0
LOCATION_ID = -1

LANGUAGE_VALUES = {
    "not_mentioned": "The posting says nothing about this language being needed or wanted.",
    "required_fluent": (
        "Required at a high level: C1, C2, native, fluent, excellent, very good, business fluent, "
        "verhandlungssicher, fliessend, courant."
    ),
    "required_intermediate": (
        "Required below fluent: A2-B2, good, working knowledge, good school grades, "
        "gute Kenntnisse, bonnes connaissances."
    ),
    "required_level_not_stated": (
        'Required, but no level is given: "German required", "Deutsch ist ein Muss".'
    ),
    "either_required": (
        "One of several languages is required and any of them is enough, for example "
        '"fluent in German or French" (then both german and french get this value).'
    ),
    "nice_to_have": (
        "Wanted but not required: plus, advantage, preferred, von Vorteil, un atout; "
        "also 'English and/or French' for French."
    ),
}


@dataclass(frozen=True)
class Field:
    name: str
    question: str
    values: dict[str, str]  # value -> definition, in the order shown to the models
    no_quote: frozenset[str]  # values that carry no supporting sentence


def _language(name: str, label: str) -> Field:
    return Field(
        name,
        f"Does the posting require {label} (not the language the posting is written in)?",
        LANGUAGE_VALUES,
        frozenset({"not_mentioned"}),
    )


_YEARS: dict[str, str] = {
    "not_stated": "The posting states no minimum years of experience.",
    **{
        str(n): f"The posting requires a minimum of {n} year{'s' if n != 1 else ''} of experience."
        for n in range(16)
    },
    "15_plus": "The posting requires a minimum of more than 15 years of experience.",
}
_YEARS["0"] = (
    "The posting says that no experience at all is needed (0 years, entry level, "
    "'no prior experience'). Waiving only some experience ('no prior AI experience "
    "required', 'Erfahrung im Finanzsektor keine Voraussetzung') is not_stated."
)

FIELDS: dict[str, Field] = {
    f.name: f
    for f in [
        Field(
            "work_mode",
            "Where is the work done: at the office, partly at home, or fully remote?",
            {
                "not_stated": (
                    "The posting does not say. An office location alone, with nothing "
                    "said about home or remote working, is not_stated."
                ),
                "onsite": (
                    "Work at the office (also 'primarily on site', 'office-first', or fixed "
                    "days at several offices)."
                ),
                "hybrid": (
                    "Part office, part home (also when home working appears only as a "
                    "benefit, or as a tag such as #LI-HYBRID)."
                ),
                "remote": (
                    "Fully remote, or remote offered as an option (occasional travel or "
                    "offsites still count as remote)."
                ),
            },
            frozenset({"not_stated"}),
        ),
        Field(
            "remote_from_switzerland",
            "Can the person do this remote role while living in Switzerland?",
            {
                "not_stated": (
                    "Remote, but the posting names no region where the remote person may live "
                    "(office cities listed next to 'remote', such as 'London / New York / "
                    "Remote', are not such a region)."
                ),
                "not_applicable": (
                    "The role is not remote (work mode is onsite, hybrid or not stated)."
                ),
                "allowed": (
                    "Remote, and the posting names Switzerland as allowed, or says anywhere "
                    "in the world or globally (this wins over a list of countries). A Swiss "
                    "office or employer alone does not make it allowed."
                ),
                "unclear": (
                    'Remote, but the region is only "Europe", "EU", "EMEA", a time zone '
                    "(for example CET), 'choose where you live', or a country list with EMEA, "
                    "without naming Switzerland."
                ),
                "not_allowed": (
                    "Remote, but the posting lists countries without Switzerland, or allows "
                    "only one other country."
                ),
            },
            frozenset({"not_stated", "not_applicable"}),
        ),
        _language("german", "German"),
        _language("french", "French"),
        Field(
            "min_years",
            "What is the minimum number of years of experience the posting asks for (preferred "
            "or nice-to-have years count too)? A degree is never converted into years; a range "
            "or alternatives give the lowest number; separate requirements that all apply give "
            "the highest.",
            _YEARS,
            frozenset({"not_stated"}),
        ),
        Field(
            "title_level",
            "Read only the title line (T), never the description. What seniority level does "
            "the job TITLE itself state? A range in the title (for example 'Confirmé à "
            "Sénior') gives the lower level. A level in brackets counts: '(Senior) "
            "Consultant' is senior, '(Junior-) Projektleiter' is junior.",
            {
                "not_stated": (
                    "The title states no level. 'Founding', 'Postdoc', 'Member of Technical "
                    "Staff', 'Member of Product Staff', 'Research Staff', a plain job name "
                    "such as 'Researcher' or 'Expert', 'Resident', 'Experienced ...' and "
                    "'Group Product Manager' are not levels."
                ),
                "internship": "Internship, apprenticeship, working student, thesis.",
                "junior": "A junior, graduate, entry-level or associate title.",
                "mid": 'A mid-level title (for example "Engineer II").',
                "senior": "A senior title.",
                "staff_principal": (
                    "A staff, principal, partner, lead, head-of, team-lead (Teamleiter) or "
                    "'Manager' as a rank ('Manager', 'Manager, X', 'Engineering "
                    "Manager'). Not a function title such as Sales, Key Account, "
                    "Application or Project Manager, Projektleiter or Testleiter."
                ),
            },
            frozenset({"not_stated"}),
        ),
        Field(
            "contract_type",
            "Is the position permanent (open-ended) or fixed-term?",
            {
                "not_stated": (
                    "The posting does not say. An internship is not assumed to be fixed-term."
                ),
                "permanent": (
                    "Open-ended: unbefristet, CDI, permanent position, unbefristete Anstellung."
                ),
                "fixed_term": (
                    "An end date or limited duration is stated: befristet, CDD, 12-month "
                    "contract, maternity cover, temporary staffing, or employment type 'Contract'."
                ),
            },
            frozenset({"not_stated"}),
        ),
    ]
}

SENIORITY_VALUES = ["internship", "junior", "mid", "senior", "staff_principal", "not_stated"]

GENERAL_RULES = (
    "Answer only from the posting text. Never guess: when the posting does not state a "
    "fact, choose the not-stated value. Postings may be in English, German or French."
)

RELEVANCE_QUESTION = (
    "Is the main day-to-day work of this role itself AI, machine learning, data science, "
    "data engineering (including analytics engineering and web scraping / data acquisition), "
    "SQL or statistical data analysis, or AI/ML or computational research; or software or "
    "infrastructure engineering on ML/AI systems (including model-training infrastructure); "
    "or product management of an ML/AI product; or solutions architecture or "
    "forward-deployed / deployed engineering on an AI product; or building AI evaluations? "
    "Judge the role's own tasks, not the employer's business: sales, account, "
    "customer-success, support, recruiting, marketing, design, operations, finance, billing, "
    "legal and data-centre roles do not count, even at an AI company. Business-intelligence, "
    "reporting, market-insight and revenue-planning analyst roles (dashboards, Power BI) do "
    "not count."
)

# office_cantons: not a one-value field. The model picks any number of canton codes (and
# OUTSIDE); none = not stated. Evidence sentences are required when anything is picked.
OFFICE_CANTONS = "office_cantons"
OFFICE_CANTONS_QUESTION = (
    "In which Swiss cantons does the posting name an office or work location for this "
    f'role? Use the canton codes, plus "{locations.OUTSIDE}" if an office outside '
    "Switzerland is named. Pick none if the posting names no office location."
)
_NOT_REASONS = (
    "Answer no if the place is only mentioned for other reasons (company history, "
    "customers, other roles, travel) or is only a planned future office. A country or "
    "region where a remote worker may live ('Remote - United States', 'Remote (Europe)', "
    "'must be based in the US or UK') is not an office or work location."
)
CANTON_QUESTION = (
    "Does the posting name an office or work location for this role in canton {name} "
    f"({{code}}; for example {{places}})? {_NOT_REASONS}"
)
OUTSIDE_QUESTION = (
    "Does the posting name an office or work location for this role outside Switzerland "
    f"(a city or office in another country)? {_NOT_REASONS}"
)
OFFICE_EVIDENCE_QUESTION = (
    "Which line of the posting names the office or work location of this role? "
    "Choose none if no line does."
)


def canton_question(code: str) -> str:
    c = locations.CANTONS[code]
    return CANTON_QUESTION.format(name=c.en, code=code, places=", ".join(c.places))


def derive_seniority(min_years: str, title_level: str) -> str:
    """Combine the two extracted parts into `seniority` (rules in docs/fields.md).

    1. A staff/principal/lead/head-of title is always `staff_principal`.
    2. Otherwise stated years decide: 0-1 junior, 2-4 mid, 5 or more senior.
    3. Otherwise the title level (internship/junior/mid/senior).
    4. Otherwise `not_stated`.
    An internship title also wins over years (an internship is not a level of experience).
    """
    if title_level in ("staff_principal", "internship"):
        return title_level
    if min_years == "15_plus":
        return "senior"
    if min_years.isdigit():
        years = int(min_years)
        return "junior" if years <= 1 else "mid" if years <= 4 else "senior"
    if title_level in SENIORITY_VALUES:
        return title_level
    return "not_stated"


ORDERINGS = ("original", "reversed", "shuffled")
SHUFFLE_SEED = 20261102


def order_values(field_name: str, values: dict[str, str], ordering: str) -> dict[str, str]:
    """The value options in the requested order (`original` = as defined in FIELDS).

    `shuffled` uses a fixed seed per field, so the same order is produced on every run.
    """
    items = list(values.items())
    if ordering == "reversed":
        items.reverse()
    elif ordering == "shuffled":
        random.Random(f"{SHUFFLE_SEED}:{field_name}").shuffle(items)
    elif ordering != "original":
        raise ValueError(f"unknown ordering {ordering!r}")
    return dict(items)


def spec_hash(extra: str = "") -> str:
    """Version of the spec plus a prompt template (any text); changes when either changes."""
    payload = json.dumps(
        {
            "fields": {
                n: {"q": f.question, "v": f.values, "nq": sorted(f.no_quote)}
                for n, f in FIELDS.items()
            },
            "rules": GENERAL_RULES,
            "cantons": [
                OFFICE_CANTONS_QUESTION,
                OUTSIDE_QUESTION,
                OFFICE_EVIDENCE_QUESTION,
                [canton_question(c) for c in locations.CANTONS],
            ],
            "extra": extra,
        },
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:12]
