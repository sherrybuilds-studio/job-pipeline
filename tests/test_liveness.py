"""Liveness: a role is surfaced only when the employer's own page says it is
open. ATS-API items are live by construction; everything else is followed
through redirects to an employer page and read for closed markers. A URL that
only ever resolves to an aggregator is unverified, never live."""
import asyncio

import httpx

from pipeline.liveness import closed_reason, is_aggregator, verify


def test_closed_markers_in_german_and_english():
    for text in ("Diese Stelle ist nicht mehr verfügbar.", "Die Ausschreibung ist leider abgelaufen",
                 "This job is no longer available", "This position has been filled", "Sorry, this job has expired"):
        assert closed_reason(text), text


def test_open_page_has_no_closed_reason():
    assert closed_reason("<h1>Werkstudent AI</h1><p>Jetzt bewerben</p>") is None


def test_a_404_title_reads_as_closed():
    assert closed_reason("<html><title>Seite nicht gefunden</title><body>Hilfe</body></html>")


def test_is_aggregator():
    for url in ("https://de.linkedin.com/jobs/view/x-123", "https://www.xing.com/jobs/x-1",
                "https://www.adzuna.de/details/1", "https://www.arbeitnow.com/jobs/companies/x/y",
                "https://de.indeed.com/viewjob?jk=1", "https://www.stepstone.de/stellenangebote--x"):
        assert is_aggregator(url), url
    for url in ("https://jobs.ashbyhq.com/acme-invest/1", "https://acme.jobs.personio.de/job/1",
                "https://www.acme-energy.example/karriere/job/14531"):
        assert not is_aggregator(url), url


def test_university_job_boards_are_aggregators_too():
    # a university job board relays employers' postings; it is not the employer
    assert is_aggregator("https://stellenticket.example-uni.de/stellenangebote/206539?namespace=non_science")
    assert is_aggregator("https://gopraktikum.example-hochschule.de/de/1884?utm_source=xing.com")


def _job(url: str, source: str = "adzuna", **extra) -> dict:
    return {"title": "Werkstudent AI", "company": "X GmbH", "url": url, "source": source, "description": "",
            "location": "Berlin", **extra}


def _verify(job: dict, handler) -> dict:
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await verify(client, job)
    return asyncio.run(run())


def test_ats_jobs_are_live_by_construction():
    job = _job("https://jobs.ashbyhq.com/acme-invest/1", source="ats:ashby", live=True)
    out = asyncio.run(verify(None, job))
    assert out["live"] is True and out["employer_url"] == job["url"]


def test_404_marks_dead():
    out = _verify(_job("https://company.example/jobs/1"), lambda r: httpx.Response(404, text="not found"))
    assert out["live"] is False and "404" in out["live_note"]


def test_closed_marker_marks_dead():
    out = _verify(_job("https://company.example/jobs/1"),
                  lambda r: httpx.Response(200, text="<p>Diese Stelle ist nicht mehr verfügbar</p>"))
    assert out["live"] is False and out["live_note"].startswith("closed")


def test_redirect_to_employer_page_marks_live():
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host == "www.arbeitnow.com":
            assert r.url.path.endswith("/apply")  # Arbeitnow's apply link is the way out
            return httpx.Response(302, headers={"location": "https://company.example/jobs/1"})
        return httpx.Response(200, text="<h1>Werkstudent AI</h1><p>Jetzt bewerben</p>")

    out = _verify(_job("https://www.arbeitnow.com/jobs/companies/x/werkstudent-ai-1", source="arbeitnow"), handler)
    assert out["live"] is True and out["employer_url"] == "https://company.example/jobs/1"


def test_xing_uses_the_apply_url_to_reach_the_employer():
    def handler(r: httpx.Request) -> httpx.Response:
        assert r.url.host == "jobdb.softgarden.de"
        return httpx.Response(200, text="<h1>Working Student</h1>")

    job = _job("https://www.xing.com/jobs/berlin-x-1", source="xing",
               apply_url="https://jobdb.softgarden.de/jobdb/public/jobposting/applyonline/click?jp=1")
    out = _verify(job, handler)
    assert out["live"] is True and out["employer_url"].startswith("https://jobdb.softgarden.de/")


def test_aggregator_only_stays_unverified():
    out = _verify(_job("https://de.linkedin.com/jobs/view/x-123", source="linkedin"),
                  lambda r: httpx.Response(200, text="<h1>Werkstudent</h1>"))
    assert out["live"] is None and "aggregator" in out["live_note"]


def test_blocked_and_unreachable_pages_stay_unverified():
    assert _verify(_job("https://company.example/jobs/1"), lambda r: httpx.Response(403))["live"] is None

    def boom(r):
        raise httpx.ConnectError("refused", request=r)

    out = _verify(_job("https://company.example/jobs/1"), boom)
    assert out["live"] is None and out["live_note"].startswith("unreachable")


def test_closed_markers_inside_scripts_do_not_count():
    page = ('<html><head><title>Revenue Data Working Student</title></head><body><h1>GTM Engineering Working '
            'Student</h1><script id="__NEXT_DATA__">{"jobApply.jobAd.archivedJob.title":"Diese Stelle ist nicht mehr '
            'verfügbar"}</script></body></html>')
    assert closed_reason(page) is None
    assert closed_reason("<body><p>Diese Stelle ist nicht mehr verfügbar</p></body>") is not None
