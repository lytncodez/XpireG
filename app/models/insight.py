"""AI-generated insights. Only output that passed schema validation and guardrails is stored."""

from __future__ import annotations

import enum
import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.alert import AlertSeverity
from app.models.user import str_enum


class InsightCategory(str, enum.Enum):
    EXPIRY = "EXPIRY"
    INVENTORY = "INVENTORY"
    SALES = "SALES"
    PRODUCT_PERFORMANCE = "PRODUCT_PERFORMANCE"
    ANOMALY = "ANOMALY"
    WASTE_RISK = "WASTE_RISK"
    STOCK_RISK = "STOCK_RISK"


class Insight(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "insights"
    __table_args__ = (
        Index("ix_insights_company_created", "company_id", "created_at"),
        Index("ix_insights_company_category", "company_id", "category"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="SET NULL"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("batches.id", ondelete="SET NULL"))
    category: Mapped[InsightCategory] = mapped_column(str_enum(InsightCategory, 32), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(str_enum(AlertSeverity, 16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[str] = mapped_column(Text, nullable=False)
    # {"evidence": [...], "analysis_period": {...}, "guardrails": {...}}
    supporting_data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ai_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    ai_model: Mapped[str] = mapped_column(String(100), nullable=False)
