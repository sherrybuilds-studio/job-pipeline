"""The daily-run graph: each node in isolation, then whole-graph flows. Fully
offline: every source, model and Telegram call is a fake. Proves the routing
(strong, weak, no match), the bounded widen-and-retry loop, and that one
failing letter never sinks the run."""
import asyncio

import pytest

from pipeline.graph import END, GraphError, run_graph
from pipeline.job_graph import (
    HuntDeps,
    build_job_graph,
    match_node,
    new_state,
    run_hunt,
    scrape_node,
    widen_node,
    write_node,
)


def _job(company="ACME", score=30):
    return {"title": "Werkstudent AI", "company": company, "url": f"u/{company}", "score": score}


def _deps(*, scrape_by_iter=None, letter=None, **over):
    calls = {"notify": [], "persist": 0, "build_cv": 0, "letters": 0, "scrape_iters": []}

    async def scrape(pages, iteration):
        calls["scrape_iters"].append(iteration)
        return scrape_by_iter.get(iteration, []) if scrape_by_iter is not None else [_job()]

    async def notify(matched, top5, total, min_score):
        calls["notify"].append((len(matched), len(top5)))

    def build_cv():
        calls["build_cv"] += 1
        if over.get("cv_raises"):
            raise RuntimeError("fonts missing")
        return "cv.pdf"

    def write_cover_letter(job):
        calls["letters"] += 1
        if letter is not None:
            return letter(job)
        return f"Dear {job.get('company')}"

    def persist(job, score, cover):
        calls["persist"] += 1

    deps = HuntDeps(scrape=scrape, dedup=lambda jobs: jobs,
                    score=lambda jobs: sorted(jobs, key=lambda j: j["score"], reverse=True),
                    notify=notify, build_cv=build_cv, write_cover_letter=write_cover_letter, persist_job=persist)
    return deps, calls


# ── nodes ────────────────────────────────────────────────────────────────────

def test_scrape_node_dedups_and_counts():
    deps, calls = _deps()
    s = new_state(deps)
    assert asyncio.run(scrape_node(s)) == "done"
    assert s["total_scraped"] == 1 and calls["scrape_iters"] == [0]


def test_match_node_routes_strong_weak_and_no_match():
    deps, _ = _deps()
    s = new_state(deps, min_score=25)
    s["raw_jobs"] = [_job(score=40), _job(company="B", score=10)]
    assert match_node(s) == "strong" and len(s["matched"]) == 1

    s = new_state(deps, min_score=25, max_iterations=3)
    s["raw_jobs"] = [_job(score=10)]
    assert match_node(s) == "weak"

    s = new_state(deps, min_score=25)
    assert match_node(s) == "no_match"  # nothing scored

    s = new_state(deps, min_score=25, max_iterations=3)
    s["iteration"], s["raw_jobs"] = 3, [_job(score=10)]
    assert match_node(s) == "no_match"  # weak, but the widen budget is spent


def test_widen_node_lowers_the_bar_and_reads_deeper():
    deps, _ = _deps()
    s = new_state(deps, min_score=25, pages=2)
    assert widen_node(s) == "scrape"
    assert (s["iteration"], s["min_score"], s["pages"]) == (1, 15, 3)


def test_write_node_survives_a_failing_cv_and_a_failing_letter():
    def flaky(job):
        if job["company"] == "B":
            raise RuntimeError("model down")
        return "letter"

    deps, calls = _deps(cv_raises=True, letter=flaky)
    s = new_state(deps)
    s["matched"] = [_job(score=40), _job(company="B", score=40), _job(company="C", score=40)]
    assert write_node(s) == "done"
    assert s["cv_path"] == "" and calls["letters"] == 3 and calls["persist"] == 2


def test_write_node_persists_nothing_on_a_dry_run():
    deps, calls = _deps()
    s = new_state(deps, dry_run=True)
    s["matched"] = [_job(score=40)]
    write_node(s)
    assert calls["persist"] == 0


# ── the whole graph ──────────────────────────────────────────────────────────

def test_strong_path_writes_then_notifies():
    deps, calls = _deps(scrape_by_iter={0: [_job(score=45)]})
    s = new_state(deps, min_score=25)
    asyncio.run(run_hunt(s))
    assert s["result"] == "notified_matches"
    assert calls["build_cv"] == 1 and calls["persist"] == 1
    assert s["_path"] == ["scrape", "match", "write", "notify", END]


def test_weak_run_widens_then_finds_a_match():
    deps, calls = _deps(scrape_by_iter={0: [_job(score=10)], 1: [_job(score=50)]})
    s = new_state(deps, min_score=25, max_iterations=3)
    asyncio.run(run_hunt(s))
    assert s["result"] == "notified_matches" and s["iteration"] == 1
    assert calls["scrape_iters"] == [0, 1] and "widen" in s["_path"]


def test_widen_loop_is_bounded_and_terminates():
    deps, calls = _deps(scrape_by_iter={i: [_job(score=5)] for i in range(6)})
    s = new_state(deps, min_score=25, max_iterations=3)
    asyncio.run(run_hunt(s))
    # 25 → 15 (iteration 1) → 5 (iteration 2): the score-5 job finally clears the bar
    assert s["result"] == "notified_matches" and s["iteration"] == 2
    assert calls["scrape_iters"] == [0, 1, 2] and s["min_score"] == 5


def test_spent_widen_budget_sends_an_empty_digest():
    deps, calls = _deps(scrape_by_iter={i: [_job(score=5)] for i in range(6)})
    s = new_state(deps, min_score=25, max_iterations=1)
    asyncio.run(run_hunt(s))
    assert s["result"] == "notified_empty" and s["iteration"] == 1
    assert calls["scrape_iters"] == [0, 1] and calls["notify"] == [(0, 1)]


def test_no_jobs_at_all_sends_an_empty_digest_and_writes_nothing():
    deps, calls = _deps(scrape_by_iter={})
    s = new_state(deps, min_score=25)
    asyncio.run(run_hunt(s))
    assert s["result"] == "notified_empty" and calls["letters"] == 0


def test_graph_shape():
    g = build_job_graph()
    assert set(g) == {"scrape", "match", "widen", "write", "notify", "notify_empty"}
    assert set(g["match"][1]) == {"strong", "weak", "no_match"}


# ── the runner itself ────────────────────────────────────────────────────────

def test_runner_rejects_undeclared_edges_and_endless_loops():
    with pytest.raises(GraphError, match="no such edge|declared edges"):
        asyncio.run(run_graph({"a": (lambda s: "nope", {"done": END})}, "a", {}))
    with pytest.raises(GraphError, match="exceeded"):
        asyncio.run(run_graph({"a": (lambda s: "again", {"again": "a"})}, "a", {}, max_steps=5))
    with pytest.raises(GraphError, match="no such node"):
        asyncio.run(run_graph({}, "missing", {}))
