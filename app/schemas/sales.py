from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from app.schemas import ORMModel


class SaleCreate(BaseModel):
    product_id: uuid.UUID
    quantity: int = Field(gt=0, le=1_000_000)
    batch_id: uuid.UUID | None = Field(
        default=None, description="Omit to allocate automatically First-Expired-First-Out"
    )
    unit_price: Decimal | None = Field(
        default=None, ge=0, max_digits=12, decimal_places=2, description="Defaults to product selling price"
    )
    sold_at: datetime | None = Field(default=None, description="Defaults to now; cannot be in the future")


class SaleRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None = None
    batch_id: uuid.UUID | None
    batch_number: str | None = None
    quantity: int
    unit_price: Decimal
    total_amount: Decimal
    sold_at: datetime
    created_at: datetime


class SaleCreateResult(BaseModel):
    sales: list[SaleRead] = Field(description="One record per batch allocation (FEFO may split a sale)")
    total_quantity: int
    total_amount: Decimal


class TopProduct(BaseModel):
    product_id: uuid.UUID
    product_name: str
    units_sold: int
    revenue: Decimal


class SalesSummary(BaseModel):
    date_from: date
    date_to: date
    revenue: Decimal
    units_sold: int
    transactions: int
    average_sale_value: Decimal
    gross_profit: Decimal = Field(description="Revenue minus units x current cost price (estimate)")
    top_products: list[TopProduct]
    currency: str


class TrendPoint(BaseModel):
    period: date
    revenue: Decimal
    units_sold: int
    transactions: int


class SalesTrends(BaseModel):
    granularity: str
    points: list[TrendPoint]
