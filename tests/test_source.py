"""The source entry point: --show renders a saved day, a run passes its flags on."""
import json

import pytest

from pipeline import source, sourcing

_REPORT = {
    "date": "2026-10-01", "runtime_s": 1.0,
    "stats": {"raw": 3, "ready": 1, "weak": 1, "unverified": 0,
              "dropped": {"c1": 1, "degree": 0, "full_time": 0, "non_tech": 0, "location": 0, "already_applied": 0,
                          "dead": 0}},
    "sources": [{"name": "ats boards", "returned": 2, "details": {}, "blocked": None, "notes": []},
                {"name": "linkedin", "returned": 1, "details": {}, "blocked": "429 after query 2", "notes": []}],
    "boards": [],
    "ready": [{"company": "Acme Robotics", "title": "Automation Intern", "live": True,
               "employer_url": "https://jobs.ashbyhq.com/acme-robotics/x", "language": "EN", "german": "not stated",
               "hours": "full-time Praktikum", "stack": ["Python"], "projects": ["study bot"], "score": 95}],
    "weak": [{"company": "Foo", "title": "Bar", "live": None, "url": "https://x", "score": 10}],
    "dropped": [],
}


@pytest.fixture
def report_dir(tmp_path, monkeypatch):
    (tmp_path / "2026-10-01.json").write_text(json.dumps(_REPORT), encoding="utf-8")
    monkeypatch.setattr(sourcing, "OUT_DIR", tmp_path)
    return tmp_path


def test_render_ready_has_table_and_sources():
    out = source.render_ready(_REPORT)
    assert "| Acme Robotics | Automation Intern | ✅ live |" in out
    assert "full-time Praktikum" in out
    assert "| ats boards | 2 | — | — |" in out
    assert "| linkedin | 1 | 429 after query 2 | — |" in out
    assert "Foo" not in out  # WEAK rows stay in the report, not in the READY print


def test_render_ready_with_no_ready_rows():
    empty = dict(_REPORT, ready=[], stats=dict(_REPORT["stats"], ready=0))
    assert "no READY rows" in source.render_ready(empty)


def test_show_prints_a_saved_day(report_dir, capsys):
    assert source.main(["--show", "2026-10-01"]) == 0
    assert "Acme Robotics" in capsys.readouterr().out


def test_show_missing_day_says_so(report_dir, capsys):
    assert source.main(["--show", "2026-01-01"]) == 1
    assert "no sourcing run for 2026-01-01" in capsys.readouterr().out
    with pytest.raises(FileNotFoundError):
        source.load_report("2026-01-01")


def test_run_passes_its_flags_on_and_prints_ready(report_dir, monkeypatch, capsys):
    seen = {}

    async def fake_run(**kw):
        seen.update(kw)
        return report_dir / "2026-10-01.md"

    monkeypatch.setattr(sourcing, "run", fake_run)
    monkeypatch.setattr(sourcing, "bootstrap", lambda: None)
    assert source.main(["--budget", "30", "--no-indeed", "--no-aggregators"]) == 0
    assert seen == {"budget_s": 30, "max_boards": 80, "linkedin": True, "xing": True, "indeed": False,
                    "aggregators": False}
    assert "Acme Robotics" in capsys.readouterr().out
