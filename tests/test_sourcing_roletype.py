"""Role type at the source: kits are built only for postings whose own words
offer a role type the profile accepts. Internships, full-time roles and
speculative "no open role" targets are dropped after the hard bars and before
anything is scored; every drop carries a reason in the log, the stats and the
report."""
import asyncio
import json
import logging

from pipeline import matcher
from pipeline.sourcing import HistoryIndex, pipeline, render_report


def _raw(title, desc, company, url="https://x.de/j/1", **extra):
    return {"title": title, "company": company, "url": url, "source": "adzuna", "description": desc,
            "location": "Berlin", "salary": "", **extra}


async def _live(_client, job):
    job.update(live=True, employer_url=job["url"], live_note="ok")
    return job


def _run(jobs):
    return asyncio.run(pipeline(jobs, history=HistoryIndex(), verify=_live, client=None))


def test_only_accepted_roles_survive_and_every_drop_has_a_reason(caplog):
    ws = _raw("Werkstudent AI Engineering (m/w/d)", "Du baust RAG-Pipelines mit Python. 20 Stunden pro Woche.",
              "Acme GmbH", url="https://acme.de/j/1")
    intern = _raw("Praktikum AI Engineering", "Sechs Monate Vollzeit-Praktikum, Python und LLMs, RAG.", "B1",
                  url="https://b1.de/1")
    intern_en = _raw("Engineering Intern", "12-month internship. Python, RAG, LLMs. Berlin.", "B2", url="https://b2.de/1")
    full = _raw("AI Engineer (m/w/d)", "Python, LLMs, RAG. Vollzeit, unbefristet.", "B3", url="https://b3.de/1")
    junior = _raw("Junior Data Engineer", "Python, SQL, ETL pipelines, 20 hours per week possible.", "B4",
                  url="https://b4.de/1")
    speculative = _raw("Working Student AI (speculative)", "Careers page reachable, no open role listed. RAG, LLM.",
                       "B5", url="")

    with caplog.at_level(logging.INFO, logger="sourcing"):
        result = _run([ws, intern, intern_en, full, junior, speculative])

    assert [j["company"] for j in result["ready"]] == ["Acme GmbH"]
    assert result["weak"] == []
    d = result["stats"]["dropped"]
    assert d["internship"] == 2 and d["full_time"] == 1 and d["no_student_role"] == 2
    by_company = {x["company"]: x for x in result["dropped"]}
    assert by_company["B1"]["gate"] == "internship" and "Praktikum" in by_company["B1"]["why"]
    assert by_company["B2"]["gate"] == "internship"
    assert by_company["B3"]["gate"] == "full_time" and "full-time" in by_company["B3"]["why"]
    assert by_company["B4"]["gate"] == "no_student_role" and "no Werkstudent" in by_company["B4"]["why"]
    assert by_company["B5"]["gate"] == "no_student_role" and "speculative" in by_company["B5"]["why"]
    logged = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Dropped (")]
    assert len(logged) == 5 and any("Praktikum AI Engineering @ B1" in m for m in logged)


def test_a_student_word_in_the_title_does_not_rescue_an_internship_title():
    # a title with a Werkstudent marker decides: the posting offers both tracks
    both = _raw("Working Student / Intern AI", "This is a 6-month full-time internship (40h).", "C1",
                url="https://c1.de/1")
    assert _run([both])["stats"]["dropped"]["internship"] == 0
    # a title that says only "Intern" is an internship, whatever the body says
    intern_only = _raw("Software Engineer Intern", "Als Werkstudent:in möglich? Nein, Praktikum, 40 Stunden.", "C2",
                       url="https://c2.de/1")
    assert _run([intern_only])["stats"]["dropped"]["internship"] == 1


def test_internships_pass_when_the_profile_accepts_them(profile_with):
    profile_with({"search": {"role_types": ["werkstudent", "internship"]}})
    intern = _raw("Praktikum AI Engineering", "Pflichtpraktikum mit Python und RAG, 20 Stunden pro Woche.", "D0",
                  url="https://d0.de/1")
    result = _run([intern])
    assert result["stats"]["dropped"]["internship"] == 0
    assert [j["company"] for j in result["ready"]] == ["D0"]


def test_hard_bars_still_come_first_and_scoring_is_untouched():
    c1_ws = _raw("Werkstudent KI (m/w/d)", "Python, RAG. Deutsch C1 erforderlich. 20h/Woche.", "D1",
                 url="https://d1.de/1")
    masters_intern = _raw("Praktikum Data", "Laufendes Masterstudium erforderlich. Python.", "D2", url="https://d2.de/1")
    ws = _raw("Werkstudent AI (m/w/d)", "Du baust RAG-Pipelines mit Python. 20h/Woche.", "D3", url="https://d3.de/1")
    result = _run([c1_ws, masters_intern, ws])
    d = result["stats"]["dropped"]
    assert d["c1"] == 1 and d["degree"] == 1 and d["internship"] == 0
    assert result["ready"][0]["score"] == matcher._score_job(ws)["score"]


def test_report_lists_every_dropped_posting():
    stats = {"raw": 2, "dropped": {"c1": 0, "degree": 0, "internship": 1, "full_time": 0, "no_student_role": 0,
                                   "non_tech": 0, "location": 0, "already_applied": 0, "dead": 0},
             "unverified": 0, "ready": 0, "weak": 0}
    dropped = [{"company": "B1", "title": "Praktikum AI", "gate": "internship",
                "why": "internship or Praktikum, not an accepted role type", "url": "https://b1.de/1"}]
    md = render_report(ready=[], weak=[], stats=stats, sources=[], runtime_s=1, run_date="2026-10-01", dropped=dropped)
    assert "## C. DROPPED (1)" in md
    assert "| B1 | Praktikum AI | internship | internship or Praktikum, not an accepted role type | https://b1.de/1 |" \
        in md
    assert "1 internship" in md and "0 no-student-role" in md
    json.dumps(dropped)  # the JSON report carries the same rows
