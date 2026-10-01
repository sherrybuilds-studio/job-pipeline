"""A plain-text summary of every tracked application.

    python -m pipeline summary
"""
import logging
import sys
from datetime import UTC, datetime

from pipeline import config
from pipeline.tracker import TABLE, _client

logger = logging.getLogger(__name__)

STAGE_ORDER = ["applied", "replied", "interview", "offer", "rejected", "withdrawn"]


def summarise(apps: list[dict]) -> str:
    """The report for a list of tracker rows."""
    if not apps:
        return "No applications tracked yet."
    total = len(apps)
    by_stage: dict[str, int] = {}
    for app in apps:
        stage = app.get("stage") or "none yet"
        by_stage[stage] = by_stage.get(stage, 0) + 1
    sent = [a for a in apps if str(a.get("response_notes") or "").startswith("sent ") or a.get("response_at")]
    responded = [a for a in apps if a.get("response_at")]
    lines = [f"Application summary ({datetime.now(UTC).strftime('%Y-%m-%d')})", "",
             f"Tracked postings: {total}",
             f"Sent: {len(sent)}",
             f"Responses: {len(responded)}", "", "By stage:"]
    for stage in STAGE_ORDER + sorted(set(by_stage) - set(STAGE_ORDER)):
        if stage in by_stage:
            lines.append(f"  {stage}: {by_stage[stage]}")
    if responded:
        lines += ["", "Responded:"]
        lines += [f"  {a.get('company', '?')}: {a.get('job_title', '?')} [{a.get('stage', '?')}]"
                  for a in responded[:10]]
    waiting = sorted((a for a in sent if not a.get("response_at")), key=lambda a: a.get("score") or 0, reverse=True)
    if waiting:
        lines += ["", "Sent, no response yet (top 10 by score):"]
        lines += [f"  {a.get('company', '?')}: {a.get('job_title', '?')} (score {a.get('score') or 0})"
                  for a in waiting[:10]]
    return "\n".join(lines)


def generate_summary() -> str:
    try:
        apps = _client().table(TABLE).select("*").execute().data
    except Exception as e:
        logger.warning(f"Could not fetch applications: {type(e).__name__}")
        return "Could not fetch application data from Supabase."
    return summarise(apps)


def main(argv: list[str] | None = None) -> int:
    config.load_env()
    print(generate_summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
