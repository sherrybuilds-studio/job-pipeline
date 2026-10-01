"""The sourcing pass: per-role extraction, the gates in order, dedup against
everything ever kitted, parked or sent, and the report. No network, no writes
anywhere but the report."""
import asyncio
import json
import re
from pathlib import Path

from pipeline import profile
from pipeline.sourcing import (
    HistoryIndex,
    classify,
    full_time_only,
    german_level,
    hours_asked,
    listing_language,
    map_projects,
    named_stack,
    norm_company,
    norm_title,
    pipeline,
    render_report,
)

# ── per-role extraction ──────────────────────────────────────────────────────

def test_named_stack_extracts_known_tools_in_listing_order():
    stack = named_stack("You will use Python, n8n and Make; we run HubSpot, AWS and a vector database (Qdrant).")
    assert stack[:3] == ["Python", "n8n", "Make"]
    assert {"HubSpot", "AWS", "Qdrant"} <= set(stack)
    assert len(stack) == len(set(stack))


def test_map_projects_puts_the_strongest_project_first():
    assert map_projects("Build RAG pipelines over documents with a vector database and LLM APIs")[0] == \
        "course assistant"
    assert "price tracker" in map_projects("web scraping, data pipelines and ETL into PostgreSQL")
    assert "study bot" in map_projects("You build a Telegram bot and keep its pytest suite green in GitHub Actions")


def test_map_projects_is_empty_for_generic_text():
    assert map_projects("Python and SQL for Excel reports and PowerPoint decks") == []


def test_german_level_labels():
    cases = {
        "Deutschkenntnisse C1": "C1",
        "Sehr gute Deutschkenntnisse": "sehr gut",
        "Gute Deutsch- und Englischkenntnisse": "gut",
        "German is a plus": "plus",
        "Du sprichst fließend Deutsch": "fließend",
        "We work in English.": "not stated",
    }
    for text, label in cases.items():
        assert german_level(text) == label, text


def test_hours_labels_and_full_time_praktikum_flag():
    cases = {
        "15–20 Stunden pro Woche": "15–20 h/week",
        "20 hours per week alongside your studies": "20 h/week",
        "Praktikum in Vollzeit für 6 Monate": "full-time Praktikum",
        "Full-time internship (40h)": "full-time Praktikum",
        "Teilzeit, flexibel": "part-time",
        "": "not stated",
    }
    for text, label in cases.items():
        assert hours_asked(text) == label, text


def test_full_time_only_gate_drops_pure_full_time_but_keeps_internships():
    def job(title, desc):
        return {"title": title, "description": desc}
    assert full_time_only(job("AI Engineer", "Vollzeit (40h), unbefristet")) is True
    assert full_time_only(job("Werkstudent AI", "20h pro Woche")) is False
    assert full_time_only(job("Praktikum AI Engineering", "Vollzeit, 6 Monate")) is False
    assert full_time_only(job("AI Engineer", "We are a Berlin startup")) is False  # nothing stated


def test_listing_language():
    assert listing_language("Wir suchen dich für unser Team in Berlin und freuen uns auf die Bewerbung") == "DE"
    assert listing_language("We are looking for a working student to join our team in Berlin") == "EN"


def test_normalisers():
    assert norm_company("Brightline Advisory in Germany") == norm_company("Brightline Advisory GmbH")
    assert norm_company("Brightside (Brightside Solutions GmbH)") == norm_company("Brightside")
    assert norm_title("Werkstudent:in Data & Automation (m/w/d)") == norm_title("Werkstudent Data Automation")
    assert norm_title("Working Student - Data Platform & AI Tools (f/m/d)") == \
        norm_title("Working Student Data Platform AI Tools")


# ── history and dedup ────────────────────────────────────────────────────────

def _write(path: Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_history_index_sees_folders_kits_parked_and_rows(tmp_path):
    apps = tmp_path / "applications"
    _write(apps / "foo-gmbh-werkstudent-ai" / "job.json",
           json.dumps({"title": "Werkstudent AI (m/w/d)", "company": "Foo GmbH", "url": "https://foo.de/j/1"}))
    _write(apps / "batch-2" / "Bar.md", "# Bar (Bar Solutions GmbH) — Working Student AI\n\n"
                                         "**Send route:** https://bar.io/apply\n")
    _write(apps / "batch-2" / "_parked" / "Baz.md", "# Baz AG — Praktikum AI Engineering\n")
    _write(apps / "batch-2" / "CHECKLIST.md", "# Batch 2 send folder — 8 to send\n")
    history = HistoryIndex.load(apps)
    history.add_rows([{"company": "Qux", "job_title": "Junior Data Engineer", "url": "https://qux.com/1"}])

    assert history.seen({"company": "Foo GmbH", "title": "Werkstudent AI", "url": ""}) == "application folder"
    assert history.seen({"company": "Other", "title": "x", "url": "https://foo.de/j/1"}) == "application folder"
    assert history.seen({"company": "Bar", "title": "Working Student AI (f/m/d)", "url": ""}) == "kit"
    assert history.seen({"company": "Baz AG", "title": "Praktikum AI Engineering", "url": ""}) == "parked"
    assert history.seen({"company": "Qux GmbH", "title": "Junior Data Engineer", "url": ""}) == "tracker"
    assert history.seen({"company": "Batch 2 send folder", "title": "8 to send", "url": ""}) is None
    assert history.seen({"company": "Foo GmbH", "title": "Data Engineer", "url": ""}) is None
    assert history.company_seen({"company": "Foo GmbH"}) is True
    assert history.company_seen({"company": "Nobody"}) is False


# ── classification and the whole pipeline ───────────────────────────────────

def test_classify_needs_a_project_map_and_a_live_page():
    assert classify({"projects": ["course assistant"], "live": True}) == "ready"
    assert classify({"projects": [], "live": True}) == "weak"
    assert classify({"projects": ["study bot"], "live": None}) == "weak"


def _raw(title, desc, company="Acme GmbH", url="https://acme.de/j/1", source="adzuna", **extra):
    return {"title": title, "company": company, "url": url, "source": source, "description": desc,
            "location": "Berlin", "salary": "", **extra}


def test_pipeline_applies_gates_dedups_and_merges_linkedin_onto_ats(tmp_path):
    ats = _raw("Werkstudent AI Engineering (m/w/d)",
               "Du baust RAG-Pipelines mit Python und einer Vektordatenbank. 20 Stunden pro Woche. "
               "Gute Deutschkenntnisse.", url="https://acme.jobs.personio.de/job/1", source="ats:personio", live=True)
    linkedin_dup = _raw("Werkstudent AI Engineering", "Du baust RAG-Pipelines mit Python. 20 Stunden pro Woche.",
                        company="Acme", url="https://de.linkedin.com/jobs/view/x-1", source="linkedin")
    c1 = _raw("Werkstudent KI", "Python und LLMs. Deutsch C1 erforderlich. 20h/Woche", company="B1",
              url="https://b1.de/1")
    masters = _raw("Werkstudent AI", "Python, LLMs, laufendes Masterstudium. 20h/Woche", company="B2",
                   url="https://b2.de/1")
    full_time = _raw("AI Engineer", "Python, LLMs, RAG. Vollzeit, 40h.", company="B3", url="https://b3.de/1")
    non_tech = _raw("Werkstudent Marketing Manager", "Social media, 20h/Woche", company="B4", url="https://b4.de/1")
    elsewhere = _raw("Werkstudent AI", "Python, LLMs, RAG. 20h/Woche", company="B5", url="https://b5.de/1",
                     location="München")
    applied = _raw("Werkstudent AI", "Python, LLMs, RAG. 20h/Woche", company="Foo GmbH", url="https://foo.de/j/1")
    dead = _raw("Werkstudent AI", "Python, LLMs, RAG mit Vektordatenbank. 20h/Woche", company="B6",
                url="https://b6.de/1")
    generic = _raw("Werkstudent Python", "Python, SQL, Excel reports. 20h/Woche", company="B7", url="https://b7.de/1")

    apps = tmp_path / "applications"
    _write(apps / "foo" / "job.json", json.dumps({"title": "Werkstudent AI", "company": "Foo GmbH",
                                                  "url": "https://foo.de/j/1"}))
    history = HistoryIndex.load(apps)

    async def fake_verify(_client, job):
        job["live"] = job["company"] != "B6"
        job["employer_url"] = job["url"]
        job["live_note"] = "HTTP 404" if job["company"] == "B6" else "ok"
        return job

    result = asyncio.run(pipeline([ats, linkedin_dup, c1, masters, full_time, non_tech, elsewhere, applied, dead,
                                   generic], history=history, verify=fake_verify, client=None))
    ready, weak, stats = result["ready"], result["weak"], result["stats"]
    assert [j["company"] for j in ready] == ["Acme GmbH"]
    assert ready[0]["projects"] == ["course assistant"]
    assert ready[0]["merged_from"] == ["linkedin"]  # the LinkedIn duplicate folded onto the ATS row
    assert [j["company"] for j in weak] == ["B7"]
    assert stats["raw"] == 10
    assert stats["dropped"] == {"c1": 1, "degree": 1, "internship": 0, "full_time": 1, "no_student_role": 0,
                                "non_tech": 1, "location": 1, "already_applied": 1, "dead": 1}
    assert stats["ready"] == 1 and stats["weak"] == 1


def test_render_report_has_two_tables_and_the_summary_line():
    row = {"company": "Acme GmbH", "title": "Werkstudent AI", "live": True, "employer_url": "https://acme.de/j/1",
           "language": "DE", "german": "gut", "hours": "20 h/week", "stack": ["Python", "RAG"],
           "projects": ["course assistant"], "score": 77, "source": "ats:personio", "merged_from": []}
    weak = {**row, "company": "B7", "projects": [], "score": 41, "live": None}
    md = render_report(ready=[row], weak=[weak],
                       stats={"raw": 10, "dropped": {"c1": 1, "degree": 1, "full_time": 1, "non_tech": 1,
                                                     "location": 1, "already_applied": 1, "dead": 1},
                              "ready": 1, "weak": 1},
                       sources=[{"name": "ats:personio", "returned": 3, "blocked": None},
                                {"name": "indeed", "returned": 0, "blocked": "403 and captcha"}],
                       runtime_s=12.3, run_date="2026-10-01")
    assert "## A. READY" in md and "## B. WEAK" in md
    assert "| Acme GmbH | Werkstudent AI | ✅ live |" in md
    assert "| B7 | Werkstudent AI | ❓ unverified |" in md
    assert "10 raw found" in md and "1 German C1" in md and "1 already-applied" in md and "1 dead" in md
    assert "1 ready" in md and "1 weak" in md
    assert "indeed" in md and "403 and captcha" in md
    assert "runtime 12 s" in md


def test_public_board_queries_are_student_role_queries():
    """The example profile searches for student roles on every public board."""
    for name in ("linkedin", "xing", "arbeitnow"):
        queries = profile.queries(name)
        assert queries, name
        assert all(re.search(r"werkstudent|working student|praktik|intern", q.lower()) for q in queries), name
