"""Code rules applied after the model answers (decided 3 Nov 2026; see docs/decisions.md).

Models read titles and places loosely, so code checks what code can check:
1. `title_level` is kept only if the title contains a keyword for that level; if not, the
   level the title itself names is used (`title_named_level`), else `not_stated`.
1b. A title that names `staff_principal` (staff, principal, lead, head of, co-founder, CEO,
   "senior manager" ...) always gives `staff_principal`, even if the model said less.
2. A canton the model picked is dropped unless the posting names a place in that canton.
3. `remote_from_switzerland` is `not_applicable` unless `work_mode` is `remote`.
4. `german` / `french` are `not_mentioned` unless the posting names the language (or the
   national languages) somewhere; the whole posting is checked, not only the quoted sentences.
`title_excluded` is the relevance filter's deterministic title list (see its docstring).
"""

import re

from jobfacts import locations
from jobfacts.extract.base import Result
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import OFFICE_CANTONS

# Words that name a level (English, German, French). Checked against the title only, after
# typographic apostrophes are turned into plain ones. Feminine and plural forms are covered by
# stems (praktik, apprenti, stagiaire, absolvent, leiter(in), directrice, cheffe ...).
LEVEL_KEYWORDS = {
    "internship": r"\binterns?\b|internship|praktik|stagiaire|\bstage\b|lehrstelle|lehre\b|"
    r"lernende|lehrling|schnupperlehre|apprenti|alternan|werkstudent|working student|thesis|"
    r"abschlussarbeit|bachelorarbeit|masterarbeit|diplomarbeit|\bEFZ\b",
    "junior": r"junior|\bjr\b|graduate|absolvent|jeunes? dipl[oô]m[ée]|entry[- ]level|associate|"
    r"d[ée]butant|einsteiger|berufseinsteiger|trainee",
    "mid": r"\bII\b|confirm[ée]|mid[- ]level|intermediate|\bmid\b",
    "senior": r"s[ée]nior|\bsr\.?\b|\bIII\b",
    # "Manager" counts only as a rank: plain "Manager", "Manager, X", "Engineering Manager"
    # (docs/fields.md rule 11a). "Partner" is a rank in consulting and law firms.
    "staff_principal": r"\bstaff\b|principal|\blead\b|\bleader\b|head of|\bhead\b|leiter(in)?\b|"
    r"\bleitung\b|(?:team|bereichs|abteilungs|gruppen|gesch[äa]fts)leitung|\bleitende[rnms]?\b|"
    r"teamleit|chief|direct(?:or|eur|rice)|direktor|\bvp\b|vice president|\bpartner\b|"
    r"^manager\b|engineering manager|\bmanager of\b|responsable d'?[ée]quipe|"
    r"chef(?:fe)? d'?[ée]quipe|co[- ]?founder|mitgr[üu]e?nder|mitgruender|"
    r"co-?fondat(?:eur|rice)|\bceo\b|\bsenior manager\b",
}
_LEVEL_RE = {k: re.compile(v, re.IGNORECASE) for k, v in LEVEL_KEYWORDS.items()}
# "Member of Technical Staff" and "Research Staff" are job names, not ranks.
_NOT_RANK = re.compile(r"member of [\w ]*staff|research staff", re.IGNORECASE)
# Roles, not ranks: a Projektleiter(in) or Testleiter is a "staff_principal" only with a rank word.
_FUNCTION_LEITER = re.compile(r"projektleit|testleit", re.IGNORECASE)
_RANK_WORD = re.compile(r"teamleit|head|lead\b|principal|staff\b", re.IGNORECASE)
# Separators that make two level words a range or a set of alternatives ("Confirmé à Sénior").
_RANGE_SEP = re.compile(r"\s[-–/]\s|/|–|\s(?:to|à|bis|or|oder|ou)\s", re.IGNORECASE)
# Gender tags such as "(m/w/d)", "( m/w/d )", "(m,w,d)", "(m/w/div)", "(gn)", "(all genders)"
# and unbracketed "H/F" / "m/w/d" are not ranges. Unbracketed letters count only at the end of
# the title or before a separator ("Consultant - F/H - CDI"), so "Head of F/X Trading" stays.
_GENDER_ITEM = r"(?:[mwfdhx]|div(?:ers[e]?)?)"
GENDER_TAG = re.compile(
    rf"\(\s*{_GENDER_ITEM}(?:\s*[/|,.]\s*{_GENDER_ITEM})+\s*\)\*?|"
    r"\(\s*(?:gn|all genders?|alle geschlechter)\s*\)\*?|"
    rf"(?<![\w/|]){_GENDER_ITEM}(?:\s*[/|]\s*{_GENDER_ITEM})+\*?(?=\s*(?:$|[-–|,]))",
    re.IGNORECASE,
)
_LEVEL_ORDER = ["internship", "junior", "mid", "senior", "staff_principal"]


def _clean(title: str) -> str:
    t = title.strip().replace("\u2019", "'").replace("\u2018", "'").replace("\u02bc", "'")
    return GENDER_TAG.sub(" ", _NOT_RANK.sub(" ", t))


def checked_title_level(title: str, level: str) -> str:
    """`level` if the title has a keyword for it, else `not_stated`."""
    if level == "not_stated" or level not in _LEVEL_RE:
        return "not_stated"
    t = _clean(title)
    if level == "staff_principal" and _FUNCTION_LEITER.search(t) and not _RANK_WORD.search(t):
        return "not_stated"
    return level if _LEVEL_RE[level].search(t) else "not_stated"


def title_named_level(title: str) -> str:
    """The level the title itself names, or `not_stated` (docs/fields.md, title level).

    One level named: that one. Several: an internship word wins; a range or alternatives
    ("Confirmé à Sénior") give the lower end; otherwise the highest (a staff/lead title wins over
    a senior one)."""
    named = [lvl for lvl in _LEVEL_ORDER if checked_title_level(title, lvl) == lvl]
    if not named:
        return "not_stated"
    if len(named) == 1 or "internship" in named:
        return named[0]
    return named[0] if _RANGE_SEP.search(_clean(title)) else named[-1]


_NATIONAL_LANGUAGES = (
    r"landessprache|national language|langue nationale|langues nationales|lingua nazionale"
)
_LANGUAGE_NAMED = {
    "german": re.compile(rf"deutsch|german|allemand|tedesco|{_NATIONAL_LANGUAGES}", re.IGNORECASE),
    "french": re.compile(
        r"franz[öo]s|franzoes|french|fran[cç]ais|francese|francophone|" + _NATIONAL_LANGUAGES,
        re.IGNORECASE,
    ),
}


def _require_language_named(p: PostingText, result: Result) -> None:
    """Rule 4: a language level needs the language (or the national languages) named somewhere."""
    text = "\n".join([p.title or "", p.location_raw or "", *(s.text for s in p.sentences)])
    for field, pattern in _LANGUAGE_NAMED.items():
        value = result.values.get(field)
        if value is None or value == "not_mentioned" or pattern.search(text):
            continue
        result.details.setdefault("rules", []).append(
            f"{field} {value} -> not_mentioned (language not named)"
        )
        result.values[field] = "not_mentioned"
        result.sentences[field] = []


def apply_extraction_rules(p: PostingText, result: Result) -> None:
    """Rules 1-4 above, in place. `seniority` is derived from `title_level`, so it follows."""
    if result.error:
        return
    values = result.values
    if "title_level" in values:
        level = checked_title_level(p.title, values["title_level"])
        if level == "not_stated" and values["title_level"] != "not_stated":
            level = title_named_level(p.title)  # vetoed: use what the title itself says
        if title_named_level(p.title) == "staff_principal" and level != "staff_principal":
            level = "staff_principal"  # rule 1b: a rank title always wins
            result.details.setdefault("rules", []).append(
                f"title_level {values['title_level']} -> staff_principal"
            )
            values["title_level"] = level
            result.sentences["title_level"] = []
        if level != values["title_level"]:
            result.details.setdefault("rules", []).append(
                f"title_level {values['title_level']} -> {level}"
            )
            values["title_level"] = level
            result.sentences["title_level"] = []
    _require_language_named(p, result)
    if values.get("work_mode") != "remote" and "remote_from_switzerland" in values:
        if values["remote_from_switzerland"] != "not_applicable":
            result.details.setdefault("rules", []).append(
                f"remote_from_switzerland {values['remote_from_switzerland']} -> not_applicable"
            )
        values["remote_from_switzerland"] = "not_applicable"
        result.sentences["remote_from_switzerland"] = []
    hints = set(p.canton_hints())
    kept = [c for c in result.office_cantons if c == locations.OUTSIDE or c in hints]
    if kept != result.office_cantons:
        dropped = [c for c in result.office_cantons if c not in kept]
        result.details.setdefault("rules", []).append(f"cantons dropped: {dropped}")
        result.office_cantons = kept
        if not kept:
            result.sentences[OFFICE_CANTONS] = []


# --- relevance: deterministic title exclusion --------------------------------------------
# Titles whose role is plainly not AI/data work, whatever the employer does.
# Role names that are excluded even with a technical word, because the technical word is part
# of the role name (a Sales Engineer sells; a Talent Acquisition Partner recruits):
_ALWAYS = re.compile(
    r"sales engineer|customer success engineer|solutions? sales|account executive|sales executive|"
    r"talent (?:acquisition )?partner|^talent$|open application|not seeing the right|\bdesigner\b",
    re.IGNORECASE,
)
# Excluded only when the title names no technical job. "ML Engineer - Customer Success",
# "Recruitment Data Scientist", "Research Technician ML" and "Senior SWE (Agentic Search) -
# Billing" stay; a bare "Technician" or "Recruiter" goes.
_UNLESS_TECHNICAL = re.compile(
    r"\binsights?\b|\bbilling\b|\bpayroll\b|partnerships|\bsales\b|recruit\w*|sourcer|"
    r"talent (?:acquisition|pool|community)|technician|customer success",
    re.IGNORECASE,
)
_TECHNICAL = re.compile(
    r"engineer|scientist|researcher|developer|architect|\bswe\b|entwickler|ingenieur|"
    r"ing[ée]nieur|d[ée]veloppeur|programmer|\bml\b|\bai\b|machine learning",
    re.IGNORECASE,
)

# Part of the relevance version: editing any pattern makes relevance re-score everything.
TITLE_RULES = "|".join(r.pattern for r in (_ALWAYS, _UNLESS_TECHNICAL, _TECHNICAL))


def title_excluded(title: str) -> bool:
    """True if the job title alone shows a role that is not AI / ML / data work (sales, talent
    and recruiting, design, technicians, customer success, "not seeing the right opportunity"
    placeholders, insights, billing, payroll, partnerships) unless the title also names an
    engineering/science job. Exceptions that stay excluded even then: Sales Engineer, Solutions
    Sales, Talent (Acquisition) Partner, Designer. Title only: a description may mention payroll
    in a contract note. Such postings are scored 0 without asking the model."""
    t = " ".join(title.split())
    if _ALWAYS.search(t):
        return True  # "Sales Engineer" is excluded on purpose (the role is sales)
    return bool(_UNLESS_TECHNICAL.search(t)) and not _TECHNICAL.search(t)
