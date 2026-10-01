"""One chat call, used by the letter writer and the reviewer.

LLM_PROVIDER picks the backend:

  anthropic (default)  the Anthropic Messages API through the official SDK;
                       key in ANTHROPIC_API_KEY
  openai               any OpenAI-compatible Chat Completions endpoint over
                       HTTP; LLM_BASE_URL (for example a hosted router or a
                       local server) and LLM_API_KEY

LLM_MODEL names the model (anthropic default: claude-sonnet-5; the openai
provider needs it set). LLM_MAX_TOKENS caps a reply (default 16000).

chat() returns (reply, usage). Any failure, a refusal or a reply cut off at
the token cap raises LLMError, so a caller falls back instead of shipping half
a letter.
"""
from __future__ import annotations

import logging
import os
import time

import httpx

log = logging.getLogger(__name__)

PROVIDERS = ("anthropic", "openai")
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_MAX_TOKENS = 16000
RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}
BACKOFF = (2.0, 4.0)  # seconds before retry 1 and 2


class LLMError(RuntimeError):
    """The model call failed, was refused, or its reply was cut off."""


def provider() -> str:
    name = (os.getenv("LLM_PROVIDER") or "anthropic").strip().lower()
    if name not in PROVIDERS:
        raise LLMError(f"LLM_PROVIDER={name!r} is not one of {PROVIDERS}")
    return name


def max_tokens_default() -> int:
    raw = (os.getenv("LLM_MAX_TOKENS") or "").strip()
    try:
        return int(raw) if raw else DEFAULT_MAX_TOKENS
    except ValueError as e:
        raise LLMError(f"LLM_MAX_TOKENS={raw!r} is not a whole number") from e


def sanitize(messages) -> list[dict]:
    """Keep only {role, content} messages with non-empty text content."""
    if not isinstance(messages, (list, tuple)):
        raise LLMError("messages must be a list of {role, content} dicts")
    clean = []
    for m in messages:
        if not isinstance(m, dict) or not m.get("role") or m.get("content") is None:
            continue
        content = m["content"] if isinstance(m["content"], str) else str(m["content"])
        if content.strip():
            clean.append({"role": m["role"], "content": content})
    if not clean:
        raise LLMError("no message with text content to send")
    return clean


def split_system(messages: list[dict]) -> tuple[str, list[dict]]:
    """Anthropic takes the system prompt as a separate field."""
    system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
    return system, [m for m in messages if m["role"] != "system"]


def chat(messages, *, model: str | None = None, max_tokens: int | None = None, timeout: float = 120,
         agent: str = "", client: httpx.Client | None = None) -> tuple[str, dict]:
    """One completion from the configured provider. `agent` only labels the log line."""
    messages = sanitize(messages)
    max_tokens = max_tokens or max_tokens_default()
    name = provider()
    started = time.monotonic()
    if name == "anthropic":
        reply, usage = _chat_anthropic(messages, model or os.getenv("LLM_MODEL") or DEFAULT_ANTHROPIC_MODEL,
                                       max_tokens, timeout)
    else:
        reply, usage = _chat_openai(messages, model or os.getenv("LLM_MODEL") or "", max_tokens, timeout,
                                    client=client)
    log.info("llm %s via %s: %s in, %s out, %.1f s", agent or "call", name, usage.get("prompt_tokens"),
             usage.get("completion_tokens"), time.monotonic() - started)
    return reply, usage


def _chat_anthropic(messages: list[dict], model: str, max_tokens: int, timeout: float) -> tuple[str, dict]:
    try:
        import anthropic
    except ImportError as e:  # a declared dependency; a bare checkout may lack it
        raise LLMError("the anthropic package is not installed (pip install -e .)") from e
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise LLMError("ANTHROPIC_API_KEY is not set (LLM_PROVIDER=anthropic)")
    system, rest = split_system(messages)
    request = {"model": model, "max_tokens": max_tokens, "messages": rest}
    if system:
        request["system"] = system
    try:
        response = anthropic.Anthropic(timeout=timeout).messages.create(**request)
    except anthropic.APIStatusError as e:
        raise LLMError(f"Anthropic API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise LLMError(f"Anthropic API unreachable: {e}") from e
    if response.stop_reason == "refusal":
        raise LLMError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise LLMError(f"reply cut off at max_tokens={max_tokens}")
    reply = "".join(block.text for block in response.content if block.type == "text")
    usage = {"prompt_tokens": response.usage.input_tokens, "completion_tokens": response.usage.output_tokens,
             "billed": "anthropic"}
    return reply, usage


def _chat_openai(messages: list[dict], model: str, max_tokens: int, timeout: float,
                 client: httpx.Client | None = None) -> tuple[str, dict]:
    base = (os.getenv("LLM_BASE_URL") or "").rstrip("/")
    if not base:
        raise LLMError("LLM_BASE_URL is not set (LLM_PROVIDER=openai)")
    if not model:
        raise LLMError("LLM_MODEL is not set (LLM_PROVIDER=openai)")
    headers = {"Content-Type": "application/json"}
    if os.getenv("LLM_API_KEY"):
        headers["Authorization"] = f"Bearer {os.environ['LLM_API_KEY']}"
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    owns = client is None
    client = client or httpx.Client(timeout=timeout)
    try:
        data = _post_with_retry(client, f"{base}/chat/completions", headers, payload).json()
    except (httpx.HTTPError, ValueError) as e:
        raise LLMError(f"chat completion failed: {type(e).__name__}: {e}") from e
    finally:
        if owns:
            client.close()
    choice = (data.get("choices") or [{}])[0]
    if choice.get("finish_reason") == "length":
        raise LLMError(f"reply cut off at max_tokens={max_tokens}")
    reply = (choice.get("message") or {}).get("content") or ""
    if not reply.strip():
        raise LLMError("empty reply")
    u = data.get("usage") or {}
    return reply, {"prompt_tokens": u.get("prompt_tokens", 0), "completion_tokens": u.get("completion_tokens", 0),
                   "billed": "openai-compatible"}


def _post_with_retry(client: httpx.Client, url: str, headers: dict, payload: dict,
                     backoff: tuple[float, ...] = BACKOFF) -> httpx.Response:
    """POST with up to len(backoff) retries on timeouts, dropped connections, 429 and 5xx.
    Anything else (a 400 from a malformed request) fails on the first attempt."""
    for attempt in range(len(backoff) + 1):
        try:
            response = client.post(url, headers=headers, json=payload)
            if response.status_code not in RETRYABLE_STATUS:
                response.raise_for_status()
                return response
            reason = f"HTTP {response.status_code}"
            if attempt == len(backoff):
                response.raise_for_status()
        except httpx.TransportError as e:  # timeouts, refused or dropped connections
            if attempt == len(backoff):
                raise
            reason = type(e).__name__
        log.warning("llm call retry %d after %s", attempt + 1, reason)
        time.sleep(backoff[attempt])
    raise AssertionError("unreachable")  # pragma: no cover
