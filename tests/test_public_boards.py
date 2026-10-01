"""Public job search without a login: LinkedIn's guest search and job page, and
XING's public search page. A 429 stops that source for the run and is
recorded. Fixtures are anonymised copies of the public page structure."""
import asyncio
from pathlib import Path

import httpx

from pipeline.public_boards import (
    canonical_indeed_url,
    indeed_search,
    linkedin_search,
    parse_linkedin_job,
    parse_linkedin_search,
    parse_xing_job,
    parse_xing_search,
)

FIX = Path(__file__).parent / "fixtures"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_parse_linkedin_search_cards():
    jobs = parse_linkedin_search(_read("linkedin_search.html"))
    assert len(jobs) == 2
    j = jobs[0]
    assert j["title"] == "Werkstudent Datenanalyse mit Python (all genders)"
    assert j["company"] == "Brightline Advisory in Germany"
    assert j["location"] == "Berlin, Berlin, Germany"
    assert j["url"] == ("https://de.linkedin.com/jobs/view/"
                        "werkstudent-datenanalyse-mit-python-all-genders-at-brightline-advisory-4000000001")
    assert j["job_id"] == "4000000001" and j["posted"] == "2026-09-14" and j["source"] == "linkedin"
    assert jobs[1]["title"] == "Werkstudent (w/m/d) Data & AI"


def test_parse_linkedin_job_description_and_company():
    j = parse_linkedin_job(_read("linkedin_job.html"))
    assert "RAG-Pipelines" in j["description"] and "<" not in j["description"]
    assert j["company"] == "Fieldstone Capital"
    assert j["title"] == "Werkstudent (w/m/d) Data & AI"


def test_parse_xing_search_reads_the_embedded_state():
    a, b = parse_xing_search(_read("xing_search.html"))
    assert a["title"] == "Python Working Student (all genders)"
    assert a["company"] == "Kernel Motors"  # companyNameOverride wins
    assert a["location"] == "Berlin"
    assert a["url"] == "https://www.xing.com/jobs/berlin-python-working-student-genders-150000001"
    assert a["apply_url"].startswith("https://studyflix.de/jobs/")
    assert a["employment"] == "Studierende" and a["source"] == "xing"
    assert b["company"] == "Clauseworks GmbH"  # resolved through the Company reference
    assert b["apply_url"] == ""  # XING-hosted "easy apply" has no external URL


def test_parse_xing_job_reads_description_and_apply_url():
    j = parse_xing_job(_read("xing_job.html"))
    assert "n8n and the HubSpot API" in j["description"] and "<" not in j["description"]
    assert j["company"] == "Ledgerline GmbH"
    assert j["title"] == "Revenue Data Working Student"
    assert j["location"] == "Berlin"
    assert j["apply_url"].startswith("https://jobdb.softgarden.de/")


def test_linkedin_search_stops_on_first_429_and_records_it():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if len(calls) == 1:
            return httpx.Response(200, text=_read("linkedin_search.html"))
        return httpx.Response(429, text="too many requests")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await linkedin_search(client, ["werkstudent python", "working student data", "third"], pages=1,
                                         max_details=0, delay=0)

    jobs, status = asyncio.run(run())
    assert len(jobs) == 2 and status.name == "linkedin" and status.returned == 2
    assert status.blocked and "429" in status.blocked
    assert len(calls) == 2  # the third query was never sent
    assert "location=Berlin%2C+Germany" in calls[0]  # the profile's city and country


def test_linkedin_search_walks_to_the_next_page_until_one_is_empty():
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if "start=0" in str(request.url):
            return httpx.Response(200, text=_read("linkedin_search.html"))
        return httpx.Response(200, text="<ul></ul>")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await linkedin_search(client, ["werkstudent python"], pages=3, max_details=0, delay=0)

    jobs, status = asyncio.run(run())
    assert len(jobs) == 2 and status.blocked is None
    assert len(calls) == 2  # page 2 was tried, page 3 was not (page 2 came back empty)


def test_indeed_redirect_links_are_canonicalised_to_viewjob():
    # Firecrawl hands back /rc/clk redirects with a long tracking token that changes on every crawl
    raw = "https://de.indeed.com/rc/clk?jk=0abc123def456789&bb=trackingtokentrackingtoken&xkcb=more&vjs=3"
    assert canonical_indeed_url(raw) == "https://de.indeed.com/viewjob?jk=0abc123def456789"
    assert canonical_indeed_url("https://de.indeed.com/viewjob?jk=abc123&from=x") == "https://de.indeed.com/viewjob?jk=abc123"
    assert canonical_indeed_url("https://jobs.ashbyhq.com/x/1?utm=y") == "https://jobs.ashbyhq.com/x/1?utm=y"


def test_indeed_without_firecrawl_is_recorded_as_blocked():
    jobs, status = indeed_search(["werkstudent python"])
    assert jobs == [] and "FIRECRAWL_API_KEY" in status.blocked
