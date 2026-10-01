"""The words a recruiter reads on the CV, taken from the profile's cv section.

The profile's projects, skills and experience stay the fact base that the
matcher, the writer and the reviewer key off, so they may keep shorthand
("CI/CD"). This module is the presentation layer on top: the same facts,
written for a person. House rules, checked by tests/test_cv_copy.py:

  * no slash in anything rendered, URLs excepted ("12 of 12", "CI and CD");
  * no em dash as a separator;
  * nothing here may state a fact or a number the profile does not carry.

A project or job without CV copy falls back to its profile text, so a new
profile entry still renders.
"""
from pipeline import profile


def _cv() -> dict:
    return profile.get()["cv"]


def headline() -> str:
    return _cv()["headline"]


def availability_lines() -> list[str]:
    return list(_cv()["availability_lines"])


def projects_heading() -> str:
    return _cv()["projects_heading"]


def more_heading() -> str:
    return _cv()["more_heading"]


def featured() -> int:
    """How many projects get the full write-up; the rest get their short line."""
    return _cv()["featured_projects"]


def education_line() -> str:
    return _cv()["education_line"]


def project_copy(proj: dict) -> dict:
    """name, badge (the one number), text (full write-up) and short (one line)."""
    return dict(proj["cv"])


def experience_copy(entry: dict) -> tuple[str, str]:
    return entry["cv_period"], entry["cv_detail"]


def skill_groups() -> list[tuple[str, list[str]]]:
    return list(_cv()["skill_groups"])


def skill_label(skill: str) -> str:
    return _cv()["skill_labels"].get(skill, skill)


def language_level(level: str) -> str:
    return _cv()["language_levels"].get(level, level)


def all_text() -> str:
    """Every string this layer can put on the page: what the tests audit."""
    p = profile.get()
    parts = [headline(), *availability_lines(), projects_heading(), more_heading(), education_line()]
    parts += [label for label, _ in skill_groups()]
    parts += [skill_label(s) for _, names in skill_groups() for s in names]
    parts += [language_level(level) for level in p["languages"].values()]
    for proj in p["projects"]:
        copy = project_copy(proj)
        parts += [copy["name"], copy["badge"], copy["text"], copy["short"]]
    for entry in p["experience"]:
        parts += list(experience_copy(entry))
    return "\n".join(x for x in parts if x)
