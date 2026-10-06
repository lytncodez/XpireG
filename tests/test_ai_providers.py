import json

import httpx
import pytest

from app.ai.base import AIProviderError, AITask
from app.ai.factory import build_provider
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.mock_provider import MockProvider
from app.ai.providers.openai_provider import OpenAIProvider
from app.core.config import Settings

SECRET = "x" * 48
CTX = {"intent": "GENERAL", "business": {"currency": "KES"}, "inventory": {"total_units": 10, "total_products": 2},
       "alerts": {"open_total": 1}}


def _settings(**kw) -> Settings:
    base = dict(JWT_SECRET_KEY=SECRET, OPENAI_API_KEY=None, OPENAI_MODEL=None, ANTHROPIC_API_KEY=None,
                ANTHROPIC_MODEL=None, _env_file=None)
    base.update(kw)
    return Settings(**base)


def test_default_provider_is_mock():
    sel = build_provider(_settings(AI_PROVIDER="mock"))
    assert isinstance(sel.provider, MockProvider) and sel.fallback_reason is None


@pytest.mark.parametrize("choice", ["openai", "anthropic"])
def test_missing_credentials_fall_back_to_mock(choice):
    sel = build_provider(_settings(AI_PROVIDER=choice))
    assert isinstance(sel.provider, MockProvider)
    assert sel.configured == choice and "must both be set" in sel.fallback_reason


def test_blank_key_counts_as_missing():
    sel = build_provider(_settings(AI_PROVIDER="openai", OPENAI_API_KEY="", OPENAI_MODEL="test-model"))
    assert isinstance(sel.provider, MockProvider) and sel.fallback_reason


def test_real_providers_selected_from_environment():
    sel = build_provider(_settings(AI_PROVIDER="openai", OPENAI_API_KEY="sk-test", OPENAI_MODEL="test-model"))
    assert isinstance(sel.provider, OpenAIProvider) and sel.provider.model == "test-model"
    sel = build_provider(_settings(AI_PROVIDER="anthropic", ANTHROPIC_API_KEY="ak-test", ANTHROPIC_MODEL="claude-test"))
    assert isinstance(sel.provider, AnthropicProvider) and sel.provider.model == "claude-test"


async def test_openai_provider_request_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "test-model", "choices": [{"message": {"content": '{"answer": "ok"}'}}]})

    p = OpenAIProvider("sk-test", "test-model", base_url="https://api.openai.test/v1", timeout=5, max_tokens=100,
                       temperature=0.1, transport=httpx.MockTransport(handler))
    resp = await p.generate_response(CTX, "PROMPT", system="RULES", task=AITask.CHAT,
                                     history=[{"role": "user", "content": "earlier"}])
    assert resp.text == '{"answer": "ok"}' and resp.provider == "openai"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["messages"][0] == {"role": "system", "content": "RULES"}
    assert body["messages"][1]["content"] == "earlier"
    assert "VERIFIED_CONTEXT" in body["messages"][-1]["content"]
    assert body["response_format"] == {"type": "json_object"}


async def test_anthropic_provider_request_and_parsing():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["headers"] = request.headers
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"model": "claude-test", "content": [{"type": "text", "text": '{"answer": "ok"}'}]})

    p = AnthropicProvider("ak-test", "claude-test", base_url="https://api.anthropic.test/v1", timeout=5,
                          max_tokens=100, temperature=0.1, transport=httpx.MockTransport(handler))
    resp = await p.generate_response(CTX, "PROMPT", system="RULES", task=AITask.CHAT)
    assert resp.text == '{"answer": "ok"}' and resp.provider == "anthropic"
    assert seen["headers"]["x-api-key"] == "ak-test" and seen["headers"]["anthropic-version"]
    assert seen["body"]["system"] == "RULES"


@pytest.mark.parametrize("status", [401, 429, 500])
async def test_provider_http_errors_raise_provider_error(status):
    transport = httpx.MockTransport(lambda r: httpx.Response(status, json={"error": "x"}))
    p = OpenAIProvider("sk-test", "m", base_url="https://x.test/v1", timeout=5, max_tokens=10, temperature=0,
                       transport=transport)
    with pytest.raises(AIProviderError) as exc:
        await p.generate_response(CTX, "P", system="S", task=AITask.CHAT)
    assert "sk-test" not in str(exc.value)


async def test_mock_provider_is_deterministic_and_context_based():
    p = MockProvider()
    a = await p.generate_response(CTX, "P", system="S", task=AITask.CHAT)
    b = await p.generate_response(CTX, "P", system="S", task=AITask.CHAT)
    assert a.text == b.text
    assert "10 units in stock across 2 products" in json.loads(a.text)["answer"]


async def test_status_endpoint_never_exposes_credentials(client, admin):
    resp = await client.get("/ai/status", headers=admin["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["provider"] == "mock" and body["is_mock"] is True
    assert "key" not in json.dumps(body).lower()
