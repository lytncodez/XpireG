from fastapi import APIRouter

from app.core.exceptions import error_responses
from app.dependencies.auth import StaffUser
from app.dependencies.database import DbSession
from app.schemas.dashboard import (
    DashboardAlerts,
    DashboardInsights,
    DashboardInventory,
    DashboardSales,
    DashboardSummary,
)
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


@router.get("/summary", response_model=DashboardSummary, summary="Dashboard summary",
            description="Headline KPIs: products, stock, expired, critical alerts, expiring soon, sales, revenue, "
                        "inventory value, top products and recent alerts.",
            responses=error_responses(401))
async def summary(user: StaffUser, db: DbSession) -> DashboardSummary:
    return await dashboard_service.summary(db, user.company_id)


@router.get("/alerts", response_model=DashboardAlerts, summary="Alerts panel",
            description="Open alerts by type and severity, unread count, SMS delivery totals, recent alerts.",
            responses=error_responses(401))
async def alerts(user: StaffUser, db: DbSession) -> DashboardAlerts:
    return await dashboard_service.alerts(db, user.company_id)


@router.get("/sales", response_model=DashboardSales, summary="Sales panel",
            description="Revenue today / 7d / 30d, growth vs previous 30 days, daily series, top products.",
            responses=error_responses(401))
async def sales(user: StaffUser, db: DbSession) -> DashboardSales:
    return await dashboard_service.sales(db, user.company_id)


@router.get("/inventory", response_model=DashboardInventory, summary="Inventory panel",
            description="Units by expiry status, value at risk, batches expiring within 30 days, low-stock list.",
            responses=error_responses(401))
async def inventory(user: StaffUser, db: DbSession) -> DashboardInventory:
    return await dashboard_service.inventory(db, user.company_id)


@router.get("/insights", response_model=DashboardInsights, summary="AI insights panel",
            description="Validated AI insights from the last 7 days by category and severity, plus the latest ones.",
            responses=error_responses(401))
async def insights(user: StaffUser, db: DbSession) -> DashboardInsights:
    return await dashboard_service.insights(db, user.company_id)
