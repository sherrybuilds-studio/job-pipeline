"""The send gate: `tracker sent` refuses to stamp unless (a) the employer's own
page is verified live, (b) no hard bar, (c) the ARCHIVED posting offers an
accepted role type (a student word in a synthetic title rescues nothing), and
(d) the verification is under 24 hours old; plus the lint passes."""
import json
from datetime import UTC, date, datetime, timedelta

from pipeline import send_gate, tracker

NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
WS_POSTING = ("Werkstudent (m/w/d) Softwareentwicklung\nAcme AG\nStandort: Berlin\n"
              "Als Werkstudent:in unterstützt du unser Team mit bis zu 20 Stunden pro Woche.\n"
              "Du studierst Informatik. Python und SQL sind dir vertraut.\n")
INTERN_POSTING = ("Praktikum AI Engineering (m/w/d)\nSechs Monate Vollzeit-Praktikum in Berlin.\n"
                  "Python, LLMs, RAG. Pflichtpraktikum möglich.\n")


def kit(tmp_path, *, title="Werkstudent (m/w/d)", posting=WS_POSTING, url="https://jobs.acme.example/job/1",
        source="xing", employer_url=None, verification=None, folder="acme-werkstudent", captured="2026-09-28"):
    d = tmp_path / folder
    d.mkdir(parents=True, exist_ok=True)
    job = {"title": title, "company": "Acme GmbH", "url": url, "source": source}
    if employer_url:
        job["employer_url"] = employer_url
    (d / "job.json").write_text(json.dumps(job), encoding="utf-8")
    status = {"status": "pending", "response_notes": "batch-1 kit, not sent"}
    if verification:
        status["verification"] = verification
    (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if posting is not None:
        header = f"<!-- source: {url} · captured: {captured} · verbatim, never edit -->"
        (d / "posting.md").write_text(f"{header}\n\n{posting}", encoding="utf-8")
    (d / "cover_letter.txt").write_text("Dear Acme team,\n\nA letter.\n\nKind regards,\nAlex Example\n",
                                        encoding="utf-8")
    return d


def live(job):
    return {**job, "live": True, "employer_url": job["url"], "live_note": "employer page open"}


def closed(job):
    return {**job, "live": False, "employer_url": job["url"], "live_note": "closed: 'nicht mehr verfügbar'"}


def aggregator(job):
    return {**job, "live": None, "employer_url": "https://www.xing.com/jobs/1", "live_note": "aggregator-only"}


def lint_pass(_folder):
    return {"status": "pass", "total": 0, "by_rule": {}}


def lint_fail(_folder):
    return {"status": "fail", "total": 2, "by_rule": {"slash": 2}}


def check(folder, **kw):
    kw.setdefault("verify", live)
    kw.setdefault("lint_fn", lint_pass)
    kw.setdefault("now", NOW)
    return send_gate.check(folder, **kw)


# ── the happy path and each refusal ──────────────────────────────────────────

def test_werkstudent_kit_with_live_employer_page_passes(tmp_path):
    result = check(kit(tmp_path))
    assert result.ok and result.reasons == []
    assert result.checks["employment_type"]["posting"] == "werkstudent"
    stored = json.loads((tmp_path / "acme-werkstudent" / "status.json").read_text())["verification"]
    assert stored["live"] is True and stored["at"] == NOW.isoformat()


def test_a_aggregator_or_closed_page_is_not_verification(tmp_path):
    r = check(kit(tmp_path), verify=aggregator)
    assert not r.ok and any("not verified live" in x for x in r.reasons)
    r = check(kit(tmp_path), verify=closed)
    assert not r.ok and any("closed" in x for x in r.reasons)
    r = check(kit(tmp_path, url="https://www.xing.com/jobs/berlin-werkstudent-1"), verify=live)
    assert not r.ok and any("aggregator URL" in x for x in r.reasons)


def test_b_hard_bar_in_the_archived_posting_refuses(tmp_path):
    r = check(kit(tmp_path, posting=WS_POSTING + "Deutschkenntnisse C1 erforderlich.\n"))
    assert not r.ok and any(x.startswith("hard bar: german-c1") for x in r.reasons)
    r = check(kit(tmp_path, posting=WS_POSTING + "Ein laufendes Masterstudium ist Voraussetzung.\n"))
    assert not r.ok and any("masters-degree" in x for x in r.reasons)


def test_c_internship_full_time_and_missing_posting_refuse(tmp_path):
    r = check(kit(tmp_path, title="Praktikum AI Engineering", posting=INTERN_POSTING))
    assert not r.ok and any("internship" in x for x in r.reasons)
    full_time = "AI Engineer (m/w/d)\nVollzeit, unbefristet. Python.\n"
    r = check(kit(tmp_path, title="AI Engineer (m/w/d)", posting=full_time))
    assert not r.ok and any("full-time" in x for x in r.reasons)
    r = check(kit(tmp_path, posting=None, folder="acme-no-archive"))
    assert not r.ok and any("no archived posting" in x for x in r.reasons)


def test_c_speculative_title_never_rescues_the_gate(tmp_path):
    # the loophole: a synthetic "Working Student … (speculative)" kit title over a careers page
    careers = "Careers\nWerkstudenten\nOpen roles\nWe hire engineers in Berlin. Full-time.\n"
    r = check(kit(tmp_path, title="Working Student AI Engineering (speculative)", posting=careers,
                  url="https://acme-ai.example/careers", folder="acme-ai-working-student-speculative"))
    assert not r.ok
    assert any("speculative kit" in x for x in r.reasons)
    assert any("not an accepted role type" in x for x in r.reasons)  # the navigation word did not count
    # a real internship posting archived under a "Working Student" kit title is still an internship
    r = check(kit(tmp_path, title="Working Student AI (m/w/d)", posting=INTERN_POSTING))
    assert not r.ok and any("internship" in x for x in r.reasons)


def test_c_accepted_types_follow_the_profile(tmp_path, profile_with):
    profile_with({"search": {"role_types": ["werkstudent", "internship"]}})
    assert check(kit(tmp_path, title="Praktikum AI Engineering", posting=INTERN_POSTING)).ok


def test_d_verification_must_be_under_24h_old(tmp_path):
    calls = []

    def counting(job):
        calls.append(job["url"])
        return live(job)

    fresh = {"at": (NOW - timedelta(hours=1)).isoformat(), "live": True, "url": "https://jobs.acme.example/job/1",
             "final_url": "https://jobs.acme.example/job/1", "note": "employer page open"}
    r = check(kit(tmp_path, verification=fresh), verify=counting)
    assert r.ok and calls == [] and r.verification["fresh"] is False and r.verification["age_h"] == 1.0

    stale = {**fresh, "at": (NOW - timedelta(hours=25)).isoformat()}
    r = check(kit(tmp_path, verification=stale, folder="acme-2"), verify=counting)
    assert r.ok and calls == ["https://jobs.acme.example/job/1"] and r.verification["fresh"] is True

    r = check(kit(tmp_path, verification=dict(stale), folder="acme-3"), verify=closed)
    assert not r.ok
    # a stored "live" record that is stale is never trusted without a fresh fetch
    assert json.loads((tmp_path / "acme-3" / "status.json").read_text())["verification"]["live"] is False


def test_lint_failure_blocks_the_gate(tmp_path):
    r = check(kit(tmp_path), lint_fn=lint_fail)
    assert not r.ok and any(x.startswith("lint fails (2 hits: slash=2)") for x in r.reasons)


def test_ats_source_is_fetched_not_trusted_by_construction(tmp_path):
    seen = []

    def probe(job):
        seen.append(job["source"])
        return live(job)

    check(kit(tmp_path, source="ats:personio", url="https://acme.jobs.personio.de/job/1"), verify=probe)
    assert seen == ["send-gate"]  # liveness.verify would have skipped the GET for "ats:*"


def test_render_names_every_reason(tmp_path):
    text = send_gate.render(check(kit(tmp_path), verify=closed, lint_fn=lint_fail))
    assert text.startswith("SEND GATE: REFUSED") and "lint fails" in text and "not verified live" in text


# ── the tracker command ──────────────────────────────────────────────────────

def test_tracker_sent_refuses_and_leaves_status_untouched(tmp_path, capsys):
    folder = kit(tmp_path, title="Praktikum AI", posting=INTERN_POSTING)
    before = (folder / "status.json").read_text()
    rc = tracker._sent("Acme", [], apps_dir=tmp_path, gate=lambda f: check(f), stamp_tracker=False)
    assert rc == 2
    assert "REFUSED" in capsys.readouterr().out
    after = json.loads((folder / "status.json").read_text())
    assert not str(after.get("response_notes", "")).startswith("sent ")
    assert json.loads(before)["response_notes"] == after["response_notes"]
    assert not (folder / "outcome.md").exists()


def test_tracker_sent_stamps_only_after_the_gate_passes(tmp_path, monkeypatch, capsys):
    folder = kit(tmp_path)
    updates = []
    monkeypatch.setattr(tracker, "find_by_company", lambda c: {"url": "https://jobs.acme.example/job/1",
                                                               "company": "Acme GmbH", "job_title": "Werkstudent"})
    monkeypatch.setattr(tracker, "update_status", lambda url, **kw: updates.append((url, kw)))
    rc = tracker._sent("Acme", ["(application form)"], apps_dir=tmp_path, gate=lambda f: check(f),
                       today=date(2026, 10, 1))
    assert rc == 0
    status = json.loads((folder / "status.json").read_text())
    assert status["response_notes"] == "sent 2026-10-01 (application form)"
    assert status["status"] == "applied" and status["stage"] == "applied" and status["sent_at"] == "2026-10-01"
    assert status["verification"]["live"] is True
    assert "| 2026-10-01 | applied | sent 2026-10-01 (application form) |" in (folder / "outcome.md").read_text()
    stamp = "sent 2026-10-01 (application form)"
    assert updates == [("https://jobs.acme.example/job/1", {"status": "applied", "stage": "applied",
                                                            "response_notes": stamp})]
    # a second stamp is refused
    assert tracker._sent("Acme", [], apps_dir=tmp_path, gate=lambda f: check(f), stamp_tracker=False) == 1
    assert "already stamped" in capsys.readouterr().out


def test_find_folder_by_company_or_path(tmp_path):
    folder = kit(tmp_path)
    assert send_gate.find_folder("acme", tmp_path) == folder
    assert send_gate.find_folder(str(folder), tmp_path) == folder
    assert send_gate.find_folder("nobody", tmp_path) is None
