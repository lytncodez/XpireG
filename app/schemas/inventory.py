from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.models.inventory_movement import MovementType
from app.schemas import ORMModel

ADJUSTABLE_TYPES = {
    MovementType.PURCHASE,
    MovementType.RETURN,
    MovementType.ADJUSTMENT,
    MovementType.EXPIRED,
    MovementType.DAMAGED,
    MovementType.TRANSFER,
}
OUTBOUND_ONLY = {MovementType.EXPIRED, MovementType.DAMAGED, MovementType.TRANSFER}
INBOUND_ONLY = {MovementType.PURCHASE, MovementType.RETURN}


class InventoryAdjustRequest(BaseModel):
    batch_id: uuid.UUID
    movement_type: MovementType = MovementType.ADJUSTMENT
    quantity_change: int = Field(description="Signed: positive adds stock, negative removes stock")
    notes: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_rules(self) -> "InventoryAdjustRequest":
        if self.quantity_change == 0:
            raise ValueError("quantity_change cannot be zero")
        if abs(self.quantity_change) > 1_000_000_000:
            raise ValueError("quantity_change is too large")
        if self.movement_type not in ADJUSTABLE_TYPES:
            raise ValueError("Use POST /sales to record sales")
        if self.movement_type in OUTBOUND_ONLY and self.quantity_change > 0:
            raise ValueError(f"{self.movement_type.value} movements must be negative")
        if self.movement_type in INBOUND_ONLY and self.quantity_change < 0:
            raise ValueError(f"{self.movement_type.value} movements must be positive")
        return self


class MovementRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    product_id: uuid.UUID
    batch_id: uuid.UUID | None
    movement_type: MovementType
    quantity: int
    reference_type: str | None
    reference_id: uuid.UUID | None
    notes: str | None
    created_at: datetime


class InventoryAdjustResponse(BaseModel):
    movement: MovementRead
    batch_id: uuid.UUID
    previous_quantity: int
    new_quantity: int
    batch_status: str


class InventoryItem(BaseModel):
    product_id: uuid.UUID
    product_name: str
    sku: str | None
    barcode: str | None
    category_id: uuid.UUID | None
    unit: str
    total_stock: int
    sellable_stock: int
    expired_stock: int
    batch_count: int
    nearest_expiry: date | None
    inventory_value: Decimal = Field(description="total_stock x cost_price")
    is_low_stock: bool


class InventorySummary(BaseModel):
    total_products: int
    products_in_stock: int
    low_stock_products: int
    total_units: int
    sellable_units: int
    expired_units: int
    critical_units: int
    expiring_soon_units: int
    safe_units: int
    inventory_value: Decimal
    retail_value: Decimal
    value_at_risk: Decimal = Field(description="Cost value of expired + critical units")
    currency: str
