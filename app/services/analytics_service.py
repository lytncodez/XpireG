"""Deterministic analytics computed with SQL + Python. No LLM is used for any figure."""

from __future__ import annotations

import statistics
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestError
from app.models import Alert, Batch, Category, Company, Product
from app.schemas.analytics import (
    ExpiryRiskItem,
    InventoryAnalytics,
    InventoryMetrics,
    NamedAmount,
    OverviewResponse,
    ProductAnalytics,
    ProductPerformance,
    SalesAnalytics,
    SalesMetrics,
    SeriesPoint,
    TrendsAnalytics,
)
from app.services.auth_service import get_company
from app.services.expiry_service import batch_status, default_thresholds
from app.services.sales_service import bucket_series, bucket_start, sales_rows
from app.utils.dates import add_months, days_until, local_date, month_start, start_of_local_day_utc, today_in_tz, week_start
from app.utils.helpers import pct_change, to_float

FAST_SHARE = 0.2
HIGH_COVER_DAYS = 90
LOW_COVER_DAYS = 7


@dataclass
class BatchRow:
    batch_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str
    batch_number: str
    expiry_date: date
    remaining: int
    cost: Decimal
    price: Decimal


async def _context(db: AsyncSession, company_id: uuid.UUID) -> tuple[Company, date]:
    company = await get_company(db, company_id)
    return company, today_in_tz(company.timezone)


async def load_batches(db: AsyncSession, company_id: uuid.UUID) -> list[BatchRow]:
    rows = (
        await db.execute(
            select(
                Batch.id, Batch.product_id, Product.name, Batch.batch_number, Batch.expiry_date,
                Batch.remaining_quantity, Product.cost_price, Product.selling_price,
            )
            .join(Product, Product.id == Batch.product_id)
            .where(Batch.company_id == company_id, Batch.remaining_quantity > 0, Product.is_active.is_(True))
            .order_by(Batch.expiry_date)
        )
    ).all()
    return [BatchRow(*r) for r in rows]


async def load_sales(db: AsyncSession, company: Company, start: date, end: date):
    """Sales between local dates [start, end] inclusive."""
    return await sales_rows(
        db, company.id,
        start_of_local_day_utc(start, company.timezone),
        start_of_local_day_utc(end + timedelta(days=1), company.timezone),
    )


async def count_active_products(db: AsyncSession, company_id: uuid.UUID) -> int:
    return int(
        await db.scalar(
            select(func.count()).select_from(Product).where(Product.company_id == company_id, Product.is_active.is_(True))
        )
        or 0
    )


def inventory_metrics(batches: list[BatchRow], today: date, total_products: int) -> InventoryMetrics:
    t = default_thresholds()
    units = defaultdict(int)
    values = defaultdict(lambda: Decimal("0"))
    cost_total = retail_total = Decimal("0")
    for b in batches:
        status = batch_status(b.expiry_date, b.remaining, today, t).value
        units[status] += b.remaining
        values[status] += b.cost * b.remaining
        cost_total += b.cost * b.remaining
        retail_total += b.price * b.remaining
    return InventoryMetrics(
        total_products=total_products,
        total_units=sum(b.remaining for b in batches),
        inventory_value=to_float(cost_total),
        retail_value=to_float(retail_total),
        expired_units=units["EXPIRED"],
        critical_units=units["CRITICAL"],
        expiring_units=units["EXPIRING_SOON"],
        safe_units=units["SAFE"],
        expired_value=to_float(values["EXPIRED"]),
        critical_value=to_float(values["CRITICAL"]),
    )


def units_by_product(sales) -> dict[uuid.UUID, int]:
    out: dict[uuid.UUID, int] = defaultdict(int)
    for pid, _, _, qty, _, _, _ in sales:
        out[pid] += qty
    return out


def expiry_risk(batches: list[BatchRow], velocity: dict[uuid.UUID, float], today: date) -> list[ExpiryRiskItem]:
    """Project FEFO sell-through at current velocity; what cannot sell before expiry is at risk.

    Batches of the same product are consumed in expiry order, so later batches only get the
    demand left over after earlier ones.
    """
    consumed: dict[uuid.UUID, float] = defaultdict(float)
    items: list[ExpiryRiskItem] = []
    for b in sorted(batches, key=lambda x: (x.product_id, x.expiry_date)):
        days = days_until(b.expiry_date, today)
        if days <= 0:
            projected = 0.0
        else:
            demand_until_expiry = velocity.get(b.product_id, 0.0) * days
            available_demand = max(0.0, demand_until_expiry - consumed[b.product_id])
            projected = min(float(b.remaining), available_demand)
            consumed[b.product_id] += projected
        at_risk = max(0, b.remaining - int(projected))
        if at_risk > 0:
            items.append(
                ExpiryRiskItem(
                    batch_id=b.batch_id, product_id=b.product_id, product_name=b.product_name,
                    batch_number=b.batch_number, expiry_date=b.expiry_date, days_remaining=days,
                    remaining_quantity=b.remaining, projected_sales_before_expiry=round(projected, 1),
                    units_at_risk=at_risk, value_at_risk=to_float(b.cost * at_risk),
                )
            )
    items.sort(key=lambda i: (i.days_remaining, -i.value_at_risk))
    return items


# --------------------------------------------------------------------------- overview


async def overview(db: AsyncSession, company_id: uuid.UUID, days: int | None = None) -> OverviewResponse:
    company, today = await _context(db, company_id)
    days = days or settings.ANALYTICS_DEFAULT_DAYS
    batches = await load_batches(db, company_id)
    metrics = inventory_metrics(batches, today, await count_active_products(db, company_id))
    current = await load_sales(db, company, today - timedelta(days=days - 1), today)
    previous = await load_sales(db, company, today - timedelta(days=2 * days - 1), today - timedelta(days=days))
    rev = float(sum((r[4] for r in current), Decimal("0")))
    prev_rev = float(sum((r[4] for r in previous), Decimal("0")))
    units = sum(r[3] for r in current)
    prev_units = sum(r[3] for r in previous)
    velocity = {pid: u / days for pid, u in units_by_product(current).items()}
    risk = expiry_risk(batches, velocity, today)
    open_alerts = await db.scalar(
        select(func.count()).select_from(Alert).where(Alert.company_id == company_id, Alert.is_resolved.is_(False))
    )
    return OverviewResponse(
        as_of=today,
        currency=company.currency,
        inventory=metrics,
        sales=SalesMetrics(
            period_days=days, revenue=round(rev, 2), units_sold=units, transactions=len(current),
            previous_revenue=round(prev_rev, 2), revenue_growth_pct=pct_change(rev, prev_rev),
            units_growth_pct=pct_change(units, prev_units),
        ),
        potential_waste_value=round(sum(i.value_at_risk for i in risk), 2),
        open_alerts=int(open_alerts or 0),
    )


# --------------------------------------------------------------------------- sales


def _named_amounts(agg: dict, names: dict, total_revenue: float, limit: int = 20) -> list[NamedAmount]:
    ordered = sorted(agg.items(), key=lambda kv: kv[1][1], reverse=True)[:limit]
    return [
        NamedAmount(
            id=key, name=names.get(key, "Uncategorised"), units_sold=v[0], revenue=round(float(v[1]), 2),
            share_pct=round(float(v[1]) / total_revenue * 100, 2) if total_revenue else 0.0,
        )
        for key, v in ordered
    ]


def _series(sales, start: date, end: date, granularity: str, tz: str) -> list[SeriesPoint]:
    agg = {d: [Decimal("0"), 0] for d in bucket_series(start, end, granularity)}
    for _, _, _, qty, amount, _, sold_at in sales:
        key = bucket_start(local_date(sold_at, tz), granularity)
        if key in agg:
            agg[key][0] += amount
            agg[key][1] += qty
    return [SeriesPoint(period=k, revenue=to_float(v[0]), units_sold=v[1]) for k, v in sorted(agg.items())]


async def sales_analytics(
    db: AsyncSession, company_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None
) -> SalesAnalytics:
    company, today = await _context(db, company_id)
    date_to = date_to or today
    date_from = date_from or date_to - timedelta(days=settings.ANALYTICS_DEFAULT_DAYS - 1)
    if date_from > date_to:
        raise BadRequestError("date_from must be on or before date_to")
    span = (date_to - date_from).days + 1
    if span > 731:
        raise BadRequestError("Date range cannot exceed two years")

    in_range = await load_sales(db, company, date_from, date_to)
    previous = await load_sales(db, company, date_from - timedelta(days=span), date_from - timedelta(days=1))
    year_start = add_months(month_start(today), -11)
    long_window = await load_sales(db, company, min(year_start, week_start(today) - timedelta(weeks=11)), today)

    revenue = float(sum((r[4] for r in in_range), Decimal("0")))
    prev_revenue = float(sum((r[4] for r in previous), Decimal("0")))

    product_names: dict[uuid.UUID | None, str] = {}
    by_product: dict = defaultdict(lambda: [0, Decimal("0")])
    by_category: dict = defaultdict(lambda: [0, Decimal("0")])
    for pid, name, cid, qty, amount, _, _ in in_range:
        product_names[pid] = name
        by_product[pid][0] += qty
        by_product[pid][1] += amount
        by_category[cid][0] += qty
        by_category[cid][1] += amount
    category_names = {
        cid: name for cid, name in (
            await db.execute(select(Category.id, Category.name).where(Category.company_id == company_id))
        ).all()
    }
    category_names[None] = "Uncategorised"

    return SalesAnalytics(
        date_from=date_from,
        date_to=date_to,
        revenue=round(revenue, 2),
        units_sold=sum(r[3] for r in in_range),
        transactions=len(in_range),
        average_daily_revenue=round(revenue / span, 2),
        growth_pct=pct_change(revenue, prev_revenue),
        daily=_series(in_range, date_from, date_to, "day", company.timezone),
        weekly=_series(long_window, week_start(today) - timedelta(weeks=11), today, "week", company.timezone),
        monthly=_series(long_window, year_start, today, "month", company.timezone),
        by_product=_named_amounts(by_product, product_names, revenue),
        by_category=_named_amounts(by_category, category_names, revenue),
    )


# --------------------------------------------------------------------------- products & inventory


async def product_performance(
    db: AsyncSession, company: Company, today: date, window_days: int
) -> list[ProductPerformance]:
    sales = await load_sales(db, company, today - timedelta(days=window_days - 1), today)
    units = units_by_product(sales)
    revenue: dict[uuid.UUID, Decimal] = defaultdict(lambda: Decimal("0"))
    for pid, _, _, _, amount, _, _ in sales:
        revenue[pid] += amount

    batches = await load_batches(db, company.id)
    sellable: dict[uuid.UUID, int] = defaultdict(int)
    for b in batches:
        if b.expiry_date > today:
            sellable[b.product_id] += b.remaining

    products = (
        await db.execute(
            select(Product.id, Product.name, Product.sku, Category.name)
            .outerjoin(Category, Category.id == Product.category_id)
            .where(Product.company_id == company.id, Product.is_active.is_(True))
        )
    ).all()

    sellers = sorted((units[p.id] for p in products if units[p.id] > 0), reverse=True)
    fast_cutoff = sellers[max(0, int(len(sellers) * FAST_SHARE) - 1)] if sellers else None
    median_units = statistics.median(sellers) if sellers else 0
    stocked_velocities = sorted(units[p.id] / window_days for p in products if sellable[p.id] > 0)
    slow_cutoff = (
        stocked_velocities[max(0, int(len(stocked_velocities) * FAST_SHARE) - 1)] if stocked_velocities else None
    )

    out: list[ProductPerformance] = []
    for pid, name, sku, category_name in products:
        u = units[pid]
        stock = sellable[pid]
        velocity = u / window_days
        cover = round(stock / velocity, 1) if velocity > 0 else None
        if velocity > 0 and cover is not None and cover < LOW_COVER_DAYS and u >= median_units:
            cls = "LOW_STOCK_HIGH_SALES"
        elif stock > 0 and (velocity == 0 or (cover is not None and cover > HIGH_COVER_DAYS)):
            cls = "HIGH_STOCK_LOW_SALES"
        elif fast_cutoff is not None and u > 0 and u >= fast_cutoff:
            cls = "FAST_MOVER"
        elif slow_cutoff is not None and stock > 0 and velocity <= slow_cutoff:
            cls = "SLOW_MOVER"
        else:
            cls = "NORMAL"
        out.append(
            ProductPerformance(
                product_id=pid, product_name=name, sku=sku, category_name=category_name, stock=stock,
                units_sold=u, revenue=to_float(revenue[pid]), daily_velocity=round(velocity, 3),
                days_of_cover=cover, classification=cls,
            )
        )
    return out


async def product_analytics(db: AsyncSession, company_id: uuid.UUID, window_days: int | None = None) -> ProductAnalytics:
    company, today = await _context(db, company_id)
    window = window_days or settings.ANALYTICS_DEFAULT_DAYS
    perf = await product_performance(db, company, today, window)
    sellers = sorted([p for p in perf if p.units_sold > 0], key=lambda p: p.units_sold, reverse=True)
    n_fast = max(1, int(len(sellers) * FAST_SHARE)) if sellers else 0
    stocked = sorted([p for p in perf if p.stock > 0], key=lambda p: p.daily_velocity)
    n_slow = max(1, int(len(stocked) * FAST_SHARE)) if stocked else 0
    return ProductAnalytics(
        window_days=window,
        fast_movers=sellers[:n_fast],
        slow_movers=stocked[:n_slow],
        high_stock_low_sales=sorted(
            [p for p in perf if p.classification == "HIGH_STOCK_LOW_SALES"], key=lambda p: -(p.days_of_cover or 1e9)
        ),
        low_stock_high_sales=sorted(
            [p for p in perf if p.classification == "LOW_STOCK_HIGH_SALES"], key=lambda p: p.days_of_cover or 0
        ),
        products=sorted(perf, key=lambda p: p.revenue, reverse=True),
    )


async def inventory_analytics(db: AsyncSession, company_id: uuid.UUID, window_days: int | None = None) -> InventoryAnalytics:
    company, today = await _context(db, company_id)
    window = window_days or settings.ANALYTICS_DEFAULT_DAYS
    batches = await load_batches(db, company_id)
    metrics = inventory_metrics(batches, today, await count_active_products(db, company_id))
    sales = await load_sales(db, company, today - timedelta(days=window - 1), today)
    velocity = {pid: u / window for pid, u in units_by_product(sales).items()}
    risk = expiry_risk(batches, velocity, today)
    cogs = sum((r[5] * r[3] for r in sales), Decimal("0"))
    stock_value = Decimal(str(metrics.inventory_value))
    turnover = round(float(cogs / stock_value), 3) if stock_value > 0 else None
    breakdown = {
        "EXPIRED": metrics.expired_units, "CRITICAL": metrics.critical_units,
        "EXPIRING_SOON": metrics.expiring_units, "SAFE": metrics.safe_units,
    }
    return InventoryAnalytics(
        as_of=today,
        metrics=metrics,
        status_breakdown=breakdown,
        stock_turnover=turnover,
        annualized_turnover=round(turnover * 365 / window, 2) if turnover is not None else None,
        expiry_risk=risk[:100],
        potential_waste_units=sum(i.units_at_risk for i in risk),
        potential_waste_value=round(sum(i.value_at_risk for i in risk), 2),
    )


async def trends(db: AsyncSession, company_id: uuid.UUID, granularity: str = "day", periods: int = 30) -> TrendsAnalytics:
    if granularity not in {"day", "week", "month"}:
        raise BadRequestError("granularity must be day, week or month")
    company, today = await _context(db, company_id)
    if granularity == "day":
        start = today - timedelta(days=periods - 1)
    elif granularity == "week":
        start = week_start(today) - timedelta(weeks=periods - 1)
    else:
        start = add_months(month_start(today), -(periods - 1))
    sales = await load_sales(db, company, start, today)
    points = _series(sales, start, today, granularity, company.timezone)
    moving = []
    if granularity == "day":
        for i, p in enumerate(points):
            window = points[max(0, i - 6): i + 1]
            moving.append({
                "period": p.period.isoformat(),
                "revenue": round(sum(w.revenue for w in window) / len(window), 2),
                "units_sold": round(sum(w.units_sold for w in window) / len(window), 2),
            })
    return TrendsAnalytics(granularity=granularity, points=points, moving_average_7d=moving)
