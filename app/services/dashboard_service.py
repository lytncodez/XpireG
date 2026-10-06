"""Frontend-ready dashboard aggregates built from the analytics, alert and inventory services."""

from __future__ import annotations

import uuid
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Alert, AlertSeverity, AlertType, Batch, Insight, Notification, NotificationStatus, Product
from app.schemas.alerts import AlertRead
from app.schemas.analytics import ExpiryRiskItem, NamedAmount
from app.schemas.dashboard import (
    DashboardAlerts,
    DashboardInsights,
    DashboardInventory,
    DashboardSales,
    DashboardSummary,
)
from app.schemas.insights import InsightRead
from app.services import analytics_service, inventory_service
from app.services.analytics_service import _context, _series, count_active_products, load_batches, load_sales
from app.utils.dates import local_date, utcnow
from app.utils.helpers import pct_change, to_float
from app.utils.pagination import PageParams


def _top_products(sales, limit: int = 5) -> list[NamedAmount]:
    agg: dict[uuid.UUID, list] = {}
    names: dict[uuid.UUID, str] = {}
    total = Decimal("0")
    for pid, name, _, qty, amount, _, _ in sales:
        entry = agg.setdefault(pid, [0, Decimal("0")])
        entry[0] += qty
        entry[1] += amount
        names[pid] = name
        total += amount
    ordered = sorted(agg.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]
    return [
        NamedAmount(id=pid, name=names[pid], units_sold=v[0], revenue=to_float(v[1]),
                    share_pct=round(float(v[1] / total * 100), 2) if total else 0.0)
        for pid, v in ordered
    ]


async def _recent_alerts(db: AsyncSession, company_id: uuid.UUID, limit: int = 10) -> list[AlertRead]:
    rows = (
        await db.scalars(
            select(Alert)
            .where(Alert.company_id == company_id, Alert.is_resolved.is_(False))
            .order_by(Alert.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [AlertRead.model_validate(a) for a in rows]


async def summary(db: AsyncSession, company_id: uuid.UUID) -> DashboardSummary:
    company, today = await _context(db, company_id)
    batches = await load_batches(db, company_id)
    metrics = analytics_service.inventory_metrics(batches, today, await count_active_products(db, company_id))
    sales_30 = await load_sales(db, company, today - timedelta(days=29), today)
    sales_today = [r for r in sales_30 if local_date(r[6], company.timezone) == today]
    open_counts = dict(
        (await db.execute(
            select(Alert.alert_type, func.count())
            .where(Alert.company_id == company_id, Alert.is_resolved.is_(False))
            .group_by(Alert.alert_type)
        )).all()
    )
    expired_batches = sum(1 for b in batches if b.expiry_date <= today)
    return DashboardSummary(
        currency=company.currency,
        total_products=metrics.total_products,
        total_stock=metrics.total_units,
        expired_units=metrics.expired_units,
        expired_batches=expired_batches,
        critical_alerts=int(open_counts.get(AlertType.CRITICAL_EXPIRY, 0)) + int(open_counts.get(AlertType.EXPIRED, 0)),
        expiring_soon=metrics.expiring_units + metrics.critical_units,
        sales_today=sum(r[3] for r in sales_today),
        revenue_today=to_float(sum((r[4] for r in sales_today), Decimal("0"))),
        sales_30d=sum(r[3] for r in sales_30),
        revenue_30d=to_float(sum((r[4] for r in sales_30), Decimal("0"))),
        inventory_value=metrics.inventory_value,
        top_products=_top_products(sales_30),
        recent_alerts=await _recent_alerts(db, company_id, 5),
    )


async def alerts(db: AsyncSession, company_id: uuid.UUID) -> DashboardAlerts:
    base = (Alert.company_id == company_id, Alert.is_resolved.is_(False))
    by_type = {
        t.value: int(c) for t, c in (
            await db.execute(select(Alert.alert_type, func.count()).where(*base).group_by(Alert.alert_type))
        ).all()
    }
    by_sev = {
        s.value: int(c) for s, c in (
            await db.execute(select(Alert.severity, func.count()).where(*base).group_by(Alert.severity))
        ).all()
    }
    for s in AlertSeverity:
        by_sev.setdefault(s.value, 0)
    unread = await db.scalar(select(func.count()).select_from(Alert).where(*base, Alert.is_read.is_(False)))
    notif = dict(
        (await db.execute(
            select(Notification.status, func.count())
            .where(Notification.company_id == company_id)
            .group_by(Notification.status)
        )).all()
    )
    return DashboardAlerts(
        open_total=sum(by_type.values()),
        unread_total=int(unread or 0),
        by_type=by_type,
        by_severity=by_sev,
        sms_sent=int(notif.get(NotificationStatus.SENT, 0)),
        sms_failed=int(notif.get(NotificationStatus.FAILED, 0)),
        recent=await _recent_alerts(db, company_id, 10),
    )


async def sales(db: AsyncSession, company_id: uuid.UUID) -> DashboardSales:
    company, today = await _context(db, company_id)
    current = await load_sales(db, company, today - timedelta(days=29), today)
    previous = await load_sales(db, company, today - timedelta(days=59), today - timedelta(days=30))
    rev = lambda rows: float(sum((r[4] for r in rows), Decimal("0")))  # noqa: E731
    week_start = today - timedelta(days=6)
    return DashboardSales(
        currency=company.currency,
        revenue_today=round(rev([r for r in current if local_date(r[6], company.timezone) == today]), 2),
        revenue_7d=round(rev([r for r in current if local_date(r[6], company.timezone) >= week_start]), 2),
        revenue_30d=round(rev(current), 2),
        units_30d=sum(r[3] for r in current),
        growth_pct=pct_change(rev(current), rev(previous)),
        daily=_series(current, today - timedelta(days=29), today, "day", company.timezone),
        top_products=_top_products(current, 10),
    )


async def inventory(db: AsyncSession, company_id: uuid.UUID) -> DashboardInventory:
    company, today = await _context(db, company_id)
    analytics = await analytics_service.inventory_analytics(db, company_id)
    soon = [i for i in analytics.expiry_risk if 0 < i.days_remaining <= settings.EXPIRY_CRITICAL_DAYS]
    if not soon:
        # Show everything expiring in 30 days even if projected to sell through.
        rows = (
            await db.execute(
                select(Batch, Product.name, Product.cost_price)
                .join(Product, Product.id == Batch.product_id)
                .where(
                    Batch.company_id == company_id, Batch.remaining_quantity > 0, Product.is_active.is_(True),
                    Batch.expiry_date > today, Batch.expiry_date <= today + timedelta(days=30),
                )
                .order_by(Batch.expiry_date)
                .limit(50)
            )
        ).all()
        soon = [
            ExpiryRiskItem(
                batch_id=b.id, product_id=b.product_id, product_name=name, batch_number=b.batch_number,
                expiry_date=b.expiry_date, days_remaining=(b.expiry_date - today).days,
                remaining_quantity=b.remaining_quantity, projected_sales_before_expiry=0.0,
                units_at_risk=0, value_at_risk=0.0,
            )
            for b, name, _ in rows
        ]
    low_items, _ = await inventory_service.list_inventory(
        db, company_id, PageParams(page=1, page_size=20), low_stock_only=True
    )
    return DashboardInventory(
        currency=company.currency,
        status_breakdown=analytics.status_breakdown,
        inventory_value=analytics.metrics.inventory_value,
        value_at_risk=round(analytics.metrics.expired_value + analytics.metrics.critical_value, 2),
        expiring_next_30_days=soon[:50],
        low_stock=[
            {"product_id": str(i.product_id), "product_name": i.product_name, "sellable_stock": i.sellable_stock,
             "threshold": settings.LOW_STOCK_THRESHOLD}
            for i in low_items
        ],
    )


async def insights(db: AsyncSession, company_id: uuid.UUID) -> DashboardInsights:
    since = utcnow() - timedelta(days=7)
    base = (Insight.company_id == company_id, Insight.created_at >= since)
    by_cat = {c.value: int(n) for c, n in (await db.execute(
        select(Insight.category, func.count()).where(*base).group_by(Insight.category))).all()}
    by_sev = {s.value: int(n) for s, n in (await db.execute(
        select(Insight.severity, func.count()).where(*base).group_by(Insight.severity))).all()}
    latest = (await db.scalars(select(Insight).where(*base).order_by(Insight.created_at.desc()).limit(10))).all()
    return DashboardInsights(
        total=sum(by_cat.values()),
        by_category=by_cat,
        by_severity=by_sev,
        latest=[InsightRead.model_validate(i) for i in latest],
        last_generated_at=latest[0].created_at.isoformat() if latest else None,
    )
