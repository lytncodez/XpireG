"""Immutable ledger of every stock change."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import ForeignKey, Index, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, CreatedAtMixin, UUIDPrimaryKeyMixin
from app.models.user import str_enum


class MovementType(str, enum.Enum):
    PURCHASE = "PURCHASE"
    SALE = "SALE"
    RETURN = "RETURN"
    ADJUSTMENT = "ADJUSTMENT"
    EXPIRED = "EXPIRED"
    DAMAGED = "DAMAGED"
    TRANSFER = "TRANSFER"


class InventoryMovement(UUIDPrimaryKeyMixin, CreatedAtMixin, Base):
    """`quantity` is signed: positive = stock in, negative = stock out."""

    __tablename__ = "inventory_movements"
    __table_args__ = (
        Index("ix_inventory_movements_company_created", "company_id", "created_at"),
        Index("ix_inventory_movements_reference", "reference_type", "reference_id"),
    )

    company_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    product_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("products.id", ondelete="CASCADE"), nullable=False, index=True
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("batches.id", ondelete="SET NULL"), index=True
    )
    movement_type: Mapped[MovementType] = mapped_column(str_enum(MovementType, 20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(50))
    reference_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    notes: Mapped[str | None] = mapped_column(Text)
