"""The daily run wired end to end, offline: fake postings go through the real
matcher and graph on a dry run, which writes nothing and sends nothing."""
import asyncio
import json

from pipeline import agent, summary

POSTINGS = [
    {"title": "Werkstudent Data Engineering (m/w/d)", "company": "Northwind", "url": "https://northwind.example/j/1",
     "location": "Berlin", "source": "arbeitnow",
     "description": "Du baust Scraping- und ETL-Pipelines mit Python, Playwright und PostgreSQL. "
                    "20 Stunden pro Woche."},
    {"title": "Werkstudent Data Engineering (m/w/d)", "company": "Northwind", "url": "https://northwind.example/j/1",
     "location": "Berlin", "source": "adzuna", "description": "duplicate of the first posting"},
    {"title": "Senior Sales Manager", "company": "Acme", "url": "https://acme.example/j/2", "location": "Berlin",
     "source": "adzuna", "description": "Vollzeit. Vertrieb."},
    {"title": "Werkstudent KI", "company": "Brightside", "url": "https://brightside.example/j/3", "location": "Berlin",
     "source": "adzuna", "description": "Python und LLMs. Deutschkenntnisse C1 erforderlich. 20 Stunden."},
]


def test_merge_dedups_across_sources():
    merged = agent.merge([POSTINGS[:2], POSTINGS[1:]])
    assert [j["url"] for j in merged] == ["https://northwind.example/j/1", "https://acme.example/j/2",
                                          "https://brightside.example/j/3"]


def test_save_run_appends_to_the_history(tmp_path):
    path = tmp_path / "top_matches.json"
    scored = [{"title": "Werkstudent", "company": "Northwind", "score": 90, "url": "u", "source": "s"}]
    agent.save_run("2026-09-30", 3, 1, scored, path=path)
    agent.save_run("2026-10-01", 4, 0, [], path=path)
    runs = json.loads(path.read_text())
    assert [r["date"] for r in runs] == ["2026-09-30", "2026-10-01"] and runs[0]["top_10"][0]["score"] == 90


def test_a_dry_run_scores_and_reports_without_writing(monkeypatch, tmp_path):
    async def fake_scrape(pages, iteration):
        return agent.merge([POSTINGS])

    monkeypatch.setattr(agent, "scrape_all", fake_scrape)
    monkeypatch.setattr(agent, "RUNS_PATH", tmp_path / "top_matches.json")
    state = asyncio.run(agent.run(dry_run=True, min_score=25))
    assert state["result"] == "notified_matches"
    assert [j["company"] for j in state["matched"]] == ["Northwind"]  # sales role and C1 role dropped
    assert state["_path"] == ["scrape", "match", "write", "notify", "end"]
    assert not (tmp_path / "top_matches.json").exists()


def test_persist_job_writes_a_kit_folder_and_never_touches_a_sent_one(tmp_path, monkeypatch):
    from pipeline import profile, tracker
    monkeypatch.setattr(tracker, "APPS_DIR", tmp_path)
    job = {**POSTINGS[0], "score": 80}
    agent.persist_job(job, 80, "Dear Northwind team,\n\nA letter.\n\nKind regards,\nAlex Example\n")
    folder = tmp_path / tracker.slugify("Northwind-Werkstudent Data Engineering (m/w/d)")
    assert (folder / profile.cv_filename()).exists() and (folder / "cv.pdf").exists()
    assert (folder / "lint.md").exists()

    sent = tmp_path / tracker.slugify("Acme-Werkstudent AI")
    sent.mkdir()
    (sent / "status.json").write_text(json.dumps({"response_notes": "sent 2026-09-30"}))
    agent.persist_job({"title": "Werkstudent AI", "company": "Acme", "url": "u", "description": "x"}, 50, "new")
    assert sorted(p.name for p in sent.iterdir()) == ["status.json"]  # no CV, no letter, no lint written


def test_cv_keywords_for_a_posting_use_the_profile_skills():
    assert agent._cv_keywords(POSTINGS[0]) == ["Python", "Playwright", "SQL"]


def test_summary_counts_only_stamped_sends_as_sent():
    rows = [{"company": "Northwind", "job_title": "Werkstudent", "stage": "applied", "score": 80,
             "response_notes": "sent 2026-09-30"},
            {"company": "Acme", "job_title": "Werkstudent", "stage": "replied", "score": 70,
             "response_notes": "sent 2026-09-28", "response_at": "2026-10-01T09:00:00+00:00"},
            {"company": "Generated Only", "job_title": "Werkstudent", "stage": None, "score": 60}]
    text = summary.summarise(rows)
    assert "Tracked postings: 3" in text and "Sent: 2" in text and "Responses: 1" in text
    assert "Northwind: Werkstudent (score 80)" in text and "Generated Only" not in text.split("Sent, no response")[1]
    assert summary.summarise([]) == "No applications tracked yet."
