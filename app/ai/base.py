"""Provider-neutral interface. Nothing outside app/ai/providers knows which vendor is used."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class AITask(str, Enum):
    INSIGHTS = "insights"
    RECOMMENDATIONS = "recommendations"
    CHAT = "chat"
    ANOMALY_EXPLANATION = "anomaly_explanation"


@dataclass(frozen=True)
class AIResponse:
    text: str
    provider: str
    model: str
    usage: dict[str, Any] = field(default_factory=dict)


class AIProviderError(Exception):
    """The provider could not produce a response (network, auth, rate limit, refusal...)."""


class AIProvider(ABC):
    name: str = "base"
    model: str = "unknown"

    @abstractmethod
    async def generate_response(
        self,
        context: dict[str, Any],
        prompt: str,
        *,
        system: str,
        task: AITask,
        history: list[dict[str, str]] | None = None,
    ) -> AIResponse:
        """Return raw model text (expected to be JSON). Must raise AIProviderError on failure."""
        raise NotImplementedError  # pragma: no cover - interface
