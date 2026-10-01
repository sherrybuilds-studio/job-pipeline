"""The Telegram digest: MarkdownV2-escaped, scored against the profile's maximum."""
import asyncio

from pipeline import matcher, notifier, telegram

JOB = {"title": "Working Student (Python/Data)", "company": "Acme GmbH", "score": 77, "url": "https://acme.de/j/1",
       "matched_skills": ["Python", "SQL and databases"], "missing_skills": ["RAG and vectors"]}


def test_digest_escapes_markdown_and_scores_against_the_profile_maximum():
    text = notifier.format_digest([JOB], top5=[JOB], total_scraped=120, min_score=40)
    top = matcher.max_score()
    assert f"77/{top}" in text
    assert "Working Student \\(Python/Data\\)" in text
    assert "[View job](https://acme.de/j/1)" in text
    assert "Scraped: 120" in text
    assert notifier.esc("1.5 (a_b)") == "1\\.5 \\(a\\_b\\)"


def test_an_empty_day_still_shows_the_top_five():
    text = notifier.format_digest([], top5=[JOB], total_scraped=40, min_score=60)
    assert "No matches above 60 today" in text and "TOP 5 TODAY" in text


def test_send_digest_goes_out_as_markdown(monkeypatch):
    sent = []
    monkeypatch.setattr(telegram, "send", lambda text, **kw: sent.append(kw) or True)
    assert asyncio.run(notifier.send_digest([JOB], top5=[JOB], total_scraped=1, min_score=40)) is True
    assert sent == [{"parse_mode": "MarkdownV2"}]
