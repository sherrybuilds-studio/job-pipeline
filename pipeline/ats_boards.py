"""Company career boards: Greenhouse, Ashby, Lever, Personio, SmartRecruiters, Join.

Every applicant-tracking system exposes a public board per company; none of
them has a global search by city. So discovery runs off a registry of boards
(data/sources/ats_boards.json) that grows by itself: any ATS URL seen in an
application folder, a tracker row or a public-search apply link becomes a
board entry, and scripts/seed_ats_boards.py adds a list you curate.

Each parser turns one board payload into the scraper's job-dict shape
(title, company, salary, location, description, url, source) plus live=True
and employer_url: the URL is the employer's own ATS page, so the role is live
by construction and needs no liveness probe.

Read-only. Nothing here applies, logs in, or writes anywhere but the registry.
"""
import asyncio
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import httpx

from pipeline import config

logger = logging.getLogger(__name__)

REGISTRY_PATH = config.SOURCES_DIR / "ats_boards.json"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
TIMEOUT = 20
DESCRIPTION_CAP = 3000

# Titles worth a per-job detail fetch on boards whose list endpoint carries no
# description (SmartRecruiters, Join). Everything else is out of scope anyway.
_STUDENT_TITLE = re.compile(r"werkstudent|working[- ]student|intern|praktik|student|junior|trainee", re.I)

_BLOCK_END = re.compile(r"</(?:p|li|div|h\d|tr|section|ul|ol)>|<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_NL = re.compile(r"\n\s*\n+")


def _text(raw: str) -> str:
    """HTML (possibly entity-escaped twice, as Greenhouse sends it) to plain text
    with line breaks kept, so the matcher's per-sentence hard-bar logic works."""
    s = html.unescape(html.unescape(raw or ""))
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = _WS.sub(" ", s)
    s = _NL.sub("\n", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def _job(title: str, company: str, location: str, description: str, url: str, source: str, **extra) -> dict:
    return {
        "title": html.unescape(html.unescape(title or "")).strip(),
        "company": html.unescape(html.unescape(company or "")).strip(),
        "salary": "",
        "location": (location or "").strip(),
        "description": (description or "")[:DESCRIPTION_CAP],
        "url": url,
        "source": source,
        "live": True,
        "employer_url": url,
        **extra,
    }


# ─── registry ────────────────────────────────────────────────────────────────

_HOST_RULES: list[tuple[str, re.Pattern, re.Pattern | None]] = [
    ("greenhouse", re.compile(r"^(?:boards|job-boards)\.greenhouse\.io$"), re.compile(r"^/([^/]+)")),
    ("ashby", re.compile(r"^jobs\.ashbyhq\.com$"), re.compile(r"^/([^/]+)")),
    ("lever", re.compile(r"^jobs\.lever\.co$"), re.compile(r"^/([^/]+)")),
    ("personio", re.compile(r"^([a-z0-9-]+)\.jobs\.personio\.(?:de|com)$"), None),
    ("smartrecruiters", re.compile(r"^jobs\.smartrecruiters\.com$"), re.compile(r"^/([^/]+)")),
    ("join", re.compile(r"^(?:www\.)?join\.com$"), re.compile(r"^/companies/([^/]+)")),
]


def board_from_url(url: str) -> tuple[str, str] | None:
    """('ats', 'slug') when the URL is a posting on a known ATS, else None."""
    if not url:
        return None
    parsed = urlparse(url.strip())
    host = (parsed.netloc or "").lower()
    for ats, host_re, path_re in _HOST_RULES:
        m = host_re.match(host)
        if not m:
            continue
        if path_re is None:
            return ats, m.group(1)
        pm = path_re.match(parsed.path or "")
        if pm and pm.group(1) not in ("jobs", "embed", "api"):
            return ats, pm.group(1)
    return None


def load_registry(path: Path = REGISTRY_PATH) -> list[dict]:
    if not Path(path).exists():
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"registry unreadable ({e}), starting empty")
        return []
    return data.get("boards", data) if isinstance(data, dict) else data


def save_registry(registry: list[dict], path: Path = REGISTRY_PATH) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps({"boards": registry}, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _key(ats: str, slug: str) -> tuple[str, str]:
    return ats.lower().strip(), slug.lower().strip()


def add_boards(registry: list[dict], boards, *, via: str, company: str = "") -> int:
    """Append unknown (ats, slug) pairs; returns how many were new."""
    known = {_key(b["ats"], b["slug"]) for b in registry}
    added = 0
    for ats, slug in boards:
        key = _key(ats, slug)
        if not slug or key in known:
            continue
        known.add(key)
        registry.append({"ats": key[0], "slug": slug.strip(), "company": company, "via": via,
                         "added": date.today().isoformat(), "last_status": "", "last_jobs": 0})
        added += 1
    return added


# ─── parsers (pure) ──────────────────────────────────────────────────────────

def parse_greenhouse(data: dict, company: str) -> list[dict]:
    jobs = []
    for j in data.get("jobs", []):
        location = (j.get("location") or {}).get("name", "")
        jobs.append(_job(j.get("title", ""), j.get("company_name") or company, location,
                         _text(j.get("content", "")), j.get("absolute_url", ""),
                         "ats:greenhouse", posted=(j.get("first_published") or "")[:10]))
    return jobs


def parse_ashby(data: dict, company: str) -> list[dict]:
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        location = j.get("location", "") or ""
        if j.get("isRemote") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        meta = f"Employment: {j.get('employmentType', '')} · {j.get('workplaceType', '')}"
        body = j.get("descriptionPlain") or _text(j.get("descriptionHtml", ""))
        jobs.append(_job(j.get("title", ""), company, location, f"{meta}\n{body}",
                         j.get("jobUrl", ""), "ats:ashby", posted=(j.get("publishedAt") or "")[:10]))
    return jobs


def parse_lever(data: list, company: str) -> list[dict]:
    jobs = []
    for j in data or []:
        cats = j.get("categories") or {}
        location = cats.get("location") or ", ".join(cats.get("allLocations") or [])
        if (j.get("workplaceType") or "").lower() == "remote" and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        meta = f"Commitment: {cats.get('commitment', '')} · Team: {cats.get('team', '')}"
        body = "\n".join(x for x in (j.get("descriptionPlain"), j.get("additionalPlain")) if x)
        jobs.append(_job(j.get("text", ""), company, location, f"{meta}\n{body}", j.get("hostedUrl", ""), "ats:lever"))
    return jobs


def _xml_text(node, tag: str) -> str:
    el = node.find(tag)
    return (el.text or "").strip() if el is not None else ""


def parse_personio(xml_text: str, company: str, slug: str, tld: str = "de") -> list[dict]:
    root = ET.fromstring(xml_text)
    jobs = []
    for p in root.findall("position"):
        pid = _xml_text(p, "id")
        meta = (f"Employment: {_xml_text(p, 'employmentType')} · Schedule: {_xml_text(p, 'schedule')}"
                f" · Category: {_xml_text(p, 'recruitingCategory')} · Seniority: {_xml_text(p, 'seniority')}")
        parts = [f"{_xml_text(jd, 'name')}\n{_text(_xml_text(jd, 'value'))}" for jd in p.iter("jobDescription")]
        jobs.append(_job(_xml_text(p, "name"), company, _xml_text(p, "office") or "Germany",
                         f"{meta}\n" + "\n".join(parts),
                         f"https://{slug}.jobs.personio.{tld}/job/{pid}", "ats:personio",
                         posted=_xml_text(p, "createdAt")[:10]))
    return jobs


def parse_smartrecruiters_list(data: dict, company: str) -> list[dict]:
    jobs = []
    for c in data.get("content", []):
        loc = c.get("location") or {}
        location = loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x)
        if loc.get("remote") and "remote" not in location.lower():
            location = f"{location} (Remote)".strip()
        ident = (c.get("company") or {}).get("identifier") or company
        meta = (f"Employment: {(c.get('typeOfEmployment') or {}).get('label', '')}"
                f" · Level: {(c.get('experienceLevel') or {}).get('label', '')}")
        jobs.append(_job(c.get("name", ""), (c.get("company") or {}).get("name") or company, location, meta,
                         f"https://jobs.smartrecruiters.com/{ident}/{c.get('id', '')}", "ats:smartrecruiters",
                         detail_url=c.get("ref", ""), posted=(c.get("releasedDate") or "")[:10]))
    return jobs


def parse_smartrecruiters_detail(data: dict) -> str:
    sections = (data.get("jobAd") or {}).get("sections") or {}
    parts = []
    for name in ("jobDescription", "qualifications", "additionalInformation", "companyDescription"):
        sec = sections.get(name) or {}
        if sec.get("text"):
            parts.append(_text(sec["text"]))
    return "\n".join(parts)


def parse_join_company(html_text: str, slug: str) -> list[str]:
    pat = re.compile(rf'href="(?:https://(?:www\.)?join\.com)?(/companies/{re.escape(slug)}/\d+-[a-z0-9-]+)"')
    urls: list[str] = []
    for m in pat.finditer(html_text):
        url = f"https://join.com{m.group(1)}"
        if url not in urls:
            urls.append(url)
    return urls


def _find_job_posting(obj):
    """Depth-first search for a schema.org JobPosting dict inside parsed JSON."""
    if isinstance(obj, dict):
        if obj.get("@type") == "JobPosting":
            return obj
        for v in obj.values():
            hit = _find_job_posting(v)
            if hit:
                return hit
    elif isinstance(obj, list):
        for v in obj:
            hit = _find_job_posting(v)
            if hit:
                return hit
    return None


def _json_ld_posting(html_text: str) -> dict:
    for m in re.finditer(r'<script[^>]*type="application/(?:ld\+json|json)"[^>]*>(.*?)</script>', html_text, re.S):
        try:
            hit = _find_job_posting(json.loads(m.group(1)))
        except json.JSONDecodeError:
            continue
        if hit:
            return hit
    return {}


def parse_join_job(html_text: str, url: str) -> dict:
    posting = _json_ld_posting(html_text)
    location = posting.get("jobLocation")
    addr = (location.get("address") or {}) if isinstance(location, dict) else {}
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.S)
    title = posting.get("title") or (html.unescape(_TAG.sub("", h1.group(1))).strip() if h1 else "")
    company = (posting.get("hiringOrganization") or {}).get("name", "")
    if not company:
        m = re.search(r"/companies/([^/]+)/", url)
        company = m.group(1) if m else ""
    meta = f"Employment: {posting.get('employmentType', '')}"
    return _job(title, company, addr.get("addressLocality", ""), f"{meta}\n{_text(posting.get('description', ''))}",
                url, "ats:join", posted=(posting.get("datePosted") or "")[:10])


# ─── fetching ────────────────────────────────────────────────────────────────

async def _get(client: httpx.AsyncClient, url: str, **kw) -> httpx.Response:
    r = await client.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True, **kw)
    r.raise_for_status()
    return r


async def pull_board(client: httpx.AsyncClient, board: dict, *, deadline: float | None = None,
                     detail_cap: int = 8, retry_wait: float = 20.0) -> tuple[list[dict], str]:
    """One board → (jobs, status). Status is the HTTP code as text or a short
    error. A Personio 429 is retried exactly once after `retry_wait` seconds."""
    ats, slug = board["ats"], board["slug"]
    company = board.get("company") or slug

    def _time_left() -> bool:
        return deadline is None or time.monotonic() < deadline

    try:
        if ats == "greenhouse":
            r = await _get(client, f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                           params={"content": "true"})
            return parse_greenhouse(r.json(), company), "200"
        if ats == "ashby":
            r = await _get(client, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
            return parse_ashby(r.json(), company), "200"
        if ats == "lever":
            r = await _get(client, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json"})
            return parse_lever(r.json(), company), "200"
        if ats == "personio":
            last = "404"
            for tld in ("de", "com"):
                for attempt in (1, 2):
                    try:
                        r = await _get(client, f"https://{slug}.jobs.personio.{tld}/xml")
                    except httpx.HTTPStatusError as e:
                        last = str(e.response.status_code)
                        if last == "429" and attempt == 1:
                            await asyncio.sleep(retry_wait)
                            continue
                        break
                    return parse_personio(r.text, company, slug, tld), "200"
                if last == "429":
                    return [], "429"
            return [], last
        if ats == "smartrecruiters":
            r = await _get(client, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings",
                           params={"limit": 100})
            jobs = parse_smartrecruiters_list(r.json(), company)
            fetched = 0
            for j in jobs:
                if fetched >= detail_cap or not _time_left():
                    break
                if not _STUDENT_TITLE.search(j["title"]) or not j.get("detail_url"):
                    continue
                try:
                    d = await _get(client, j["detail_url"])
                    j["description"] = f"{j['description']}\n{parse_smartrecruiters_detail(d.json())}"[:DESCRIPTION_CAP]
                    fetched += 1
                except httpx.HTTPError as e:
                    logger.debug(f"[smartrecruiters/{slug}] detail failed: {e}")
            return jobs, "200"
        if ats == "join":
            r = await _get(client, f"https://join.com/companies/{slug}")
            urls = [u for u in parse_join_company(r.text, slug) if _STUDENT_TITLE.search(u)]
            jobs = []
            for u in urls[:detail_cap]:
                if not _time_left():
                    break
                try:
                    d = await _get(client, u)
                    jobs.append(parse_join_job(d.text, u))
                except httpx.HTTPError as e:
                    logger.debug(f"[join/{slug}] {u}: {e}")
                await asyncio.sleep(0.5)
            return jobs, "200"
        return [], f"unknown ats {ats!r}"
    except httpx.HTTPStatusError as e:
        return [], str(e.response.status_code)
    except (httpx.HTTPError, ValueError, ET.ParseError) as e:
        return [], f"error: {type(e).__name__}"


async def pull_all(registry: list[dict], *, client: httpx.AsyncClient, max_boards: int = 80,
                   concurrency: int = 3, deadline: float | None = None, personio_spacing: float = 1.5,
                   retry_wait: float = 20.0) -> tuple[list[dict], list[dict]]:
    """Pull every registered board (bounded) → (jobs, per-board status rows).
    Personio rate-limits bursts for minutes, so its boards run one at a time
    with spacing, and two 429s in a row stop the rest of Personio."""
    sem = asyncio.Semaphore(concurrency)
    personio_lock = asyncio.Lock()
    personio_429 = 0
    jobs: list[dict] = []
    rows: list[dict] = []

    async def one(board: dict):
        nonlocal personio_429
        name = f"{board['ats']}:{board['slug']}"
        if deadline is not None and time.monotonic() > deadline:
            rows.append({"board": name, "status": "skipped (deadline)", "jobs": 0})
            return
        if board["ats"] == "personio":
            async with personio_lock:
                if personio_429 >= 2:
                    status, found = "skipped (personio 429)", []
                else:
                    found, status = await pull_board(client, board, deadline=deadline, retry_wait=retry_wait)
                    personio_429 = personio_429 + 1 if status == "429" else 0
                    await asyncio.sleep(personio_spacing)
        else:
            async with sem:
                found, status = await pull_board(client, board, deadline=deadline)
        board["last_status"] = status
        board["last_jobs"] = len(found)
        board["last_seen"] = date.today().isoformat()
        for j in found:
            j["board"] = name
        jobs.extend(found)
        rows.append({"board": name, "status": status, "jobs": len(found)})
        logger.info(f"[ats:{board['ats']}] {board['slug']}: {status}, {len(found)} jobs")

    await asyncio.gather(*(one(b) for b in registry[:max_boards]))
    return jobs, rows
