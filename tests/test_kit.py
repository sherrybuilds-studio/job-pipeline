"""kit.py: READY rows → reviewer-cleared kits. Nothing is sent. A kit is CLEARED
only when the reviewer finds zero unsupported claims, the regex gate is clean,
the letter has no slash and stays under 250 words; otherwise it is BLOCKED and
no letter PDF is rendered. One kit per company, never a company already
contacted."""
import json

import httpx

from pipeline import archive, kit, profile
from pipeline.sourcing import HistoryIndex


def row(company, title, score, **extra):
    return {"company": company, "title": title, "score": score, "live": True, "projects": ["study bot"],
            "url": f"https://{company.lower().replace(' ', '')}.example/j", "employer_url": "", "stack": ["Python"],
            "description": "x", "hours": "20 h/week", "german": "not stated", "language": "EN", **extra}


def test_candidates_take_one_role_per_company_and_skip_contacted_ones(tmp_path):
    apps = tmp_path / "applications"
    (apps / "foo").mkdir(parents=True)
    (apps / "foo" / "job.json").write_text(json.dumps({"company": "Foo GmbH", "title": "Old role", "url": "u"}))
    history = HistoryIndex.load(apps)
    ready = [row("Northwind", "Data Engineer Intern", 75),
             row("Northwind", "Data Engineer (Working Student)", 75),
             row("Foo", "New role", 99), row("Acme", "Werkstudent A", 60), row("Acme", "Werkstudent B", 55),
             row("Dead", "x", 50, live=None), row("NoMap", "x", 50, projects=[])]
    ready[0]["hours"] = "full-time Praktikum"
    picked = kit.pick_candidates(ready, history, limit=10)
    assert [(c["company"], c["title"]) for c in picked] == [
        ("Northwind", "Data Engineer (Working Student)"),  # part-time beats the full-time Praktikum at equal fit
        ("Acme", "Werkstudent A")]


def test_limit_caps_the_batch():
    ready = [row(f"C{i}", "Werkstudent", 90 - i) for i in range(30)]
    assert len(kit.pick_candidates(ready, HistoryIndex(), limit=17)) == 17


def test_cv_keywords_come_only_from_profile_skills():
    kws = kit.cv_keywords(["Python", "Kubernetes", "PostgreSQL", "Docker", "Java", "Vector DB", "pandas"])
    assert kws == ["Python", "SQL", "Docker", "RAG", "pandas"]  # Kubernetes and Java are not in the profile


def test_assemble_adds_greeting_closing_and_signature_once():
    closing = profile.get()["letters"]["closing"]
    letter = kit.assemble("One gap first: no AWS.\n\nI built a price tracker.", contact="")
    assert letter.startswith("Dear hiring team,\n\nOne gap first")
    assert letter.rstrip().endswith("Kind regards,\nAlex Example")
    assert letter.count(closing) == 1
    body_with_closing = "Gap.\n\nI'm based in Berlin and available up to 20 hours per week."
    assert kit.assemble(body_with_closing, contact="Ms Example").count("based in Berlin") == 1
    assert kit.assemble("Gap.", contact="Ms Example").startswith("Dear Ms Example,")
    replaced = kit.assemble("Dear Sir,\nGap.\n\nKind regards,\nSomeone", company="Acme")
    assert replaced.startswith("Dear Acme team,\n\nGap.") and "Someone" not in replaced


def test_clearance_needs_zero_unsupported_claims_clean_gate_no_slash_and_under_250_words():
    ok = {"ungrounded_claims": [], "gate": [], "unaddressed_musts": ["Commitment to excellence"]}
    assert kit.clearance("A clean letter.", ok) == ("CLEARED", [])
    status, why = kit.clearance("Python/FastAPI letter " + "word " * 260,
                                {"ungrounded_claims": [{"claim": "43 tickets", "why": "x"}], "gate": ["client"],
                                 "unaddressed_musts": []})
    assert status == "BLOCKED"
    assert len(why) == 4  # unsupported claim, gate hit, slash, length


def test_batch_folders_count_as_history(tmp_path):
    apps = tmp_path / "applications"
    (apps / "batch-2").mkdir(parents=True)
    (apps / "batch-2" / "Acme.md").write_text("# Acme GmbH — Werkstudent AI\n", encoding="utf-8")
    assert HistoryIndex.load(apps).seen({"company": "Acme", "title": "Werkstudent AI (m/w/d)", "url": ""}) == "kit"


BODY = ("I have not worked with Kafka, which your posting names first. My German is B1, so I am writing in English.\n\n"
        "My price tracker scrapes 120 product pages every morning with Playwright and stores them in PostgreSQL. "
        "The course assistant answers 34 of 40 test questions with retrieval over ChromaDB.\n\n"
        "Your team builds the data platform for parcel logistics in Berlin, and I want to learn how such a system "
        "stays reliable.")
POSTING = ("Werkstudent Data Engineering (m/w/d) at Northwind. You build scraping pipelines in Python and keep "
           "PostgreSQL tidy. Kafka is a plus. 20 hours per week. " * 6)


def test_build_kit_writes_a_cleared_kit_end_to_end(tmp_path, monkeypatch):
    apps = tmp_path / "applications"
    monkeypatch.setattr(kit, "APPS_DIR", apps)

    def offline(url):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr(archive, "fetch", offline)
    monkeypatch.setattr(kit, "_chat", lambda messages, agent: BODY)
    monkeypatch.setattr(kit.reviewer, "review_letter", lambda posting, letter: {
        "requirements": [{"requirement": "Python", "kind": "must", "status": "matched", "in_letter": True}],
        "ungrounded_claims": [], "voice_notes": [], "unaddressed_musts": [], "gate": [], "billed": "fake",
        "verdict": "pass"})
    batch = apps / "batch-1"
    batch.mkdir(parents=True)
    r = row("Northwind", "Werkstudent Data Engineering (m/w/d)", 80, description=POSTING,
            stack=["Python", "PostgreSQL", "Kafka"], projects=["price tracker"])
    result = kit.build_kit(r, batch)
    assert result["status"] == "CLEARED", result
    folder = apps / result["folder"]
    for name in ("job.json", "jd.txt", "posting.md", "status.json", "cv.pdf", "cover_letter.txt", "review.json",
                 "review.md", "lint.md", profile.cv_filename(), profile.letter_filename("Northwind")):
        assert (folder / name).exists(), name
    status = json.loads((folder / "status.json").read_text())
    assert status["cv_keywords"] == ["Python", "SQL"] and status["lint"]["status"] == "pass"
    note = (batch / "Northwind.md").read_text(encoding="utf-8")
    assert "**Status:** CLEARED" in note and "NOT SENT" in note
    assert note.startswith("# Northwind — Werkstudent Data Engineering (m/w/d)")  # the history index reads this


def test_a_blocked_kit_gets_no_letter_pdf(tmp_path, monkeypatch):
    apps = tmp_path / "applications"
    monkeypatch.setattr(kit, "APPS_DIR", apps)
    monkeypatch.setattr(archive, "fetch", lambda url: "")
    monkeypatch.setattr(kit, "_chat", lambda messages, agent: BODY + " I cut costs for a client by 10x.")
    monkeypatch.setattr(kit.reviewer, "review_letter", lambda posting, letter: {
        "requirements": [], "ungrounded_claims": [{"claim": "10x", "why": "not in the facts"}], "voice_notes": [],
        "unaddressed_musts": [], "gate": [], "billed": "fake", "verdict": "revise"})
    batch = apps / "batch-1"
    batch.mkdir(parents=True)
    result = kit.build_kit(row("Northwind", "Werkstudent Data", 80, description=POSTING), batch)
    assert result["status"] in ("BLOCKED", "NEEDS-HUMAN-FIX")
    assert not (apps / result["folder"] / profile.letter_filename("Northwind")).exists()
