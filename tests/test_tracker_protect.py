"""The daily run must never rewrite a sent application or a built kit: the
letter and CV in that folder are the ones that were actually sent."""
import json
from pathlib import Path

from pipeline import tracker


def _job():
    return {"company": "Acme GmbH", "title": "Werkstudent AI", "url": "https://acme.de/j/1", "description": "x"}


def _folder(tmp_path, status):
    folder = tmp_path / tracker.slugify("Acme GmbH-Werkstudent AI")
    folder.mkdir(parents=True)
    (folder / "status.json").write_text(json.dumps(status))
    (folder / "cover_letter.txt").write_text("the letter that was sent")
    return folder


def test_sent_folder_is_never_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "APPS_DIR", tmp_path)
    folder = _folder(tmp_path, {"status": "applied", "response_notes": "sent 2026-09-30 (application form)"})
    out = tracker.save_application_artifacts(_job(), "a regenerated letter", "", ["Python"])
    assert out["skipped"] is True
    assert (folder / "cover_letter.txt").read_text() == "the letter that was sent"


def test_kit_folder_is_never_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "APPS_DIR", tmp_path)
    folder = _folder(tmp_path, {"status": "pending", "kit_status": "CLEARED",
                                "response_notes": "batch-1 kit, not sent"})
    assert tracker.save_application_artifacts(_job(), "new", "", [])["skipped"] is True
    assert (folder / "cover_letter.txt").read_text() == "the letter that was sent"


def test_new_or_plain_daily_folder_is_written(tmp_path, monkeypatch):
    monkeypatch.setattr(tracker, "APPS_DIR", tmp_path)
    out = tracker.save_application_artifacts(_job(), "fresh letter", "", ["Python"])
    assert "skipped" not in out
    folder = Path(out["folder"])
    assert (folder / "cover_letter.txt").read_text() == "fresh letter"
    assert {"job.json", "jd.txt", "notes.md", "followups.json", "status.json"} <= {p.name for p in folder.iterdir()}
    assert tracker.is_protected(folder) is False
    assert json.loads((folder / "status.json").read_text())["status"] == "pending"  # generated is not sent


def test_tracker_degrades_without_supabase():
    assert tracker.already_tracked("https://acme.de/j/1") is False
    assert tracker.insert_job(_job(), score=50, cover_letter="x") == {}
    assert tracker.get_sent_applications() == []
