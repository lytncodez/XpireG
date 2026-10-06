"""Anthropic Messages API provider (HTTPS via httpx; no SDK dependency)."""

from __future__ import annotations

from typing import Any

import httpx

from app.ai.base import AIProvider, AIProviderError, AIResponse, AITask
from app.ai.prompts import render_user_message

ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str, *, base_url: str, timeout: float, max_tokens: int,
                 temperature: float, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._api_key = api_key
        self.model = model
        self.url = base_url.rstrip("/") + "/messages"
        self.timeout = timeout
        self.max_tokens = max_tokens
        self.temperature = temperature
        self._transport = transport

    async def generate_response(self, context: dict[str, Any], prompt: str, *, system: str, task: AITask,
                                history: list[dict[str, str]] | None = None) -> AIResponse:
        messages = [{"role": m["role"], "content": m["content"]} for m in (history or [])]
        messages.append({"role": "user", "content": render_user_message(prompt, context)})
        payload = {
            "model": self.model,
            "system": system,
            "messages": messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
        }
        headers = {"x-api-key": self._api_key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                resp = await client.post(self.url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            raise AIProviderError(f"Anthropic request failed: {type(exc).__name__}") from exc
        if resp.status_code >= 400:
            raise AIProviderError(f"Anthropic returned HTTP {resp.status_code}")
        try:
            data = resp.json()
            text = "".join(block.get("text", "") for block in data["content"] if block.get("type") == "text")
        except (ValueError, KeyError, TypeError) as exc:
            raise AIProviderError("Anthropic returned an unexpected payload") from exc
        if not text.strip():
            raise AIProviderError("Anthropic returned an empty response")
        return AIResponse(text=text, provider=self.name, model=data.get("model", self.model), usage=data.get("usage") or {})
