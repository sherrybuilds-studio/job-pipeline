"""Follow-up drafts for applications that were really sent. Nothing is e-mailed.

Rule: a follow-up is due on day 4 after the send, rolled forward to Monday when
day 4 falls on a weekend. Only rows with a "sent YYYY-MM-DD" stamp count as
sent (tracker.get_sent_applications), and a row drops out once a follow-up is
logged or a reply stage is recorded. The daily run writes the schedule to
data/state/followups.json, where a morning brief or dashboard can read it.

    python -m pipeline followups     # drafts for today's due follow-ups → Telegram (or stdout)
"""
import json
import logging
import re
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from pipeline import config, profile
from pipeline.tracker import get_sent_applications

logger = logging.getLogger(__name__)

DUE_AFTER_DAYS = 4
STATE_PATH = config.STATE_DIR / "followups.json"
_SENT_RE = re.compile(r"\bsent (\d{4}-\d{2}-\d{2})")
_OPEN_STAGES = (None, "", "applied")  # replied, interview, rejected… need no nudge


def sent_date(row: dict) -> date | None:
    """The send date from the stamp, or None."""
    m = _SENT_RE.search(row.get("response_notes") or "")
    try:
        return date.fromisoformat(m.group(1)) if m else None
    except ValueError:
        return None


def due_date(sent: date, after_days: int = DUE_AFTER_DAYS) -> date:
    """Day `after_days` after the send; Saturday and Sunday roll to Monday."""
    d = sent + timedelta(days=after_days)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def followup_schedule(rows: list[dict], today: date | None = None) -> dict:
    """Split sent applications into {"due": [...], "upcoming": [...]}.

    A row is in play while it has a send stamp, no follow-up logged yet and no
    reply stage. Items carry company, job_title, url, sent, due and the day count."""
    today = today or date.today()
    due, upcoming = [], []
    for r in rows:
        sent = sent_date(r)
        if not sent or (r.get("followup_count") or 0) > 0 or r.get("stage") not in _OPEN_STAGES:
            continue
        when = due_date(sent)
        item = {"company": r.get("company"), "job_title": r.get("job_title"), "url": r.get("url"),
                "sent": sent.isoformat(), "due": when.isoformat(), "day": (today - sent).days}
        (due if today >= when else upcoming).append(item)
    due.sort(key=lambda i: (i["sent"], i["company"] or ""))
    upcoming.sort(key=lambda i: (i["due"], i["company"] or ""))
    return {"due": due, "upcoming": upcoming}


def write_state(path: Path | None = None, today: date | None = None, rows: list[dict] | None = None) -> dict:
    """Compute the schedule from the tracker and write it as JSON."""
    path = Path(path) if path is not None else STATE_PATH
    rows = get_sent_applications() if rows is None else rows
    schedule = followup_schedule(rows, today)
    state = {"generated_at": datetime.now(UTC).isoformat(), "today": (today or date.today()).isoformat(),
             "rule": f"day {DUE_AFTER_DAYS} after send, weekend → Monday", **schedule}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    logger.info(f"follow-up schedule: {len(schedule['due'])} due, {len(schedule['upcoming'])} upcoming → {path}")
    return state


FOLLOWUP_TEMPLATE = """Hi {contact_name},

Following up on my application for {job_title} at {company}, sent on {sent_date}.

I am still interested in the role and happy to share a work sample or talk for 15 minutes whenever it suits you.

Thanks,
{signature}
"""


def draft_followup(job: dict) -> dict:
    """{to, subject, body, job_url, company, job_title} for one sent application."""
    sent = sent_date(job)
    company = job.get("company") or "the company"
    job_title = job.get("job_title") or "the role"
    body = FOLLOWUP_TEMPLATE.format(contact_name=job.get("contact_name") or "hiring team", job_title=job_title,
                                    company=company,
                                    sent_date=f"{sent.day} {sent.strftime('%B %Y')}" if sent else "recently",
                                    signature=profile.get()["first_name"])
    return {"to": job.get("contact_email") or "", "subject": f"Following up: {job_title} application", "body": body,
            "job_url": job.get("url", ""), "company": company, "job_title": job_title}


def check_and_draft_followups(rows: list[dict] | None = None, today: date | None = None) -> list[dict]:
    """Drafts for the applications due today. Never sends an e-mail."""
    rows = get_sent_applications() if rows is None else rows
    due_urls = {i["url"] for i in followup_schedule(rows, today)["due"]}
    drafts = [draft_followup(r) for r in rows if r.get("url") in due_urls]
    if not drafts:
        logger.info("No follow-ups due today")
    return drafts


def format_drafts(drafts: list[dict]) -> str:
    lines = ["Follow-up drafts ready for your review:", ""]
    for i, d in enumerate(drafts, 1):
        lines += [f"{i}. {d['company']}: {d['job_title']}",
                  f"   To: {d['to'] or '(no e-mail on file)'}",
                  f"   Subject: {d['subject']}",
                  "", d["body"], f"   Job: {d['job_url']}", ""]
    lines.append("Not sent. Copy each one into your mail client, then log it in the tracker.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    config.setup_logging()
    drafts = check_and_draft_followups()
    if not drafts:
        print("No follow-ups due today.")
        return 0
    message = format_drafts(drafts)
    from pipeline import telegram
    if not telegram.send(message):
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())
