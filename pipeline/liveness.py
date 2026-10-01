"""Liveness: a role counts as open only when the employer's own page says so.

* ats:* jobs come straight from the employer's ATS API, so they are live by
  construction.
* Everything else is followed through redirects to the page the listing really
  applies on (Arbeitnow's /apply, XING's applyUrl, Adzuna's redirect), and that
  page is read for closed markers.
* A URL that only ever lands on an aggregator (LinkedIn, XING, Adzuna, a
  university job board) is unverified: never live, never READY.

Read-only GETs, one per role.
"""
import logging
import re
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "de-DE,de;q=0.9,en;q=0.8"}
TIMEOUT = 20

# Hosts that list other people's jobs: reaching one is not reaching the
# employer. (join.com and softgarden are ATSs, so employer routes.)
AGGREGATOR_HOSTS = (
    "linkedin.com", "xing.com", "adzuna.", "arbeitnow.com", "indeed.", "stepstone.",
    "kimeta.", "glassdoor.", "jobware.", "stellenanzeigen.", "monster.", "jooble.",
    "talent.com", "kununu.", "arbeitsagentur.de", "berlinstartupjobs.com",
    "himalayas.app", "remoteok.com", "studyflix.de", "jobvector.", "absolventa.",
    "unicum.", "jobteaser.", "stellenwerk.", "praktikum.info", "meinpraktikum.",
    "campusjaeger.", "workwise.", "google.com", "bing.com",
    # university and student job boards relay employers' postings
    "stellenticket.", "gopraktikum.", "hochschuljobboerse.", "unijobs.", "jobboerse.", "studentjob.", "jobmensa.",
)

_CLOSED_BODY = (
    "nicht mehr verfügbar", "nicht mehr verfuegbar", "nicht mehr online", "nicht mehr aktiv",
    "nicht mehr aktuell", "bereits besetzt", "stelle wurde besetzt", "ist abgelaufen",
    "leider abgelaufen", "abgelaufen.", "ist beendet", "wurde geschlossen", "bewerbungsfrist abgelaufen",
    "no longer available", "no longer accepting", "no longer open", "no longer active",
    "position has been filled", "has been filled", "job has expired", "job expired", "has expired",
    "this job is closed", "posting has closed", "has been closed", "not available anymore", "is closed.",
)
_CLOSED_TITLE = re.compile(r"\b(404|not found|nicht gefunden|page not found|seite nicht gefunden)\b", re.I)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_SCRIPTS = re.compile(r"<(script|style|noscript|template)\b.*?</\1>", re.S | re.I)


def is_aggregator(url: str) -> bool:
    host = (urlparse(url or "").netloc or "").lower()
    return any(h in host for h in AGGREGATOR_HOSTS)


def closed_reason(text: str) -> str | None:
    """Why the page says the role is gone, or None when it reads as open.

    Markers are matched in the visible text only: some ATS pages ship their
    translation table ("Diese Stelle ist nicht mehr verfügbar") inside a script
    tag on every job page, which would read every open posting as closed."""
    low = _SCRIPTS.sub(" ", text or "").lower()
    for marker in _CLOSED_BODY:
        if marker in low:
            return f"closed: {marker!r}"
    m = _TITLE.search(text or "")
    if m and _CLOSED_TITLE.search(m.group(1)):
        return f"closed: title {m.group(1).strip()[:60]!r}"
    return None


def probe_url(job: dict) -> str:
    """The URL that actually leads towards the employer for this listing."""
    if job.get("apply_url"):
        return job["apply_url"]
    url = job.get("url", "")
    if job.get("source") == "arbeitnow" and url:
        return url.rstrip("/") + "/apply"
    return url


async def verify(client: httpx.AsyncClient | None, job: dict) -> dict:
    """Set job['live'] (True, False, or None for unverified), 'employer_url', 'live_note'."""
    if str(job.get("source", "")).startswith("ats:"):
        job["live"] = True
        job["employer_url"] = job.get("employer_url") or job.get("url", "")
        job["live_note"] = "employer ATS"
        return job

    url = probe_url(job)
    if not url or client is None:
        job.update(live=None, employer_url=job.get("url", ""), live_note="no URL to verify")
        return job
    try:
        r = await client.get(url, headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)
    except httpx.HTTPError as e:
        job.update(live=None, employer_url=url, live_note=f"unreachable: {type(e).__name__}")
        return job

    final = str(r.url)
    job["employer_url"] = final
    if r.status_code in (404, 410):
        job.update(live=False, live_note=f"HTTP {r.status_code}")
    elif r.status_code >= 400:
        job.update(live=None, live_note=f"HTTP {r.status_code} (blocked?)")
    elif is_aggregator(final):
        job.update(live=None, live_note="aggregator-only (no employer page reached)")
    else:
        reason = closed_reason(r.text)
        job.update(live=reason is None, live_note=reason or "employer page open")
    return job
