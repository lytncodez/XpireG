from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from pydantic import BaseModel, Field


class InventoryMetrics(BaseModel):
    total_products: int
    total_units: int
    inventory_value: float
    retail_value: float
    expired_units: int
    critical_units: int
    expiring_units: int
    safe_units: int
    expired_value: float
    critical_value: float


class SalesMetrics(BaseModel):
    period_days: int
    revenue: float
    units_sold: int
    transactions: int
    previous_revenue: float
    revenue_growth_pct: float | None = Field(description="None when the previous period had no revenue")
    units_growth_pct: float | None


class OverviewResponse(BaseModel):
    as_of: date
    currency: str
    inventory: InventoryMetrics
    sales: SalesMetrics
    potential_waste_value: float
    open_alerts: int


class SeriesPoint(BaseModel):
    period: date
    revenue: float
    units_sold: int


class NamedAmount(BaseModel):
    id: uuid.UUID | None
    name: str
    units_sold: int
    revenue: float
    share_pct: float


class SalesAnalytics(BaseModel):
    date_from: date
    date_to: date
    revenue: float
    units_sold: int
    transactions: int
    average_daily_revenue: float
    growth_pct: float | None
    daily: list[SeriesPoint]
    weekly: list[SeriesPoint]
    monthly: list[SeriesPoint]
    by_product: list[NamedAmount]
    by_category: list[NamedAmount]


class ProductPerformance(BaseModel):
    product_id: uuid.UUID
    product_name: str
    sku: str | None
    category_name: str | None
    stock: int
    units_sold: int
    revenue: float
    daily_velocity: float = Field(description="Average units sold per day over the window")
    days_of_cover: float | None = Field(description="stock / daily_velocity; None if no sales")
    classification: str


class ExpiryRiskItem(BaseModel):
    batch_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    batch_number: str
    expiry_date: date
    days_remaining: int
    remaining_quantity: int
    projected_sales_before_expiry: float
    units_at_risk: int
    value_at_risk: float


class InventoryAnalytics(BaseModel):
    as_of: date
    metrics: InventoryMetrics
    status_breakdown: dict[str, int]
    stock_turnover: float | None = Field(description="COGS over window / current inventory cost value")
    annualized_turnover: float | None
    expiry_risk: list[ExpiryRiskItem]
    potential_waste_units: int
    potential_waste_value: float


class ProductAnalytics(BaseModel):
    window_days: int
    fast_movers: list[ProductPerformance]
    slow_movers: list[ProductPerformance]
    high_stock_low_sales: list[ProductPerformance]
    low_stock_high_sales: list[ProductPerformance]
    products: list[ProductPerformance]


class TrendsAnalytics(BaseModel):
    granularity: str
    points: list[SeriesPoint]
    moving_average_7d: list[dict[str, Any]]


class Anomaly(BaseModel):
    anomaly_type: str
    severity: str
    entity_type: str
    entity_id: uuid.UUID | None
    entity_name: str
    metric: str
    observed: float
    expected: float
    deviation_score: float | None = Field(description="z-score where applicable")
    pct_change: float | None
    window: str
    explanation: str


class AnomalyResponse(BaseModel):
    as_of: date
    method: str
    disclaimer: str
    anomalies: list[Anomaly]
