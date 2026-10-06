"""Alerts. Partial unique indexes guarantee at most one unresolved alert per (batch|product, type)."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.user import str_enum


class AlertType(str, enum.Enum):
    EXPIRING_SOON = "EXPIRING_SOON"
    CRITICAL_EXPIRY = "CRITICAL_EXPIRY"
    EXPIRED = "EXPIRED"
    LOW_STOCK = "LOW_STOCK"
    OVERSTOCK = "OVERSTOCK"
    UNUSUAL_STOCK = "UNUSUAL_STOCK"
    IMPORT_ERROR = "IMPORT_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"


class AlertSeverity(str, enum.Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"
    URGENT = "URGENT"


SEVERITY_RANK = {AlertSeverity.INFO: 0, AlertSeverity.WARNING: 1, AlertSeverity.CRITICAL: 2, AlertSeverity.URGENT: 3}
EXPIRY_ALERT_TYPES = (AlertType.EXPIRING_SOON, AlertType.CRITICAL_EXPIRY, AlertType.EXPIRED)


class Alert(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "alerts"
    __table_args__ = (
        Index(
            "uq_alerts_open_batch_type",
            "batch_id",
            "alert_type",
            unique=True,
            postgresql_where=text("is_resolved = false AND batch_id IS NOT NULL"),
        ),
        Index(
            "uq_alerts_open_product_type",
            "product_id",
            "alert_type",
            unique=True,
            postgresql_where=text("is_resolved = false AND batch_id IS NULL AND product_id IS NOT NULL"),
        ),
        Index("ix_alerts_company_open", "company_id", "is_resolved", "created_at"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("products.id", ondelete="CASCADE"))
    batch_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("batches.id", ondelete="CASCADE"))
    alert_type: Mapped[AlertType] = mapped_column(str_enum(AlertType, 32), nullable=False)
    severity: Mapped[AlertSeverity] = mapped_column(str_enum(AlertSeverity, 16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    days_remaining: Mapped[int | None] = mapped_column(Integer)
    quantity_at_risk: Mapped[int | None] = mapped_column(Integer)
    recipient_phone: Mapped[str | None] = mapped_column(String(32))
    is_read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    is_resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sms_sent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    sms_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sms_error: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
