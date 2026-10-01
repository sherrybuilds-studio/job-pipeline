"""Seed the ATS board registry from a list you curate; re-run to top it up.

    python scripts/seed_ats_boards.py my_boards.csv            # probe, then write the registry
    python scripts/seed_ats_boards.py my_boards.csv --dry-run  # probe only, print the table

The CSV has one board per line: ats,slug,Company Name (ats is one of
greenhouse, ashby, lever, personio, smartrecruiters, join); lines starting with
# are ignored. See scripts/seed_boards.example.csv. Every board is probed with
the real puller (ats_boards.pull_board) and only boards that answer 200 land in
data/sources/ats_boards.json, tagged via=seed; failures are printed so the
registry stays clean. Location relevance is enforced per posting by the
sourcing pass, so a board with no roles in your area simply contributes nothing.

Read-only GETs. Personio boards are probed one at a time with spacing.
"""
import argparse
import asyncio
import csv
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.ats_boards import REGISTRY_PATH, add_boards, load_registry, pull_all, save_registry  # noqa: E402

KNOWN_ATS = {"greenhouse", "ashby", "lever", "personio", "smartrecruiters", "join"}


def read_candidates(path: Path) -> list[tuple[str, str, str]]:
    """(ats, slug, company) rows from the CSV; malformed lines are reported and skipped."""
    rows = []
    with path.open(encoding="utf-8", newline="") as f:
        for n, fields in enumerate(csv.reader(f), 1):
            if not fields or not fields[0].strip() or fields[0].lstrip().startswith("#"):
                continue
            if len(fields) < 2 or fields[0].strip().lower() not in KNOWN_ATS:
                print(f"line {n}: expected ats,slug,company with ats in {sorted(KNOWN_ATS)}; skipped")
                continue
            company = fields[2].strip() if len(fields) > 2 else fields[1].strip()
            rows.append((fields[0].strip().lower(), fields[1].strip(), company))
    return rows


async def probe(candidates: list[tuple[str, str, str]]) -> list[dict]:
    boards = [{"ats": ats, "slug": slug, "company": name} for ats, slug, name in candidates]
    async with httpx.AsyncClient() as client:
        _jobs, rows = await pull_all(boards, client=client, max_boards=len(boards), concurrency=3)
    by_board = {r["board"]: r for r in rows}
    for b in boards:
        r = by_board.get(f"{b['ats']}:{b['slug']}", {})
        b["status"], b["jobs"] = r.get("status", "?"), r.get("jobs", 0)
    return boards


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("file", type=Path, help="CSV of ats,slug,company")
    parser.add_argument("--dry-run", action="store_true", help="probe and print, do not write the registry")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    args = parser.parse_args(argv)

    candidates = read_candidates(args.file)
    if not candidates:
        print(f"no boards listed in {args.file}")
        return 1
    boards = asyncio.run(probe(candidates))
    ok = [b for b in boards if b["status"] == "200"]
    print(f"\n{len(ok)} boards answered 200, {len(boards) - len(ok)} did not\n")
    print(f"{'ats':16s} {'slug':24s} {'status':12s} jobs")
    for b in sorted(boards, key=lambda b: (b["status"] != "200", b["ats"], b["slug"])):
        print(f"{b['ats']:16s} {b['slug']:24s} {b['status']:12s} {b['jobs']}")
    if args.dry_run:
        return 0

    registry = load_registry(args.registry)
    added = 0
    for b in ok:
        added += add_boards(registry, [(b["ats"], b["slug"])], via="seed", company=b["company"])
        for entry in registry:
            if entry["ats"] == b["ats"] and entry["slug"].lower() == b["slug"].lower():
                entry["last_status"], entry["last_jobs"] = "200", b["jobs"]
                entry["company"] = entry.get("company") or b["company"]
    save_registry(registry, args.registry)
    print(f"\nregistry: {len(registry)} boards ({added} new) → {args.registry}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
