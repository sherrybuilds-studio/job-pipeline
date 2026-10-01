"""pull_board and pull_all on a mock transport: bounded, polite, honest about
failures. No real network."""
import asyncio
import json
from pathlib import Path

import httpx

from pipeline.ats_boards import pull_all, pull_board

FIX = Path(__file__).parent / "fixtures"
PERSONIO_XML = (FIX / "personio_positions.xml").read_text(encoding="utf-8")
BOARD = {"ats": "personio", "slug": "mobility-lab", "company": "Mobility Lab"}


def _pull(handler, board=BOARD, **kw):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await pull_board(client, board, retry_wait=0, **kw)
    return asyncio.run(go())


def test_personio_retries_once_after_a_429_then_succeeds():
    calls: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        return httpx.Response(429, text="slow down") if len(calls) == 1 else httpx.Response(200, text=PERSONIO_XML)

    jobs, status = _pull(handler)
    assert status == "200" and len(jobs) == 2
    assert calls == ["https://mobility-lab.jobs.personio.de/xml"] * 2


def test_personio_gives_up_after_the_single_retry():
    calls: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        calls.append(str(r.url))
        return httpx.Response(429, text="slow down")

    jobs, status = _pull(handler)
    assert status == "429" and jobs == []
    assert len(calls) == 2  # one try, one retry, never a third


def test_personio_falls_back_from_de_to_com():
    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.host.endswith(".de"):
            return httpx.Response(404)
        return httpx.Response(200, text=PERSONIO_XML)

    jobs, status = _pull(handler)
    assert status == "200" and jobs[0]["url"].startswith("https://mobility-lab.jobs.personio.com/job/")


def test_smartrecruiters_fetches_details_for_student_titles_only():
    listing = json.loads((FIX / "smartrecruiters_postings.json").read_text(encoding="utf-8"))
    listing["content"][1]["name"] = "Werkstudent Logistics Data (m/w/d)"
    detail = (FIX / "smartrecruiters_posting_detail.json").read_text(encoding="utf-8")
    fetched: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        if r.url.path.endswith("/postings"):
            return httpx.Response(200, json=listing)
        fetched.append(r.url.path)
        return httpx.Response(200, text=detail)

    board = {"ats": "smartrecruiters", "slug": "ParcelForward", "company": "Parcel Forward"}
    jobs, status = _pull(handler, board=board)
    assert status == "200" and len(jobs) == 2
    assert fetched == ["/v1/companies/ParcelForward/postings/700000000000002"]  # the senior role got no detail fetch
    assert "GDPR" in jobs[1]["description"]


def test_a_failing_board_reports_its_status_instead_of_raising():
    jobs, status = _pull(lambda r: httpx.Response(500), board={"ats": "greenhouse", "slug": "x", "company": "X"})
    assert (jobs, status) == ([], "500")
    jobs, status = _pull(lambda r: httpx.Response(200), board={"ats": "unknown", "slug": "x"})
    assert status.startswith("unknown ats")


def test_pull_all_stops_personio_after_two_consecutive_429s():
    seen: list[str] = []

    def handler(r: httpx.Request) -> httpx.Response:
        seen.append(r.url.host)
        return httpx.Response(429, text="slow down")

    boards = [{"ats": "personio", "slug": s, "company": s} for s in ("a", "b", "c", "d")]

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await pull_all(boards, client=client, personio_spacing=0, retry_wait=0)

    jobs, rows = asyncio.run(go())
    assert jobs == []
    statuses = [r["status"] for r in sorted(rows, key=lambda r: r["board"])]
    assert statuses == ["429", "429", "skipped (personio 429)", "skipped (personio 429)"]
    assert len(set(seen)) == 2  # boards c and d were never contacted
