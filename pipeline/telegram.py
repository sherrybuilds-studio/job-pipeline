"""Telegram Bot API over plain HTTP: send a message, split long ones.

TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID come from the environment. Without a
token the message is logged instead of sent, so a fresh checkout never needs
real credentials. The token is part of the request URL, so the URL is never
logged (config.setup_logging also quiets httpx's own request log).
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org"
# Telegram rejects messages over 4096 characters; the margin leaves room for
# MarkdownV2 escapes added after a split.
MAX_LEN = 3900


def chunks(message: str, limit: int = MAX_LEN) -> list[str]:
    """Split on blank lines so a digest entry is never cut mid-line; a single
    block longer than the limit is hard-wrapped."""
    if len(message) <= limit:
        return [message]
    parts, current = [], ""
    for block in message.split("\n\n"):
        candidate = block if not current else current + "\n\n" + block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            parts.append(current)
        while len(block) > limit:
            parts.append(block[:limit])
            block = block[limit:]
        current = block
    if current:
        parts.append(current)
    return parts


def send(text: str, *, parse_mode: str | None = None, token: str | None = None, chat_id: str | None = None,
         client: httpx.Client | None = None, retries: int = 3, backoff: float = 2.0) -> bool:
    """Send one message (split when long). True when every part was delivered.
    Never raises: a failed notification must not take the run down with it."""
    token = token if token is not None else os.getenv("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id if chat_id is not None else os.getenv("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        log.info("telegram not configured (TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID); message not sent:\n%s", text[:500])
        return False
    owns = client is None
    client = client or httpx.Client(timeout=httpx.Timeout(30.0, connect=5.0))
    try:
        return all(_send_part(client, token, chat_id, part, parse_mode, retries, backoff) for part in chunks(text))
    finally:
        if owns:
            client.close()


def _send_part(client: httpx.Client, token: str, chat_id: str, text: str, parse_mode: str | None,
               retries: int, backoff: float) -> bool:
    payload: dict = {"chat_id": chat_id, "text": text, "link_preview_options": {"is_disabled": True}}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    wait = backoff
    for attempt in range(1, retries + 1):
        try:
            r = client.post(f"{API}/bot{token}/sendMessage", json=payload)
        except httpx.TransportError as e:
            log.warning("telegram send attempt %d failed: %s", attempt, type(e).__name__)
        else:
            if r.status_code == 200:
                return True
            if r.status_code == 429:
                try:
                    wait = float(r.json().get("parameters", {}).get("retry_after", wait))
                except ValueError:
                    pass
                log.warning("telegram rate limited, retry in %.0f s", wait)
            elif r.status_code < 500:  # bad chat id or bad markup: retrying cannot help
                log.error("telegram rejected the message: HTTP %d %s", r.status_code, r.text[:200])
                return False
            else:
                log.warning("telegram HTTP %d on attempt %d", r.status_code, attempt)
        if attempt < retries:
            time.sleep(min(wait, 30.0))
            wait = min(wait * 2, 30.0)
    log.error("telegram send failed after %d attempts", retries)
    return False
