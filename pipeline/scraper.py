"""Daily-run sources: Adzuna, Arbeitnow, Bundesagentur, Himalayas, RemoteOK
and Firecrawl-read pages. Queries, city and country come from the profile.

Every scraper returns the same job dict shape:
    {title, company, salary, location, description, url, source}
A failing source logs an error and contributes nothing; it never stops the run.
"""
import asyncio
import logging
import os
import re

import httpx

from pipeline import profile

logger = logging.getLogger(__name__)

# Cheap keyword prefilter for the remote boards: they list every industry, and
# without it hundreds of irrelevant postings reach the matcher on every run.
_TECH_HINTS = [
    "python", "backend", "ai", "ml", "machine learning", "llm", "data",
    "devops", "automation", "engineer", "developer", "software",
]


def _looks_technical(title: str) -> bool:
    t = f" {title.lower()} "
    return any(h in t for h in _TECH_HINTS)


_HTML_TAG = re.compile(r"<[^>]+>")


def _strip_html(text: str) -> str:
    return _HTML_TAG.sub(" ", text).strip()


# ─── Firecrawl ───────────────────────────────────────────────────────────────
# Boards without a public API are read through Firecrawl's LLM extraction.
# Each page is best-effort: a blocked page logs a warning and is skipped.

_JOB_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "company": {"type": "string"},
                    "location": {"type": "string"},
                    "url": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title"],
            },
        }
    },
}

_EXTRACT_PROMPT = (
    "Extract every job listing from this page. "
    "For each job include: title, company name, location, "
    "direct URL to the job posting, and any description or requirements shown."
)


def _normalise_fc(raw: dict, source: str, default_location: str) -> dict:
    return {
        "title": raw.get("title", "").strip(),
        "company": raw.get("company", "").strip(),
        "salary": "",
        "location": raw.get("location", "").strip() or default_location,
        "description": raw.get("description", "").strip()[:800],
        "url": raw.get("url", "").strip(),
        "source": source,
    }


def scrape_firecrawl_boards() -> list[dict]:
    """The profile's search.firecrawl_pages."""
    return scrape_firecrawl_entries(profile.get()["search"]["firecrawl_pages"])


def scrape_firecrawl_entries(entries: list[dict]) -> list[dict]:
    """Extract listings from {url, source, location} entries via Firecrawl.
    Needs FIRECRAWL_API_KEY and the optional firecrawl extra."""
    api_key = os.getenv("FIRECRAWL_API_KEY")
    if not api_key:
        logger.warning("FIRECRAWL_API_KEY not set, skipping Firecrawl pages")
        return []
    try:
        from firecrawl import V1FirecrawlApp, V1JsonConfig
    except ImportError:
        logger.warning("firecrawl-py not installed (pip install -e '.[firecrawl]'), skipping Firecrawl pages")
        return []

    fc = V1FirecrawlApp(api_key=api_key)
    all_jobs: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        url, source = entry["url"], entry["source"]
        location = entry.get("location") or ""
        try:
            resp = fc.scrape_url(url, formats=["json", "markdown"],
                                 json_options=V1JsonConfig(schema=_JOB_SCHEMA, prompt=_EXTRACT_PROMPT),
                                 timeout=30000)
            new = 0
            for raw in (resp.json_field or {}).get("jobs", []):
                title = raw.get("title", "").strip()
                if not title:
                    continue
                key = (raw.get("company", "").strip().lower(), title.lower())
                if key not in seen:
                    seen.add(key)
                    all_jobs.append(_normalise_fc(raw, source, location))
                    new += 1
            logger.info(f"[Firecrawl/{source}] {url}: +{new} jobs extracted")
            if new == 0:
                logger.warning(f"[Firecrawl/{source}] 0 jobs from {url} (page blocked or layout changed?)")
        except Exception as e:  # one blocked page must not stop the others
            logger.error(f"Firecrawl error for {url}: {e}")
    logger.info(f"Firecrawl total unique jobs: {len(all_jobs)}")
    return all_jobs


# ─── Arbeitnow ───────────────────────────────────────────────────────────────

def arbeitnow_endpoints(queries: list[str]) -> list[str]:
    city = profile.city().lower().replace(" ", "+")
    return [f"https://www.arbeitnow.com/api/job-board-api?q={q.replace(' ', '+')}&location={city}" for q in queries]


def _normalise_arbeitnow(raw: dict) -> dict:
    tags = raw.get("tags", [])
    description = _strip_html(raw.get("description", ""))
    if tags:
        description = f"Tags: {', '.join(tags)}\n\n{description}"
    location = raw.get("location", "").strip()
    if raw.get("remote") and "remote" not in location.lower():
        location = f"{location} (Remote)".strip()
    return {
        "title": raw.get("title", "").strip(),
        "company": raw.get("company_name", "").strip(),
        "salary": "",
        "location": location,
        "description": description[:800],
        "url": raw.get("url", "").strip(),
        "source": "arbeitnow",
    }


async def scrape_arbeitnow(endpoints: list[str] | None = None) -> list[dict]:
    """`endpoints` defaults to the profile's arbeitnow_daily queries; the
    sourcing pass passes its wider set."""
    endpoints = endpoints if endpoints is not None else arbeitnow_endpoints(profile.queries("arbeitnow_daily"))
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient() as client:
        for endpoint in endpoints:
            try:
                response = await client.get(endpoint, timeout=20, follow_redirects=True,
                                            headers={"Accept": "application/json"})
                response.raise_for_status()
                data = response.json()
                jobs_raw = data.get("data", data) if isinstance(data, dict) else data
                new = 0
                for raw in jobs_raw:
                    job_url = raw.get("url", "").strip()
                    if not job_url or job_url in seen_urls:
                        continue
                    seen_urls.add(job_url)
                    all_jobs.append(_normalise_arbeitnow(raw))
                    new += 1
                logger.info(f"[Arbeitnow] {endpoint}: +{new} jobs")
            except httpx.HTTPStatusError as e:
                logger.error(f"Arbeitnow HTTP {e.response.status_code} for {endpoint}")
            except Exception as e:  # a failing source contributes nothing
                logger.error(f"Arbeitnow error for {endpoint}: {e}")
    logger.info(f"Arbeitnow total unique jobs: {len(all_jobs)}")
    return all_jobs


# ─── Adzuna ──────────────────────────────────────────────────────────────────
# Three query tiers from the profile: primary gets every page, secondary and
# fallback one page each; the matcher's gates weed out the rest.

def _adzuna_base() -> str:
    return f"https://api.adzuna.com/v1/api/jobs/{profile.get()['search']['adzuna_country']}/search"


def _creds() -> tuple[str, str]:
    app_id = os.getenv("ADZUNA_APP_ID")
    app_key = os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise ValueError("ADZUNA_APP_ID and ADZUNA_APP_KEY must be set in the environment")
    return app_id, app_key


async def _fetch_page(client: httpx.AsyncClient, query: str, page: int) -> list[dict]:
    app_id, app_key = _creds()
    params = {
        "app_id": app_id,
        "app_key": app_key,
        "what": query,
        "where": profile.city(),
        "distance": 25,
        "results_per_page": 20,
        "content-type": "application/json",
        "sort_by": "date",
    }
    response = await client.get(f"{_adzuna_base()}/{page}", params=params, timeout=20)
    response.raise_for_status()
    return response.json().get("results", [])


def _normalise(raw: dict) -> dict:
    salary_min = raw.get("salary_min")
    salary_max = raw.get("salary_max")
    if salary_min and salary_max:
        salary = f"€{int(salary_min):,}–€{int(salary_max):,}"
    elif salary_min:
        salary = f"from €{int(salary_min):,}"
    else:
        salary = ""
    return {
        "title": raw.get("title", "").strip(),
        "company": raw.get("company", {}).get("display_name", "").strip(),
        "salary": salary,
        "location": (raw.get("location") or {}).get("display_name", "").strip(),
        "description": raw.get("description", "").strip()[:800],
        "url": raw.get("redirect_url", "").strip(),
        "source": "adzuna",
    }


async def scrape_jobs(pages_per_query: int = 2) -> list[dict]:
    _creds()  # fail fast (ValueError) when the source is not configured
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()

    async def _run_query(client: httpx.AsyncClient, query: str, pages: int, tier: str):
        for page in range(1, pages + 1):
            try:
                results = await _fetch_page(client, query, page)
                jobs = [_normalise(r) for r in results if r.get("redirect_url")]
                new = [j for j in jobs if j["url"] not in seen_urls]
                seen_urls.update(j["url"] for j in new)
                all_jobs.extend(new)
                logger.info(f"[Adzuna {tier}] '{query}' page {page}: +{len(new)} jobs")
                if len(results) < 20:
                    break
                await asyncio.sleep(0.5)
            except httpx.HTTPStatusError as e:
                logger.error(f"Adzuna HTTP {e.response.status_code} for '{query}' page {page}")
            except Exception as e:  # a failing source contributes nothing
                logger.error(f"Adzuna error for '{query}' page {page}: {e}")

    async with httpx.AsyncClient() as client:
        for query in profile.queries("adzuna_primary"):
            await _run_query(client, query, pages_per_query, "primary")
        for query in profile.queries("adzuna_secondary"):
            await _run_query(client, query, 1, "secondary")
        for query in profile.queries("adzuna_fallback"):
            await _run_query(client, query, 1, "fallback")
    logger.info(f"Adzuna total unique jobs: {len(all_jobs)}")
    return all_jobs


# ─── Bundesagentur für Arbeit (Jobbörse) ─────────────────────────────────────
# Germany's public job board. The Jobsuche API needs only the client id that
# the public jobsuche web app itself sends; it is not a secret.

BA_BASE = "https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v4/jobs"
BA_CLIENT_ID = "jobboerse-jobsuche"


def _normalise_ba(raw: dict) -> dict:
    ort = raw.get("arbeitsort") or {}
    location = ", ".join(x for x in [ort.get("ort", ""), ort.get("region", "")] if x)
    refnr = raw.get("refnr", "")
    url = raw.get("externeUrl") or (f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}" if refnr else "")
    # The list endpoint has no description: pack the metadata it does return
    # so the matcher has more than a bare title.
    description = ". ".join(x for x in [raw.get("beruf", ""), f"Arbeitgeber: {raw.get('arbeitgeber', '')}", location]
                            if x)
    return {
        "title": raw.get("titel", "").strip() or raw.get("beruf", "").strip(),
        "company": raw.get("arbeitgeber", "").strip(),
        "salary": "",
        "location": location or profile.get()["search"]["country"],
        "description": description[:800],
        "url": url,
        "source": "arbeitsagentur",
    }


async def scrape_bundesagentur() -> list[dict]:
    all_jobs: list[dict] = []
    seen_urls: set[str] = set()
    async with httpx.AsyncClient() as client:
        for query in profile.queries("bundesagentur"):
            try:
                response = await client.get(
                    BA_BASE,
                    params={"was": query, "wo": profile.city(), "umkreis": 25, "size": 50, "page": 1},
                    headers={"X-API-Key": BA_CLIENT_ID, "Accept": "application/json"},
                    timeout=20,
                )
                response.raise_for_status()
                new = 0
                for raw in response.json().get("stellenangebote", []) or []:
                    job = _normalise_ba(raw)
                    if not job["title"] or not job["url"] or job["url"] in seen_urls:
                        continue
                    seen_urls.add(job["url"])
                    all_jobs.append(job)
                    new += 1
                logger.info(f"[Arbeitsagentur] '{query}': +{new} jobs")
                await asyncio.sleep(0.3)
            except httpx.HTTPStatusError as e:
                logger.error(f"Arbeitsagentur HTTP {e.response.status_code} for '{query}'")
            except Exception as e:  # a failing source contributes nothing
                logger.error(f"Arbeitsagentur error for '{query}': {e}")
    logger.info(f"Arbeitsagentur total unique jobs: {len(all_jobs)}")
    return all_jobs


# ─── remote boards: Himalayas, RemoteOK ──────────────────────────────────────

HIMALAYAS_API = "https://himalayas.app/jobs/api"
REMOTEOK_API = "https://remoteok.com/api"


def _remote_location_ok(location_text: str) -> bool:
    """No restriction means worldwide; otherwise one of the profile's
    search.remote_regions must be eligible."""
    text = location_text.lower().strip()
    if not text:
        return True
    return any(region in text for region in profile.get()["search"]["remote_regions"])


def _normalise_himalayas(raw: dict) -> dict:
    restrictions = raw.get("locationRestrictions") or []
    if isinstance(restrictions, str):
        restrictions = [restrictions]
    location = "Remote" + (f" ({', '.join(restrictions)})" if restrictions else " (Worldwide)")
    description = _strip_html(raw.get("description", "") or raw.get("excerpt", ""))
    return {
        "title": raw.get("title", "").strip(),
        "company": (raw.get("companyName") or raw.get("company", "")).strip(),
        "salary": "",
        "location": location,
        "description": description[:800],
        "url": (raw.get("applicationLink") or raw.get("guid") or "").strip(),
        "source": "himalayas",
    }


async def scrape_himalayas() -> list[dict]:
    all_jobs: list[dict] = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(HIMALAYAS_API, params={"limit": 100}, timeout=25, follow_redirects=True,
                                        headers={"Accept": "application/json"})
            response.raise_for_status()
            data = response.json()
            for raw in (data.get("jobs", data) if isinstance(data, dict) else data) or []:
                job = _normalise_himalayas(raw)
                if not job["title"] or not job["url"] or not _looks_technical(job["title"]):
                    continue
                restrictions = raw.get("locationRestrictions") or []
                if isinstance(restrictions, str):
                    restrictions = [restrictions]
                if not _remote_location_ok(", ".join(restrictions)):
                    continue
                all_jobs.append(job)
    except httpx.HTTPStatusError as e:
        logger.error(f"Himalayas HTTP {e.response.status_code}")
    except Exception as e:  # a failing source contributes nothing
        logger.error(f"Himalayas error: {e}")
    logger.info(f"Himalayas total jobs (eligible region, technical): {len(all_jobs)}")
    return all_jobs


def _normalise_remoteok(raw: dict) -> dict:
    location = raw.get("location", "").strip() or "Remote (Worldwide)"
    if "remote" not in location.lower():
        location = f"Remote ({location})"
    tags = raw.get("tags") or []
    description = _strip_html(raw.get("description", ""))
    if tags:
        description = f"Tags: {', '.join(tags)}\n\n{description}"
    return {
        "title": (raw.get("position") or raw.get("title", "")).strip(),
        "company": raw.get("company", "").strip(),
        "salary": "",
        "location": location,
        "description": description[:800],
        "url": raw.get("url", "").strip(),
        "source": "remoteok",
    }


async def scrape_remoteok() -> list[dict]:
    all_jobs: list[dict] = []
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(REMOTEOK_API, timeout=25, follow_redirects=True,
                                        headers={"Accept": "application/json",
                                                 "User-Agent": "job-pipeline/2.0 (job search tool)"})
            response.raise_for_status()
            data = response.json()
            # The first element is a legal notice, not a job.
            for raw in data[1:] if isinstance(data, list) else []:
                job = _normalise_remoteok(raw)
                if not job["title"] or not job["url"] or not _looks_technical(job["title"]):
                    continue
                if not _remote_location_ok(raw.get("location", "")):
                    continue
                all_jobs.append(job)
    except httpx.HTTPStatusError as e:
        logger.error(f"RemoteOK HTTP {e.response.status_code}")
    except Exception as e:  # a failing source contributes nothing
        logger.error(f"RemoteOK error: {e}")
    logger.info(f"RemoteOK total jobs (eligible region, technical): {len(all_jobs)}")
    return all_jobs
