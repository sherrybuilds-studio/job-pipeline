"""READY has to mean "an honest, matched letter can be written": a single generic
word is not a project map, the tech gate reads the title, a full-time role is
full-time even when its boilerplate mentions part-time, a kit under a brand
name dedups under the legal name too, and the search area follows the profile."""
from pipeline.sourcing import HistoryIndex, full_time_only, in_search_area, is_tech, map_projects, norm_title


def test_one_generic_word_is_not_a_project_map():
    for text in ("Monitoring of KPIs in Excel and a scheduler for team meetings",
                 "Du bist telefonisch erreichbar und betreust die Anrufe der Gäste",
                 "CRM hygiene and outreach for the partner sales team",
                 "Evaluation of credit applications; UI mockups in Figma",
                 "Our stack runs on Docker"):
        assert map_projects(text) == [], text


def test_one_strong_signal_or_two_supporting_ones_map():
    assert map_projects("You set up retrieval with embeddings for our support search") == ["course assistant"]
    assert map_projects("Docker and Linux on our build servers, Git for everything") == ["study bot"]  # 3 supporting
    assert "price tracker" in map_projects("web scraping into PostgreSQL")
    assert "study bot" in map_projects("a Telegram bot for our team")


def test_tech_gate_reads_the_title():
    def job(title, desc=""):
        return {"title": title, "description": desc}
    for title in ("Junior Credit Associate", "Working Student - Partner Sales, German Market (m/f/d)",
                  "Restaurant Onboarding Associate", "AI Performance Marketing (SEM) Intern",
                  "Intern Venture Development (w/m/d)", "Strategy Associate to the CGO (Intern)",
                  "Praktikant:in (w/m/d) - für Integration und Deutschsprachlehre",
                  "Werkstudent:in Marketing & Vertrieb (m/w/d)"):
        assert is_tech(job(title, "We use Python, SQL and APIs to automate data reports.")) is False, title
    for title, desc in (("Werkstudent (m/w/d)", "Du entwickelst in Python und SQL auf PostgreSQL."),
                        ("Platform Engineer (Working Student) - m/f/d", ""),
                        ("Werkstudent Softwareentwicklung", ""),
                        ("Working Student Data Engineering", ""),
                        ("Werkstudent:in Data & Automation", "")):
        assert is_tech(job(title, desc)) is True, title


def test_full_time_role_is_full_time_despite_part_time_boilerplate():
    assert full_time_only({"title": "Full-Stack Engineer (f/m/d)",
                           "description": "Employment: FullTime · Hybrid. Part-time arrangements possible."}) is True
    assert full_time_only({"title": "Working Student Data (m/f/d)", "description": "Employment: FullTime · Hybrid"}) \
        is False
    either = {"title": "Backend Engineer", "description": "Werkstudent oder Vollzeit möglich, 20h/Woche"}
    assert full_time_only(either) is False


def test_norm_title_unescapes_entities():
    assert norm_title("Werkstudent:in Data &amp; Automation") == norm_title("Werkstudent Data Automation")


def test_kit_aliases_dedup_under_both_company_names(tmp_path):
    kits = tmp_path / "batch-1"
    kits.mkdir(parents=True)
    (kits / "Brightside.md").write_text(
        "# Brightside (Brightside Solutions GmbH) — Werkstudent:in Data & Automation\n", encoding="utf-8")
    history = HistoryIndex.load(tmp_path)
    assert history.seen({"company": "Brightside Solutions GmbH", "title": "Werkstudent:in Data &amp; Automation",
                         "url": ""}) == "kit"
    assert history.seen({"company": "Brightside", "title": "Werkstudent:in Data & Automation", "url": ""}) == "kit"
    assert history.company_seen({"company": "Brightside Solutions GmbH"}) is True


def test_requisition_ids_and_footnote_marks_do_not_defeat_dedup():
    assert norm_title("Praktikum AI Engineering (k123)") == norm_title("Praktikum AI Engineering *")
    assert norm_title("Werkstudent Data (Req. 2026-042)") == norm_title("Werkstudent Data")


def test_search_area_follows_the_profile(profile_with):
    def job(location, desc=""):
        return {"title": "Werkstudent Python", "location": location, "description": desc}
    assert in_search_area(job("Berlin, Germany"))
    assert in_search_area(job("Germany", "Remote within Germany"))
    assert in_search_area(job(""))
    assert not in_search_area(job("Hamburg"))
    assert not in_search_area(job("Paris, France"))
    profile_with({"search": {"city": "Hamburg"}})
    assert in_search_area(job("Hamburg")) and not in_search_area(job("Berlin"))
