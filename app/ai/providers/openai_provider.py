"""OpenAI Chat Completions provider (HTTPS via httpx; no SDK dependency)."""

from __future__ import annotations

import asyncio
from typing import Any

import httpx

from app.ai.base import AIProvider, AIProviderError, AIResponse, AITask
from app.ai.prompts import render_user_message
from app.schemas.insights import ModelInsightBatchOutput


def _insight_response_format() -> dict[str, Any]:
    schema = ModelInsightBatchOutput.model_json_schema()

    def make_strict(node):
        if isinstance(node, dict):
            node.pop("default", None)
            if node.get("type") == "object":
                node["additionalProperties"] = False
                node["required"] = list(node.get("properties", {}))
            for value in node.values():
                make_strict(value)
        elif isinstance(node, list):
            for value in node:
                make_strict(value)

    make_strict(schema)
    return {"type": "json_schema", "json_schema": {"name": "insights", "strict": True, "schema": schema}}


class OpenAIProvider(AIProvider):
    name = "openai"

    def __init__(self, api_key: str, model: str, *, base_url: str, timeout: float, max_tokens: int,
                 temperature: float, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._transport = transport

    async def generate_response(self, context: dict[str, Any], prompt: str, *, system: str, task: AITask,
                                history: list[dict[str, str]] | None = None) -> AIResponse:
        messages = [{"role": "system", "content": system}]
        messages += [{"role": m["role"], "content": m["content"]} for m in (history or [])]
        messages.append({"role": "user", "content": render_user_message(prompt, context)})
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "response_format": (_insight_response_format() if task in (AITask.INSIGHTS, AITask.ANOMALY_EXPLANATION)
                                else {"type": "json_object"}),
        }
        # At most two attempts: each has the configured timeout; only timeouts are retried.
        async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
            for attempt in range(2):
                try:
                    async with asyncio.timeout(self.timeout):
                        resp = await client.post(self.url, json=payload, headers={"Authorization": f"Bearer {self._api_key}"})
                    break
                except (httpx.TimeoutException, TimeoutError) as exc:
                    if attempt == 1:
                        raise AIProviderError("OpenAI timed out after two attempts") from exc
                except httpx.HTTPError as exc:
                    raise AIProviderError(f"OpenAI request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise AIProviderError(f"OpenAI returned HTTP {resp.status_code}")
        try:
            data = resp.json()
            choice = data["choices"][0]
            text = choice["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("OpenAI returned an unexpected payload") from exc
        finish_reason = choice.get("finish_reason")
        if finish_reason not in (None, "stop"):
            raise AIProviderError(f"OpenAI response incomplete: finish_reason={finish_reason}")
        if choice["message"].get("refusal"):
            raise AIProviderError("OpenAI refused the request")
        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("OpenAI returned an empty response")
        return AIResponse(text=text, provider=self.name, model=data.get("model", self.model), usage=data.get("usage") or {})
