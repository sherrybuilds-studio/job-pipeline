"""The application tracker: a Supabase table plus one folder per application.

Supabase (table `applications`, schema in migrations/) is the queryable index;
the folder under data/applications/ holds the artefacts and is the record.
Every call degrades gracefully when Supabase is not configured: dedup assumes
a posting is new, inserts are skipped with a warning.

    python -m pipeline tracker sent <company> [note…]     # send gate first, then the "sent YYYY-MM-DD" stamp
    python -m pipeline tracker stage <company> <stage> [note…]
    python -m pipeline tracker show <company>

`sent` is the only way a send is recorded, and it is gated: send_gate.check
must pass (employer page live within 24 hours, no hard bar, accepted role type
in the archived posting, lint clean) or nothing is written.
"""
import json
import logging
import os
import re
import shutil
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from pipeline import config

logger = logging.getLogger(__name__)
TABLE = "applications"
APPS_DIR = config.APPS_DIR
STAGES = {"applied", "replied", "interview", "offer", "rejected", "withdrawn"}


def _client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set in the environment")
    from supabase import create_client
    return create_client(url, key)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def already_tracked(url: str) -> bool:
    """True when this URL already has a row. False when Supabase is unavailable
    (dedup skipped): re-processing a posting is better than skipping a new one."""
    try:
        return len(_client().table(TABLE).select("id").eq("url", url).execute().data) > 0
    except Exception as e:
        logger.warning(f"Supabase unavailable for dedup ({type(e).__name__}); treating the posting as new")
        return False


def insert_job(job: dict, score: int, cover_letter: str) -> dict:
    """Insert one scored posting with its letter. Returns the row, or {} when Supabase is down."""
    try:
        row = {"job_title": job["title"], "company": job.get("company", ""), "url": job["url"], "score": score,
               "cover_letter": cover_letter, "status": "pending", "applied_at": _now()}
        result = _client().table(TABLE).insert(row).execute()
        logger.info(f"Inserted: {job['title']} @ {job.get('company', '?')} (score {score})")
        return result.data[0] if result.data else {}
    except Exception as e:
        logger.warning(f"Could not insert into Supabase ({type(e).__name__}); continuing without storage")
        return {}


def slugify(text: str) -> str:
    """A filesystem-safe slug from a company and job name."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "unknown"


def init_job_folder(job: dict) -> Path:
    """Create the per-job folder and write job.json, jd.txt, notes.md,
    followups.json and status.json (existing files are left alone)."""
    company = job.get("company", "unknown")
    title = job.get("title", "role")
    folder = APPS_DIR / slugify(f"{company}-{title}")
    folder.mkdir(parents=True, exist_ok=True)
    files = {
        "job.json": json.dumps(job, indent=2, default=str),
        "jd.txt": f"Title: {title}\nCompany: {company}\nURL: {job.get('url', '')}\n\n{job.get('description', '')}",
        "notes.md": f"# {title} at {company}\n\n## Notes\n\n",
        "followups.json": "[]",
        "status.json": json.dumps({"status": "pending", "stage": None, "applied_at": None}, indent=2),
    }
    for name, content in files.items():
        if not (folder / name).exists():
            (folder / name).write_text(content, encoding="utf-8")
    logger.info(f"Initialised job folder: {folder}")
    return folder


def is_protected(folder: Path) -> bool:
    """A folder that holds a sent application or a built kit is a record: the
    daily run must never rewrite the letter and CV that were actually sent."""
    status_file = Path(folder) / "status.json"
    if not status_file.exists():
        return False
    try:
        st = json.loads(status_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return True  # unreadable record: leave it alone
    notes = str(st.get("response_notes") or "")
    return (notes.startswith("sent ") or bool(st.get("kit_status")) or bool(st.get("sent_at"))
            or "kit" in notes or (Path(folder) / "posting.md").exists())


def save_application_artifacts(job: dict, cover_letter: str, cv_path: str, keywords: list[str],
                               job_url: str | None = None) -> dict:
    """Save the letter and CV into the job folder and update the tracker row.
    Never touches a protected folder (a sent application or a built kit)."""
    existing = APPS_DIR / slugify(f"{job.get('company', 'unknown')}-{job.get('title', 'role')}")
    if existing.exists() and is_protected(existing):
        logger.warning(f"{existing.name}: sent or kitted; the daily run leaves the record untouched")
        return {"folder": str(existing), "cv_path": str(existing / "cv.pdf"), "skipped": True}
    folder = init_job_folder(job)
    (folder / "cover_letter.txt").write_text(cover_letter, encoding="utf-8")
    if cv_path and os.path.exists(cv_path):
        dest_cv = folder / "cv.pdf"
        if os.path.abspath(cv_path) != str(dest_cv):
            shutil.copy2(cv_path, dest_cv)
        cv_path = str(dest_cv)
    status_file = folder / "status.json"
    status = json.loads(status_file.read_text(encoding="utf-8"))
    status.update(status="pending", cv_path=str(cv_path), cv_keywords=keywords)
    status_file.write_text(json.dumps(status, indent=2), encoding="utf-8")
    try:
        _client().table(TABLE).update({"cv_path": str(cv_path), "cv_keywords": keywords, "job_folder": str(folder)}) \
            .eq("url", job_url or job.get("url", "")).execute()
    except Exception as e:
        logger.warning(f"Could not update Supabase: {type(e).__name__}")
    return {"folder": str(folder), "cv_path": str(cv_path)}


def update_status(url: str, status: str | None = None, stage: str | None = None, response_type: str | None = None,
                  response_notes: str | None = None) -> None:
    """Update a tracker row and, when it has one, its folder's status.json."""
    updates: dict = {}
    if status:
        updates["status"] = status
    if stage:
        updates["stage"] = stage
    if response_type:
        updates["response_type"] = response_type
        updates["response_at"] = _now()
    if response_notes:
        updates["response_notes"] = response_notes
    if not updates:
        return
    try:
        client = _client()
        client.table(TABLE).update(updates).eq("url", url).execute()
        logger.info(f"Updated status for {url}: {updates}")
        result = client.table(TABLE).select("job_folder").eq("url", url).execute()
    except Exception as e:
        logger.warning(f"Could not update Supabase: {type(e).__name__}")
        return
    if result.data and result.data[0].get("job_folder"):
        status_file = Path(result.data[0]["job_folder"]) / "status.json"
        if status_file.exists():
            data = json.loads(status_file.read_text(encoding="utf-8"))
            data.update(updates)
            status_file.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_sent_applications() -> list[dict]:
    """Rows a person actually sent: response_notes carries a "sent YYYY-MM-DD"
    stamp. applied_at is generation time and is no evidence of a send."""
    try:
        return _client().table(TABLE).select("*").ilike("response_notes", "sent %").execute().data
    except Exception as e:
        logger.warning(f"Could not query sent applications: {type(e).__name__}")
        return []


def mark_followup_sent(url: str) -> None:
    """Increment followup_count and set last_followup_at."""
    try:
        client = _client()
        result = client.table(TABLE).select("followup_count").eq("url", url).execute()
        current = (result.data[0].get("followup_count") or 0) if result.data else 0
        client.table(TABLE).update({"followup_count": current + 1, "last_followup_at": _now()}).eq("url", url).execute()
        logger.info(f"Marked follow-up sent for {url}")
    except Exception as e:
        logger.warning(f"Could not mark the follow-up: {type(e).__name__}")


def find_by_company(company: str) -> dict | None:
    """Latest tracker row matching a (partial) company name."""
    result = (_client().table(TABLE).select("url,company,job_title,status,stage")
              .ilike("company", f"%{company}%").order("applied_at", desc=True).limit(1).execute())
    return result.data[0] if result.data else None


def _sent(company: str, note_words: list[str], *, apps_dir: Path | None = None, gate=None, stamp_tracker: bool = True,
          today: date | None = None) -> int:
    from pipeline import archive, send_gate

    apps_dir = Path(apps_dir) if apps_dir is not None else APPS_DIR
    folder = send_gate.find_folder(company, apps_dir)
    if folder is None:
        print(f"no single kit folder under {apps_dir} matches {company!r}; nothing stamped")
        return 1
    status_path = folder / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    if str(status.get("response_notes") or "").startswith("sent "):
        print(f"{folder.name}: already stamped: {status['response_notes'][:70]}")
        return 1
    result = (gate or send_gate.check)(folder)
    print(send_gate.render(result))
    if not result.ok:
        return 2
    today = today or date.today()
    stamp = f"sent {today.isoformat()}" + (" " + " ".join(note_words) if note_words else "")
    # re-read: the gate may have written a fresh verification record meanwhile
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    status.update(status="applied", stage="applied", response_notes=stamp, sent_at=today.isoformat())
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    archive.record_outcome(folder, "applied", stamp, on=today.isoformat())
    if stamp_tracker:
        try:
            row = find_by_company(company)
            if row:
                update_status(row["url"], status="applied", stage="applied", response_notes=stamp)
                print(f"tracker row updated: {row['company']}, {row['job_title']}")
            else:
                print("no tracker row for this company; the kit folder is the record")
        except Exception as e:  # Supabase down or unset must not undo a real send
            print(f"tracker row not updated ({type(e).__name__}); the kit folder is the record")
    print(f"{folder.name}: {stamp}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    config.load_env()
    if len(argv) < 2 or argv[0] not in ("sent", "stage", "show"):
        print(__doc__)
        return 1
    cmd, company = argv[0], argv[1]
    if cmd == "sent":
        return _sent(company, argv[2:])
    try:
        row = find_by_company(company)
    except ValueError as e:
        print(e)
        return 1
    if row is None:
        print(f"no application matches company ~ {company!r}")
        return 1
    if cmd == "show":
        print(row)
        return 0
    if len(argv) < 3 or argv[2] not in STAGES:
        print(f"stage must be one of {sorted(STAGES)}")
        return 1
    update_status(row["url"], stage=argv[2], response_type=argv[2] if argv[2] != "applied" else None,
                  response_notes=" ".join(argv[3:]) or None)
    print(f"{row['company']}, {row['job_title']}: stage {argv[2]} recorded")
    return 0


if __name__ == "__main__":
    sys.exit(main())
