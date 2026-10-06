from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.schemas.alerts import AlertRead
from app.schemas.analytics import ExpiryRiskItem, NamedAmount, SeriesPoint
from app.schemas.insights import InsightRead


class DashboardSummary(BaseModel):
    currency: str
    total_products: int
    total_stock: int
    expired_units: int
    expired_batches: int
    critical_alerts: int
    expiring_soon: int
    sales_today: int
    revenue_today: float
    sales_30d: int
    revenue_30d: float
    inventory_value: float
    top_products: list[NamedAmount]
    recent_alerts: list[AlertRead]


class DashboardAlerts(BaseModel):
    open_total: int
    unread_total: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    sms_sent: int
    sms_failed: int
    recent: list[AlertRead]


class DashboardSales(BaseModel):
    currency: str
    revenue_today: float
    revenue_7d: float
    revenue_30d: float
    units_30d: int
    growth_pct: float | None
    daily: list[SeriesPoint]
    top_products: list[NamedAmount]


class DashboardInventory(BaseModel):
    currency: str
    status_breakdown: dict[str, int]
    inventory_value: float
    value_at_risk: float
    expiring_next_30_days: list[ExpiryRiskItem]
    low_stock: list[dict[str, Any]]


class DashboardInsights(BaseModel):
    """Latest validated AI insights for the dashboard (Phase 2)."""

    total: int
    by_category: dict[str, int]
    by_severity: dict[str, int]
    latest: list[InsightRead]
    last_generated_at: str | None
