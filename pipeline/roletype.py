"""What kind of employment a posting actually offers.

One classifier, two callers that must never drift apart: sourcing.py drops a
posting whose type the profile does not accept before a kit exists, and
send_gate.py refuses to record a send unless the ARCHIVED posting has an
accepted type. Precedence:

  werkstudent  >  internship  >  full_time  >  unspecified

A Werkstudent marker wins when a posting offers both ("Intern / Working
Student"); an internship-only posting stays an internship even when a kit
title says "Working Student"; a posting that says nothing about students is
unspecified, which is never accepted.

Evidence has to come from a line that reads like content: at least 25
characters, or a title line with a gender marker ("Werkstudent (m/w/d)"),
never a one-word navigation item ("Werkstudenten", "Praktikum") on the
employer's page. A given title is classified first and decides on its own
when it carries any marker; the body is read only for a title with none.
"""
import re

from pipeline import profile

WERKSTUDENT = "werkstudent"
INTERNSHIP = "internship"
FULL_TIME = "full_time"
UNSPECIFIED = "unspecified"

_WS = re.compile(
    r"werkstudent\w*|workstudent\w*|working[- ]student|student(?:ische[nrs]?|\*in|:in|_in)?\s+(?:hilfskraft|aushilfe|"
    r"mitarbeit\w*|assistant|worker|employee|job)|studentische[nrs]?\s+\w*hilfskr\w*|studentenjob|student\s+job|"
    r"\bhiwi\b|\bshk\b|\bwhk\b|wissenschaftliche[nrs]?\s+hilfskraft|werkstudium|"
    # "Student (m/w/d) ... 20 Std./Woche": a student named next to student hours
    r"\bstudent(?:\*in|:in|_in|in|innen)?\b(?=[^.\n]{0,60}(?:\d{1,2}\s*(?:h\b|std|stunden|hours)|teilzeit|"
    r"part[- ]time|neben dem studium|alongside (?:your|the) (?:studies|degree)))",
    re.I)
_INTERN = re.compile(r"praktik\w*|\bintern(?:ship|s)?\b|pflichtpraktikum|mandatory internship", re.I)
_FULL = re.compile(r"vollzeit|full[- ]?time|\b40\s*(?:h\b|std|stunden|hours)|ganztags|ganztägig|unbefristet", re.I)
_GENDER = re.compile(r"\((?:[mwfdxhgn]\s*/\s*)+[mwfdxhgn]\)|\(all genders\)|\(gn\)", re.I)
_MIN_CONTENT_LINE = 25


def _evidence(text: str, pattern: re.Pattern, *, strict: bool) -> str | None:
    """The line that carries a marker, or None. In strict mode a short line
    without a gender marker (a navigation item) never counts."""
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not pattern.search(stripped):
            continue
        if not strict or len(stripped) >= _MIN_CONTENT_LINE or _GENDER.search(stripped):
            return stripped[:120]
    return None


def _classify_text(text: str, *, strict: bool) -> tuple[str, str]:
    """(type, evidence) for one block of text, applying the precedence."""
    for kind, pattern in ((WERKSTUDENT, _WS), (INTERNSHIP, _INTERN), (FULL_TIME, _FULL)):
        line = _evidence(text, pattern, strict=strict)
        if line:
            return kind, line
    return UNSPECIFIED, ""


def classify(text: str, title: str | None = None) -> str:
    return classify_with_evidence(text, title)[0]


def classify_with_evidence(text: str, title: str | None = None) -> tuple[str, str]:
    """Employment type of a posting. A title decides whenever it carries a
    marker; the body is consulted only when the title says nothing."""
    if title:
        kind, evidence = _classify_text(title, strict=False)  # a title is short by nature
        if kind != UNSPECIFIED:
            return kind, f"title: {evidence}"
    return _classify_text(text, strict=True)


def is_accepted(kind: str) -> bool:
    """True when the profile's search.role_types include this type."""
    return kind in profile.accepted_role_types()


def drop_reason(kind: str) -> str:
    """The one-line reason a posting of this type is dropped or refused."""
    return {
        WERKSTUDENT: "Werkstudent role, not an accepted role type",
        INTERNSHIP: "internship or Praktikum, not an accepted role type",
        FULL_TIME: "full-time role, no student option",
        UNSPECIFIED: "no Werkstudent or working-student role stated",
    }.get(kind, "")
