"""Recommendations: verified context + recent validated insights -> AI -> validation -> guardrails.

Recommendations are suggestions for a person to review. Nothing is executed automatically. If the AI
output fails validation entirely, deterministic rule-based recommendations (same evidence rules) are
returned and flagged with fallback_used=True.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIProvider, AIProviderError, AITask
from app.ai.context_builder import ALL_SECTIONS, build_context
from app.ai.guardrails import (
    AIOutputError,
    collect_facts,
    parse_json_object,
    validate_model,
    validate_recommendation,
)
from app.ai.insight_generator import summarize_for_context
from app.ai.prompts import RECOMMENDATION_PROMPT, SYSTEM_RULES
from app.ai.providers.mock_provider import build_recommendations
from app.core.logging import get_logger
from app.models import Insight, User
from app.schemas.insights import ModelRecommendationOutput, RecommendationRead, RecommendationsResponse
from app.utils.dates import utcnow

logger = get_logger(__name__)

DISCLAIMER = ("Recommendations are suggestions grounded in ExpireGuard's verified data. They are not executed "
              "automatically and do not establish the causes of any change.")
PRIORITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def _validate_batch(raw_items: list, ctx: dict, facts) -> tuple[list[RecommendationRead], int]:
    valid: list[RecommendationRead] = []
    rejected = 0
    for item in raw_items[:20]:
        try:
            out: ModelRecommendationOutput = validate_model(item, ModelRecommendationOutput)  # type: ignore[assignment]
        except AIOutputError:
            rejected += 1
            continue
        result = validate_recommendation(out, ctx, facts)
        if not result.ok:
            logger.info("Recommendation rejected: %s", result.violations[:3])
            rejected += 1
            continue
        valid.append(RecommendationRead(**out.model_dump()))
    return valid, rejected


async def generate_recommendations(
    db: AsyncSession, user: User, provider: AIProvider, *, period_days: int = 30, limit: int = 8
) -> RecommendationsResponse:
    ctx = await build_context(db, user.company_id, sections=ALL_SECTIONS, period_days=period_days, intent="PRIORITIES")
    recent = (
        await db.scalars(
            select(Insight)
            .where(Insight.company_id == user.company_id, Insight.created_at >= utcnow() - timedelta(days=7))
            .order_by(Insight.created_at.desc())
            .limit(20)
        )
    ).all()
    ctx["insights"] = summarize_for_context(list(recent))
    facts = collect_facts(ctx)

    provider_name, model_name = provider.name, provider.model
    valid: list[RecommendationRead] = []
    rejected = 0
    insufficient = False
    ai_failed = False
    try:
        response = await provider.generate_response(ctx, RECOMMENDATION_PROMPT, system=SYSTEM_RULES,
                                                    task=AITask.RECOMMENDATIONS)
        provider_name, model_name = response.provider, response.model
        data = parse_json_object(response.text)
        raw = data.get("recommendations")
        if not isinstance(raw, list):
            raise AIOutputError("'recommendations' must be a list")
        insufficient = bool(data.get("insufficient_evidence", False))
        valid, rejected = _validate_batch(raw, ctx, facts)
        ai_failed = not valid and rejected > 0
    except (AIProviderError, AIOutputError) as exc:
        logger.warning("Recommendation generation fell back to rules: %s", exc)
        ai_failed = True
        rejected += 1

    fallback_used = False
    if ai_failed:
        fallback, _ = _validate_batch(build_recommendations(ctx)["recommendations"], ctx, facts)
        valid, fallback_used = fallback, True
        insufficient = not fallback

    valid.sort(key=lambda r: PRIORITY_ORDER[r.priority.value])
    return RecommendationsResponse(
        provider=provider_name,
        model=model_name,
        generated_at=utcnow(),
        analysis_period=ctx["analysis_period"],
        recommendations=valid[:limit],
        rejected_count=rejected,
        fallback_used=fallback_used,
        insufficient_evidence=insufficient or not valid,
        disclaimer=DISCLAIMER,
    )
