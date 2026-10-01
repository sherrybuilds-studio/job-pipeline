"""The daily run as an explicit graph.

A straight line (scrape → score → letters → digest) has no answer for a day
when nothing clears the bar. The graph adds that branch: widen the search and
try again, a bounded number of times, before sending a "no matches" digest.

    scrape ─▶ match ─┬─ strong ─────▶ write ─▶ notify ─────────▶ END
                     ├─ weak ───────▶ widen ─▶ scrape    (at most max_iterations)
                     └─ no_match ───▶ notify_empty ────────────▶ END

  strong   : at least one job reaches min_score → CV, letters, digest
  weak     : jobs were scored but none reach min_score and iterations remain →
             lower min_score, read one more page, scrape again
  no_match : nothing passed the gates, or the widen budget is spent

Nodes call injected functions on state["deps"] (a HuntDeps), so the whole
graph runs offline against fakes in tests and against the real sources in
production, on the same code path.
"""
import logging
from collections.abc import Callable
from dataclasses import dataclass

from pipeline.graph import END, run_graph

log = logging.getLogger("pipeline.graph")


def _noop(*_args, **_kwargs):
    return None


@dataclass
class HuntDeps:
    """The work each node does: real modules in production, fakes in tests."""

    scrape: Callable                          # async (pages, iteration) -> list[dict] merged raw jobs
    dedup: Callable                           # (jobs) -> jobs not tracked yet
    score: Callable                           # (jobs) -> scored jobs, best first
    notify: Callable                          # async (matched, top5, total, min_score) -> None
    build_cv: Callable = _noop                # () -> master CV path
    write_cover_letter: Callable = _noop      # (job) -> letter or None
    save_run: Callable = _noop                # (date, total, matched_count, scored) -> None
    persist_job: Callable = _noop             # (job, score, letter) -> None


def new_state(deps: HuntDeps, *, dry_run: bool = False, min_score: int = 25, pages: int = 2,
              max_iterations: int = 3, run_date: str = "") -> dict:
    return {"deps": deps, "dry_run": dry_run, "min_score": min_score, "start_min_score": min_score, "pages": pages,
            "iteration": 0, "max_iterations": max_iterations, "run_date": run_date, "raw_jobs": [], "scored": [],
            "matched": [], "top5": [], "total_scraped": 0, "result": None}


async def scrape_node(state: dict) -> str:
    """Fetch and merge every source, then drop postings already tracked."""
    deps = state["deps"]
    jobs = await deps.scrape(state["pages"], state["iteration"])
    state["raw_jobs"] = deps.dedup(jobs)
    state["total_scraped"] = len(state["raw_jobs"])
    log.info("scrape (iteration %d, pages %d): %d jobs after dedup", state["iteration"], state["pages"],
             state["total_scraped"])
    return "done"


def match_node(state: dict) -> str:
    """Score everything and classify the run: strong, weak or no match."""
    deps = state["deps"]
    scored = deps.score(state["raw_jobs"])
    state["scored"] = scored
    state["matched"] = [j for j in scored if j.get("score", 0) >= state["min_score"]]
    state["top5"] = scored[:5]
    deps.save_run(state["run_date"], state["total_scraped"], len(state["matched"]), scored)
    log.info("match: %d scored, %d at or above min_score %d", len(scored), len(state["matched"]), state["min_score"])
    if state["matched"]:
        return "strong"
    if scored and state["iteration"] < state["max_iterations"]:
        return "weak"
    return "no_match"


def widen_node(state: dict) -> str:
    """Broaden the search for another pass: lower the bar, read deeper."""
    state["iteration"] += 1
    state["min_score"] = max(0, state["min_score"] - 10)
    state["pages"] += 1
    log.info("widen → iteration %d, min_score %d, pages %d", state["iteration"], state["min_score"], state["pages"])
    return "scrape"


def write_node(state: dict) -> str:
    """Regenerate the master CV, then a reviewed letter per match (persisted
    unless this is a dry run). A failure on one job never stops the others."""
    deps = state["deps"]
    try:
        state["cv_path"] = deps.build_cv()
    except Exception as e:
        log.warning("CV build failed: %s", e)
        state["cv_path"] = ""
    for job in state["matched"]:
        try:
            letter = deps.write_cover_letter(job)
            if letter is not None and not state["dry_run"]:
                deps.persist_job(job, job.get("score", 0), letter)
        except Exception as e:
            log.warning("letter or record failed for %s: %s", job.get("company"), e)
    return "done"


async def notify_node(state: dict) -> str:
    """The digest with the matches and the day's top 5."""
    await state["deps"].notify(state["matched"], state["top5"], state["total_scraped"], state["min_score"])
    state["result"] = "notified_matches"
    return "done"


async def notify_empty_node(state: dict) -> str:
    """No matches after widening: a "no matches today" digest that still shows the top 5."""
    await state["deps"].notify([], state["top5"], state["total_scraped"], state["min_score"])
    state["result"] = "notified_empty"
    return "done"


def build_job_graph() -> dict:
    return {
        "scrape": (scrape_node, {"done": "match"}),
        "match": (match_node, {"strong": "write", "weak": "widen", "no_match": "notify_empty"}),
        "widen": (widen_node, {"scrape": "scrape"}),
        "write": (write_node, {"done": "notify"}),
        "notify": (notify_node, {"done": END}),
        "notify_empty": (notify_empty_node, {"done": END}),
    }


async def run_hunt(state: dict) -> dict:
    """Run the graph from `scrape`. state["result"] is the outcome and
    state["_path"] the nodes visited."""
    return await run_graph(build_job_graph(), "scrape", state)
