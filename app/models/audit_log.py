"""Append-only audit trail."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import ForeignKey, Index, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin


class AuditAction:
    COMPANY_REGISTERED = "COMPANY_REGISTERED"
    COMPANY_UPDATED = "COMPANY_UPDATED"
    USER_LOGIN = "USER_LOGIN"
    USER_LOGOUT = "USER_LOGOUT"
    USER_CREATED = "USER_CREATED"
    USER_UPDATED = "USER_UPDATED"
    PASSWORD_RESET_REQUESTED = "PASSWORD_RESET_REQUESTED"
    PASSWORD_RESET = "PASSWORD_RESET"
    CATEGORY_CREATED = "CATEGORY_CREATED"
    CATEGORY_UPDATED = "CATEGORY_UPDATED"
    CATEGORY_DELETED = "CATEGORY_DELETED"
    PRODUCT_CREATED = "PRODUCT_CREATED"
    PRODUCT_UPDATED = "PRODUCT_UPDATED"
    PRODUCT_DELETED = "PRODUCT_DELETED"
    BATCH_CREATED = "BATCH_CREATED"
    BATCH_UPDATED = "BATCH_UPDATED"
    BATCH_DELETED = "BATCH_DELETED"
    INVENTORY_ADJUSTED = "INVENTORY_ADJUSTED"
    SALE_CREATED = "SALE_CREATED"
    IMPORT_STARTED = "IMPORT_STARTED"
    IMPORT_COMPLETED = "IMPORT_COMPLETED"
    IMPORT_FAILED = "IMPORT_FAILED"
    EXPIRY_CHECK_RUN = "EXPIRY_CHECK_RUN"
    ALERT_CREATED = "ALERT_CREATED"
    ALERT_RESOLVED = "ALERT_RESOLVED"
    SMS_SENT = "SMS_SENT"
    SMS_FAILED = "SMS_FAILED"
    AI_INSIGHTS_GENERATED = "AI_INSIGHTS_GENERATED"
    AI_OUTPUT_REJECTED = "AI_OUTPUT_REJECTED"
    AI_CHAT_MESSAGE = "AI_CHAT_MESSAGE"


class AuditLog(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_logs_company_created", "company_id", "created_at"),)

    company_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    # "metadata" is reserved on declarative classes, so the attribute is `meta`.
    meta: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONB)
    ip_address: Mapped[str | None] = mapped_column(String(45))
