"""API routers. Each router is thin: validate input, call a service, shape the response."""

from fastapi import APIRouter

from app.routes import (
    ai,
    alerts,
    analytics,
    auth,
    barcode,
    batches,
    categories,
    companies,
    dashboard,
    expiry,
    health,
    imports,
    insights,
    inventory,
    notifications,
    products,
    sales,
    users,
)

api_router = APIRouter()
for module in (
    health, auth, users, companies, categories, products, batches, inventory, sales, imports,
    barcode, expiry, alerts, notifications, analytics, dashboard, insights, ai,
):
    api_router.include_router(module.router)
