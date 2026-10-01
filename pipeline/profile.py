"""The candidate profile: one YAML file, read and checked once per process.

Everything the pipeline states about the candidate comes from this file: the
CV, the letters and their closing lines, the honesty gate's banned claims, the
hard bars, the matcher's weights and the search queries. The code holds no
personal data; profile.example.yaml describes a made-up candidate.

Lookup: $JOB_PIPELINE_PROFILE, else <home>/profile.yaml. A missing or broken
profile raises ProfileError with every problem listed, before any network call.
"""
from __future__ import annotations

import os
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from pipeline import config

ROLE_TYPES = ("werkstudent", "internship")
ROLE_TYPE_LABELS = {"werkstudent": "Werkstudent (working student)", "internship": "internship"}
QUERY_SETS = ("adzuna_primary", "adzuna_secondary", "adzuna_fallback", "arbeitnow_daily", "arbeitnow",
              "bundesagentur", "linkedin", "xing", "indeed")
HARD_BARS = ("german_c1", "masters_degree", "completed_degree")


class ProfileError(RuntimeError):
    """The profile file is missing or does not describe a usable candidate."""


_loaded: dict[str, dict] = {}
_derived: dict[str, Any] = {}


def profile_path() -> Path:
    return Path(os.getenv("JOB_PIPELINE_PROFILE") or config.HOME / "profile.yaml")


def get() -> dict:
    """The active profile (validated, defaults filled in), cached per file."""
    path = profile_path()
    key = str(path.resolve())
    if key not in _loaded:
        reset()
        _loaded[key] = load(path)
    return _loaded[key]


def reset() -> None:
    """Forget the cached profile and everything derived from it."""
    _loaded.clear()
    _derived.clear()


def derived(name: str, build: Callable[[dict], Any]) -> Any:
    """A value built once from the active profile (compiled patterns, word lists)."""
    p = get()
    if name not in _derived:
        _derived[name] = build(p)
    return _derived[name]


# ─── loading and validation ──────────────────────────────────────────────────

def load(path: Path) -> dict:
    """Read, check and normalise one profile file."""
    path = Path(path)
    if not path.exists():
        raise ProfileError(f"no profile at {path}. Copy profile.example.yaml to profile.yaml and fill it in, "
                           "or set JOB_PIPELINE_PROFILE to your profile's path.")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ProfileError(f"{path}: not valid YAML ({e})") from e
    if not isinstance(data, dict):
        raise ProfileError(f"{path}: the top level must be a mapping of fields")
    problems: list[str] = []
    profile = _normalise(data, problems)
    if problems:
        raise ProfileError(f"{path}: " + "; ".join(problems))
    return profile


def _text(data: dict, key: str, problems: list[str], where: str = "", *, required: bool = False,
          default: str = "") -> str:
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        if required:
            problems.append(f"{where}{key} is required")
        return default
    if not isinstance(value, (str, int, float)):
        problems.append(f"{where}{key} must be text")
        return default
    return str(value).strip()


def _texts(data: dict, key: str, problems: list[str], where: str = "", *, required: bool = False) -> list[str]:
    value = data.get(key)
    if value is None:
        if required:
            problems.append(f"{where}{key} is required")
        return []
    if not isinstance(value, list) or not all(isinstance(v, (str, int, float)) for v in value):
        problems.append(f"{where}{key} must be a list of text items")
        return []
    out = [str(v).strip() for v in value if str(v).strip()]
    if required and not out:
        problems.append(f"{where}{key} must not be empty")
    return out


def _mapping(data: dict, key: str, problems: list[str], where: str = "", *, required: bool = False) -> dict:
    value = data.get(key)
    if value is None:
        if required:
            problems.append(f"{where}{key} is required")
        return {}
    if not isinstance(value, dict):
        problems.append(f"{where}{key} must be a mapping")
        return {}
    if required and not value:
        problems.append(f"{where}{key} must not be empty")
    return value


def _regexes(patterns: list[str], problems: list[str], where: str) -> list[str]:
    for pattern in patterns:
        try:
            re.compile(pattern, re.I)
        except re.error as e:
            problems.append(f"{where}: {pattern!r} is not a valid regular expression ({e})")
    return patterns


def _int(data: dict, key: str, problems: list[str], where: str, default: int) -> int:
    value = data.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        problems.append(f"{where}{key} must be a whole number")
        return default
    return value


def _normalise(data: dict, problems: list[str]) -> dict:
    p: dict[str, Any] = {}
    p["name"] = _text(data, "name", problems, required=True)
    p["first_name"] = _text(data, "first_name", problems, default=(p["name"].split() or [""])[0])
    p["email"] = _text(data, "email", problems, required=True)
    for key in ("phone", "website", "code_url", "work_authorization"):
        p[key] = _text(data, key, problems)
    p["location"] = _text(data, "location", problems, required=True)
    p["availability"] = _text(data, "availability", problems, required=True)

    edu = _mapping(data, "education", problems, required=True)
    p["education"] = {"degree": _text(edu, "degree", problems, "education.", required=True),
                      "school": _text(edu, "school", problems, "education.", required=True),
                      "status": _text(edu, "status", problems, "education.")}

    langs = _mapping(data, "languages", problems, required=True)
    p["languages"] = {str(k): str(v) for k, v in langs.items()}
    p["facts"] = _texts(data, "facts", problems)

    p["search"] = _search(_mapping(data, "search", problems, required=True), problems)
    bars = _mapping(data, "hard_bars", problems)
    unknown = sorted(set(bars) - set(HARD_BARS))
    if unknown:
        problems.append(f"hard_bars: unknown {unknown} (known: {list(HARD_BARS)})")
    p["hard_bars"] = {k: bool(bars.get(k, False)) for k in HARD_BARS}

    skills = _mapping(data, "core_skills", problems, required=True)
    p["core_skills"] = {str(k): str(v or "") for k, v in skills.items()}
    p["not_my_skills"] = _texts(data, "not_my_skills", problems)
    aliases = {str(k): str(v) for k, v in _mapping(data, "skill_aliases", problems).items()}
    bad = sorted(v for v in aliases.values() if v not in p["core_skills"])
    if bad:
        problems.append(f"skill_aliases point at skills that are not in core_skills: {bad}")
    p["skill_aliases"] = aliases

    p["projects"] = _projects(data.get("projects"), problems)
    p["experience"] = _experience(data.get("experience"), problems)
    p["verified_claims"] = _claims(data.get("verified_claims"), problems)
    p["cover_letter_hooks"] = _texts(data, "cover_letter_hooks", problems)
    p["evidence_anchors"] = _texts(data, "evidence_anchors", problems)
    p["honesty"] = _honesty(_mapping(data, "honesty", problems), problems)
    p["letters"] = _letters(_mapping(data, "letters", problems, required=True), p["name"], problems)
    p["cv"] = _cv(_mapping(data, "cv", problems, required=True), p["core_skills"], problems)
    p["scoring"] = _scoring(_mapping(data, "scoring", problems, required=True), problems)
    return p


def _search(s: dict, problems: list[str]) -> dict:
    w = "search."
    out: dict[str, Any] = {"city": _text(s, "city", problems, w, required=True),
                           "country": _text(s, "country", problems, w, required=True)}
    terms = [t.lower() for t in _texts(s, "country_terms", problems, w)]
    out["country_terms"] = terms or ([out["country"].lower()] if out["country"] else [])
    roles = [r.lower() for r in _texts(s, "role_types", problems, w)] or ["werkstudent"]
    bad = [r for r in roles if r not in ROLE_TYPES]
    if bad:
        problems.append(f"search.role_types: {bad} not supported (use {list(ROLE_TYPES)})")
    out["role_types"] = [r for r in roles if r in ROLE_TYPES]
    out["target_roles"] = _texts(s, "target_roles", problems, w)
    out["target_titles"] = [t.lower() for t in _texts(s, "target_titles", problems, w)]
    out["target_companies"] = _text(s, "target_companies", problems, w)
    out["remote_regions"] = [t.lower() for t in _texts(s, "remote_regions", problems, w)]
    out["adzuna_country"] = _text(s, "adzuna_country", problems, w, default="de").lower()
    queries = _mapping(s, "queries", problems, w)
    unknown = sorted(set(queries) - set(QUERY_SETS))
    if unknown:
        problems.append(f"search.queries: unknown {unknown} (known: {list(QUERY_SETS)})")
    out["queries"] = {name: _texts(queries, name, problems, "search.queries.") for name in QUERY_SETS}
    pages = s.get("firecrawl_pages") or []
    out["firecrawl_pages"] = []
    if not isinstance(pages, list):
        problems.append("search.firecrawl_pages must be a list")
        pages = []
    for i, page in enumerate(pages):
        if not isinstance(page, dict) or not page.get("url") or not page.get("source"):
            problems.append(f"search.firecrawl_pages[{i}] needs url and source")
            continue
        out["firecrawl_pages"].append({"url": str(page["url"]), "source": str(page["source"]),
                                       "location": str(page.get("location") or f"{out['city']}, {out['country']}")})
    return out


def _projects(raw: Any, problems: list[str]) -> list[dict]:
    if not isinstance(raw, list) or not raw:
        problems.append("projects must be a non-empty list")
        return []
    out = []
    for i, proj in enumerate(raw):
        w = f"projects[{i}]."
        if not isinstance(proj, dict):
            problems.append(f"projects[{i}] must be a mapping")
            continue
        name = _text(proj, "name", problems, w, required=True)
        description = _text(proj, "description", problems, w, required=True)
        evidence = _text(proj, "evidence", problems, w)
        cv = _mapping(proj, "cv", problems, w)
        match = _mapping(proj, "match", problems, w)
        out.append({
            "name": name,
            "key": _text(proj, "key", problems, w, default=name.lower()),
            "description": description,
            "stack": _texts(proj, "stack", problems, w),
            "evidence": evidence,
            "status": _text(proj, "status", problems, w),
            "repo": _text(proj, "repo", problems, w),
            "repo_url": _text(proj, "repo_url", problems, w),
            "repo_label": _text(proj, "repo_label", problems, w),
            "repo_lead": _text(proj, "repo_lead", problems, w),
            "cv": {"name": _text(cv, "name", problems, w + "cv.", default=name),
                   "badge": _text(cv, "badge", problems, w + "cv.", default=evidence),
                   "text": _text(cv, "text", problems, w + "cv.", default=description),
                   "short": _text(cv, "short", problems, w + "cv.", default=description)},
            "match": {"strong": _regexes(_texts(match, "strong", problems, w + "match."), problems, w + "match.strong"),
                      "supporting": _regexes(_texts(match, "supporting", problems, w + "match."), problems,
                                             w + "match.supporting")},
        })
    keys = [p["key"] for p in out]
    if len(set(keys)) != len(keys):
        problems.append("projects: every key must be unique")
    return out


def _experience(raw: Any, problems: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append("experience must be a list")
        return []
    out = []
    for i, e in enumerate(raw):
        w = f"experience[{i}]."
        if not isinstance(e, dict):
            problems.append(f"experience[{i}] must be a mapping")
            continue
        period = _text(e, "period", problems, w)
        detail = _text(e, "detail", problems, w)
        out.append({"role": _text(e, "role", problems, w, required=True),
                    "company": _text(e, "company", problems, w, required=True),
                    "period": period, "detail": detail,
                    "cv_period": _text(e, "cv_period", problems, w, default=period),
                    "cv_detail": _text(e, "cv_detail", problems, w, default=detail)})
    return out


def _claims(raw: Any, problems: list[str]) -> list[dict]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append("verified_claims must be a list")
        return []
    out = []
    for i, c in enumerate(raw):
        if not isinstance(c, dict):
            problems.append(f"verified_claims[{i}] must be a mapping with claim and evidence")
            continue
        out.append({"claim": _text(c, "claim", problems, f"verified_claims[{i}].", required=True),
                    "evidence": _text(c, "evidence", problems, f"verified_claims[{i}].", required=True)})
    return out


def _rules(raw: Any, problems: list[str], where: str) -> list[tuple[str, str]]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        problems.append(f"{where} must be a list")
        return []
    out = []
    for i, r in enumerate(raw):
        if not isinstance(r, dict) or not r.get("pattern") or not r.get("reason"):
            problems.append(f"{where}[{i}] needs pattern and reason")
            continue
        out.append((str(r["pattern"]), str(r["reason"])))
    _regexes([pattern for pattern, _ in out], problems, where)
    return out


def _honesty(h: dict, problems: list[str]) -> dict:
    return {"banned": _rules(h.get("banned"), problems, "honesty.banned"),
            "letter_only": _rules(h.get("letter_only"), problems, "honesty.letter_only")}


def _letters(letters: dict, name: str, problems: list[str]) -> dict:
    w = "letters."
    availability = _text(letters, "availability_sentence", problems, w, required=True)
    return {"persona": _text(letters, "persona", problems, w, default="a candidate applying for working-student roles"),
            "availability_sentence": availability,
            "closing": _text(letters, "closing", problems, w, default=availability),
            "sign_off": _text(letters, "sign_off", problems, w, default=f"Kind regards,\n{name}"),
            "rules": _texts(letters, "rules", problems, w)}


def _cv(cv: dict, skills: dict, problems: list[str]) -> dict:
    w = "cv."
    groups = []
    raw_groups = cv.get("skill_groups")
    if raw_groups is None:
        raw_groups = [{"label": "Skills", "skills": list(skills)}]
    if not isinstance(raw_groups, list):
        problems.append("cv.skill_groups must be a list")
        raw_groups = []
    for i, g in enumerate(raw_groups):
        if not isinstance(g, dict) or not g.get("label"):
            problems.append(f"cv.skill_groups[{i}] needs a label and skills")
            continue
        names = _texts(g, "skills", problems, f"cv.skill_groups[{i}].")
        unknown = [n for n in names if n not in skills]
        if unknown:
            problems.append(f"cv.skill_groups[{i}]: {unknown} are not in core_skills")
        groups.append((str(g["label"]), names))
    labels = {str(k): str(v) for k, v in _mapping(cv, "skill_labels", problems, w).items()}
    unknown = sorted(k for k in labels if k not in skills)
    if unknown:
        problems.append(f"cv.skill_labels: {unknown} are not in core_skills")
    return {"headline": _text(cv, "headline", problems, w, required=True),
            "availability_lines": _texts(cv, "availability_lines", problems, w, required=True),
            "education_line": _text(cv, "education_line", problems, w),
            "featured_projects": _int(cv, "featured_projects", problems, w, 3),
            "projects_heading": _text(cv, "projects_heading", problems, w, default="Projects"),
            "more_heading": _text(cv, "more_heading", problems, w, default="Also built"),
            "skill_groups": groups,
            "skill_labels": labels,
            "language_levels": {str(k): str(v) for k, v in _mapping(cv, "language_levels", problems, w).items()}}


def _scoring(s: dict, problems: list[str]) -> dict:
    w = "scoring."
    groups = s.get("core_groups") or []
    if not isinstance(groups, list) or not all(isinstance(g, list) and g for g in groups):
        problems.append("scoring.core_groups must be a list of non-empty keyword lists")
        groups = []
    weights = []
    raw = s.get("skill_weights")
    if not isinstance(raw, list) or not raw:
        problems.append("scoring.skill_weights must be a non-empty list")
        raw = []
    for i, entry in enumerate(raw):
        if (not isinstance(entry, dict) or not entry.get("label") or not isinstance(entry.get("weight"), int)
                or not isinstance(entry.get("keywords"), list) or not entry["keywords"]):
            problems.append(f"scoring.skill_weights[{i}] needs label, a whole-number weight and keywords")
            continue
        weights.append(([str(k).lower() for k in entry["keywords"]], entry["weight"], str(entry["label"])))
    min_groups = _int(s, "min_core_groups", problems, w, 2)
    if groups and min_groups > len(groups):
        problems.append("scoring.min_core_groups is larger than the number of core_groups")
    return {"core_groups": [[str(k).lower() for k in g] for g in groups],
            "min_core_groups": min_groups,
            "skill_weights": weights,
            "english_bonus": _int(s, "english_bonus", problems, w, 10),
            "location_bonus": _int(s, "location_bonus", problems, w, 10),
            "role_bonus": _int(s, "role_bonus", problems, w, 10)}


# ─── accessors ───────────────────────────────────────────────────────────────

def city() -> str:
    return get()["search"]["city"]


def accepted_role_types() -> tuple[str, ...]:
    return tuple(get()["search"]["role_types"])


def queries(name: str) -> list[str]:
    return list(get()["search"]["queries"][name])


def file_token(text: str) -> str:
    """Text as a file-name part: "Alex Example" → "Alex_Example"."""
    return re.sub(r"\W+", "_", text or "").strip("_")


def cv_filename() -> str:
    return f"{file_token(get()['name'])}_CV.pdf"


def letter_filename(company: str) -> str:
    token = file_token(company.split("(")[0])[:24] or "Company"
    return f"{file_token(get()['name'])}_Cover_Letter_{token}.pdf"


def education_line(p: dict | None = None) -> str:
    edu = (p or get())["education"]
    return f"{edu['degree']}, {edu['school']}" + (f" ({edu['status']})" if edu["status"] else "")


def hard_bar_summary(p: dict | None = None) -> str:
    bars = (p or get())["hard_bars"]
    parts = [text for key, text in (("german_c1", "German at C1 or above"),
                                    ("masters_degree", "a master's degree"),
                                    ("completed_degree", "a finished degree")) if bars[key]]
    return f"postings that require {', '.join(parts)} are out of reach" if parts else "none"


def profile_as_text() -> str:
    """The candidate as plain text: the context every model call gets."""
    p = get()
    contact = " | ".join(x for x in (p["email"], p["phone"]) if x)
    web = " | ".join(x for x in (p["website"], p["code_url"]) if x)
    roles = ", ".join(ROLE_TYPE_LABELS[r] for r in p["search"]["role_types"])
    lines = [f"Name: {p['name']}",
             f"Contact: {contact}",
             f"Location: {p['location']}" + (f" | Web: {web}" if web else ""),
             f"Education: {education_line(p)}",
             f"Availability: {p['availability']} | Role types sought: {roles}"]
    if p["work_authorization"]:
        lines.append(f"Work authorization: {p['work_authorization']}")
    lines.append("Languages: " + ", ".join(f"{lang} ({level})" for lang, level in p["languages"].items()))
    lines.append(f"Hard bars: {hard_bar_summary(p)}")
    search = p["search"]
    if search["target_roles"]:
        lines += ["", f"Target roles: {', '.join(search['target_roles'])}"]
    if search["target_companies"]:
        lines.append(f"Target companies: {search['target_companies']}")
    lines += ["", "Core skills:"] + [f"  {k}: {v}" for k, v in p["core_skills"].items()]
    lines += ["", "Projects:"] + [f"  {pr['name']}: {pr['description']}"
                                  + (f" | Evidence: {pr['evidence']}" if pr["evidence"] else "")
                                  for pr in p["projects"]]
    if p["experience"]:
        lines += ["", "Work experience:"] + [f"  {e['role']} at {e['company']} ({e['period']}): {e['detail']}"
                                             for e in p["experience"]]
    return "\n".join(lines)
