"""Insight generation: verified context -> AI -> schema validation -> guardrails -> store valid insights only."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.base import AIProvider, AIProviderError, AITask
from app.ai.context_builder import build_context, sections_for_categories
from app.ai.guardrails import AIOutputError, collect_facts, parse_json_object, validate_insight, validate_model
from app.ai.prompts import ANOMALY_EXPLANATION_PROMPT, SYSTEM_RULES, insight_prompt
from app.core.exceptions import AIUnavailableError
from app.core.logging import get_logger
from app.models import AuditAction, Insight, InsightCategory, User
from app.schemas.insights import InsightGenerateResponse, InsightRead, ModelInsightOutput, RejectedOutput
from app.services import audit_service

logger = get_logger(__name__)


async def generate_insights(
    db: AsyncSession,
    user: User,
    provider: AIProvider,
    *,
    categories: list[InsightCategory] | None = None,
    period_days: int = 30,
    ip: str | None = None,
) -> InsightGenerateResponse:
    cats = list(dict.fromkeys(categories or list(InsightCategory)))
    anomaly_only = cats == [InsightCategory.ANOMALY]
    ctx = await build_context(
        db, user.company_id, sections=sections_for_categories(cats), period_days=period_days, intent="INSIGHTS"
    )
    ctx["requested_categories"] = [c.value for c in cats]
    task = AITask.ANOMALY_EXPLANATION if anomaly_only else AITask.INSIGHTS
    prompt = ANOMALY_EXPLANATION_PROMPT if anomaly_only else insight_prompt([c.value for c in cats])

    try:
        response = await provider.generate_response(ctx, prompt, system=SYSTEM_RULES, task=task)
    except AIProviderError as exc:
        logger.warning("Insight generation provider failure: %s", exc)
        raise AIUnavailableError("The AI provider is unavailable; no insights were generated") from exc

    rejected: list[RejectedOutput] = []
    stored: list[Insight] = []
    insufficient = False
    notes: str | None = None
    try:
        data = parse_json_object(response.text)
        raw_items = data.get("insights")
        if not isinstance(raw_items, list):
            raise AIOutputError("'insights' must be a list")
        insufficient = bool(data.get("insufficient_evidence", False))
        notes = data.get("notes") if isinstance(data.get("notes"), str) else None
    except AIOutputError as exc:
        raw_items = []
        rejected.append(RejectedOutput(title=None, reasons=[str(exc)]))

    facts = collect_facts(ctx)
    generation_id = str(uuid.uuid4())
    for item in raw_items[:20]:
        title = item.get("title") if isinstance(item, dict) else None
        try:
            out: ModelInsightOutput = validate_model(item, ModelInsightOutput)  # type: ignore[assignment]
        except AIOutputError as exc:
            rejected.append(RejectedOutput(title=title, reasons=[str(exc)]))
            continue
        if out.category not in cats:
            rejected.append(RejectedOutput(title=out.title, reasons=[f"Category {out.category.value} was not requested"]))
            continue
        result = validate_insight(out, ctx, facts)
        if not result.ok:
            rejected.append(RejectedOutput(title=out.title, reasons=result.violations[:10]))
            continue
        insight = Insight(
            company_id=user.company_id,
            product_id=out.product_id,
            batch_id=out.batch_id,
            category=out.category,
            severity=out.severity,
            title=out.title,
            summary=out.summary,
            explanation=out.explanation,
            recommendation=out.recommendation,
            supporting_data={
                "evidence": [e.model_dump() for e in out.supporting_evidence],
                "analysis_period": ctx["analysis_period"],
                "as_of": ctx["business"]["as_of"],
                "generation_id": generation_id,
                "guardrails": result.as_dict(),
            },
            ai_provider=response.provider,
            ai_model=response.model[:100],
        )
        db.add(insight)
        stored.append(insight)

    await db.flush()
    audit_service.record(
        db, action=AuditAction.AI_INSIGHTS_GENERATED, company_id=user.company_id, user_id=user.id,
        entity_type="insight_generation", entity_id=generation_id,
        metadata={"provider": response.provider, "model": response.model, "stored": len(stored),
                  "rejected": len(rejected), "categories": [c.value for c in cats]},
        ip_address=ip,
    )
    if rejected:
        audit_service.record(
            db, action=AuditAction.AI_OUTPUT_REJECTED, company_id=user.company_id, user_id=user.id,
            entity_type="insight_generation", entity_id=generation_id,
            metadata={"rejected": [r.model_dump() for r in rejected[:10]]}, ip_address=ip,
        )
    await db.commit()
    for insight in stored:
        await db.refresh(insight)
    generation_status = ("PARTIAL" if rejected else "SUCCESS") if stored else ("REJECTED" if rejected else "NO_EVIDENCE")
    return InsightGenerateResponse(
        generation_status=generation_status,
        provider=response.provider,
        model=response.model,
        analysis_period=ctx["analysis_period"],
        generated=[InsightRead.model_validate(i) for i in stored],
        rejected_count=len(rejected),
        rejected=rejected,
        insufficient_evidence=insufficient or (not stored and not rejected),
        notes=notes,
    )


def summarize_for_context(insights: list[Insight]) -> list[dict[str, Any]]:
    """Validated insights passed back to the AI (e.g. for recommendations)."""
    return [
        {"id": str(i.id), "category": i.category.value, "severity": i.severity.value, "title": i.title,
         "summary": i.summary, "product_id": str(i.product_id) if i.product_id else None}
        for i in insights
    ]
