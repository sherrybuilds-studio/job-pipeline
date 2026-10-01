"""Entry point for the sourcing pass.

    python -m pipeline source                   # full pass → data/sourcing/YYYY-MM-DD.{md,json}
    python -m pipeline source --budget 300      # shorter run; other flags as in sourcing.py
    python -m pipeline source --show            # print today's READY table and source counts, no run
    python -m pipeline source --show 2026-10-01

Find only. Nothing here applies, builds kits, or writes to the tracker or Telegram.
"""
import argparse
import asyncio
import json
import sys
from datetime import date
from pathlib import Path

from pipeline import sourcing


def load_report(run_date: str, out_dir: Path | None = None) -> dict:
    path = (out_dir or sourcing.OUT_DIR) / f"{run_date}.json"
    if not path.exists():
        raise FileNotFoundError(f"no sourcing run for {run_date}: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _join(v) -> str:
    return ", ".join(str(x) for x in v) if isinstance(v, (list, tuple)) else str(v or "")


def render_ready(report: dict) -> str:
    """READY table plus per-source returned and blocked counts for one saved run."""
    lines = [f"# READY {report['date']}", "", sourcing.summary_line(report["stats"]), "",
             sourcing.COLUMNS, sourcing.RULE]
    lines += [sourcing.row(job) for job in report["ready"]] or ["| — | no READY rows | | | | | | | | |"]
    lines += ["", "## Sources", "", "| source | returned | blocked | notes |", "|---|---|---|---|"]
    for s in report["sources"]:
        lines.append(f"| {s['name']} | {s['returned']} | {_join(s.get('blocked')) or '—'} | "
                     f"{_join(s.get('notes')) or '—'} |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sourcing pass: find and filter roles (no kits, no writes)")
    parser.add_argument("--show", nargs="?", const=date.today().isoformat(), metavar="YYYY-MM-DD",
                        help="print a saved day's READY table and source counts instead of running")
    parser.add_argument("--budget", type=int, default=600, help="whole-run cap in seconds (default 600)")
    parser.add_argument("--max-boards", type=int, default=80)
    parser.add_argument("--no-linkedin", action="store_true")
    parser.add_argument("--no-xing", action="store_true")
    parser.add_argument("--no-indeed", action="store_true")
    parser.add_argument("--no-aggregators", action="store_true", help="skip Arbeitnow, Adzuna and the Bundesagentur")
    args = parser.parse_args(argv)

    if args.show:
        try:
            print(render_ready(load_report(args.show)))
        except FileNotFoundError as e:
            print(e)
            return 1
        return 0

    sourcing.bootstrap()
    md_path = asyncio.run(sourcing.run(budget_s=args.budget, max_boards=args.max_boards,
                                       linkedin=not args.no_linkedin, xing=not args.no_xing,
                                       indeed=not args.no_indeed, aggregators=not args.no_aggregators))
    print(render_ready(load_report(md_path.stem, md_path.parent)))
    print(f"\nreport → {md_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
