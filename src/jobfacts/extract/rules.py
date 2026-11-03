"""Code rules applied after the model answers (decided 3 Nov 2026; see docs/decisions.md).

Models read titles and places loosely, so code checks what code can check:
1. `title_level` is kept only if the title contains a keyword for that level.
2. A canton the model picked is dropped unless the posting names a place in that canton.
3. `remote_from_switzerland` is `not_applicable` unless `work_mode` is `remote`.
`title_excluded` is the relevance filter's deterministic title list (see its docstring).
"""

import re

from jobfacts import locations
from jobfacts.extract.base import Result
from jobfacts.extract.posting import PostingText
from jobfacts.extract.spec import OFFICE_CANTONS

# Words that name a level (English, German, French). Checked against the title only.
LEVEL_KEYWORDS = {
    "internship": r"intern(ship)?\b|praktik|stagiaire|\bstage\b|lehrstelle|lehre\b|"
    r"schnupperlehre|apprenti|werkstudent|working student|thesis|abschlussarbeit|"
    r"masterarbeit|\bEFZ\b",
    "junior": r"junior|\bjr\b|graduate|entry[- ]level|associate|d[ée]butant|einsteiger|"
    r"berufseinsteiger|trainee",
    "mid": r"\bII\b|confirm[ée]|mid[- ]level|intermediate|\bmid\b",
    "senior": r"s[ée]nior|\bsr\.?\b|\bIII\b",
    # "Manager" counts only as a rank: plain "Manager", "Manager, X", "Engineering Manager"
    # (docs/fields.md rule 11a). "Partner" is a rank in consulting and law firms.
    "staff_principal": r"\bstaff\b|principal|\blead\b|\bleader\b|head of|\bhead\b|leiter(in)?\b|"
    r"teamleit|chief|director|direktor|\bvp\b|vice president|\bpartner\b|"
    r"^manager\b|engineering manager|\bmanager of\b|responsable d'?[ée]quipe|chef d'?[ée]quipe",
}
_LEVEL_RE = {k: re.compile(v, re.IGNORECASE) for k, v in LEVEL_KEYWORDS.items()}
# "Member of Technical Staff" and "Research Staff" are job names, not ranks.
_NOT_RANK = re.compile(r"member of [\w ]*staff|research staff", re.IGNORECASE)
# Roles, not ranks: a Projektleiter or Testleiter is a "staff_principal" only with a rank word.
_FUNCTION_LEITER = re.compile(r"projektleiter|testleiter", re.IGNORECASE)
_RANK_WORD = re.compile(r"teamleit|head|lead\b|principal|staff\b", re.IGNORECASE)


def checked_title_level(title: str, level: str) -> str:
    """`level` if the title has a keyword for it, else `not_stated`."""
    if level == "not_stated" or level not in _LEVEL_RE:
        return "not_stated"
    t = _NOT_RANK.sub(" ", title.strip())
    if level == "staff_principal" and _FUNCTION_LEITER.search(t) and not _RANK_WORD.search(t):
        return "not_stated"
    return level if _LEVEL_RE[level].search(t) else "not_stated"


def apply_extraction_rules(p: PostingText, result: Result) -> None:
    """Rules 1-3 above, in place. `seniority` is derived from `title_level`, so it follows."""
    if result.error:
        return
    values = result.values
    if "title_level" in values:
        level = checked_title_level(p.title, values["title_level"])
        if level != values["title_level"]:
            result.details.setdefault("rules", []).append(
                f"title_level {values['title_level']} -> {level}"
            )
            values["title_level"] = level
            result.sentences["title_level"] = []
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
# Titles whose role is plainly not AI/data work, whatever the employer does. Always excluded:
_ALWAYS = re.compile(
    r"\bsales\b|account executive|recruit\w*|sourcer|talent (?:acquisition|partner|pool|"
    r"community)|^talent$|open application|not seeing the right|\bdesigner\b|technician|"
    r"customer success",
    re.IGNORECASE,
)
# Excluded only when the title names no technical job: "Senior SWE (Agentic Search) - Billing"
# or "Senior Data Engineer ... (Contract through our external payroll partner)" stay.
_UNLESS_TECHNICAL = re.compile(r"\binsights?\b|\bbilling\b|\bpayroll\b|partnerships", re.IGNORECASE)
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
    placeholders; insights, billing, payroll and partnerships unless the title also names an
    engineering/science job). Title only: a description may mention payroll in a contract note.
    Such postings are scored 0 without asking the model. "Sales Engineer" is excluded on
    purpose (the role is sales)."""
    t = " ".join(title.split())
    if _ALWAYS.search(t):
        return True
    return bool(_UNLESS_TECHNICAL.search(t)) and not _TECHNICAL.search(t)
