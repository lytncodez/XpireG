from datetime import date

from fastapi import APIRouter, Query

from app.core.exceptions import error_responses
from app.dependencies.auth import StaffUser
from app.dependencies.database import DbSession
from app.schemas.analytics import (
    AnomalyResponse,
    InventoryAnalytics,
    OverviewResponse,
    ProductAnalytics,
    SalesAnalytics,
    TrendsAnalytics,
)
from app.services import analytics_service, anomaly_service

router = APIRouter(prefix="/analytics", tags=["Analytics"])

NOTE = " All figures are computed deterministically with SQL/Python."


@router.get("/overview", response_model=OverviewResponse, summary="Business overview",
            description="Inventory by expiry status and value, sales vs previous period, potential waste." + NOTE,
            responses=error_responses(401, 422))
async def overview(user: StaffUser, db: DbSession, days: int = Query(30, ge=1, le=365)) -> OverviewResponse:
    return await analytics_service.overview(db, user.company_id, days)


@router.get("/sales", response_model=SalesAnalytics, summary="Sales analytics",
            description="Revenue, units, growth, daily/weekly/monthly series, product and category breakdowns." + NOTE,
            responses=error_responses(400, 401, 422))
async def sales(
    user: StaffUser, db: DbSession, date_from: date | None = None, date_to: date | None = None
) -> SalesAnalytics:
    return await analytics_service.sales_analytics(db, user.company_id, date_from, date_to)


@router.get("/inventory", response_model=InventoryAnalytics, summary="Inventory analytics",
            description="Stock turnover, expiry risk (FEFO sell-through projection at current velocity) and "
                        "potential waste." + NOTE,
            responses=error_responses(401, 422))
async def inventory(user: StaffUser, db: DbSession, window_days: int = Query(30, ge=7, le=365)) -> InventoryAnalytics:
    return await analytics_service.inventory_analytics(db, user.company_id, window_days)


@router.get("/products", response_model=ProductAnalytics, summary="Product performance",
            description="Fast movers, slow movers, high-stock/low-sales and low-stock/high-sales products with "
                        "velocity and days of cover." + NOTE,
            responses=error_responses(401, 422))
async def products(user: StaffUser, db: DbSession, window_days: int = Query(30, ge=7, le=365)) -> ProductAnalytics:
    return await analytics_service.product_analytics(db, user.company_id, window_days)


@router.get("/trends", response_model=TrendsAnalytics, summary="Sales trends",
            description="Zero-filled series with a 7-day moving average for daily granularity." + NOTE,
            responses=error_responses(400, 401, 422))
async def trends(
    user: StaffUser, db: DbSession,
    granularity: str = Query("day", pattern="^(day|week|month)$"), periods: int = Query(30, ge=1, le=366),
) -> TrendsAnalytics:
    return await analytics_service.trends(db, user.company_id, granularity, periods)


@router.get("/anomalies", response_model=AnomalyResponse, summary="Anomaly detection",
            description="Explainable statistical anomalies: unusual sales increase/decline, unusually high/low "
                        "inventory and abnormal non-sale stock movement. Observations only; no causal claims.",
            responses=error_responses(401))
async def anomalies(user: StaffUser, db: DbSession) -> AnomalyResponse:
    return await anomaly_service.detect(db, user.company_id)
