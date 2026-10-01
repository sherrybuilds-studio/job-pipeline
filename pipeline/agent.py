"""The daily run: scrape, score, write letters for strong matches, send the digest.

    python -m pipeline daily --dry-run        # no tracker writes, no Telegram, no files
    python -m pipeline daily                  # live run
    python -m pipeline daily --min-score 40

Wires job_graph's nodes to the real modules. Every source is best-effort: a
source that fails or has no credentials logs a warning and contributes
nothing. Nothing is ever sent to an employer; letters and CVs land in
data/applications/ for a person to review.
"""
import argparse
import asyncio
import json
import logging
import sys
from datetime import date

from pipeline import config, profile
from pipeline.job_graph import HuntDeps, new_state, run_hunt

logger = logging.getLogger("agent")

RUNS_PATH = config.DATA_DIR / "top_matches.json"


def merge(lists: list[list[dict]]) -> list[dict]:
    """Deduplicate across sources by URL and by (company, title)."""
    seen_urls: set[str] = set()
    seen_keys: set[tuple[str, str]] = set()
    merged: list[dict] = []
    for jobs in lists:
        for job in jobs:
            url = job.get("url", "").strip()
            key = (job.get("company", "").lower(), job.get("title", "").lower())
            if (url and url in seen_urls) or key in seen_keys:
                continue
            if url:
                seen_urls.add(url)
            seen_keys.add(key)
            merged.append(job)
    return merged


def save_run(run_date: str, total_scraped: int, matched_count: int, scored: list[dict], path=None) -> None:
    """Append this run's counts and top 10 to data/top_matches.json."""
    path = path or RUNS_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        history = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (json.JSONDecodeError, OSError):
        history = []
    history.append({"date": run_date, "total_scraped": total_scraped, "matched_count": matched_count,
                    "top_10": [{"title": j["title"], "company": j.get("company", ""), "score": j["score"],
                                "matched_skills": j.get("matched_skills", []), "url": j.get("url", ""),
                                "source": j.get("source", "")} for j in scored[:10]]})
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False), encoding="utf-8")
    logger.info(f"Run data saved to {path}")


async def scrape_all(pages: int, iteration: int) -> list[dict]:
    """scrape node: every daily source, merged and deduplicated."""
    from pipeline import scraper

    async def adzuna() -> list[dict]:
        try:
            return await scraper.scrape_jobs(pages_per_query=pages)
        except ValueError as e:  # no credentials
            logger.warning(f"Adzuna skipped: {e}")
            return []

    results = {
        "adzuna": await adzuna(),
        "arbeitnow": await scraper.scrape_arbeitnow(),
        "firecrawl": await asyncio.to_thread(scraper.scrape_firecrawl_boards),
        "bundesagentur": await scraper.scrape_bundesagentur(),
        "himalayas": await scraper.scrape_himalayas(),
        "remoteok": await scraper.scrape_remoteok(),
    }
    merged = merge(list(results.values()))
    logger.info(f"Merged {len(merged)} unique jobs (" + ", ".join(f"{k}={len(v)}" for k, v in results.items()) + ")")
    return merged


def _cv_keywords(job: dict) -> list[str]:
    """The profile skills this posting names: the same mapping the kits use."""
    from pipeline.kit import cv_keywords
    from pipeline.sourcing import named_stack
    return cv_keywords(named_stack(f"{job.get('title', '')}\n{job.get('description', '')}"))


def persist_job(job: dict, score: int, letter: str) -> dict:
    """Tracker row, per-job folder, tailored CV, then the output lint and the
    ATS parser check. The lint marks a failing folder needs-human-fix."""
    from pipeline import cv_builder, lint, parser_check, tracker

    row = tracker.insert_job(job, score=score, cover_letter=letter)
    folder = tracker.APPS_DIR / tracker.slugify(f"{job.get('company', 'unknown')}-{job.get('title', 'role')}")
    if folder.exists() and tracker.is_protected(folder):
        # checked before the CV is rendered: the CV in a sent or kitted folder is the one that went out
        logger.warning(f"{folder.name}: sent or kitted; the daily run leaves the record untouched")
        return row
    keywords = _cv_keywords(job)
    try:
        cv_path = cv_builder.build_tailored_cv(job, keywords, output_path=str(folder / profile.cv_filename()))
        saved = tracker.save_application_artifacts(job, letter, cv_path, keywords, job_url=job.get("url"))
    except Exception as e:
        logger.warning(f"Tailored CV or folder failed for {job.get('company', '?')}: {e}")
        return row
    if saved.get("skipped"):
        return row
    report = lint.lint_kit(saved["folder"])
    if report["status"] == "fail":
        logger.warning(f"Lint failed for {job.get('company', '?')}: {report['total']} hits, "
                       f"needs-human-fix, see {saved['folder']}/lint.md")
    check = parser_check.check_cv(saved["cv_path"], keywords, profile.get())
    if not check["ok"]:
        logger.warning(f"ATS parser check failed for {job.get('company', '?')}: {check}")
    return row


async def _dry_notify(matched, top5, total_scraped, min_score):
    logger.info(f"[DRY RUN] Telegram skipped: {len(matched)} matches of {total_scraped} scraped")
    if top5:
        logger.info(f"[DRY RUN] Top match: {top5[0]['title']} @ {top5[0].get('company', '?')}, {top5[0]['score']}")


def build_deps(dry_run: bool) -> HuntDeps:
    """Wire the graph's nodes to the real modules, or to dry-run stand-ins."""
    from pipeline import matcher, notifier, tracker, writer

    def dedup(jobs: list[dict]) -> list[dict]:
        if dry_run:
            logger.info("[DRY RUN] tracker dedup skipped: every posting counts as new")
            return jobs
        new = [j for j in jobs if not tracker.already_tracked(j["url"])]
        logger.info(f"{len(new)} new jobs (not yet in the tracker)")
        return new

    def dry_letter(job: dict) -> str | None:
        logger.info(f"[DRY RUN] letter not written: {job['title']} @ {job.get('company', '?')} ({job.get('score')})")
        return None

    def build_cv() -> str:
        from pipeline import cv_builder
        return cv_builder.build_cv()

    return HuntDeps(
        scrape=scrape_all,
        dedup=dedup,
        score=lambda jobs: matcher.match_jobs(jobs, min_score=0),  # keep all; the graph applies the threshold
        notify=_dry_notify if dry_run else notifier.send_digest,
        build_cv=(lambda: "") if dry_run else build_cv,
        write_cover_letter=dry_letter if dry_run else writer.write_cover_letter,
        save_run=(lambda *_: None) if dry_run else save_run,
        persist_job=persist_job,
    )


async def run(dry_run: bool = False, min_score: int = 25, pages: int = 2) -> dict:
    run_date = date.today().isoformat()
    logger.info(f"Starting the daily run | dry_run={dry_run} min_score={min_score} date={run_date}")
    if not dry_run:
        # the follow-up schedule is written before the hunt, so a scraper or
        # model failure never hides a due follow-up
        try:
            from pipeline.followup import write_state
            write_state()
        except Exception as e:
            logger.warning(f"follow-up schedule not written: {e}")
    state = new_state(build_deps(dry_run), dry_run=dry_run, min_score=min_score, pages=pages, run_date=run_date)
    await run_hunt(state)
    logger.info(f"Run complete: {state['result']} (iterations={state['iteration']}, "
                f"path={' → '.join(state['_path'])})")
    return state


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Daily run: scrape, score, letters for strong matches, digest")
    parser.add_argument("--dry-run", action="store_true", help="no tracker writes, no Telegram, no letters")
    parser.add_argument("--min-score", type=int, default=25, help="minimum score for a match (default 25)")
    parser.add_argument("--pages", type=int, default=2, help="Adzuna pages per primary query (default 2)")
    args = parser.parse_args(argv)
    config.load_env()
    config.setup_logging()
    profile.get()  # fail before any network call when the profile is missing or broken
    asyncio.run(run(dry_run=args.dry_run, min_score=args.min_score, pages=args.pages))
    return 0


if __name__ == "__main__":
    sys.exit(main())
