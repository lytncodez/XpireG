"""Insight and recommendation contracts.

Two kinds of schema live here:
  * AI OUTPUT schemas (Model*Output) — what the LLM must return. Strict: extra keys are
    rejected and every insight needs at least one piece of supporting evidence.
  * API schemas — what clients send and receive.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.alert import AlertSeverity
from app.models.insight import InsightCategory
from app.schemas import ORMModel


class EvidenceItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=200, description="What the value is")
    value: str | int | float | bool | None = Field(description="Copied verbatim from the verified context")
    source: str = Field(min_length=1, max_length=200, description="Context path, e.g. expiry_risks[0].units_at_risk")


# --------------------------------------------------------------------------- AI output (strict)


class ModelInsightOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    summary: str = Field(min_length=3, max_length=1000)
    explanation: str = Field(min_length=3, max_length=3000)
    severity: AlertSeverity
    category: InsightCategory
    supporting_evidence: list[EvidenceItem] = Field(min_length=1, max_length=20)
    recommendation: str = Field(min_length=3, max_length=1500)
    product_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None


class ModelInsightBatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    insights: list[ModelInsightOutput] = Field(default_factory=list, max_length=20)
    insufficient_evidence: bool = False
    notes: str | None = Field(default=None, max_length=1000)


class RecommendationPriority(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class ModelRecommendationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=3, max_length=200)
    action: str = Field(min_length=3, max_length=1000, description="A suggestion for a human; never an executed action")
    rationale: str = Field(min_length=3, max_length=2000)
    priority: RecommendationPriority
    category: InsightCategory
    supporting_evidence: list[EvidenceItem] = Field(min_length=1, max_length=20)
    product_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None


class ModelRecommendationBatchOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendations: list[ModelRecommendationOutput] = Field(default_factory=list, max_length=20)
    insufficient_evidence: bool = False
    notes: str | None = Field(default=None, max_length=1000)


# --------------------------------------------------------------------------- API


class InsightGenerateRequest(BaseModel):
    categories: list[InsightCategory] | None = Field(
        default=None, description="Limit generation to these categories (default: all)"
    )
    period_days: int = Field(default=30, ge=7, le=365, description="Analysis window for sales-based metrics")


class InsightRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    product_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    category: InsightCategory
    severity: AlertSeverity
    title: str
    summary: str
    explanation: str
    recommendation: str
    supporting_data: dict[str, Any]
    ai_provider: str
    ai_model: str
    created_at: datetime
    updated_at: datetime


class RejectedOutput(BaseModel):
    title: str | None
    reasons: list[str]


class InsightGenerateResponse(BaseModel):
    generation_status: Literal["SUCCESS", "PARTIAL", "REJECTED", "NO_EVIDENCE"] = Field(
        description="Generation outcome; HTTP 200 alone does not mean insights were saved"
    )
    provider: str
    model: str
    analysis_period: dict[str, Any]
    generated: list[InsightRead]
    rejected_count: int
    rejected: list[RejectedOutput] = Field(description="Outputs discarded by validation/guardrails (not stored)")
    insufficient_evidence: bool
    notes: str | None = None


class RecommendationRead(BaseModel):
    title: str
    action: str
    rationale: str
    priority: RecommendationPriority
    category: InsightCategory
    supporting_evidence: list[EvidenceItem]
    product_id: uuid.UUID | None = None
    batch_id: uuid.UUID | None = None


class RecommendationsResponse(BaseModel):
    provider: str
    model: str
    generated_at: datetime
    analysis_period: dict[str, Any]
    recommendations: list[RecommendationRead]
    rejected_count: int
    fallback_used: bool = Field(description="True when AI output failed validation and deterministic rules were used")
    insufficient_evidence: bool
    disclaimer: str
