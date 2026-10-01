"""Keep what was applied to, and what came of it.

Scraped descriptions are cut short and postings vanish, so every application
folder holds two plain files:

  posting.md   the verbatim posting plus where and when it was captured. Written
               once; a later capture (an edited or closed page) never overwrites it.
  outcome.md   one dated line per stage (applied, replied, interview, offer,
               rejected, withdrawn, no_response) with whatever feedback came.

    python -m pipeline archive posting <folder> [...]     # fetch job.json's URL, store posting.md
    python -m pipeline archive outcome <company> <stage> [note…]
    python -m pipeline archive funnel                     # latest stage per source

Read-only GETs; nothing is sent anywhere. Interview preparation and score
calibration read these files later.
"""
import html
import json
import re
import sys
from datetime import date
from pathlib import Path

from pipeline import config

APPS_DIR = config.APPS_DIR
STAGES = ("applied", "replied", "interview", "offer", "rejected", "withdrawn", "no_response")

_DROP = re.compile(r"<(script|style|noscript|svg|head)\b.*?</\1>", re.S | re.I)
_BLOCK = re.compile(r"</(?:p|li|div|h\d|tr|section|ul|ol)>|<br\s*/?>", re.I)
_TAG = re.compile(r"<[^>]+>")
_CLOSED = ("no longer active", "no longer available", "no longer accepting", "position has been filled",
           "has expired", "nicht mehr verfügbar", "nicht mehr aktiv", "bereits besetzt", "abgelaufen",
           "has been filled")


def html_to_text(raw: str) -> str:
    s = _DROP.sub(" ", raw or "")
    s = _BLOCK.sub("\n", s)
    s = html.unescape(_TAG.sub(" ", s))
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    return "\n".join(line.strip() for line in s.split("\n") if line.strip())


def looks_closed(text: str) -> bool:
    """A page can answer 200 and still be a tombstone; never archive that as the posting."""
    low = (text or "").lower()
    return any(m in low for m in _CLOSED)


def archive_posting(folder: Path, text: str, *, url: str, fetched: str | None = None) -> Path:
    path = Path(folder) / "posting.md"
    if path.exists():
        return path
    path.write_text(f"<!-- source: {url} · captured: {fetched or date.today().isoformat()} · verbatim, never edit -->"
                    f"\n\n{text.strip()}\n", encoding="utf-8")
    return path


def record_outcome(folder: Path, stage: str, note: str = "", *, on: str | None = None) -> Path:
    if stage not in STAGES:
        raise ValueError(f"stage must be one of {STAGES}")
    path = Path(folder) / "outcome.md"
    if not path.exists():
        path.write_text("# Outcome\n\n| date | stage | note |\n|---|---|---|\n", encoding="utf-8")
    clean = (note or "").replace("|", ",").replace("\n", " ").strip()
    with path.open("a", encoding="utf-8") as f:
        f.write(f"| {on or date.today().isoformat()} | {stage} | {clean} |\n")
    return path


def latest_stage(folder: Path) -> str | None:
    path = Path(folder) / "outcome.md"
    if not path.exists():
        return None
    rows = re.findall(r"^\| \d{4}-\d{2}-\d{2} \| (\w+) \|", path.read_text(encoding="utf-8"), re.M)
    return rows[-1] if rows else None


def funnel(apps_dir: Path | None = None) -> dict[str, dict[str, int]]:
    apps_dir = Path(apps_dir) if apps_dir is not None else APPS_DIR
    out: dict[str, dict[str, int]] = {}
    for folder in sorted(apps_dir.iterdir()) if apps_dir.exists() else []:
        stage = latest_stage(folder) if folder.is_dir() else None
        if not stage:
            continue
        try:
            source = json.loads((folder / "job.json").read_text(encoding="utf-8")).get("source") or "unknown"
        except (OSError, json.JSONDecodeError):
            source = "unknown"
        out.setdefault(source, {})
        out[source][stage] = out[source].get(stage, 0) + 1
    return out


def find_folder(company: str, apps_dir: Path | None = None) -> Path:
    apps_dir = Path(apps_dir) if apps_dir is not None else APPS_DIR
    hits = []
    for folder in sorted(apps_dir.iterdir()) if apps_dir.exists() else []:
        job = folder / "job.json"
        if job.exists() and company.lower() in json.loads(job.read_text(encoding="utf-8")).get("company", "").lower():
            hits.append(folder)
    if len(hits) != 1:
        raise SystemExit(f"{len(hits)} folders match {company!r}: {[h.name for h in hits]}")
    return hits[0]


def fetch(url: str) -> str:
    """The posting page as plain text (one read-only GET)."""
    import httpx
    r = httpx.get(url, timeout=25, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/128 Safari/537.36"})
    r.raise_for_status()
    return html_to_text(r.text)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 1
    cmd, rest = argv[0], argv[1:]
    if cmd == "posting":
        for arg in rest:
            folder = Path(arg)
            job = json.loads((folder / "job.json").read_text(encoding="utf-8"))
            url = job.get("employer_url") or job.get("url", "")
            jd = folder / "jd.txt"
            try:
                text, origin = fetch(url), url
            except Exception as e:  # closed or blocked page: keep what we have
                text = jd.read_text(encoding="utf-8") if jd.exists() else ""
                origin = f"{url} (live fetch failed: {type(e).__name__}; text from jd.txt)"
            if looks_closed(text) and jd.exists():
                text = jd.read_text(encoding="utf-8")
                origin = f"{url} (⚠ LISTING CLOSED when captured; text from jd.txt)"
            if len(text) < 400 and jd.exists():
                text, origin = jd.read_text(encoding="utf-8"), f"{url} (page empty; text from jd.txt)"
            print(archive_posting(folder, text, url=origin), f"({len(text)} chars)")
    elif cmd == "outcome" and len(rest) >= 2:
        print(record_outcome(find_folder(rest[0]), rest[1], " ".join(rest[2:])))
    elif cmd == "funnel":
        for source, stages in funnel().items():
            print(f"{source:22s}", "  ".join(f"{k}={v}" for k, v in stages.items()))
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
