"""Public job search that needs no login: LinkedIn guest pages, XING, Indeed.

* LinkedIn's guest search (jobs-guest/jobs/api/seeMoreJobPostings/search) and
  guest job page. Never the logged-in site.
* XING's public search page, whose embedded state carries title, company,
  city, employment type and, for third-party postings, the employer's
  applyUrl, which the liveness check follows later.
* Indeed answers plain HTTP with 403 and a captcha, so it goes through
  Firecrawl when a key is configured and is otherwise recorded as blocked.

A 429 (LinkedIn also uses 999) stops that source for the rest of the run and
is written into its status, so a run never hammers a rate-limited site.
"""
import asyncio
import html
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from urllib.parse import quote_plus, urlsplit, urlunsplit

import httpx

from pipeline import profile

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9,de;q=0.8"}
TIMEOUT = 25
RATE_LIMIT_CODES = (429, 999)

LINKEDIN_SEARCH = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
LINKEDIN_JOB = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
XING_SEARCH = "https://www.xing.com/jobs/search"

_TAG = re.compile(r"<[^>]+>")
_BLOCK_END = re.compile(r"</(?:p|li|div|h\d|tr|section|ul|ol)>|<br\s*/?>", re.I)


@dataclass
class SourceStatus:
    name: str
    returned: int = 0
    details: int = 0
    blocked: str | None = None
    notes: list[str] = field(default_factory=list)

    def as_row(self) -> dict:
        return {"name": self.name, "returned": self.returned, "details": self.details,
                "blocked": self.blocked, "notes": self.notes}


def _text(raw: str) -> str:
    s = html.unescape(raw or "")
    s = _BLOCK_END.sub("\n", s)
    s = _TAG.sub(" ", s)
    s = html.unescape(s)
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n+", "\n", s)
    return "\n".join(line.strip() for line in s.split("\n")).strip()


def _strip_query(url: str) -> str:
    parts = urlsplit(html.unescape(url))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _first(pattern: str, text: str, flags=re.S) -> str:
    m = re.search(pattern, text, flags)
    return html.unescape(_TAG.sub("", m.group(1))).strip() if m else ""


def _default_location(with_country: bool) -> str:
    search = profile.get()["search"]
    return f"{search['city']}, {search['country']}" if with_country else search["city"]


# ─── LinkedIn (guest) ────────────────────────────────────────────────────────

def parse_linkedin_search(html_text: str) -> list[dict]:
    jobs = []
    for card in re.split(r"<li\b[^>]*>", html_text)[1:]:
        job_id = _first(r'data-entity-urn="urn:li:jobPosting:(\d+)"', card)
        href = re.search(r'class="base-card__full-link[^"]*"[^>]*href="([^"]+)"', card)
        title = _first(r'class="base-search-card__title[^"]*"[^>]*>(.*?)</h3>', card)
        if not (job_id and href and title):
            continue
        jobs.append({
            "title": title,
            "company": _first(r'class="base-search-card__subtitle[^"]*"[^>]*>(.*?)</h4>', card),
            "salary": "",
            "location": _first(r'class="job-search-card__location[^"]*"[^>]*>(.*?)</span>', card),
            "description": "",
            "url": _strip_query(href.group(1)),
            "source": "linkedin",
            "job_id": job_id,
            "posted": _first(r'<time[^>]*datetime="([^"]+)"', card),
        })
    return jobs


def parse_linkedin_job(html_text: str) -> dict:
    desc = re.search(r'class="show-more-less-html__markup[^"]*"[^>]*>(.*?)</section>', html_text, re.S)
    return {
        "title": _first(r'class="[^"]*topcard__title[^"]*"[^>]*>(.*?)</h1>', html_text),
        "company": _first(r'class="topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', html_text),
        "location": _first(r'class="topcard__flavor topcard__flavor--bullet"[^>]*>(.*?)</span>', html_text),
        "description": _text(desc.group(1)) if desc else "",
    }


async def _fetch(client: httpx.AsyncClient, status: SourceStatus, url: str, what: str, **kw) -> httpx.Response | None:
    """GET or None. A rate-limit code sets status.blocked; the caller must stop."""
    try:
        r = await client.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True, **kw)
    except httpx.HTTPError as e:
        status.notes.append(f"{what}: {type(e).__name__}")
        return None
    if r.status_code in RATE_LIMIT_CODES:
        status.blocked = f"HTTP {r.status_code} on {what}, source stopped"
        logger.warning(f"[{status.name}] {status.blocked}")
        return None
    if r.status_code >= 400:
        status.notes.append(f"{what}: HTTP {r.status_code}")
        return None
    return r


async def linkedin_search(client: httpx.AsyncClient, queries: list[str], *, location: str | None = None,
                          pages: int = 2, max_details: int = 60, delay: float = 1.5,
                          deadline: float | None = None) -> tuple[list[dict], SourceStatus]:
    status = SourceStatus("linkedin")
    location = location or _default_location(with_country=True)
    seen: dict[str, dict] = {}

    def _out_of_time() -> bool:
        return deadline is not None and time.monotonic() > deadline

    for qi, q in enumerate(queries, 1):
        for page in range(pages):
            if status.blocked or _out_of_time():
                break
            params = {"keywords": q, "location": location, "start": page * 25, "f_TPR": "r2592000"}
            r = await _fetch(client, status, LINKEDIN_SEARCH, f"query {qi} page {page + 1}", params=params)
            if r is None:
                break
            found = parse_linkedin_search(r.text)
            new = [j for j in found if j["job_id"] not in seen]
            for j in new:
                seen[j["job_id"]] = j
            logger.info(f"[linkedin] '{q}' page {page + 1}: +{len(new)} (of {len(found)})")
            if delay:
                await asyncio.sleep(delay)
            if not found:  # an empty page ends the query
                break
        if status.blocked:
            break

    jobs = list(seen.values())
    for j in jobs[:max_details]:
        if status.blocked or _out_of_time():
            break
        r = await _fetch(client, status, LINKEDIN_JOB.format(job_id=j["job_id"]), f"job {j['job_id']}")
        if r is None:
            continue
        detail = parse_linkedin_job(r.text)
        j["description"] = detail["description"]
        j["company"] = j["company"] or detail["company"]
        status.details += 1
        if delay:
            await asyncio.sleep(delay)

    status.returned = len(jobs)
    return jobs, status


# ─── XING (public search page) ───────────────────────────────────────────────

def _json_objects(text: str, key_prefix: str) -> dict[str, dict]:
    """Every `"<key_prefix>…":{…}` object in an embedded JSON state, by key.
    Brace-matched with strings respected, so it works whatever wrapper XING uses."""
    out: dict[str, dict] = {}
    for m in re.finditer(rf'"({re.escape(key_prefix)}[^"]*)":\{{', text):
        start = m.end() - 1
        depth, i, in_str, esc = 0, start, False, False
        while i < len(text):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            elif ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        try:
            out[m.group(1)] = json.loads(text[start:i + 1])
        except json.JSONDecodeError:
            continue
    return out


def _xing_job(obj: dict, companies: dict[str, dict]) -> dict:
    info = obj.get("companyInfo") or {}
    company = info.get("companyNameOverride") or ""
    if not company:
        ref = (info.get("company") or {}).get("__ref") or ""
        company = (companies.get(ref) or {}).get("companyName", "")
    loc = obj.get("location") or {}
    city = loc.get("city") or next((x.get("city") for x in obj.get("locations") or [] if x.get("city")), "")
    app = obj.get("application") or {}
    return {
        "title": obj.get("title", ""),
        "company": company,
        "salary": "",
        "location": city,
        "description": "",
        "url": obj.get("url", ""),
        "source": "xing",
        "apply_url": app.get("applyUrl") or "",
        "employment": (obj.get("employmentType") or {}).get("localizationValue", ""),
        "posted": (obj.get("refreshedAt") or "")[:10],
        "xing_id": (obj.get("id") or "").split(".")[0],
    }


def parse_xing_search(html_text: str) -> list[dict]:
    companies = _json_objects(html_text, "Company:")
    return [_xing_job(obj, companies) for obj in _json_objects(html_text, "VisibleJob:").values()
            if obj.get("url") and obj.get("title")]


def parse_xing_job(html_text: str) -> dict:
    posting = {}
    for m in re.finditer(r'<script[^>]*type="application/ld\+json"[^>]*>(.*?)</script>', html_text, re.S):
        try:
            data = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict) and data.get("@type") == "JobPosting":
            posting = data
            break
    state = list(_json_objects(html_text, "VisibleJob:").values())
    vis = _xing_job(state[0], _json_objects(html_text, "Company:")) if state else {}
    location = posting.get("jobLocation")
    addr = (location.get("address") or {}) if isinstance(location, dict) else {}
    return {
        "title": posting.get("title") or vis.get("title", ""),
        "company": (posting.get("hiringOrganization") or {}).get("name") or vis.get("company", ""),
        "location": addr.get("addressLocality") or vis.get("location", ""),
        "description": _text(posting.get("description", "")),
        "apply_url": vis.get("apply_url", ""),
        "employment": posting.get("employmentType") or vis.get("employment", ""),
    }


async def xing_search(client: httpx.AsyncClient, queries: list[str], *, location: str | None = None,
                      max_details: int = 40, delay: float = 1.5,
                      deadline: float | None = None) -> tuple[list[dict], SourceStatus]:
    status = SourceStatus("xing")
    location = location or _default_location(with_country=False)
    seen: dict[str, dict] = {}

    def _out_of_time() -> bool:
        return deadline is not None and time.monotonic() > deadline

    for qi, q in enumerate(queries, 1):
        if status.blocked or _out_of_time():
            break
        r = await _fetch(client, status, XING_SEARCH, f"query {qi}", params={"keywords": q, "location": location})
        if r is None:
            continue
        found = parse_xing_search(r.text)
        new = [j for j in found if j["url"] not in seen]
        for j in new:
            seen[j["url"]] = j
        logger.info(f"[xing] '{q}': +{len(new)} (of {len(found)})")
        if delay:
            await asyncio.sleep(delay)

    jobs = list(seen.values())
    for j in jobs[:max_details]:
        if status.blocked or _out_of_time():
            break
        r = await _fetch(client, status, j["url"], f"job {j.get('xing_id') or j['url']}")
        if r is None:
            continue
        detail = parse_xing_job(r.text)
        j["description"] = detail["description"]
        j["apply_url"] = j.get("apply_url") or detail.get("apply_url", "")
        j["company"] = j["company"] or detail["company"]
        status.details += 1
        if delay:
            await asyncio.sleep(delay)

    status.returned = len(jobs)
    return jobs, status


# ─── Indeed (Firecrawl or blocked) ───────────────────────────────────────────

_INDEED_JK = re.compile(r"[?&]jk=([0-9a-f]{6,})", re.I)


def canonical_indeed_url(url: str) -> str:
    """Indeed's /rc/clk redirects carry a long opaque tracking token that changes
    on every crawl and reads like a key to secret scanners. Any Indeed link with
    a job key is reduced to the stable viewjob form."""
    parts = urlsplit(url or "")
    if "indeed." not in parts.netloc.lower():
        return url
    m = _INDEED_JK.search(url)
    if not m:
        return url
    return f"{parts.scheme}://{parts.netloc}/viewjob?jk={m.group(1)}"


def indeed_search(queries: list[str]) -> tuple[list[dict], SourceStatus]:
    """Plain HTTP gets a 403 and a captcha. With a Firecrawl key the public result
    pages go through its extractor; without one the source is recorded as
    blocked. Never retried, never hammered."""
    status = SourceStatus("indeed")
    if not os.getenv("FIRECRAWL_API_KEY"):
        status.blocked = "403 and captcha for plain HTTP; no FIRECRAWL_API_KEY set, skipped"
        return [], status
    from pipeline.scraper import scrape_firecrawl_entries

    city = _default_location(with_country=False)
    entries = [{"url": f"https://de.indeed.com/jobs?q={quote_plus(q)}&l={quote_plus(city)}", "source": "indeed",
                "location": _default_location(with_country=True)} for q in queries]
    jobs = scrape_firecrawl_entries(entries)
    for j in jobs:
        j["url"] = canonical_indeed_url(j.get("url", ""))
    status.returned = len(jobs)
    if not jobs:
        status.blocked = "Firecrawl returned 0 listings (page blocked or empty)"
    return jobs, status
