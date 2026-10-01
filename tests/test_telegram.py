"""Telegram over plain HTTP: long digests split on blank lines, rate limits are
retried, a rejected message is not, and no token means no request at all."""
import json

import httpx

from pipeline import telegram


def test_short_message_is_one_chunk():
    assert telegram.chunks("hello") == ["hello"]


def test_long_digest_splits_on_blank_lines():
    entry = "*Job*\nCompany\n" + "x" * 500
    msg = "\n\n".join([entry] * 12)
    parts = telegram.chunks(msg, limit=3900)
    assert len(parts) >= 2
    assert all(len(p) <= 3900 for p in parts)
    assert "\n\n".join(parts) == msg  # nothing lost, nothing cut mid-entry


def test_giant_block_is_hard_wrapped():
    assert [len(p) for p in telegram.chunks("y" * 9000, limit=3900)] == [3900, 3900, 1200]


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_no_token_means_no_request():
    calls = []
    assert telegram.send("hi", client=_client(lambda r: calls.append(r) or httpx.Response(200))) is False
    assert calls == []


def test_rate_limit_is_retried_and_a_long_message_goes_out_in_parts():
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            return httpx.Response(429, json={"ok": False, "parameters": {"retry_after": 0}})
        return httpx.Response(200, json={"ok": True})

    text = "\n\n".join(["a" * 3000, "b" * 3000])
    assert telegram.send(text, token="t", chat_id="42", client=_client(handler), backoff=0) is True
    assert [c["text"][0] for c in calls] == ["a", "a", "b"]  # first part retried once, then the second part
    assert calls[0]["chat_id"] == "42" and calls[0]["link_preview_options"] == {"is_disabled": True}


def test_a_rejected_message_is_not_retried():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"ok": False, "description": "can't parse entities"})

    assert telegram.send("bad *markup", token="t", chat_id="42", client=_client(handler), backoff=0) is False
    assert len(calls) == 1
