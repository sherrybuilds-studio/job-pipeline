"""pipeline.llm: message hygiene, provider selection, and the OpenAI-compatible
path's retry and cut-off handling on a mock transport. No network."""
import json

import httpx
import pytest

from pipeline import llm
from pipeline.llm import LLMError


def test_sanitize_drops_empty_and_non_text_messages():
    clean = llm.sanitize([{"role": "system", "content": "rules"}, {"role": "user", "content": None},
                          {"role": "user", "content": "  "}, "junk", {"content": "no role"},
                          {"role": "user", "content": 42}])
    assert clean == [{"role": "system", "content": "rules"}, {"role": "user", "content": "42"}]
    with pytest.raises(LLMError):
        llm.sanitize([{"role": "user", "content": ""}])


def test_split_system_joins_system_prompts():
    system, rest = llm.split_system([{"role": "system", "content": "a"}, {"role": "user", "content": "q"},
                                     {"role": "system", "content": "b"}])
    assert system == "a\n\nb" and rest == [{"role": "user", "content": "q"}]


def test_provider_defaults_to_anthropic_and_rejects_unknown(monkeypatch):
    assert llm.provider() == "anthropic"
    monkeypatch.setenv("LLM_PROVIDER", "carrier-pigeon")
    with pytest.raises(LLMError, match="LLM_PROVIDER"):
        llm.provider()


def test_anthropic_without_a_key_fails_before_any_request():
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        llm.chat([{"role": "user", "content": "hi"}])


def _openai(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    monkeypatch.setenv("LLM_MODEL", "some-model")
    monkeypatch.setenv("LLM_API_KEY", "test-key")


def _reply(text="Hello.", finish="stop"):
    return {"choices": [{"message": {"content": text}, "finish_reason": finish}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3}}


def test_openai_compatible_call_retries_a_429(monkeypatch):
    _openai(monkeypatch)
    monkeypatch.setattr(llm, "BACKOFF", (0.0, 0.0))
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(429, json={"error": "slow down"})
        return httpx.Response(200, json=_reply())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    reply, usage = llm.chat([{"role": "system", "content": "rules"}, {"role": "user", "content": "hi"}],
                            client=client)
    assert reply == "Hello." and usage["prompt_tokens"] == 7 and usage["billed"] == "openai-compatible"
    assert len(seen) == 2
    assert str(seen[0].url) == "https://llm.example/v1/chat/completions"
    assert seen[0].headers["authorization"] == "Bearer test-key"
    body = json.loads(seen[0].content)
    assert body["model"] == "some-model" and body["messages"][0]["role"] == "system"


def test_openai_compatible_call_does_not_retry_a_400(monkeypatch):
    _openai(monkeypatch)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(400, json={"error": "bad request"})

    with pytest.raises(LLMError):
        llm.chat([{"role": "user", "content": "hi"}], client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert len(calls) == 1


def test_a_reply_cut_off_at_the_token_cap_is_an_error(monkeypatch):
    _openai(monkeypatch)
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=_reply("Half a", "length"))))
    with pytest.raises(LLMError, match="cut off"):
        llm.chat([{"role": "user", "content": "hi"}], client=client)


def test_openai_provider_needs_a_base_url_and_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(LLMError, match="LLM_BASE_URL"):
        llm.chat([{"role": "user", "content": "hi"}])
    monkeypatch.setenv("LLM_BASE_URL", "https://llm.example/v1")
    with pytest.raises(LLMError, match="LLM_MODEL"):
        llm.chat([{"role": "user", "content": "hi"}])


def test_max_tokens_comes_from_the_environment(monkeypatch):
    assert llm.max_tokens_default() == llm.DEFAULT_MAX_TOKENS
    monkeypatch.setenv("LLM_MAX_TOKENS", "4000")
    assert llm.max_tokens_default() == 4000
    monkeypatch.setenv("LLM_MAX_TOKENS", "lots")
    with pytest.raises(LLMError):
        llm.max_tokens_default()
