"""The sourcing pass: find, filter and map roles. It never applies.

    python -m pipeline source        # → data/sourcing/YYYY-MM-DD.md and .json

Discovery, dedup and mapping only. Nothing here applies, builds a kit, or
writes to the tracker or Telegram. A person picks from the report; kits are
built separately (kit.py).

The pass, in order:
  1. pull every registered ATS board (Greenhouse, Ashby, Lever, Personio,
     SmartRecruiters, Join): ats_boards.py, live by construction;
  2. public discovery: LinkedIn guest, XING, Indeed through Firecrawl or
     blocked, plus Arbeitnow, Adzuna and the Bundesagentur;
  3. merge duplicates (LinkedIn and XING copies fold onto the ATS row);
  4. gates: hard bars → accepted role type (roletype.py; a posting with no
     listing URL is a speculative target and dropped) → non-tech → full-time
     only or no student option → location → already applied, kitted or parked
     → dead (liveness.py, the employer's own page). Every drop is logged with
     its reason and listed in the report; nothing is discarded silently;
  5. per role: named stack, project map, German level asked, hours, listing
     language, fit score (matcher._score_job);
  6. READY = maps to at least one of the profile's projects AND the employer
     page was verified open; everything else that passed the gates is WEAK.

The whole run is bounded by --budget seconds; every source reports what it
returned and whether it was blocked.
"""
import argparse
import asyncio
import html
import json
import logging
import re
import sys
import time
from datetime import date
from pathlib import Path

import httpx

from pipeline import config, matcher, profile, roletype
from pipeline.ats_boards import REGISTRY_PATH, add_boards, board_from_url, load_registry, pull_all, save_registry
from pipeline.liveness import verify as verify_live
from pipeline.public_boards import SourceStatus, indeed_search, linkedin_search, xing_search

logger = logging.getLogger("sourcing")

APPS_DIR = config.APPS_DIR
OUT_DIR = config.SOURCING_DIR

DROP_KEYS = ("c1", "degree", "internship", "full_time", "no_student_role", "non_tech", "location",
             "already_applied", "dead")

# ─── per-role extraction ─────────────────────────────────────────────────────

# (label, regex, flags). Output follows the order the tools appear in the text.
_I = re.IGNORECASE
STACK_VOCAB: list[tuple[str, str, int]] = [
    ("Python", r"\bpython\b", _I), ("n8n", r"\bn8n\b", _I), ("Make", r"\bMake(?:\.com)?\b(?=[\s,;./)]|$)", 0),
    ("Zapier", r"\bzapier\b", _I), ("HubSpot", r"\bhubspot\b", _I), ("Salesforce", r"\bsalesforce\b", _I),
    ("Pipedrive", r"\bpipedrive\b", _I), ("Clay", r"\bClay\b", 0), ("Notion", r"\bnotion\b", _I),
    ("AWS", r"\baws\b", _I), ("Azure", r"\bazure\b", _I), ("GCP", r"\bgcp\b|google cloud", _I),
    ("Docker", r"\bdocker\b", _I), ("Kubernetes", r"\bkubernetes\b|\bk8s\b", _I),
    ("PostgreSQL", r"\bpostgres(?:ql)?\b", _I), ("SQL", r"\bsql\b", _I), ("Supabase", r"\bsupabase\b", _I),
    ("Redshift", r"\bredshift\b", _I), ("BigQuery", r"\bbigquery\b", _I), ("Snowflake", r"\bsnowflake\b", _I),
    ("FastAPI", r"\bfastapi\b", _I), ("Django", r"\bdjango\b", _I), ("Flask", r"\bflask\b", _I),
    ("React", r"\breact\b(?! native)", _I), ("React Native", r"react native", _I),
    ("Next.js", r"\bnext\.?js\b", _I), ("Vue", r"\bvue(?:\.js)?\b", _I), ("TypeScript", r"\btypescript\b", _I),
    ("JavaScript", r"\bjavascript\b", _I), ("Node.js", r"\bnode\.?js\b", _I), ("Tailwind", r"\btailwind\b", _I),
    ("LangChain", r"\blangchain\b", _I), ("LlamaIndex", r"\bllamaindex\b", _I),
    ("OpenAI", r"\bopenai\b", _I), ("Claude", r"\bclaude\b(?! code)", _I), ("Claude Code", r"claude code", _I),
    ("Gemini", r"\bgemini\b", _I), ("LLM", r"\bllms?\b|large language model", _I), ("RAG", r"\brag\b", _I),
    ("Qdrant", r"\bqdrant\b", _I), ("Pinecone", r"\bpinecone\b", _I), ("Weaviate", r"\bweaviate\b", _I),
    ("ChromaDB", r"\bchroma(?:db)?\b", _I), ("pgvector", r"\bpgvector\b", _I),
    ("Vector DB", r"vector ?(?:database|db|store)s?|vektordatenbank", _I),
    ("Playwright", r"\bplaywright\b", _I), ("Selenium", r"\bselenium\b", _I), ("Scrapy", r"\bscrapy\b", _I),
    ("BeautifulSoup", r"beautifulsoup|\bbs4\b", _I), ("pandas", r"\bpandas\b", _I), ("NumPy", r"\bnumpy\b", _I),
    ("scikit-learn", r"scikit-learn|\bsklearn\b", _I), ("PyTorch", r"\bpytorch\b", _I),
    ("TensorFlow", r"\btensorflow\b", _I), ("Hugging Face", r"hugging ?face", _I),
    ("Airflow", r"\bairflow\b", _I), ("dbt", r"\bdbt\b", 0), ("Spark", r"\bspark\b", _I), ("Kafka", r"\bkafka\b", _I),
    ("Tableau", r"\btableau\b", _I), ("Power BI", r"power ?bi\b", _I), ("Plotly", r"\bplotly\b", _I),
    ("Excel", r"\bexcel\b", _I), ("Git", r"\bgit\b", _I), ("GitHub Actions", r"github actions", _I),
    ("CI/CD", r"\bci/cd\b|\bcicd\b", _I), ("Linux", r"\blinux\b", _I), ("REST", r"\brest(?:ful)?\b", _I),
    ("GraphQL", r"\bgraphql\b", _I), ("Webhooks", r"\bwebhooks?\b", _I), ("SOAP", r"\bsoap\b", 0),
    ("Telegram", r"\btelegram\b", _I), ("WhatsApp", r"\bwhatsapp\b", _I), ("Twilio", r"\btwilio\b", _I),
    ("Rasa", r"\bRasa\b", 0), ("Cursor", r"\bCursor\b", 0), ("MCP", r"\bMCP\b", 0),
    ("Prompt Engineering", r"prompt[- ]engineering", _I), ("Fine-tuning", r"fine-?tun", _I),
    ("Streamlit", r"\bstreamlit\b", _I), ("Gradio", r"\bgradio\b", _I), ("Ollama", r"\bollama\b", _I),
    ("Java", r"\bjava\b(?!script)", _I), ("C++", r"\bc\+\+", _I), ("Go", r"\bGolang\b|\bGo\b(?= |,|/)", 0),
    ("Rust", r"\bRust\b", 0), ("SAP", r"\bSAP\b", 0), ("Power Automate", r"power automate", _I),
    ("UiPath", r"\buipath\b", _I), ("Slack", r"\bslack\b", _I), ("Jira", r"\bjira\b", _I),
]


def named_stack(text: str) -> list[str]:
    hits: list[tuple[int, str]] = []
    for label, pat, flags in STACK_VOCAB:
        m = re.search(pat, text or "", flags)
        if m:
            hits.append((m.start(), label))
    return [label for _, label in sorted(hits)]


def _project_rules() -> list[tuple[str, list[re.Pattern], list[re.Pattern]]]:
    def build(p: dict):
        return [(proj["key"], [re.compile(x, re.I) for x in proj["match"]["strong"]],
                 [re.compile(x, re.I) for x in proj["match"]["supporting"]]) for proj in p["projects"]]
    return profile.derived("sourcing.project_rules", build)


def map_projects(text: str) -> list[str]:
    """The profile projects a posting's own words map to, strongest first. One
    strong signal or two supporting ones make a map; a lone generic word never
    does, because READY has to mean an honest, matched letter can be written."""
    scored = []
    for order, (key, strong, weak) in enumerate(_project_rules()):
        s_hits = sum(1 for p in strong if p.search(text or ""))
        w_hits = sum(1 for p in weak if p.search(text or ""))
        if s_hits >= 1 or w_hits >= 2:
            scored.append((-(2 * s_hits + w_hits), order, key))
    return [key for _, _, key in sorted(scored)]


_G = r"(?:deutsch(?!land)\w*|german\b)"


def german_level(text: str) -> str:
    t = text or ""
    if re.search(rf"{_G}[^.\n]{{0,40}}\bc2\b|\bc2\b[^.\n]{{0,40}}{_G}", t, re.I):
        return "C2"
    if re.search(rf"{_G}[^.\n]{{0,40}}\bc1\b|\bc1\b[^.\n]{{0,40}}{_G}", t, re.I):
        return "C1"
    if re.search(rf"verhandlungssicher\w*[^.\n]{{0,30}}{_G}|{_G}[^.\n]{{0,30}}verhandlungssicher", t, re.I):
        return "verhandlungssicher"
    if re.search(rf"muttersprach\w*|native {_G}|{_G}[^.\n]{{0,30}}native", t, re.I):
        return "muttersprachlich"
    if re.search(rf"flie(?:ß|ss)end\w*[^.\n]{{0,30}}{_G}|{_G}[^.\n]{{0,30}}flie(?:ß|ss)end|fluent (?:in )?{_G}", t,
                 re.I):
        return "fließend"
    if re.search(rf"{_G}[^.\n]{{0,40}}(?:plus|von vorteil|nice to have|wünschenswert|bonus)"
                 rf"|(?:plus|von vorteil|nice to have|wünschenswert|bonus)[^.\n]{{0,40}}{_G}", t, re.I):
        return "plus"
    if re.search(rf"sehr gute?n?\s+{_G}|{_G}[^.\n]{{0,20}}sehr gut", t, re.I):
        return "sehr gut"
    if re.search(rf"\bgute?n?\s+{_G}|\bgood {_G}|{_G}[^.\n]{{0,25}}\b(?:gut|good)\b", t, re.I):
        return "gut"
    m = re.search(rf"{_G}[^.\n]{{0,40}}\b(b1|b2)\b|\b(b1|b2)\b[^.\n]{{0,40}}{_G}", t, re.I)
    if m:
        return (m.group(1) or m.group(2)).upper()
    if re.search(_G, t, re.I):
        return "mentioned"
    return "not stated"


_INTERN = re.compile(r"praktik|intern(?:ship)?\b", re.I)
_FULL = re.compile(r"vollzeit|full[- ]?time|\b40\s*(?:h\b|std|stunden|hours)|ganztags|ganztägig", re.I)
_RANGE = re.compile(r"(\d{1,2})\s*(?:–|-|bis|to)\s*(\d{1,2})\s*(?:h\b|std|stunden|hours|hrs)", re.I)
_SINGLE = re.compile(r"\b(\d{1,2})\s*(?:h\b|std\.?|stunden|hours|hrs)", re.I)
_PART = re.compile(r"teilzeit|part[- ]?time", re.I)


def hours_asked(text: str) -> str:
    t = text or ""
    if _INTERN.search(t) and _FULL.search(t):
        return "full-time Praktikum"
    m = _RANGE.search(t)
    if m:
        return f"{m.group(1)}–{m.group(2)} h/week"
    m = _SINGLE.search(t)
    if m and int(m.group(1)) <= 40:
        return f"{m.group(1)} h/week"
    if _FULL.search(t):
        return "full-time"
    if _PART.search(t):
        return "part-time"
    return "not stated"


_STUDENT_TITLE = re.compile(r"werkstudent|working[- ]student|studentische|hiwi|intern|praktik|student", re.I)
_STUDENT_ANYWHERE = re.compile(r"werkstudent|working[- ]student|studentische|hiwi|studentenjob|student job", re.I)


def full_time_only(job: dict) -> bool:
    """Pure full-time roles are out; a full-time Praktikum stays (flagged). A
    student title, or an explicit Werkstudent option in the text, rescues; a
    stray "part-time possible" in the boilerplate does not."""
    title, desc = job.get("title", ""), job.get("description", "")
    if not _FULL.search(f"{title} {desc}"):
        return False
    if _INTERN.search(f"{title} {desc}") or _STUDENT_TITLE.search(title):
        return False
    return not _STUDENT_ANYWHERE.search(desc)


def listing_language(text: str) -> str:
    hay = f" {(text or '').lower()} "
    return "DE" if sum(1 for m in matcher._GERMAN_MARKERS if m in hay) >= 4 else "EN"


_LEGAL = re.compile(r"\b(?:gmbh|ag|se|ug|kg|kgaa|ohg|mbh|inc|ltd|llc|co|e\.?\s?v\.?|haftungsbeschränkt|"
                    r"in germany|germany|deutschland|group)\b\.?", re.I)
_GENDER = re.compile(r"\((?:[mwfdxhgn]\s*/\s*)+[mwfdxhgn]\)|\(all genders\)|\(gn\)|\(d/w/m\)|\(f/m/x\)", re.I)


def norm_company(name: str) -> str:
    s = re.sub(r"\(.*?\)", " ", (name or "").lower())
    s = _LEGAL.sub(" ", s)
    s = re.sub(r"[^a-z0-9äöüß]+", " ", s)
    return " ".join(s.split())


def norm_title(title: str) -> str:
    s = _GENDER.sub(" ", html.unescape(html.unescape(title or "")))
    # requisition ids: (k123), (Req. 2026-042)
    s = re.sub(r"\((?:req\.?\s*)?[a-z]{0,3}[-\s]?\d[\d-]*\)", " ", s, flags=re.I)
    s = re.sub(r"\b(werkstudent|praktikant|student|mitarbeiter|entwickler|ingenieur|berater)(?:in|:in|\*in|/in|_in)\b",
               r"\1", s, flags=re.I)
    s = re.sub(r"[^a-z0-9äöüß]+", " ", s.lower())
    return " ".join(s.split())


# ─── history and dedup ───────────────────────────────────────────────────────

_KIT_HEADER = re.compile(r"^#\s+(.+?)\s+[—–-]\s+(.+?)\s*$", re.M)
_KIT_ROUTE = re.compile(r"\*\*Send route:\*\*\s*(https?://\S+)")


class HistoryIndex:
    """Everything ever kitted, parked, dropped or sent, so nothing is picked twice.

    Sources: every data/applications/*/job.json, the kit notes kit.py writes to
    data/applications/batch-*/ (a note moved into a _parked or _dropped folder
    counts as parked or dropped), and tracker rows."""

    def __init__(self):
        self.urls: dict[str, str] = {}
        self.roles: dict[tuple[str, str], str] = {}
        self.companies: set[str] = set()

    def _add(self, company: str, title: str, url: str, reason: str) -> None:
        if url:
            self.urls.setdefault(url.strip(), reason)
        t = norm_title(title)
        # "Brand (Legal Name GmbH)" must dedup under either name
        for name in [company or ""] + re.findall(r"\(([^)]+)\)", company or ""):
            c = norm_company(name)
            if c and t:
                self.roles.setdefault((c, t), reason)
            if c:
                self.companies.add(c)

    @classmethod
    def load(cls, apps_dir: Path = APPS_DIR) -> "HistoryIndex":
        idx = cls()
        apps_dir = Path(apps_dir)
        for job_json in sorted(apps_dir.glob("*/job.json")):
            try:
                j = json.loads(job_json.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            idx._add(j.get("company", ""), j.get("title", ""), j.get("url", ""), "application folder")
        for md in sorted(apps_dir.glob("batch-*/**/*.md")):
            if md.name.upper().startswith("CHECKLIST") or md.name in ("review.md", "posting.md", "lint.md"):
                continue
            parts = {p.lower() for p in md.parts}
            reason = "parked" if "_parked" in parts else "dropped" if "_dropped" in parts else "kit"
            text = md.read_text(encoding="utf-8", errors="replace")
            head = _KIT_HEADER.search(text)
            route = _KIT_ROUTE.search(text)
            if head:
                idx._add(head.group(1), head.group(2), route.group(1) if route else "", reason)
        return idx

    def add_rows(self, rows: list[dict]) -> None:
        for r in rows or []:
            self._add(r.get("company", ""), r.get("job_title", ""), r.get("url", ""), "tracker")

    def seen(self, job: dict) -> str | None:
        url = (job.get("url") or "").strip()
        if url and url in self.urls:
            return self.urls[url]
        return self.roles.get((norm_company(job.get("company", "")), norm_title(job.get("title", ""))))

    def company_seen(self, job: dict) -> bool:
        return norm_company(job.get("company", "")) in self.companies


# ─── gates ───────────────────────────────────────────────────────────────────

_TECH_TITLE = re.compile(
    r"developer|engineer|entwickl|software|\bdata\b|\bai\b|\bki\b|\bml\b|llm|python|backend|automat|devops|"
    r"\bit\b|tech|informatik|analyt|programm|research|scien|cloud|platform|infrastruktur|security|\bqa\b|test",
    re.I)
# Titles that say business, not building, even when the description name-drops
# Python or an API.
_NONTECH_TITLE = re.compile(
    r"sales|vertrieb|marketing|\bsem\b|\bseo\b|growth|business development|venture|founders? associate|"
    r"strateg|credit|onboarding|recruit|\bhr\b|people|legal|finance|accounting|controlling|procurement|"
    r"einkauf|customer (?:success|support|service)|community|content|copy|product (?:manager|owner)|"
    r"project manag|projektmanag|office|assisten|sprachlehre|\bevent|partnership|account manag|consult",
    re.I)
# "Business Development" is not development: only an engineering noun overrides.
_TECH_ROLE_OVERRIDE = re.compile(r"engineer|developer\b|entwickler|software", re.I)
_HARD_TECH = re.compile(
    r"\bpython\b|\bsql\b|\bapis?\b|\bdocker\b|\bgit\b|typescript|\bjava\b|\bllm|machine learning|"
    r"\bbackend\b|\bsoftware\b|postgres|\bdata pipeline|\brag\b|\bci/cd\b", re.I)


def is_tech(job: dict) -> bool:
    """The title decides; a title with no signal either way ("Werkstudent (m/w/d)")
    needs two hard tech terms in the description."""
    title = job.get("title", "")
    if matcher._reject_non_tech_role({"title": title, "description": ""}):
        return False
    if _NONTECH_TITLE.search(title) and not _TECH_ROLE_OVERRIDE.search(title):
        return False
    if _TECH_TITLE.search(title):
        return True
    return len({m.lower() for m in _HARD_TECH.findall(job.get("description", ""))}) >= 2


def in_search_area(job: dict) -> bool:
    """In the search city, remote in the country, or with no location stated."""
    search = profile.get()["search"]
    loc = (job.get("location") or "").lower()
    hay = f"{job.get('title', '')} {loc} {(job.get('description') or '')[:600]}".lower()
    if any(name in hay for name in matcher.city_names()):
        return True
    remote = any(t in hay for t in matcher._REMOTE_TERMS)
    # a bare country code ("de") only counts as the whole location field
    in_country = any(t in loc for t in search["country_terms"]) or loc.strip() == search["adzuna_country"]
    if remote and in_country:
        return True
    if any(c in loc for c in matcher.other_cities()):
        return False
    return not loc or in_country


def classify(job: dict) -> str:
    return "ready" if job.get("projects") and job.get("live") is True else "weak"


def _merge(jobs: list[dict]) -> list[dict]:
    """Fold duplicates onto one row; ATS rows win, the others are noted in merged_from."""
    ordered = sorted(jobs, key=lambda j: 0 if str(j.get("source", "")).startswith("ats:") else 1)
    by_key: dict[tuple[str, str], dict] = {}
    by_url: dict[str, dict] = {}
    out: list[dict] = []
    for job in ordered:
        key = (norm_company(job.get("company", "")), norm_title(job.get("title", "")))
        url = (job.get("url") or "").strip()
        base = by_url.get(url) if url else None
        if base is None and all(key):
            base = by_key.get(key)
        if base is not None:
            base.setdefault("merged_from", []).append(job.get("source", "?"))
            if not base.get("description") and job.get("description"):
                base["description"] = job["description"]
            if not base.get("apply_url") and job.get("apply_url"):
                base["apply_url"] = job["apply_url"]
            continue
        job.setdefault("merged_from", [])
        if url:
            by_url[url] = job
        if all(key):
            by_key[key] = job
        out.append(job)
    return out


async def pipeline(jobs: list[dict], *, history: HistoryIndex, verify, client, deadline: float | None = None) -> dict:
    stats = {"raw": len(jobs), "dropped": {k: 0 for k in DROP_KEYS}, "unverified": 0, "ready": 0, "weak": 0}
    dropped: list[dict] = []
    survivors: list[dict] = []

    def drop(job: dict, gate: str, why: str):
        stats["dropped"][gate] += 1
        dropped.append({"company": job.get("company"), "title": job.get("title"), "gate": gate, "why": why,
                        "url": job.get("url")})
        logger.info(f"Dropped ({gate}: {why}): {job.get('title')} @ {job.get('company')}")

    for job in _merge(jobs):
        bar = matcher.hard_bar_reason(job)
        if bar:
            drop(job, "c1" if bar.startswith("german-c1") else "degree", bar)
            continue
        # The posting's own words decide the role type: a student word in a title
        # cannot rescue an internship body, and a row with no listing URL is a
        # speculative target, not a role.
        if not (job.get("url") or "").strip():
            drop(job, "no_student_role", "no listing URL (speculative target)")
            continue
        kind, evidence = roletype.classify_with_evidence(job.get("description", ""), job.get("title", ""))
        if not roletype.is_accepted(kind):
            gate = kind if kind in ("internship", "full_time") else "no_student_role"
            drop(job, gate, roletype.drop_reason(kind) + (f": {evidence!r}" if evidence else ""))
            continue
        if not is_tech(job):
            drop(job, "non_tech", "role outside the stack")
            continue
        if full_time_only(job) or not matcher._passes_entry_gate(job):
            drop(job, "full_time", "full-time only, no student option")
            continue
        if not in_search_area(job):
            drop(job, "location", job.get("location", ""))
            continue
        seen = history.seen(job)
        if seen:
            drop(job, "already_applied", seen)
            continue
        survivors.append(job)

    ready: list[dict] = []
    weak: list[dict] = []
    for job in survivors:
        if deadline is not None and time.monotonic() > deadline and not str(job.get("source", "")).startswith("ats:"):
            job.update(live=None, employer_url=job.get("url", ""), live_note="not checked (deadline)")
        else:
            await verify(client, job)
        if job.get("live") is False:
            drop(job, "dead", job.get("live_note", ""))
            continue
        if job.get("live") is None:
            stats["unverified"] += 1
        text = f"{job.get('title', '')}\n{job.get('description', '')}"
        job["stack"] = named_stack(text)
        job["projects"] = map_projects(text)
        job["german"] = german_level(text)
        job["hours"] = hours_asked(text)
        job["language"] = listing_language(text)
        job["score"] = matcher._score_job(job)["score"]
        job["company_note"] = "contacted before" if history.company_seen(job) else ""
        (ready if classify(job) == "ready" else weak).append(job)

    ready.sort(key=lambda j: j["score"], reverse=True)
    weak.sort(key=lambda j: j["score"], reverse=True)
    stats["ready"], stats["weak"] = len(ready), len(weak)
    return {"ready": ready, "weak": weak, "stats": stats, "dropped": dropped}


# ─── report ──────────────────────────────────────────────────────────────────

COLUMNS = "| company | role | live? | route | language | German asked | hours | named stack | maps to | fit |"
RULE = "|---|---|---|---|---|---|---|---|---|---|"
_DROP_LABELS = [("c1", "German C1"), ("degree", "degree"), ("internship", "internship"), ("full_time", "full-time"),
                ("no_student_role", "no-student-role"), ("non_tech", "non-tech"), ("location", "location"),
                ("already_applied", "already-applied"), ("dead", "dead")]


def _cell(s) -> str:
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def _live_cell(job: dict) -> str:
    if job.get("live") is True:
        return "✅ live"
    if job.get("live") is False:
        return "❌ dead"
    return "❓ unverified"


def row(job: dict) -> str:
    company = _cell(job.get("company"))
    if job.get("company_note"):
        company += f" ⚠ {job['company_note']}"
    route = job.get("employer_url") or job.get("url") or ""
    cells = [company, _cell(job.get("title")), _live_cell(job), _cell(route), _cell(job.get("language")),
             _cell(job.get("german")), _cell(job.get("hours")), _cell(", ".join(job.get("stack") or []) or "—"),
             _cell(", ".join(job.get("projects") or []) or "—"), _cell(job.get("score"))]
    return "| " + " | ".join(cells) + " |"


def summary_line(stats: dict) -> str:
    d = stats["dropped"]
    dropped = " · ".join(f"{d.get(k, 0)} {label}" for k, label in _DROP_LABELS)
    extra = f" | {stats['unverified']} unverified" if stats.get("unverified") else ""
    return f"**{stats['raw']} raw found | dropped: {dropped} | {stats['ready']} ready | {stats['weak']} weak**{extra}"


def render_report(*, ready: list[dict], weak: list[dict], stats: dict, sources: list[dict], runtime_s: float,
                  run_date: str, boards: list[dict] | None = None, dropped: list[dict] | None = None) -> str:
    lines = [f"# Sourcing {run_date}", "",
             f"{summary_line(stats)}, runtime {round(runtime_s)} s", "",
             "Find only. Nothing here has been applied to, kitted or written to the tracker.", "",
             f"## A. READY ({len(ready)})", "",
             "Maps to at least one project **and** the employer's own page was verified open. Sorted by fit.", "",
             COLUMNS, RULE]
    lines += [row(j) for j in ready] or ["| — | no ready roles this run | | | | | | | | |"]
    lines += ["", f"## B. WEAK ({len(weak)})", "",
              "Passed every gate but maps to no project, or the employer page could not be verified. For review.", "",
              COLUMNS, RULE]
    lines += [row(j) for j in weak] or ["| — | nothing here | | | | | | | | |"]
    lines += ["", "## Sources", "", "| source | returned | details fetched | blocked or note |", "|---|---|---|---|"]
    for s in sources:
        note = s.get("blocked") or "; ".join(s.get("notes") or []) or "—"
        lines.append(f"| {_cell(s['name'])} | {s.get('returned', 0)} | {s.get('details', '—')} | {_cell(note)} |")
    if boards:
        ok = [b for b in boards if b["status"] == "200"]
        bad = [b for b in boards if b["status"] != "200"]
        lines += ["", f"ATS boards: {len(ok)} answered 200 ({sum(b['jobs'] for b in ok)} postings), "
                      f"{len(bad)} did not: " + (", ".join(f"{b['board']}={b['status']}" for b in bad) or "—")]
    if dropped:
        # every gate decision is visible: a posting is never discarded silently
        lines += ["", f"## C. DROPPED ({len(dropped)})", "", "| company | role | gate | why | URL |",
                  "|---|---|---|---|---|"]
        lines += [f"| {_cell(d.get('company'))} | {_cell(d.get('title'))} | {_cell(d.get('gate'))} | "
                  f"{_cell(d.get('why'))} | {_cell(d.get('url'))} |" for d in dropped]
    return "\n".join(lines) + "\n"


# ─── run ─────────────────────────────────────────────────────────────────────

def _tracker_rows() -> list[dict]:
    """Read-only: every tracker row, for dedup. Empty when the tracker is unreachable."""
    try:
        from pipeline.tracker import TABLE, _client
        return _client().table(TABLE).select("company,job_title,url").execute().data or []
    except Exception as e:  # dedup degrades, the run must not
        logger.warning(f"tracker rows unavailable for dedup: {type(e).__name__}")
        return []


async def _aggregators(status_rows: list[dict], *, arbeitnow: bool, adzuna: bool, ba: bool) -> list[dict]:
    from pipeline.scraper import arbeitnow_endpoints, scrape_arbeitnow, scrape_bundesagentur, scrape_jobs
    jobs: list[dict] = []
    if arbeitnow:
        found = await scrape_arbeitnow(arbeitnow_endpoints(profile.queries("arbeitnow")))
        jobs += found
        status_rows.append(SourceStatus("arbeitnow", returned=len(found)).as_row())
    if adzuna:
        try:
            found = await scrape_jobs(pages_per_query=1)
            jobs += found
            status_rows.append(SourceStatus("adzuna", returned=len(found)).as_row())
        except ValueError as e:  # no credentials
            status_rows.append(SourceStatus("adzuna", blocked=str(e)).as_row())
    if ba:
        found = await scrape_bundesagentur()
        jobs += found
        status_rows.append(SourceStatus("arbeitsagentur", returned=len(found)).as_row())
    return jobs


async def run(*, budget_s: int = 600, max_boards: int = 80, linkedin: bool = True, xing: bool = True,
              indeed: bool = True, aggregators: bool = True, out_dir: Path | None = None,
              registry_path: Path = REGISTRY_PATH) -> Path:
    out_dir = out_dir or OUT_DIR
    t0 = time.monotonic()
    deadline = t0 + budget_s
    run_date = date.today().isoformat()
    logger.info(f"sourcing run {run_date}: budget {budget_s}s, max_boards {max_boards}")

    history = HistoryIndex.load(APPS_DIR)
    history.add_rows(_tracker_rows())

    registry = load_registry(registry_path)
    new = add_boards(registry, [b for b in (board_from_url(u) for u in history.urls) if b], via="history")
    logger.info(f"registry: {len(registry)} boards (+{new} from history)")

    sources: list[dict] = []
    jobs: list[dict] = []
    async with httpx.AsyncClient() as client:
        ats_jobs, board_rows = await pull_all(registry, client=client, max_boards=max_boards, deadline=deadline)
        jobs += ats_jobs
        sources.append(SourceStatus("ats boards", returned=len(ats_jobs)).as_row())
        if linkedin:
            found, st = await linkedin_search(client, profile.queries("linkedin"), max_details=110, deadline=deadline)
            jobs += found
            sources.append(st.as_row())
        if xing:
            found, st = await xing_search(client, profile.queries("xing"), max_details=80, deadline=deadline)
            jobs += found
            sources.append(st.as_row())
            # XING's applyUrl often points straight at an ATS board: register it
            grown = add_boards(registry, [b for b in (board_from_url(j.get("apply_url", "")) for j in found) if b],
                               via="xing")
            if grown:
                logger.info(f"registry: +{grown} boards discovered via XING apply links (pulled next run)")
        if indeed:
            found, st = indeed_search(profile.queries("indeed"))
            jobs += found
            sources.append(st.as_row())
        if aggregators:
            jobs += await _aggregators(sources, arbeitnow=True, adzuna=True, ba=True)
        result = await pipeline(jobs, history=history, verify=verify_live, client=client, deadline=deadline)

    save_registry(registry, registry_path)
    runtime = time.monotonic() - t0
    md = render_report(ready=result["ready"], weak=result["weak"], stats=result["stats"], sources=sources,
                       runtime_s=runtime, run_date=run_date, boards=board_rows, dropped=result["dropped"])
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{run_date}.md"
    md_path.write_text(md, encoding="utf-8")
    (out_dir / f"{run_date}.json").write_text(json.dumps(
        {"date": run_date, "runtime_s": round(runtime, 1), "stats": result["stats"], "sources": sources,
         "boards": board_rows, "ready": result["ready"], "weak": result["weak"], "dropped": result["dropped"]},
        indent=1, ensure_ascii=False, default=str), encoding="utf-8")
    logger.info(f"report → {md_path} ({result['stats']['ready']} ready, {result['stats']['weak']} weak, "
                f"{round(runtime)} s)")
    return md_path


def bootstrap() -> None:
    """Environment and logging for a CLI run; loads .env, never prints it."""
    config.load_env()
    config.setup_logging()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sourcing pass: find and filter roles (no kits, no writes)")
    parser.add_argument("--budget", type=int, default=600, help="whole-run cap in seconds (default 600)")
    parser.add_argument("--max-boards", type=int, default=80)
    parser.add_argument("--no-linkedin", action="store_true")
    parser.add_argument("--no-xing", action="store_true")
    parser.add_argument("--no-indeed", action="store_true")
    parser.add_argument("--no-aggregators", action="store_true", help="skip Arbeitnow, Adzuna and the Bundesagentur")
    args = parser.parse_args(argv)
    bootstrap()
    path = asyncio.run(run(budget_s=args.budget, max_boards=args.max_boards, linkedin=not args.no_linkedin,
                           xing=not args.no_xing, indeed=not args.no_indeed, aggregators=not args.no_aggregators))
    print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
