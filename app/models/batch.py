"""Batches (lots) of a product, each with its own expiry date and quantity."""

from __future__ import annotations

import enum
import uuid
from datetime import date

from sqlalchemy import CheckConstraint, Date, ForeignKey, Index, Integer, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.user import str_enum


class BatchStatus(str, enum.Enum):
    SAFE = "SAFE"
    EXPIRING_SOON = "EXPIRING_SOON"
    CRITICAL = "CRITICAL"
    EXPIRED = "EXPIRED"
    DEPLETED = "DEPLETED"


class Batch(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "batches"
    __table_args__ = (
        UniqueConstraint("product_id", "batch_number", name="uq_batches_product_batch_number"),
        CheckConstraint("initial_quantity >= 0", name="initial_quantity_non_negative"),
        CheckConstraint(
            "manufacturing_date IS NULL OR expiry_date >= manufacturing_date", name="expiry_after_manufacturing"
        ),
        Index("ix_batches_company_status", "company_id", "status"),
        Index("ix_batches_company_expiry", "company_id", "expiry_date"),
    )

    # company_id is denormalised from product for strict, cheap tenant isolation.
    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_number: Mapped[str] = mapped_column(String(64), nullable=False)
    manufacturing_date: Mapped[date | None] = mapped_column(Date)
    expiry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    initial_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    remaining_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[BatchStatus] = mapped_column(str_enum(BatchStatus, 20), nullable=False, default=BatchStatus.SAFE)
