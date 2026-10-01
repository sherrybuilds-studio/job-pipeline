"""READY sourcing rows → reviewer-cleared application kits. Builds, never sends.

    python -m pipeline kit plan  data/sourcing/2026-10-01.json --limit 10
    python -m pipeline kit build data/sourcing/2026-10-01.json --limit 10 --batch batch-1

Per picked role: a folder under data/applications/ with job.json, jd.txt, the
verbatim posting.md and status.json; a tailored one-page CV; a letter written
through draft → reviewer → revise (at most MAX_REVIEWS reviews); review.json
and review.md; and a kit note in data/applications/<batch>/.

A kit is CLEARED only when the reviewer finds ZERO unsupported claims, the
regex gate is clean, the letter has no slash and stays under MAX_WORDS words.
Anything else is BLOCKED: the draft and the review are kept for a person, but
no letter PDF is rendered, so an unsupported claim never ships. The output
lint then runs over the letter and the CV on disk; a hit marks the kit
NEEDS-HUMAN-FIX.

One role per company; a company already in the tracker, a kit folder or a
_parked folder is never picked again. A listing that reads closed at build
time is skipped.
"""
import argparse
import json
import logging
import re
import shutil
import sys
from datetime import date
from pathlib import Path

from pipeline import archive, config, profile, reviewer
from pipeline.honesty import gate
from pipeline.sourcing import HistoryIndex, norm_company

logger = logging.getLogger("kit")
APPS_DIR = config.APPS_DIR
MAX_WORDS = 249
MAX_REVIEWS = 3
MAX_CV_KEYWORDS = 7

KIT_RULES = """KIT RULES (these override the length and format rules above):
- Write the letter BODY only, in English, 150 to 185 words, as 3 short paragraphs. No greeting, no sign-off,
  no availability sentence: those are added for you.
- Sentence one names, plainly, the most important requirement of this posting that the candidate does NOT
  meet (a tool, a language level, a degree stage, the hours). If the posting asks for a language level the
  profile does not have, that is the gap to name.
- Then the one or two projects that match this posting's own words, with at most two of the verified numbers.
  Use ONLY facts from the profile. Do not describe behaviours, architectures, dates or habits the profile does
  not state. Do not claim soft traits.
- One sentence on why this company, taken from the posting.
- Never use the slash character. Write "12 of 12", "Python and FastAPI", "continuous delivery". Plain, warm,
  first person; no taglines."""


def cv_keywords(stack: list[str]) -> list[str]:
    """Named-stack labels from a posting → the profile's own skill names, via
    skill_aliases or an exact name match. Skills the profile lacks are dropped."""
    p = profile.get()
    skills = p["core_skills"]
    by_lower = {name.lower(): name for name in skills}
    out: list[str] = []
    for label in stack or []:
        skill = p["skill_aliases"].get(label) or by_lower.get(label.lower())
        if skill in skills and skill not in out:
            out.append(skill)
    return out[:MAX_CV_KEYWORDS]


def pick_candidates(ready: list[dict], history: HistoryIndex, limit: int) -> list[dict]:
    rows = [r for r in ready if r.get("live") is True and r.get("projects")
            and not history.seen(r) and not history.company_seen(r)]
    rows.sort(key=lambda r: (-r.get("score", 0), r.get("hours") == "full-time Praktikum"))
    picked, seen = [], set()
    for r in rows:
        key = norm_company(r.get("company", ""))
        if key and key not in seen:
            seen.add(key)
            picked.append(r)
    return picked[:limit]


def _closing_marker() -> str:
    return f"based in {profile.city()}"


def assemble(body: str, contact: str = "", company: str = "") -> str:
    """Greeting, the model's body, the profile's closing paragraph (once) and sign-off."""
    letters = profile.get()["letters"]
    body = re.sub(r"^\s*dear[^\n]*\n+", "", (body or "").strip(), flags=re.I)
    body = re.sub(r"\n+\s*(kind regards|best regards|sincerely)[\s\S]*$", "", body, flags=re.I).strip()
    greeting = f"Dear {contact}," if contact else f"Dear {company} team," if company else "Dear hiring team,"
    closing = "" if _closing_marker() in body else f"\n\n{letters['closing']}"
    return f"{greeting}\n\n{body}{closing}\n\n{letters['sign_off']}\n"


def clearance(letter: str, review: dict) -> tuple[str, list[str]]:
    why = [f"unsupported claim: {c.get('claim', '')[:90]}" for c in review.get("ungrounded_claims", [])]
    why += [f"regex gate: {h[:90]}" for h in (review.get("gate") or gate(letter))]
    if "/" in letter:
        why.append("slash in the letter")
    words = len(letter.split())
    if words > MAX_WORDS:
        why.append(f"{words} words (limit {MAX_WORDS})")
    return ("CLEARED" if not why else "BLOCKED"), why


def _slug(row: dict) -> str:
    return re.sub(r"[^a-z0-9]+", "-", f"{row.get('company', '')}-{row.get('title', '')}".lower()).strip("-")[:60]


def _token(company: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", company.split("(")[0])[:24] or "Company"


def _chat(messages, agent: str) -> str:
    from pipeline.llm import chat
    reply, _ = chat(messages, timeout=180, agent=agent)
    return (reply or "").strip()


def write_letter(row: dict, posting: str) -> tuple[str, dict, str, list[str]]:
    """draft → review → revise, at most MAX_REVIEWS reviews. Returns (letter, review, status, why)."""
    from pipeline import writer
    ask = (f"Candidate profile:\n{profile.profile_as_text()}\n\n{KIT_RULES}\n\nRole: {row['title']}\n"
           f"Company: {row['company']}\n{reviewer.fence('posting', posting[:7000])}")
    messages = [{"role": "system", "content": writer.system_prompt()}, {"role": "user", "content": ask}]
    company = row["company"].split("(")[0].strip()
    letter = assemble(_chat(messages, "kit-writer"), company=company)
    review, status, why = {}, "BLOCKED", ["not reviewed"]
    for n in range(1, MAX_REVIEWS + 1):
        review = reviewer.review_letter(posting, letter)
        status, why = clearance(letter, review)
        logger.info(f"{company}: review {n} → {status} {why}")
        if status == "CLEARED" or n == MAX_REVIEWS:
            break
        fixes = "\n".join(f"- {w}" for w in why if not w.startswith("unsupported claim"))
        fixes += "".join(f"\n- unsupported claim, remove or correct: \"{c.get('claim', '')[:220]}\" "
                         f"(reviewer: {c.get('why', '')[:260]})" for c in review.get("ungrounded_claims", []))
        if n == 1:
            fixes += "".join(f"\n- requirement never engaged (name it as a match or a gap, only if the facts allow): "
                             f"{m}" for m in review.get("unaddressed_musts", [])[:3])
        messages += [{"role": "assistant", "content": letter},
                     {"role": "user", "content": "A reviewer checked this letter against the posting and the verified "
                      "facts. Fix exactly these points by REMOVING or correcting the text, never by inventing support. "
                      f"Keep the KIT RULES. Return the body only:\n{fixes}"}]
        letter = assemble(_chat(messages, "kit-revise"), company=company)
    return letter, review, status, why


def build_kit(row: dict, batch_dir: Path) -> dict:
    from pipeline import cv_builder, letter_pdf
    from pipeline import lint as output_lint
    p = profile.get()
    company = row["company"].split("(")[0].strip()
    folder = APPS_DIR / _slug(row)
    folder.mkdir(parents=True, exist_ok=True)
    url = row.get("employer_url") or row.get("url", "")
    try:
        posting = archive.fetch(url)
    except Exception as e:  # a failed live fetch falls back to the sourced description
        posting = ""
        logger.warning(f"{company}: live fetch failed ({type(e).__name__}), using the sourced description")
    if archive.looks_closed(posting):
        shutil.rmtree(folder, ignore_errors=True)
        return {"company": company, "title": row["title"], "status": "SKIPPED", "why": ["listing closed at build time"]}
    if len(posting) < 600:
        posting = row.get("description", "")
    job = {k: row.get(k) for k in ("title", "company", "location", "url", "employer_url", "source", "score",
                                   "stack", "projects", "german", "hours", "language")}
    (folder / "job.json").write_text(json.dumps(job, indent=2, ensure_ascii=False), encoding="utf-8")
    (folder / "jd.txt").write_text(f"Title: {row['title']}\nCompany: {row['company']}\nURL: {url}\n\n"
                                   f"{row.get('description', '')}", encoding="utf-8")
    archive.archive_posting(folder, posting, url=url)
    keywords = cv_keywords(row.get("stack", []))
    cv_name = profile.cv_filename()
    (folder / "status.json").write_text(json.dumps({
        "status": "pending", "stage": None, "applied_at": None, "cv_path": str(folder / cv_name),
        "cv_keywords": keywords, "response_notes": f"{batch_dir.name} kit {date.today().isoformat()}, not sent"},
        indent=2), encoding="utf-8")
    cv_builder.build_tailored_cv(job, keywords, output_path=str(folder / cv_name))
    shutil.copyfile(folder / cv_name, folder / "cv.pdf")

    letter, review, status, why = write_letter(row, posting)
    (folder / "cover_letter.txt").write_text(letter, encoding="utf-8")
    review["kit_status"], review["kit_why"] = status, why
    (folder / "review.json").write_text(json.dumps(review, indent=1, ensure_ascii=False), encoding="utf-8")
    (folder / "review.md").write_text(reviewer.render_review_md(review, f"{company}, {row['title']}"), encoding="utf-8")
    pdf_name = profile.letter_filename(company)
    cleared = status == "CLEARED"
    if cleared:
        letter_pdf.render(letter, str(folder / pdf_name))
    # Output lint: post-generation, fail and report. A hit marks the kit
    # needs-human-fix and the send gate refuses it; nothing is regenerated here.
    lint_report = output_lint.lint_kit(folder, APPS_DIR)
    if lint_report["status"] == "fail":
        status = "NEEDS-HUMAN-FIX"
        rules = ", ".join(f"{r}={n}" for r, n in lint_report["by_rule"].items() if n)
        why = why + [f"lint: {lint_report['total']} hits ({rules}), see lint.md"]
    gaps = [r["requirement"] for r in review.get("requirements", []) if r.get("status") == "gapped"]
    note = [f"# {row['company']} — {row['title']}", "",
            f"**Status:** {status} ({batch_dir.name}, built {date.today().isoformat()}), NOT SENT",
            *([f"**Blocked because:** {'; '.join(why)}"] if status != "CLEARED" else []),
            f"**Lint:** {lint_report['status'].upper()} ({lint_report['total']} hits), `{folder / 'lint.md'}`",
            f"**Maps to:** {', '.join(row.get('projects', []))} · **Fit:** {row.get('score')}",
            f"**Their named stack:** {', '.join(row.get('stack', [])) or '—'}",
            f"**Gaps the reviewer lists:** {'; '.join(gaps) or '—'}",
            f"**Language:** English letter (listing: {row.get('language')}; German asked: {row.get('german')}) · "
            f"**Hours:** {row.get('hours')}",
            f"**Send route:** {url}",
            f"**Attach:** `{folder / cv_name}`"
            + (f" and `{folder / pdf_name}`" if cleared else " (letter PDF withheld)"),
            f"**Subject:** Application: {row['title']}, {p['name']}",
            f"**Review:** `{folder / 'review.md'}`: {len(review.get('requirements', []))} requirements, "
            f"{len(review.get('ungrounded_claims', []))} unsupported, musts not engaged: "
            f"{review.get('unaddressed_musts') or 'none'}; billed {review.get('billed')}",
            f"**After sending, run `python -m pipeline tracker sent \"{company}\"`.**", "", "---", "",
            "## Letter (paste-ready)", "", letter.strip(), "", "---", f"Package: `{folder}`", ""]
    (batch_dir / f"{_token(company)}.md").write_text("\n".join(note), encoding="utf-8")
    return {"company": company, "title": row["title"], "status": status, "why": why, "words": len(letter.split()),
            "folder": folder.name, "score": row.get("score"), "projects": row.get("projects"),
            "musts_open": review.get("unaddressed_musts", []), "billed": review.get("billed"),
            "lint": lint_report["status"], "lint_hits": lint_report["total"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Build reviewer-cleared kits from a sourcing report (never sends)")
    ap.add_argument("cmd", choices=["build", "plan"])
    ap.add_argument("report", help="data/sourcing/YYYY-MM-DD.json")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--batch", default="batch-1", help="kit notes go to data/applications/<batch>/ (batch-*)")
    args = ap.parse_args(argv)
    if not args.batch.startswith("batch-"):
        ap.error("--batch must start with batch- so the history index sees the kit notes")
    config.load_env()
    config.setup_logging()
    data = json.loads(Path(args.report).read_text(encoding="utf-8"))
    history = HistoryIndex.load(APPS_DIR)
    from pipeline.sourcing import _tracker_rows
    history.add_rows(_tracker_rows())
    picked = pick_candidates(data["ready"], history, args.limit)
    print(f"{len(data['ready'])} READY rows → {len(picked)} distinct companies not contacted before")
    for r in picked:
        print(f"  {r['score']:>3} {r['company']} — {r['title']} [{r.get('hours')}; German: {r.get('german')}]")
    if args.cmd == "plan":
        return 0
    batch_dir = APPS_DIR / args.batch
    batch_dir.mkdir(parents=True, exist_ok=True)
    results = []
    for r in picked:
        try:
            results.append(build_kit(r, batch_dir))
        except Exception as e:  # one bad kit must not sink the batch
            logger.exception(f"{r['company']}: build failed")
            results.append({"company": r["company"], "title": r["title"], "status": "ERROR", "why": [str(e)[:200]]})
        (batch_dir / "build_results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False),
                                                      encoding="utf-8")
    for x in results:
        print(f"{x['status']:8s} {x['company']} — {x['title']} {x.get('why') or ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
