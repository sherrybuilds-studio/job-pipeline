"""The follow-up schedule: day 4 after a real send, weekends roll to Monday, and
only "sent YYYY-MM-DD" stamps count as sends. Drafts are never e-mailed."""
from datetime import date

from pipeline import followup
from pipeline.followup import due_date, followup_schedule, sent_date, write_state


def _row(company, notes, stage="applied", followups=0):
    return {"company": company, "job_title": "Werkstudent", "url": f"https://x.example/{company}",
            "response_notes": notes, "stage": stage, "followup_count": followups}


def test_sent_date_comes_from_the_stamp_only():
    assert sent_date(_row("a", "sent 2026-09-30 (application form)")) == date(2026, 9, 30)
    assert sent_date(_row("a", "")) is None
    assert sent_date({"response_notes": None}) is None
    assert sent_date(_row("a", "parked 2026-09-30: not sent")) is None


def test_day_four_rolls_over_the_weekend_to_monday():
    assert due_date(date(2026, 9, 30)) == date(2026, 10, 5)  # Wednesday send: day 4 is a Sunday, so Monday
    assert due_date(date(2026, 9, 28)) == date(2026, 10, 2)  # Monday send: Friday, no roll
    assert due_date(date(2026, 9, 29)) == date(2026, 10, 5)  # Tuesday send: Saturday, so Monday


def test_schedule_splits_due_and_upcoming_and_skips_handled_rows():
    rows = [
        _row("Northwind Bank", "sent 2026-09-30"),
        _row("Acme Robotics", "sent 2026-09-30 (confirmation received)"),
        _row("Old pipeline row", None),                                  # generated, never sent
        _row("Replied Co", "sent 2026-09-30", stage="replied"),          # already answered
        _row("Nudged Co", "sent 2026-09-30", followups=1),               # follow-up already logged
        _row("Parked Co", "parked 2026-09-30: not sent"),
    ]
    before = followup_schedule(rows, today=date(2026, 10, 4))
    assert before["due"] == [] and [i["company"] for i in before["upcoming"]] == ["Acme Robotics", "Northwind Bank"]
    assert before["upcoming"][0]["due"] == "2026-10-05"
    on_day = followup_schedule(rows, today=date(2026, 10, 5))
    assert [i["company"] for i in on_day["due"]] == ["Acme Robotics", "Northwind Bank"]
    assert on_day["due"][0]["day"] == 5 and on_day["upcoming"] == []


def test_write_state_persists_the_schedule(tmp_path):
    out = tmp_path / "followups.json"
    st = write_state(out, today=date(2026, 10, 5), rows=[_row("Northwind Bank", "sent 2026-09-30")])
    assert out.exists() and st["due"][0]["company"] == "Northwind Bank" and "generated_at" in st
    assert "day 4" in st["rule"]


def test_drafts_are_signed_from_the_profile_and_never_sent():
    rows = [{**_row("Northwind Bank", "sent 2026-09-30"), "contact_name": "Ms Example",
             "contact_email": "jobs@x.example"}]
    drafts = followup.check_and_draft_followups(rows, today=date(2026, 10, 5))
    assert len(drafts) == 1
    d = drafts[0]
    assert d["to"] == "jobs@x.example" and d["body"].startswith("Hi Ms Example,")
    assert "30 September 2026" in d["body"] and d["body"].rstrip().endswith("Thanks,\nAlex")
    text = followup.format_drafts(drafts)
    assert "Not sent." in text and "Northwind Bank" in text


def test_no_contact_falls_back_to_the_hiring_team():
    d = followup.draft_followup(_row("Acme Robotics", "sent 2026-09-30"))
    assert d["body"].startswith("Hi hiring team,") and d["to"] == ""
