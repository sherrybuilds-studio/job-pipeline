"""Daily-run matcher: hard bars first, then gates, then a rule-based score. No LLM.

For every posting, in order:

  0. hard bars (profile hard_bars): German at C1 or above required, a running
     or finished master's required, any finished degree required. A stated
     preference ("C1 von Vorteil", "Master wünschenswert") is not a bar, and
     anything that also accepts a bachelor is not a master's bar.
  1. entry level: working-student, intern or part-time wording, no senior title
  2. non-tech roles out (marketing, sales, finance, ...)
  3. core tech: at least scoring.min_core_groups of the profile's core groups
  4. location: the search city, remote in the country, or remote elsewhere
  5. score: the profile's skill weights plus language, location and role
     bonuses; kept when the score reaches min_score

A dropped posting never reaches the digest; hard-bar drops are logged with
their reason so the run log shows what was filtered and why.
"""
import logging
import re

from pipeline import profile

logger = logging.getLogger(__name__)

# ─── vocabulary (job-market words, not preferences) ──────────────────────────

# Reject only when the ROLE itself is one of these, not when it is mentioned:
# "Marketing Automation" is marketing, "AI Automatisierung HR" is automation.
_NOT_YOUR_ROLE = [
    "marketing specialist", "marketing manager", "marketing praktikant",
    "sales representative", "sales manager", "sales intern",
    "verkauf", "vertrieb", "business development representative",
    "printdesign", "grafikdesigner", "graphic designer", "ux design", "ui design",
    "finanzen", "accounting", "buchhaltung", "finance intern",
    "law intern", "legal counsel", "anwalt", "recht", "compliance officer",
    "operations manager", "projektmanager", "produktmanager", "product owner",
    "management consultant", "strategieberater", "strategy intern",
    "social media manager", "social media praktikant", "seo praktikant",
    "copywriter", "copywriting", "redakteur", "content manager",
    "customer success manager", "kundenservice", "kundensupport",
    "procurement", "einkauf", "supply chain", "logistik", "logistics intern",
    "assistenz", "sekretär", "office management", "empfang",
    "qa analyst", "qa tester", "quality analyst", "software tester",
    "research analyst", "marktforschung",
    "event manager", "messe", "public relations", "pr manager",
    "growth marketing", "growth hacker", "revenue operations",
    "talent acquisition", "personalentwicklung", "recruiter",
    "hr manager", "human resources",  # only when the role IS HR, not "AI for HR"
]

# Entry gate: a student can only take working-student, intern or part-time roles.
MUST_HAVE = [
    "werkstudent", "working student", "internship", "part-time student",
    "student assistant", "studentische", "hiwi", "teilzeit",
    "part time", "part-time", "studentenjob", "student job", " intern ",
    "praktikum", "praktikant", "20 std", "20 stunden", "20 hours", "20h/",
    "15-20", "16-20", "duales studium", "dual student",
]

_JUNIOR_TERMS = ["junior", "entry level", "entry-level", "graduate", "trainee", "absolvent", "berufseinsteiger",
                 "einsteiger"]

# Title-level seniority reject: a part-time student cannot fill these.
_SENIORITY_REJECT = ["senior", "sr.", "(sr", "lead ", "principal", "staff ", "head of", "director", "vp ", "chief ",
                     "architect", "team lead", "teamlead", "teamleiter", "manager"]

_GERMAN_REQUIRED = [
    "deutsch c1", "deutsch c2", "c1 deutsch", "c2 deutsch",
    "german c1", "german c2", "deutschkenntnisse c1", "deutschkenntnisse c2",
    "verhandlungssicheres deutsch", "fließendes deutsch",
    "german native", "native german",
]

# 4+ German function words: the listing is written in German.
_GERMAN_MARKERS = [" wir ", " sie ", " ihr ", " haben ", " werden ", " und ", " oder ", " für ", " mit ", " sind ",
                   " die ", " der ", " das "]

_REMOTE_TERMS = ["remote", "home office", "homeoffice", "home-office", "work from anywhere"]

# Major German cities; onsite in one that is not the search city is out of reach
# for a part-time role. Spellings of one city share a tuple.
_CITIES = [("berlin",), ("münchen", "munich"), ("hamburg",), ("frankfurt",), ("köln", "cologne"), ("stuttgart",),
           ("düsseldorf",), ("leipzig",), ("dresden",), ("hannover",), ("nürnberg", "nuremberg"), ("bremen",),
           ("dortmund",), ("essen",), ("karlsruhe",), ("mannheim",)]

# Remote-but-not-for-us restrictions (location field only)
_NON_EU_ONLY = ["usa only", "us only", "us-only", "united states", "north america", "canada", "americas", "latam",
                "apac", "asia", "australia", "new zealand", "africa", "middle east"]

_REMOTE_COUNTRY_BONUS = 6
_REMOTE_ABROAD_BONUS = 3
_JUNIOR_BONUS = 8
_TARGET_TITLE_BONUS = 5


def city_names() -> tuple[str, ...]:
    """The search city and its other spellings, lower-case."""
    city = profile.city().lower()
    for names in _CITIES:
        if city in names:
            return names
    return (city,)


def other_cities() -> list[str]:
    own = set(city_names())
    return [name for names in _CITIES if not own & set(names) for name in names]


def max_score() -> int:
    """Best possible score: every skill group plus every bonus."""
    s = profile.get()["scoring"]
    return sum(w for _, w, _ in s["skill_weights"]) + s["english_bonus"] + s["location_bonus"] + s["role_bonus"]


def _not_my_skill_terms() -> list[str]:
    def build(p: dict) -> list[str]:
        # one- and two-letter names ("R", "Go") only count as whole words
        return [f" {t} " if len(t) <= 2 else t for t in (s.lower() for s in p["not_my_skills"])]
    return profile.derived("matcher.not_my_skill_terms", build)


# ─── hard bars ───────────────────────────────────────────────────────────────

_PREFERENCE_MARKERS = [
    "von vorteil", "wünschenswert", "wuenschenswert", "nice to have", "kein muss",
    "idealerweise", "ideally", "a plus", "ein plus", "plus:", "preferred", "bonus",
    "optional", "gerne gesehen", "is a plus",
]
_BACHELOR_OK_MARKERS = ["bachelor", "b.sc", "bsc", "b. sc"]

# "Germany" and "Deutschland" are places, not the language.
_GERMAN_WORD = r"(?:deutsch(?!land)\w*|german\b)"
_GERMAN_HARD_LEVEL = (r"(?:c1|c2|verhandlungssicher\w*|muttersprachlich\w*|muttersprache|"
                      r"native|fließend\w*|fliessend\w*|fluent)")
_GERMAN_BAR_RE = re.compile(
    rf"(?:{_GERMAN_WORD}[^.;!?\n]{{0,60}}?{_GERMAN_HARD_LEVEL}"
    rf"|{_GERMAN_HARD_LEVEL}[^.;!?\n]{{0,60}}?{_GERMAN_WORD})",
    re.IGNORECASE)
# Master's required: a bachelor named in the same sentence rescues it.
_MASTER_BAR_RE = re.compile(
    r"(?:laufendes\s+master\w*|master[-\s]?studium|master'?s?\s+degree|master\s+degree)", re.IGNORECASE)
# A finished degree of any kind: nothing rescues it for someone still studying.
_COMPLETED_DEGREE_RE = re.compile(
    r"abgeschlossene[a-z]*\s+(?:studium|hochschulstudium|\w*studium|master\w*|bachelor\w*)", re.IGNORECASE)

_SENTENCE_SPLIT = re.compile(r"[.;!?\n•·]|\s{3,}")


def _sentence_around(text: str, start: int, end: int) -> str:
    """The sentence a match sits in. A preference one sentence earlier ("if you
    live in Berlin that is a plus.") must not rescue the requirement in the next
    one ("Your German is at least C1")."""
    lo = 0
    for m in _SENTENCE_SPLIT.finditer(text, 0, start):
        lo = m.end()
    m = _SENTENCE_SPLIT.search(text, end)
    hi = m.start() if m else len(text)
    return text[lo:hi].lower()


def _is_preference(sentence: str) -> bool:
    return any(m in sentence for m in _PREFERENCE_MARKERS)


def hard_bar_reason(job: dict) -> str | None:
    """None keeps the posting. Otherwise a short reason for the log, such as
    "german-c1 ('deutschkenntnisse c1')". Title and description are both read;
    only the bars switched on in the profile apply."""
    bars = profile.get()["hard_bars"]
    text = f"{job.get('title', '')} {job.get('description', '')}"
    reasons: list[str] = []

    if bars["german_c1"]:
        for m in _GERMAN_BAR_RE.finditer(text):
            if _is_preference(_sentence_around(text, m.start(), m.end())):
                continue
            reasons.append(f"german-c1 ({m.group(0).strip().lower()!r})")
            break

    degree_hit = False
    if bars["masters_degree"]:
        for m in _MASTER_BAR_RE.finditer(text):
            sentence = _sentence_around(text, m.start(), m.end())
            # "Bachelor- oder Masterstudium" accepts a bachelor student
            if _is_preference(sentence) or any(b in sentence for b in _BACHELOR_OK_MARKERS):
                continue
            reasons.append(f"masters-degree ({m.group(0).strip().lower()!r})")
            degree_hit = True
            break
    if bars["completed_degree"] and not degree_hit:
        for m in _COMPLETED_DEGREE_RE.finditer(text):
            # no bachelor rescue: a finished bachelor is out of reach while studying
            if _is_preference(_sentence_around(text, m.start(), m.end())):
                continue
            reasons.append(f"completed-degree ({m.group(0).strip().lower()!r})")
            break

    return "; ".join(reasons) or None


# ─── gates ───────────────────────────────────────────────────────────────────

def _location_tier(job: dict) -> tuple[int, str] | None:
    """Score bonus and label for the job's location, or None for a hard reject.

    Search city: full bonus | remote in the country | remote elsewhere | onsite
    elsewhere: rejected."""
    search = profile.get()["search"]
    loc = job.get("location", "").lower()
    title = job.get("title", "").lower()
    desc = job.get("description", "").lower()[:600]
    hay = f" {title} {loc} {desc} "
    in_country = any(t in loc for t in search["country_terms"])

    eu_ok = in_country or "europe" in loc or "worldwide" in loc
    if loc and any(t in loc for t in _NON_EU_ONLY) and not eu_ok:
        return None

    names = city_names()
    if any(n in loc or n in title or n in desc for n in names):
        return profile.get()["scoring"]["location_bonus"], search["city"]

    is_remote = any(t in hay for t in _REMOTE_TERMS)
    if is_remote and in_country:
        return _REMOTE_COUNTRY_BONUS, f"Remote ({search['country']})"
    if is_remote:
        return _REMOTE_ABROAD_BONUS, "Remote (elsewhere)"

    if any(c in loc for c in other_cities()):
        return None  # onsite in another city
    if in_country or not loc:
        return 0, ""  # unknown or country-wide: kept, no bonus
    return None  # onsite abroad


def _role_fit_bonus(title: str) -> tuple[int, str]:
    role_bonus = profile.get()["scoring"]["role_bonus"]
    t = f" {title.lower()} "
    if any(k in t for k in ["werkstudent", "working student", "intern", "hiwi", "studentische"]):
        return role_bonus, "Working-student or intern role"
    if any(k in t for k in _JUNIOR_TERMS):
        return min(_JUNIOR_BONUS, role_bonus), "Junior role"
    if any(k in t for k in profile.get()["search"]["target_titles"]):
        return min(_TARGET_TITLE_BONUS, role_bonus), "Target role title"
    return 0, ""


def _is_hr_core_role(title: str, description: str) -> bool:
    """True only when the role IS an HR or recruiting job, not when it mentions HR."""
    text = f"{title} {description}".lower()
    return any(t in text for t in ["hr manager", "human resources", "talent acquisition", "recruiter",
                                   "personalentwicklung", "hr praktikant"])


def _reject_non_tech_role(job: dict) -> bool:
    title = job.get("title", "").lower()
    description = job.get("description", "").lower()[:500]
    combined = f"{title} {description}"
    for term in _NOT_YOUR_ROLE:
        if term not in combined:
            continue
        # software testing that is really automation work stays
        if term in ("qa analyst", "qa tester", "quality analyst", "software tester") and any(
                kw in combined for kw in [" ai", " automation", " python", " ml "]):
            continue
        # HR words only reject when HR is the core of the role
        if term in ("hr manager", "human resources", "talent acquisition", "recruiter") and not _is_hr_core_role(
                title, description):
            continue
        return True
    return False


def _passes_core_tech_gate(job: dict) -> bool:
    scoring = profile.get()["scoring"]
    haystack = " " + (job.get("title", "") + " " + job.get("description", "")).lower() + " "
    matched = sum(1 for group in scoring["core_groups"] if any(kw in haystack for kw in group))
    return matched >= scoring["min_core_groups"]


def _passes_entry_gate(job: dict) -> bool:
    """Working-student, internship or part-time only: never senior, never full-time."""
    title = " " + job.get("title", "").lower() + " "
    haystack = title + " " + job.get("description", "").lower()
    if any(t in title for t in _SENIORITY_REJECT):
        return False
    return any(kw in haystack for kw in MUST_HAVE)


# ─── score ───────────────────────────────────────────────────────────────────

def _score_job(job: dict) -> dict:
    scoring = profile.get()["scoring"]
    haystack = " " + (job.get("title", "") + " " + job.get("description", "")).lower() + " "

    score = 0
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    for keywords, weight, label in scoring["skill_weights"]:
        if any(kw in haystack for kw in keywords):
            score += weight
            matched_skills.append(label)
        else:
            missing_skills.append(label)

    if any(p in haystack for p in _GERMAN_REQUIRED):
        score -= 20
        missing_skills.append("German C1 or C2 required")
        red_flags = ["German C1 or C2 required"]
    else:
        if sum(1 for m in _GERMAN_MARKERS if m in haystack) < 4:
            score += scoring["english_bonus"]
            matched_skills.append("English listing")
        red_flags = []

    loc_tier = _location_tier(job)
    if loc_tier and loc_tier[0]:
        score += loc_tier[0]
        matched_skills.append(loc_tier[1])

    role_bonus, role_label = _role_fit_bonus(job.get("title", ""))
    if role_bonus:
        score += role_bonus
        matched_skills.append(role_label)

    # Not-my-skills penalty: 15 per term in the title, at most 30
    title_padded = " " + job.get("title", "").lower() + " "
    penalty = 0
    for term in _not_my_skill_terms():
        if term in title_padded and penalty < 30:
            penalty += 15
            missing_skills.append(f"Title asks for {term.strip()} (not in the profile)")
    score -= penalty

    return {"score": max(0, score), "matched_skills": matched_skills, "missing_skills": missing_skills,
            "reasons": matched_skills[:3], "red_flags": red_flags}


def match_jobs(jobs: list[dict], min_score: int = 40) -> list[dict]:
    matched = []
    rejected: dict[str, int] = {"hard_bar": 0, "not_entry_level": 0, "non_tech_role": 0, "core_tech_fail": 0,
                                "location_mismatch": 0, "below_threshold": 0}
    top = max_score()
    for job in jobs:
        label = f"{job['title']} @ {job.get('company', '?')}"
        bar = hard_bar_reason(job)
        if bar:
            rejected["hard_bar"] += 1
            logger.info(f"Dropped (hard bar: {bar}): {label}")
            continue
        if not _passes_entry_gate(job):
            rejected["not_entry_level"] += 1
            logger.debug(f"Rejected (not entry level): {label}")
            continue
        if _reject_non_tech_role(job):
            rejected["non_tech_role"] += 1
            logger.debug(f"Rejected (non-tech role): {label}")
            continue
        if not _passes_core_tech_gate(job):
            rejected["core_tech_fail"] += 1
            logger.debug(f"Rejected (core tech gate): {label}")
            continue
        if _location_tier(job) is None:
            rejected["location_mismatch"] += 1
            logger.debug(f"Rejected (location): {label}")
            continue
        result = _score_job(job)
        logger.info(f"Score {result['score']}/{top} {label}")
        if result["score"] >= min_score:
            matched.append({**job, **result})
        else:
            rejected["below_threshold"] += 1
    logger.info(f"Match filter summary: {rejected}")
    return sorted(matched, key=lambda x: x["score"], reverse=True)
