"""OpenAI Chat Completions provider (HTTPS via httpx; no SDK dependency)."""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.base import AIProvider, AIProviderError, AIResponse, AITask
from app.ai.prompts import render_user_message


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
            "response_format": {"type": "json_object"},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                resp = await client.post(self.url, json=payload, headers={"Authorization": f"Bearer {self._api_key}"})
        except httpx.HTTPError as exc:
            raise AIProviderError(f"OpenAI request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise AIProviderError(f"OpenAI returned HTTP {resp.status_code}")
        try:
            data = resp.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AIProviderError("OpenAI returned an unexpected payload") from exc
        if not isinstance(text, str) or not text.strip():
            raise AIProviderError("OpenAI returned an empty response")
        return AIResponse(text=text, provider=self.name, model=data.get("model", self.model), usage=data.get("usage") or {})
