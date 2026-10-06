import uuid

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select

from app.ai.base import AIProvider
from app.ai.factory import get_ai_provider
from app.ai.insight_generator import generate_insights
from app.ai.recommendation_generator import generate_recommendations
from app.core.config import settings
from app.core.exceptions import NotFoundError, error_responses
from app.dependencies.auth import ManagerUser, StaffUser, rate_limit
from app.dependencies.database import DbSession
from app.models import AlertSeverity, Insight, InsightCategory
from app.schemas import Page
from app.schemas.insights import (
    InsightGenerateRequest,
    InsightGenerateResponse,
    InsightRead,
    RecommendationsResponse,
)
from app.utils.helpers import client_ip
from app.utils.pagination import PageParams, page_meta, page_params, paginate

router = APIRouter(prefix="/insights", tags=["AI insights"])
AI_LIMIT = Depends(rate_limit("ai", settings.AI_RATE_LIMIT_PER_MINUTE))


@router.post(
    "/generate", response_model=InsightGenerateResponse, summary="Generate AI insights",
    description="Builds a verified context from deterministic analytics, anomaly detection and alerts, asks the "
                "configured AI provider for insights, validates each against the schema and guardrails (no invented "
                "numbers, dates, products or ids; evidence must match the context; no unhedged causal claims) and "
                "stores only valid insights. Rejected outputs are reported, never stored. MANAGER or ADMIN.",
    responses=error_responses(401, 403, 422, 429, 503), dependencies=[AI_LIMIT],
)
async def generate(
    data: InsightGenerateRequest, user: ManagerUser, request: Request, db: DbSession,
    provider: AIProvider = Depends(get_ai_provider),
) -> InsightGenerateResponse:
    return await generate_insights(
        db, user, provider, categories=data.categories, period_days=data.period_days, ip=client_ip(request)
    )


@router.get(
    "", response_model=Page[InsightRead], summary="List insights",
    description="Stored, validated insights for your company, newest first.", responses=error_responses(401, 422),
)
async def list_insights(
    user: StaffUser, db: DbSession, params: PageParams = Depends(page_params),
    category: InsightCategory | None = None, severity: AlertSeverity | None = None,
    product_id: uuid.UUID | None = None,
) -> Page[InsightRead]:
    stmt = select(Insight).where(Insight.company_id == user.company_id)
    if category:
        stmt = stmt.where(Insight.category == category)
    if severity:
        stmt = stmt.where(Insight.severity == severity)
    if product_id:
        stmt = stmt.where(Insight.product_id == product_id)
    items, total = await paginate(db, stmt.order_by(Insight.created_at.desc()), params)
    return Page(items=[InsightRead.model_validate(i) for i in items], **page_meta(total, params))


@router.get(
    "/recommendations", response_model=RecommendationsResponse, summary="AI recommendations",
    description="Prioritised, evidence-grounded suggestions for today, built from the verified context plus "
                "insights from the last 7 days. Nothing is executed automatically. If AI output fails validation, "
                "deterministic rules produce the list (fallback_used=true). MANAGER or ADMIN.",
    responses=error_responses(401, 403, 422, 429), dependencies=[AI_LIMIT],
)
async def recommendations(
    user: ManagerUser, db: DbSession, provider: AIProvider = Depends(get_ai_provider),
    period_days: int = Query(30, ge=7, le=365), limit: int = Query(8, ge=1, le=20),
) -> RecommendationsResponse:
    return await generate_recommendations(db, user, provider, period_days=period_days, limit=limit)


@router.get(
    "/{insight_id}", response_model=InsightRead, summary="Get insight",
    description="One insight with its supporting evidence and guardrail record.", responses=error_responses(401, 404),
)
async def get_insight(insight_id: uuid.UUID, user: StaffUser, db: DbSession) -> InsightRead:
    insight = await db.scalar(select(Insight).where(Insight.id == insight_id, Insight.company_id == user.company_id))
    if insight is None:
        raise NotFoundError("Insight not found")
    return InsightRead.model_validate(insight)
