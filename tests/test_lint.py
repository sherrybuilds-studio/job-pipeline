"""Output lint on every generated CV and cover letter: post-generation, fail and
report, no regeneration. Every rule has a test that fails on a bad input and
passes on a clean one."""
import json

from pipeline import cv_builder, lint, pdftext

CLEAN_LETTER = """Dear Acme team,

I have not worked with Kafka, which your posting names first. My German is B1, so I am writing in
English.

For my price tracker I wrote the Playwright scraper that reads 120 product pages every morning and stores the
prices in PostgreSQL. The course assistant answers 34 of 40 questions of its test set with retrieval over
ChromaDB.

Your team runs the booking platform for restaurants across Berlin, and I want to learn how such a system is
kept reliable at that scale.

I'm based in Berlin and available up to 20 hours per week.

Kind regards,
Alex Example
"""

CLEAN_CV = """Alex Example
Computer science student in Berlin. I build small Python services and data pipelines and keep them tested.
Projects
◆ Price Tracker Pipeline  120 product pages every morning
Every morning a cron job on a small Linux server scrapes 120 product pages with Playwright and stores them with SQL.
Python, Playwright, pandas, PostgreSQL, Linux · code: price-tracker
◆ Course Q&A Assistant  34 of 40 test questions correct
Retrieval (RAG) over ChromaDB behind a FastAPI service in Docker.
Experience
◆ Teaching Assistant, Introduction to Programming, Example University of Applied Sciences   Oct 2025 to present
Education
◆ B.Sc. Computer Science, Example University of Applied Sciences
CONTACT
Berlin, Germany
code.example.com/alex
TECHNICAL SKILLS
Languages and frameworks
Python
FastAPI
SQL
AI and data
RAG
ChromaDB
Tools and infrastructure
Docker
Linux
Playwright
"""


def texts(hits, rule):
    return [h.text for h in hits if h.rule == rule]


# ── rule 1: slashes ──────────────────────────────────────────────────────────

def test_slash_rule_fails_on_slashes_and_proposes_words():
    bad = "I work in Python / FastAPI and AI/ML with a CI/CD pipeline, 12/12 calls, 20h/week."
    got = {h.text: h.proposal for h in lint.lint_slashes(bad, "cover_letter.txt")}
    assert got["Python / FastAPI"] == "Python and FastAPI"
    assert got["AI/ML"] == "AI and machine learning"
    assert got["CI/CD"] == "continuous integration and delivery"
    assert got["12/12"] == "12 of 12"
    assert got["20h/week"] == "20 hours per week"
    assert lint.propose_slash("m/w/d") == "drop the gender marker"
    assert lint.propose_slash("hours/week") == "hours per week"
    assert lint.propose_slash("€400/month") == "€400 per month"
    assert lint.propose_slash("24/7").startswith("remove")


def test_slash_rule_exempts_urls_and_dates_only():
    clean = ("See https://code.example.com/alex/price-tracker and alex.example.com/work, code.example.com/alex, "
             "applied on 24/09/2026 and 09/2026, semester 2025/26. Python and FastAPI.")
    assert lint.lint_slashes(clean, "cv.pdf") == []


# ── rule 2: machine tells ────────────────────────────────────────────────────

def test_machine_tells_each_phrase_fails_and_clean_passes():
    for phrase in ("I am thrilled to apply", "I'm excited about", "I am truly passionate about", "leverage my skills",
                   "create synergies", "delve into", "in today's fast-paced world", "I thrive in a fast-paced team"):
        hits = lint.lint_machine_tells(f"Dear team,\n\nFirst sentence here. I {phrase} the role.\n", "cover_letter.txt")
        assert texts(hits, "machine-tell"), phrase
    assert lint.lint_machine_tells(CLEAN_LETTER, "cover_letter.txt") == []


def test_em_dash_chain_and_adjective_stack():
    chain = "Dear team,\n\nOne — two — three — four.\n"
    assert any("em dashes" in t for t in texts(lint.lint_machine_tells(chain, "x"), "machine-tell"))
    assert lint.lint_machine_tells("Dear team,\n\nOne — two — three.\n", "x") == []
    stack = "Dear team,\n\nI build reliable, scalable, and efficient systems.\n"
    assert texts(lint.lint_machine_tells(stack, "x"), "machine-tell") == ["reliable, scalable, and efficient"]
    assert lint.lint_machine_tells("Dear team,\n\nI use Python, FastAPI and Docker daily.\n", "x") == []


def test_identical_opening_sentence_across_letters_fails(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    for d in (a, b):
        d.mkdir()
        (d / "cover_letter.txt").write_text(CLEAN_LETTER, encoding="utf-8")
    hits = lint.lint_machine_tells(CLEAN_LETTER, "cover_letter.txt", lint.other_openings(tmp_path, exclude=a))
    assert [h for h in hits if "identical opening" in h.proposal and "b" in h.proposal]
    (b / "cover_letter.txt").write_text(CLEAN_LETTER.replace("I have not worked with Kafka", "Kafka is new to me"),
                                        encoding="utf-8")
    assert lint.lint_machine_tells(CLEAN_LETTER, "cover_letter.txt", lint.other_openings(tmp_path, exclude=a)) == []


# ── rule 3: empty buzzwords ──────────────────────────────────────────────────

def test_bare_buzzword_sentence_fails_and_anchored_sentence_passes():
    bare = "Dear team,\n\nI have strong skills in AI, automation and cloud. Python and SQL are my daily tools.\n"
    assert len(texts(lint.lint_buzzwords_prose(bare, "cover_letter.txt"), "buzzword")) == 2
    assert lint.lint_buzzwords_prose(CLEAN_LETTER, "cover_letter.txt") == []


def test_bare_list_fails_but_a_sentence_about_the_company_passes():
    assert texts(lint.lint_buzzwords_prose("Dear team,\n\nStrong skills in AI, automation and cloud.\n", "x"),
                 "buzzword")
    company = "Dear team,\n\nAcme is building the automation platform for solar installers.\n"
    assert lint.lint_buzzwords_prose(company, "x") == []
    why = "Dear team,\n\nWhat draws me to Northwind is the AI enablement team building internal tools first.\n"
    assert lint.lint_buzzwords_prose(why, "x", company="Northwind Tech GmbH") == []  # about them, not a claim
    assert lint.lint_buzzwords_prose(why, "x") != []                                # without the name it is bare
    gap = "Dear team,\n\nI have not used Make or Zapier; my automation work is in Python.\n"
    assert lint.lint_buzzwords_prose(gap, "x") == []  # an honest gap is not an empty claim
    verb = "Dear team,\n\nResults go to PostgreSQL and a Telegram message for the price tracker.\n"
    assert lint.lint_buzzwords_prose(verb, "x") == []  # "go" is a verb here, not the language


def test_not_my_skill_claimed_fails_but_a_stated_gap_passes():
    claim = "Dear team,\n\nI have shipped services on Kubernetes for the study bot.\n"
    assert [h for h in lint.lint_buzzwords_prose(claim, "x") if "not-my-skill" in h.where]
    gap = "Dear team,\n\nI have not worked with Kubernetes, which your posting names first.\n"
    assert lint.lint_buzzwords_prose(gap, "x") == []


def test_cv_skill_chip_without_an_instance_on_the_page_fails():
    assert lint.lint_cv_skills(CLEAN_CV, "cv.pdf") == []
    hits = lint.lint_cv_skills(CLEAN_CV + "Kubernetes\nTerraform\n", "cv.pdf")
    assert sorted(h.text for h in hits) == ["Kubernetes", "Terraform"]


# ── rule 4: format and the honesty list ──────────────────────────────────────

def test_format_rule_placeholders_and_honesty_list():
    bad = "Worked from [Start year] for 3 clients with 95% accuracy and 10x speed."
    got = " ".join(h.text + " " + h.proposal for h in lint.lint_format(bad, "cv.pdf"))
    assert "[Start year]" in got and "client" in got and "95" in got and "10x" in got
    assert lint.lint_format(CLEAN_LETTER, "cover_letter.txt") == []
    assert lint.lint_format(CLEAN_CV, "cv.pdf") == []


# ── the whole kit ────────────────────────────────────────────────────────────

def _kit(tmp_path, letter=CLEAN_LETTER):
    folder = tmp_path / "acme-werkstudent"
    folder.mkdir()
    (folder / "job.json").write_text(json.dumps({"company": "Acme GmbH", "title": "Werkstudent AI"}), encoding="utf-8")
    (folder / "status.json").write_text(json.dumps({"status": "pending", "response_notes": "kit, not sent"}),
                                        encoding="utf-8")
    (folder / "cover_letter.txt").write_text(letter, encoding="utf-8")
    cv_builder.build_tailored_cv({"company": "Acme GmbH", "title": "Werkstudent AI"}, ["Python"],
                                 output_path=str(folder / "cv.pdf"))
    return folder


def test_a_clean_kit_passes_on_the_real_pdf(tmp_path):
    folder = _kit(tmp_path)
    report = lint.lint_kit(folder, tmp_path)
    assert report["files"] == {"cover_letter.txt": "linted", "cv.pdf": "linted"}
    assert report["status"] == "pass", report["hits"]
    assert (folder / "lint.md").exists() and (folder / "lint.json").exists()
    status = json.loads((folder / "status.json").read_text(encoding="utf-8"))
    assert status["lint"]["status"] == "pass" and status["status"] == "pending"


def test_lint_kit_fails_a_bad_letter_and_never_edits_it(tmp_path):
    bad = CLEAN_LETTER.replace("I have not worked with Kafka", "I am thrilled to leverage Python / FastAPI")
    folder = _kit(tmp_path, bad)
    report = lint.lint_kit(folder, tmp_path)
    assert report["status"] == "fail"
    assert {h["rule"] for h in report["hits"] if h["file"] == "cover_letter.txt"} >= {"slash", "machine-tell"}
    assert all(h["proposal"] for h in report["hits"])
    assert (folder / "cover_letter.txt").read_text(encoding="utf-8") == bad  # fail and report, no regeneration
    assert json.loads((folder / "status.json").read_text(encoding="utf-8"))["status"] == "needs-human-fix"


def test_missing_files_fail_the_kit(tmp_path):
    folder = tmp_path / "empty-kit"
    folder.mkdir()
    report = lint.lint_kit(folder, tmp_path, write=False)
    assert report["files"] == {"cover_letter.txt": "missing", "cv.pdf": "missing"} and report["status"] == "fail"


def test_builtin_pdf_reader_reads_a_reportlab_cv(tmp_path):
    pdf = tmp_path / "cv.pdf"
    cv_builder.build_tailored_cv({"company": "Acme", "title": "Werkstudent"}, ["Python"], output_path=str(pdf))
    text = pdftext.builtin_extract(pdf)
    assert "Alex Example" in text and "Python" in text and "TECHNICAL SKILLS" in text
    assert lint.cv_parts(text)[1]  # skill chips were found after the sidebar heading
    assert pdftext.extract_text(pdf) == text  # the built-in reader goes first


def test_cli_lints_unsent_kits_only_and_dry_writes_nothing(tmp_path, capsys):
    folder = _kit(tmp_path)
    sent = tmp_path / "sent-kit"
    sent.mkdir()
    (sent / "job.json").write_text("{}", encoding="utf-8")
    (sent / "status.json").write_text(json.dumps({"response_notes": "sent 2026-09-30"}), encoding="utf-8")
    assert lint.main(["--apps-dir", str(tmp_path), "--dry"]) == 0
    out = capsys.readouterr().out
    assert "1 kits linted" in out and folder.name in out and "sent-kit" not in out
    assert not (folder / "lint.md").exists()
