"""ATS board pullers. Every ATS exposes a per-company public board and none has
a global search by city, so the puller runs off a registry of boards and each
parser turns one board's payload into the scraper's job shape, with `url` on
the employer's own ATS page (live by construction). Fixtures are anonymised
copies of the public API responses."""
import json
from pathlib import Path

from pipeline.ats_boards import (
    add_boards,
    board_from_url,
    load_registry,
    parse_ashby,
    parse_greenhouse,
    parse_join_company,
    parse_join_job,
    parse_lever,
    parse_personio,
    parse_smartrecruiters_detail,
    parse_smartrecruiters_list,
    save_registry,
)

FIX = Path(__file__).parent / "fixtures"
JOB_KEYS = {"title", "company", "salary", "location", "description", "url", "source", "live"}


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ── registry ─────────────────────────────────────────────────────────────────

def test_board_from_url_recognises_each_ats():
    cases = {
        "https://boards.greenhouse.io/northwind/jobs/12345": ("greenhouse", "northwind"),
        "https://job-boards.greenhouse.io/acme/jobs/4": ("greenhouse", "acme"),
        "https://jobs.ashbyhq.com/acme-invest/1c1f88e0-2d0c-47df": ("ashby", "acme-invest"),
        "https://jobs.lever.co/paperstack/abc-def": ("lever", "paperstack"),
        "https://mobility-lab.jobs.personio.de/job/3000001?language=de": ("personio", "mobility-lab"),
        "https://acme-labs.jobs.personio.com/job/2633368": ("personio", "acme-labs"),
        "https://jobs.smartrecruiters.com/ParcelForward/700000000000001-junior": ("smartrecruiters", "ParcelForward"),
        "https://join.com/companies/acme-learning/10000002-software-engineer": ("join", "acme-learning"),
    }
    for url, expected in cases.items():
        assert board_from_url(url) == expected, url


def test_board_from_url_ignores_aggregators_and_employer_sites():
    for url in ("https://www.adzuna.de/details/5852698795", "https://www.acme-energy.example/karriere/job/14531",
                "https://www.linkedin.com/jobs/view/123", ""):
        assert board_from_url(url) is None, url


def test_registry_dedupes_and_roundtrips(tmp_path):
    path = tmp_path / "ats_boards.json"
    assert load_registry(path) == []
    registry: list[dict] = []
    added = add_boards(registry, [("greenhouse", "northwind"), ("Greenhouse", "NORTHWIND"), ("ashby", "acme-invest")],
                       via="test")
    assert added == 2
    assert [(b["ats"], b["slug"]) for b in registry] == [("greenhouse", "northwind"), ("ashby", "acme-invest")]
    assert registry[0]["via"] == "test" and registry[0]["added"]
    save_registry(registry, path)
    assert load_registry(path) == registry
    assert add_boards(registry, [("ashby", "acme-invest")], via="again") == 0


def test_an_unreadable_registry_starts_empty(tmp_path):
    path = tmp_path / "ats_boards.json"
    path.write_text("{not json", encoding="utf-8")
    assert load_registry(path) == []


# ── parsers ──────────────────────────────────────────────────────────────────

def test_parse_greenhouse_yields_employer_urls_and_plain_text():
    jobs = parse_greenhouse(json.loads(_read("greenhouse_jobs.json")), company="Northwind Bank")
    assert len(jobs) == 2
    j = jobs[0]
    assert JOB_KEYS <= set(j)
    assert j["source"] == "ats:greenhouse" and j["live"] is True
    assert j["company"] == "Northwind Bank" and j["url"].startswith("https://")
    assert "<" not in j["description"] and "product & technology" in j["description"]
    assert j["location"] == "Berlin" and j["posted"] == "2026-03-16"


def test_parse_ashby_uses_plain_description_and_job_url():
    jobs = parse_ashby(json.loads(_read("ashby_jobs.json")), company="Acme Invest")
    assert len(jobs) == 2
    j = jobs[0]
    assert j["source"] == "ats:ashby" and j["live"] is True
    assert j["url"].startswith("https://jobs.ashbyhq.com/acme-invest/")
    assert "intern" in j["title"].lower()
    assert "<" not in j["description"] and j["description"].startswith("Employment: Intern")
    assert "Berlin" in j["location"]
    assert jobs[1]["location"] == "Berlin (Remote)"


def test_parse_lever_reads_categories():
    jobs = parse_lever(json.loads(_read("lever_postings.json")), company="Paperstack")
    assert len(jobs) == 1
    j = jobs[0]
    assert j["source"] == "ats:lever" and j["live"] is True
    assert j["url"].startswith("https://jobs.lever.co/paperstack/")
    assert j["location"] == "Berlin"
    assert "Full-time" in j["description"]  # the commitment is surfaced for the hours column
    assert j["title"] == "Senior Frontend Engineer - React"


def test_parse_personio_builds_job_urls_and_surfaces_employment_type():
    jobs = parse_personio(_read("personio_positions.xml"), company="Mobility Lab GmbH", slug="mobility-lab", tld="de")
    assert len(jobs) == 2
    j = jobs[0]
    assert j["source"] == "ats:personio" and j["live"] is True
    assert j["url"] == "https://mobility-lab.jobs.personio.de/job/3000001"
    assert "Berlin" in j["location"]
    assert "working_student" in j["description"] and "part-time" in j["description"]
    assert "<" not in j["description"]


def test_parse_smartrecruiters_list_and_detail():
    listing = parse_smartrecruiters_list(json.loads(_read("smartrecruiters_postings.json")), company="Parcel Forward")
    assert len(listing) == 2
    j = listing[0]
    assert j["source"] == "ats:smartrecruiters" and j["live"] is True
    assert j["url"] == "https://jobs.smartrecruiters.com/ParcelForward/700000000000001"
    assert j["detail_url"].startswith("https://api.smartrecruiters.com/v1/companies/ParcelForward/postings/")
    assert "Berlin" in j["location"]
    text = parse_smartrecruiters_detail(json.loads(_read("smartrecruiters_posting_detail.json")))
    assert text and "<" not in text and "GDPR" in text


def test_parse_join_company_lists_that_companys_jobs_once():
    urls = parse_join_company(_read("join_company.html"), slug="acme-learning")
    assert urls == [
        "https://join.com/companies/acme-learning/10000001-mobile-engineer-react-native",
        "https://join.com/companies/acme-learning/10000002-backend-engineer-intern-working-student",
        "https://join.com/companies/acme-learning/10000003-werkstudent-in-marketing-und-vertrieb-m-w-d",
    ]


def test_parse_join_job_reads_the_json_ld_posting():
    url = "https://join.com/companies/acme-learning/10000002-backend-engineer-intern-working-student"
    j = parse_join_job(_read("join_job.html"), url=url)
    assert j["title"] == "Backend Engineer (Intern / Working Student)"
    assert j["company"] == "Acme Learning"
    assert j["location"] == "Berlin"
    assert j["url"] == url and j["source"] == "ats:join" and j["live"] is True
    assert "TypeScript and Python" in j["description"] and "<" not in j["description"]
    assert "INTERN" in j["description"]  # employmentType surfaced for the hours column
