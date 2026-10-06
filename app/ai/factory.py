"""Provider selection from environment. Falls back to the mock provider when credentials are missing."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from app.ai.base import AIProvider
from app.ai.providers.anthropic_provider import AnthropicProvider
from app.ai.providers.mock_provider import MockProvider
from app.core.config import Settings, secret_value, settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ProviderSelection:
    provider: AIProvider
    configured: str
    fallback_reason: str | None


def build_provider(cfg: Settings) -> ProviderSelection:
    choice = cfg.AI_PROVIDER
    common = {
        "timeout": cfg.AI_TIMEOUT_SECONDS,
        "max_tokens": cfg.AI_MAX_TOKENS,
        "temperature": cfg.AI_TEMPERATURE,
    }
    if choice == "openai":
        key = secret_value(cfg.OPENAI_API_KEY)
        if key and cfg.OPENAI_MODEL and cfg.OPENAI_MODEL.strip():
            from app.ai.providers.openai_provider import OpenAIProvider

            return ProviderSelection(
                OpenAIProvider(key, cfg.OPENAI_MODEL.strip(), base_url=cfg.OPENAI_BASE_URL, **common),
                choice, None,
            )
        reason = "OPENAI_API_KEY and OPENAI_MODEL must both be set"
    elif choice == "anthropic":
        key = secret_value(cfg.ANTHROPIC_API_KEY)
        if key and cfg.ANTHROPIC_MODEL and cfg.ANTHROPIC_MODEL.strip():
            return ProviderSelection(
                AnthropicProvider(
                    key, cfg.ANTHROPIC_MODEL.strip(), base_url=cfg.ANTHROPIC_BASE_URL, **common
                ),
                choice, None,
            )
        reason = "ANTHROPIC_API_KEY and ANTHROPIC_MODEL must both be set"
    else:
        return ProviderSelection(MockProvider(), choice, None)
    logger.warning("AI_PROVIDER=%s but %s; using the mock provider", choice, reason)
    return ProviderSelection(MockProvider(), choice, reason)


@lru_cache
def get_provider_selection() -> ProviderSelection:
    return build_provider(settings)


def get_ai_provider() -> AIProvider:
    """FastAPI dependency (override in tests with app.dependency_overrides)."""
    return get_provider_selection().provider
