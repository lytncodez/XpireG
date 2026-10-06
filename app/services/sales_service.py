"""Sales recording (FEFO allocation, atomic stock decrement) and sales reporting."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestError, InsufficientStockError, NotFoundError
from app.models import AuditAction, Batch, InventoryMovement, MovementType, Product, Sale, User
from app.schemas.sales import SaleCreate, SaleCreateResult, SaleRead, SalesSummary, SalesTrends, TopProduct, TrendPoint
from app.services import audit_service
from app.services.auth_service import get_company
from app.services.expiry_service import batch_status
from app.services.product_service import get_product
from app.utils.dates import (
    add_months,
    local_date,
    month_start,
    start_of_local_day_utc,
    today_in_tz,
    utcnow,
    week_start,
)
from app.utils.helpers import money
from app.utils.pagination import PageParams, paginate_rows


async def record_sale(db: AsyncSession, user: User, data: SaleCreate, ip: str | None) -> SaleCreateResult:
    """1 validate product, 2 validate/allocate batch(es), 3 validate quantity, 4 decrement stock,
    5 create sale(s), 6 create movement(s), 7 commit — all in one transaction with row locks."""
    product = await get_product(db, user.company_id, data.product_id, active_only=True)
    company = await get_company(db, user.company_id)
    today = today_in_tz(company.timezone)
    now = utcnow()
    sold_at = data.sold_at or now
    if sold_at.tzinfo is None:
        sold_at = sold_at.replace(tzinfo=timezone.utc)
    if sold_at > now + timedelta(minutes=5):
        raise BadRequestError("sold_at cannot be in the future")
    unit_price = money(data.unit_price if data.unit_price is not None else product.selling_price)

    allocations: list[tuple[Batch, int]] = []
    if data.batch_id:
        batch = await db.scalar(
            select(Batch)
            .where(Batch.id == data.batch_id, Batch.company_id == user.company_id)
            .with_for_update()
        )
        if batch is None:
            raise NotFoundError("Batch not found")
        if batch.product_id != product.id:
            raise BadRequestError("Batch does not belong to this product")
        if batch.expiry_date <= today:
            raise BadRequestError(
                "Cannot sell from an expired batch",
                details={"batch_number": batch.batch_number, "expiry_date": batch.expiry_date.isoformat()},
            )
        if batch.remaining_quantity < data.quantity and not settings.ALLOW_NEGATIVE_INVENTORY:
            raise InsufficientStockError(
                "Insufficient stock in batch",
                details={"available": batch.remaining_quantity, "requested": data.quantity},
            )
        allocations.append((batch, data.quantity))
    else:
        # FEFO: soonest-expiring sellable batches first.
        batches = (
            await db.scalars(
                select(Batch)
                .where(
                    Batch.product_id == product.id,
                    Batch.company_id == user.company_id,
                    Batch.remaining_quantity > 0,
                    Batch.expiry_date > today,
                )
                .order_by(Batch.expiry_date, Batch.created_at)
                .with_for_update()
            )
        ).all()
        available = sum(b.remaining_quantity for b in batches)
        if available < data.quantity and not (settings.ALLOW_NEGATIVE_INVENTORY and batches):
            raise InsufficientStockError(
                "Insufficient sellable stock",
                details={"available": available, "requested": data.quantity},
            )
        remaining = data.quantity
        for batch in batches:
            if remaining <= 0:
                break
            take = min(batch.remaining_quantity, remaining)
            allocations.append((batch, take))
            remaining -= take
        if remaining > 0:  # only reachable when negative inventory is allowed
            last_batch, last_take = allocations[-1] if allocations else (batches[-1], 0)
            if allocations:
                allocations[-1] = (last_batch, last_take + remaining)
            else:
                allocations.append((last_batch, remaining))

    created: list[tuple[Sale, Batch]] = []
    for batch, qty in allocations:
        batch.remaining_quantity -= qty
        batch.status = batch_status(batch.expiry_date, batch.remaining_quantity, today)
        sale = Sale(
            id=uuid.uuid4(),
            company_id=user.company_id,
            product_id=product.id,
            batch_id=batch.id,
            quantity=qty,
            unit_price=unit_price,
            total_amount=money(unit_price * qty),
            sold_at=sold_at,
        )
        db.add(sale)
        db.add(
            InventoryMovement(
                company_id=user.company_id,
                product_id=product.id,
                batch_id=batch.id,
                movement_type=MovementType.SALE,
                quantity=-qty,
                reference_type="SALE",
                reference_id=sale.id,
            )
        )
        created.append((sale, batch))
    await db.flush()
    total_amount = money(sum((s.total_amount for s, _ in created), Decimal("0")))
    audit_service.record(
        db, action=AuditAction.SALE_CREATED, company_id=user.company_id, user_id=user.id,
        entity_type="sale", entity_id=created[0][0].id,
        metadata={
            "product_id": str(product.id), "quantity": data.quantity, "total_amount": str(total_amount),
            "allocations": [{"batch_id": str(b.id), "quantity": s.quantity} for s, b in created],
        },
        ip_address=ip,
    )
    await db.commit()

    reads = []
    for sale, batch in created:
        read = SaleRead.model_validate(sale)
        read.product_name = product.name
        read.batch_number = batch.batch_number
        reads.append(read)
    return SaleCreateResult(sales=reads, total_quantity=data.quantity, total_amount=total_amount)


async def list_sales(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    *,
    product_id: uuid.UUID | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[SaleRead], int]:
    company = await get_company(db, company_id)
    stmt = (
        select(Sale, Product.name, Batch.batch_number)
        .join(Product, Product.id == Sale.product_id)
        .outerjoin(Batch, Batch.id == Sale.batch_id)
        .where(Sale.company_id == company_id)
    )
    if product_id:
        stmt = stmt.where(Sale.product_id == product_id)
    if date_from:
        stmt = stmt.where(Sale.sold_at >= start_of_local_day_utc(date_from, company.timezone))
    if date_to:
        stmt = stmt.where(Sale.sold_at < start_of_local_day_utc(date_to + timedelta(days=1), company.timezone))
    rows, total = await paginate_rows(db, stmt.order_by(Sale.sold_at.desc()), params)
    reads = []
    for sale, product_name, batch_number in rows:
        read = SaleRead.model_validate(sale)
        read.product_name = product_name
        read.batch_number = batch_number
        reads.append(read)
    return reads, total


async def get_sale(db: AsyncSession, company_id: uuid.UUID, sale_id: uuid.UUID) -> SaleRead:
    row = (
        await db.execute(
            select(Sale, Product.name, Batch.batch_number)
            .join(Product, Product.id == Sale.product_id)
            .outerjoin(Batch, Batch.id == Sale.batch_id)
            .where(Sale.id == sale_id, Sale.company_id == company_id)
        )
    ).first()
    if row is None:
        raise NotFoundError("Sale not found")
    read = SaleRead.model_validate(row[0])
    read.product_name, read.batch_number = row[1], row[2]
    return read


async def sales_rows(
    db: AsyncSession, company_id: uuid.UUID, start: datetime, end: datetime
) -> list[tuple[uuid.UUID, str, uuid.UUID | None, int, Decimal, Decimal, datetime]]:
    """(product_id, product_name, category_id, quantity, total_amount, cost_price, sold_at) in [start, end)."""
    result = await db.execute(
        select(
            Sale.product_id, Product.name, Product.category_id, Sale.quantity, Sale.total_amount,
            Product.cost_price, Sale.sold_at,
        )
        .join(Product, Product.id == Sale.product_id)
        .where(Sale.company_id == company_id, Sale.sold_at >= start, Sale.sold_at < end)
    )
    return [tuple(r) for r in result.all()]


async def summary(
    db: AsyncSession, company_id: uuid.UUID, date_from: date | None = None, date_to: date | None = None
) -> SalesSummary:
    company = await get_company(db, company_id)
    today = today_in_tz(company.timezone)
    date_to = date_to or today
    date_from = date_from or (date_to - timedelta(days=settings.ANALYTICS_DEFAULT_DAYS - 1))
    if date_from > date_to:
        raise BadRequestError("date_from must be on or before date_to")
    rows = await sales_rows(
        db, company_id,
        start_of_local_day_utc(date_from, company.timezone),
        start_of_local_day_utc(date_to + timedelta(days=1), company.timezone),
    )
    revenue = sum((r[4] for r in rows), Decimal("0"))
    units = sum(r[3] for r in rows)
    cogs = sum((r[5] * r[3] for r in rows), Decimal("0"))
    per_product: dict[uuid.UUID, list] = defaultdict(lambda: ["", 0, Decimal("0")])
    for pid, name, _, qty, amount, _, _ in rows:
        entry = per_product[pid]
        entry[0], entry[1], entry[2] = name, entry[1] + qty, entry[2] + amount
    top = sorted(per_product.items(), key=lambda kv: kv[1][2], reverse=True)[:10]
    return SalesSummary(
        date_from=date_from,
        date_to=date_to,
        revenue=money(revenue),
        units_sold=units,
        transactions=len(rows),
        average_sale_value=money(revenue / len(rows)) if rows else money(0),
        gross_profit=money(revenue - cogs),
        top_products=[TopProduct(product_id=pid, product_name=v[0], units_sold=v[1], revenue=money(v[2])) for pid, v in top],
        currency=company.currency,
    )


def bucket_start(d: date, granularity: str) -> date:
    if granularity == "week":
        return week_start(d)
    if granularity == "month":
        return month_start(d)
    return d


def bucket_series(start: date, end: date, granularity: str) -> list[date]:
    out: list[date] = []
    cursor = bucket_start(start, granularity)
    while cursor <= end:
        out.append(cursor)
        if granularity == "week":
            cursor += timedelta(days=7)
        elif granularity == "month":
            cursor = add_months(cursor, 1)
        else:
            cursor += timedelta(days=1)
    return out


async def trends(db: AsyncSession, company_id: uuid.UUID, granularity: str = "day", periods: int = 30) -> SalesTrends:
    if granularity not in {"day", "week", "month"}:
        raise BadRequestError("granularity must be day, week or month")
    company = await get_company(db, company_id)
    today = today_in_tz(company.timezone)
    if granularity == "day":
        start = today - timedelta(days=periods - 1)
    elif granularity == "week":
        start = week_start(today) - timedelta(weeks=periods - 1)
    else:
        start = add_months(month_start(today), -(periods - 1))
    rows = await sales_rows(
        db, company_id, start_of_local_day_utc(start, company.timezone),
        start_of_local_day_utc(today + timedelta(days=1), company.timezone),
    )
    agg: dict[date, list] = {d: [Decimal("0"), 0, 0] for d in bucket_series(start, today, granularity)}
    for _, _, _, qty, amount, _, sold_at in rows:
        key = bucket_start(local_date(sold_at, company.timezone), granularity)
        if key in agg:
            agg[key][0] += amount
            agg[key][1] += qty
            agg[key][2] += 1
    return SalesTrends(
        granularity=granularity,
        points=[TrendPoint(period=k, revenue=money(v[0]), units_sold=v[1], transactions=v[2]) for k, v in sorted(agg.items())],
    )

