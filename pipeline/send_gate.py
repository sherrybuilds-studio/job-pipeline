"""The conditions for recording a kit as sent.

`python -m pipeline tracker sent <company>` refuses to write the
"sent YYYY-MM-DD" stamp unless every check here passes. Nothing is sent by
this code; it decides whether a send may be RECORDED, which is what the
follow-up schedule, the funnel and the history index all key off.

  (a) liveness: liveness.verify against the employer's own page, fetched now
      (an "ats:" source is not trusted by construction here). A URL that lands
      on LinkedIn, XING or any other aggregator is not verification, nor is a
      closed page.
  (b) hard bars: matcher.hard_bar_reason over the ARCHIVED posting text (plus
      the kit title) must be empty.
  (c) employment type: roletype.classify over the ARCHIVED posting (posting.md,
      the verbatim capture) must be a type the profile accepts. The kit title
      is never trusted for this: a student word in a synthetic "speculative"
      title cannot rescue the gate, and a kit whose title, source or folder
      says "speculative", or that has no posting.md, is refused.
  (d) freshness: the verification must be less than 24 hours old at send time.
      A stored record younger than that (status.json["verification"]) is
      accepted; otherwise the page is fetched again and the record replaced.
  (+) lint: lint.lint_kit must pass; a kit marked needs-human-fix cannot be stamped.

There is no override flag.

    python -m pipeline send-gate <company | folder>     # dry check, prints the verdict
"""
import asyncio
import json
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from pipeline import config, liveness, matcher, roletype

APPS_DIR = config.APPS_DIR
MAX_AGE_H = 24
_HEADER = re.compile(r"^<!--\s*source:\s*(?P<source>.*?)\s*·\s*captured:\s*(?P<captured>\S+)(?P<rest>.*?)-->\s*", re.S)
_SPECULATIVE = re.compile(r"speculativ", re.I)


@dataclass
class GateResult:
    ok: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict = field(default_factory=dict)
    verification: dict | None = None


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def archived_posting(folder: Path) -> tuple[str, dict] | None:
    """(verbatim text, header meta) from posting.md, or None when there is none."""
    path = Path(folder) / "posting.md"
    if not path.exists():
        return None
    raw = path.read_text(encoding="utf-8", errors="replace")
    m = _HEADER.match(raw)
    meta = {"source": m.group("source"), "captured": m.group("captured"), "note": m.group("rest").strip()} if m else {}
    return (raw[m.end():] if m else raw), meta


def default_verify(job: dict) -> dict:
    """One read-only GET through liveness.verify, on a fresh client."""
    import httpx

    async def go():
        async with httpx.AsyncClient() as client:
            return await liveness.verify(client, job)

    return asyncio.run(go())


def _age_hours(stamp: str, now: datetime) -> float | None:
    try:
        at = datetime.fromisoformat(stamp)
    except (TypeError, ValueError):
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return (now - at).total_seconds() / 3600


def verify_now(folder: Path, job: dict, *, verify=default_verify, now: datetime | None = None,
               max_age_h: float = MAX_AGE_H, reverify: bool = False) -> dict:
    """The verification record to judge by: the stored one when it is live and
    younger than max_age_h, otherwise a fresh fetch written to status.json."""
    now = now or datetime.now(UTC)
    status_path = Path(folder) / "status.json"
    status = _read_json(status_path)
    stored = status.get("verification") or {}
    age = _age_hours(stored.get("at", ""), now) if stored else None
    if not reverify and stored.get("live") is True and age is not None and 0 <= age < max_age_h:
        return {**stored, "age_h": round(age, 2), "fresh": False}
    url = job.get("employer_url") or job.get("url") or ""
    probe = {"url": url, "source": "send-gate", "company": job.get("company"), "title": job.get("title")}
    result = verify(probe)
    record = {"at": now.replace(microsecond=0).isoformat(), "url": url, "final_url": result.get("employer_url", url),
              "live": result.get("live"), "note": result.get("live_note", "")}
    status["verification"] = record
    status_path.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
    return {**record, "age_h": 0.0, "fresh": True}


def check(folder: Path, *, verify=default_verify, lint_fn=None, now: datetime | None = None,
          max_age_h: float = MAX_AGE_H, reverify: bool = False, apps_dir: Path | None = None) -> GateResult:
    folder = Path(folder)
    now = now or datetime.now(UTC)
    reasons: list[str] = []
    checks: dict = {}
    job = _read_json(folder / "job.json")
    if not job:
        return GateResult(False, ["no job.json in the kit folder"], {})
    title = str(job.get("title") or "")

    # (c) the kit itself must not be speculative, and the posting must be archived
    spec_marks = [k for k, v in (("title", title), ("source", str(job.get("source") or "")), ("folder", folder.name))
                  if _SPECULATIVE.search(v)]
    if spec_marks:
        reasons.append(f"speculative kit ({', '.join(spec_marks)} says so): no open role to send to")
    archived = archived_posting(folder)
    if archived is None:
        reasons.append("no archived posting (posting.md); run: python -m pipeline archive posting <folder>")
        posting_text, meta = "", {}
    else:
        posting_text, meta = archived
        if "LISTING CLOSED" in meta.get("note", ""):
            reasons.append("posting.md was captured from a CLOSED listing")
    if posting_text:
        kind, evidence = roletype.classify_with_evidence(posting_text)
        title_kind = roletype.classify("", title=title) if title else roletype.UNSPECIFIED
        checks["employment_type"] = {"posting": kind, "title": title_kind, "evidence": evidence}
        if not roletype.is_accepted(kind):
            reasons.append(f"archived posting is not an accepted role type: {roletype.drop_reason(kind)}"
                           + (f" ({evidence!r})" if evidence else ""))
        elif title_kind != roletype.UNSPECIFIED and not roletype.is_accepted(title_kind):
            reasons.append(f"kit title says {roletype.drop_reason(title_kind)}; the posting does not override it")

        # (b) hard bars on the archived text
        bar = matcher.hard_bar_reason({"title": title, "description": posting_text})
        checks["hard_bar"] = bar
        if bar:
            reasons.append(f"hard bar: {bar}")

    # (a) and (d): a live employer page, verified within max_age_h
    url = job.get("employer_url") or job.get("url") or ""
    verification = None
    if not url:
        reasons.append("no URL to verify")
        checks["verification"] = None
    else:
        if liveness.is_aggregator(url) and not job.get("employer_url"):
            reasons.append(f"only an aggregator URL is known ({url}); XING or LinkedIn is not verification")
        verification = verify_now(folder, job, verify=verify, now=now, max_age_h=max_age_h, reverify=reverify)
        checks["verification"] = verification
        if verification.get("live") is not True:
            reasons.append(f"employer page not verified live: {verification.get('note') or verification.get('live')!s}"
                           f" ({verification.get('final_url') or url})")
        elif liveness.is_aggregator(verification.get("final_url") or ""):
            reasons.append("verification landed on an aggregator page, not the employer")
        age = verification.get("age_h")
        if age is None or age >= max_age_h:
            reasons.append(f"verification is {age} h old (limit {max_age_h} h)")

    # (+) lint
    lint_report = (lint_fn or _default_lint(apps_dir))(folder)
    checks["lint"] = {"status": lint_report.get("status"), "total": lint_report.get("total"),
                      "by_rule": lint_report.get("by_rule")}
    if lint_report.get("status") != "pass":
        rules = ", ".join(f"{r}={n}" for r, n in (lint_report.get("by_rule") or {}).items() if n)
        reasons.append(f"lint fails ({lint_report.get('total')} hits: {rules}); see lint.md, fix, then re-run")

    return GateResult(not reasons, reasons, checks, verification)


def _default_lint(apps_dir: Path | None):
    def run(folder: Path) -> dict:
        from pipeline import lint
        return lint.lint_kit(folder, apps_dir)
    return run


def find_folder(company: str, apps_dir: Path | None = None) -> Path | None:
    """The one kit folder whose company (job.json) contains the name; a path is taken as is."""
    apps_dir = Path(apps_dir) if apps_dir is not None else APPS_DIR
    direct = Path(company)
    if direct.is_dir() and (direct / "job.json").exists():
        return direct
    needle = company.lower().strip()
    hits = []
    for folder in sorted(apps_dir.iterdir()) if apps_dir.exists() else []:
        job = _read_json(folder / "job.json")
        if job and needle in str(job.get("company", "")).lower():
            hits.append(folder)
    if len(hits) > 1:
        exact = [h for h in hits
                 if _read_json(h / "job.json").get("company", "").lower().split("(")[0].strip() == needle]
        hits = exact or hits
    return hits[0] if len(hits) == 1 else None


def render(result: GateResult) -> str:
    head = "SEND GATE: PASS, may be stamped sent" if result.ok else "SEND GATE: REFUSED, not stamped"
    lines = [head] + [f"  ✗ {r}" for r in result.reasons]
    v = result.verification or {}
    if v:
        age = "fresh" if v.get("fresh") else f"{v.get('age_h')} h old"
        lines.append(f"  verification: live={v.get('live')} {v.get('note', '')} at {v.get('at')} ({age})")
    et = result.checks.get("employment_type")
    if et:
        lines.append(f"  employment type: posting={et['posting']} title={et['title']}")
    lint_c = result.checks.get("lint")
    if lint_c:
        lines.append(f"  lint: {lint_c['status']} ({lint_c['total']} hits)")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print(__doc__)
        return 1
    folder = find_folder(argv[0])
    if folder is None:
        print(f"no single kit folder matches {argv[0]!r}")
        return 1
    result = check(folder)
    print(render(result))
    return 0 if result.ok else 2


if __name__ == "__main__":
    sys.exit(main())
