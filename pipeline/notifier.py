"""The daily Telegram digest: matches above the threshold, then the day's top 5.

Formatted as Telegram MarkdownV2 (every special character escaped) and sent
through pipeline.telegram, which splits long digests on blank lines.
"""
import asyncio
import logging
import re

from pipeline import matcher, telegram

logger = logging.getLogger(__name__)

_MD_SPECIAL = re.compile(r"([_*\[\]()~`>#+\-=|{}.!\\])")


def esc(text) -> str:
    return _MD_SPECIAL.sub(r"\\\1", str(text))


def _url(url: str) -> str:
    # inside a MarkdownV2 link target only ")" and "\" need escaping
    return url.replace("\\", "\\\\").replace(")", "\\)")


def _job_block_full(i: int, job: dict, top: int) -> list[str]:
    lines = [f"*{i}\\. {esc(job['title'])}*",
             f"   🏢 {esc(job.get('company', 'Unknown'))}   Score: {esc(job.get('score', 0))}/{top}"]
    if job.get("salary"):
        lines.append(f"   💰 {esc(job['salary'])}")
    if job.get("matched_skills"):
        lines.append("   ✅ " + esc(", ".join(job["matched_skills"][:4])))
    if job.get("missing_skills"):
        lines.append("   ❌ " + esc(", ".join(job["missing_skills"][:3])))
    if job.get("url"):
        lines.append(f"   📎 [View job]({_url(job['url'])})")
    lines.append("")
    return lines


def _job_block_brief(i: int, job: dict, top: int) -> list[str]:
    line = f"{i}\\. *{esc(job['title'])}* — {esc(job.get('company', 'Unknown'))} \\({esc(job.get('score', 0))}/{top}\\)"
    if job.get("matched_skills"):
        line += " ✅ " + esc(", ".join(job["matched_skills"][:2]))
    lines = [line]
    if job.get("url"):
        lines.append(f"   📎 [View]({_url(job['url'])})")
    lines.append("")
    return lines


def format_digest(jobs: list[dict], top5: list[dict] | None = None, total_scraped: int = 0, min_score: int = 40) -> str:
    top = matcher.max_score()
    lines = ["*Job pipeline \\| morning digest*\n"]
    if jobs:
        lines.append(f"🔥 *MATCHES \\(score ≥ {esc(min_score)}\\)*\n")
        for i, job in enumerate(jobs, 1):
            lines.extend(_job_block_full(i, job, top))
    else:
        lines.append(f"_No matches above {esc(min_score)} today\\._\n")
    if top5:
        lines.append("📊 *TOP 5 TODAY*\n")
        for i, job in enumerate(top5, 1):
            lines.extend(_job_block_brief(i, job, top))
    best = top5[0]["score"] if top5 else (jobs[0]["score"] if jobs else 0)
    lines.append(f"_📈 Scraped: {esc(total_scraped)} \\| Passed filter: {esc(len(jobs))} \\| "
                 f"Top score: {esc(best)}/{top}_")
    return "\n".join(lines)


async def send_digest(jobs: list[dict], top5: list[dict] | None = None, total_scraped: int = 0,
                      min_score: int = 40) -> bool:
    message = format_digest(jobs, top5=top5, total_scraped=total_scraped, min_score=min_score)
    ok = await asyncio.to_thread(telegram.send, message, parse_mode="MarkdownV2")
    if ok:
        logger.info(f"Telegram digest sent: {len(jobs)} matches, top 5 {'included' if top5 else 'empty'}, "
                    f"{total_scraped} scraped")
    else:
        logger.warning("Telegram digest not delivered (see the telegram log line above)")
    return ok
