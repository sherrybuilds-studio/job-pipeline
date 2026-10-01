"""The posting archive and the outcome record. Postings close and scraped
descriptions are cut short, so the verbatim posting is kept once, and every
stage of what came of an application is appended with its date."""
import json

import pytest

from pipeline import archive


def _folder(tmp_path, name="acme-ws", **job):
    d = tmp_path / name
    d.mkdir()
    (d / "job.json").write_text(json.dumps({"title": "Working Student AI", "company": "Acme", "url": "https://acme.de/j/1",
                                            "source": "ats:personio", **job}), encoding="utf-8")
    return d


def test_posting_is_archived_verbatim_with_provenance(tmp_path):
    text = "Anforderungen\n" + "Python, Docker, RAG. " * 400  # far past an 800-character scrape
    path = archive.archive_posting(_folder(tmp_path), text, url="https://acme.de/j/1", fetched="2026-09-28")
    body = path.read_text(encoding="utf-8")
    assert path.name == "posting.md"
    assert "https://acme.de/j/1" in body and "2026-09-28" in body
    assert text.strip() in body  # never truncated


def test_archive_never_overwrites_an_earlier_capture(tmp_path):
    d = _folder(tmp_path)
    archive.archive_posting(d, "first capture", url="u", fetched="2026-09-28")
    archive.archive_posting(d, "later capture (posting edited or closed page)", url="u", fetched="2026-10-01")
    assert "first capture" in (d / "posting.md").read_text(encoding="utf-8")


def test_html_is_reduced_to_text_but_keeps_the_requirement_lines():
    html = "<html><script>x()</script><h2>Profil</h2><ul><li>Python &amp; Docker</li><li>Deutsch B2</li></ul></html>"
    text = archive.html_to_text(html)
    assert "x()" not in text and "<" not in text
    assert "Python & Docker" in text and "Deutsch B2" in text and text.index("Python") < text.index("Deutsch")


def test_outcome_record_appends_stages_in_order(tmp_path):
    d = _folder(tmp_path)
    archive.record_outcome(d, "applied", "e-mail to the hiring team", on="2026-09-28")
    archive.record_outcome(d, "interview", "30-minute technical call | asked about the test set", on="2026-10-01")
    text = (d / "outcome.md").read_text(encoding="utf-8")
    assert text.index("applied") < text.index("interview")
    assert "asked about the test set" in text and "call , asked" in text  # a pipe cannot break the table
    assert archive.latest_stage(d) == "interview"


def test_outcome_rejects_unknown_stages(tmp_path):
    with pytest.raises(ValueError):
        archive.record_outcome(_folder(tmp_path), "ghosted", "", on="2026-10-01")


def test_funnel_counts_latest_stage_per_source(tmp_path):
    a = _folder(tmp_path, "a", source="ats:ashby")
    b = _folder(tmp_path, "b", source="ats:ashby")
    c = _folder(tmp_path, "c", source="linkedin")
    _folder(tmp_path, "never-sent", source="adzuna")  # no outcome.md, so not in the funnel
    archive.record_outcome(a, "applied", "", on="2026-09-21")
    archive.record_outcome(a, "interview", "", on="2026-09-28")
    archive.record_outcome(b, "applied", "", on="2026-09-21")
    archive.record_outcome(c, "rejected", "", on="2026-09-24")
    assert archive.funnel(tmp_path) == {"ats:ashby": {"applied": 1, "interview": 1}, "linkedin": {"rejected": 1}}


def test_a_closed_page_is_never_archived_as_the_posting():
    # a page can answer 200 and still be a tombstone with a cookie banner and navigation
    assert archive.looks_closed("Cookies … This position is no longer active … Career site")
    assert archive.looks_closed("Diese Stelle ist nicht mehr verfügbar")
    assert not archive.looks_closed("Working Student AI. You build RAG pipelines. Apply now.")


def test_cli_outcome_finds_the_folder_by_company(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(archive, "APPS_DIR", tmp_path)
    d = _folder(tmp_path)
    assert archive.main(["outcome", "acme", "replied", "asked", "for", "a", "call"]) == 0
    assert archive.latest_stage(d) == "replied"
    assert archive.main([]) == 1
