"""Stock levels, adjustments and the movement ledger."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import InsufficientStockError
from app.models import AuditAction, Batch, InventoryMovement, MovementType, Product, User
from app.schemas.inventory import (
    InventoryAdjustRequest,
    InventoryAdjustResponse,
    InventoryItem,
    InventorySummary,
    MovementRead,
)
from app.services import audit_service
from app.services.auth_service import get_company
from app.services.batch_service import get_batch
from app.services.expiry_service import batch_status, default_thresholds
from app.utils.dates import start_of_local_day_utc, today_in_tz
from app.utils.helpers import money
from app.utils.pagination import PageParams, paginate, paginate_rows


def _stock_columns(today: date):
    positive = case((Batch.remaining_quantity > 0, Batch.remaining_quantity), else_=0)
    total = func.coalesce(func.sum(positive), 0)
    sellable = func.coalesce(func.sum(case((Batch.expiry_date > today, positive), else_=0)), 0)
    expired = func.coalesce(func.sum(case((Batch.expiry_date <= today, positive), else_=0)), 0)
    batches = func.count(case((Batch.remaining_quantity > 0, Batch.id)))
    nearest = func.min(case(((Batch.remaining_quantity > 0) & (Batch.expiry_date > today), Batch.expiry_date)))
    return total, sellable, expired, batches, nearest


async def list_inventory(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    low_stock_only: bool = False,
) -> tuple[list[InventoryItem], int]:
    company = await get_company(db, company_id)
    today = today_in_tz(company.timezone)
    total, sellable, expired, batches, nearest = _stock_columns(today)
    stmt = (
        select(Product, total.label("total"), sellable.label("sellable"), expired.label("expired"),
               batches.label("batches"), nearest.label("nearest"))
        .outerjoin(Batch, Batch.product_id == Product.id)
        .where(Product.company_id == company_id, Product.is_active.is_(True))
        .group_by(Product.id)
    )
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(Product.name.ilike(term) | Product.sku.ilike(term) | Product.barcode.ilike(term))
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if low_stock_only:
        stmt = stmt.having(sellable <= settings.LOW_STOCK_THRESHOLD)
    rows, count = await paginate_rows(db, stmt.order_by(Product.name), params)
    items = [
        InventoryItem(
            product_id=p.id,
            product_name=p.name,
            sku=p.sku,
            barcode=p.barcode,
            category_id=p.category_id,
            unit=p.unit,
            total_stock=int(t),
            sellable_stock=int(s),
            expired_stock=int(e),
            batch_count=int(b),
            nearest_expiry=n,
            inventory_value=money(Decimal(int(t)) * p.cost_price),
            is_low_stock=int(s) <= settings.LOW_STOCK_THRESHOLD,
        )
        for p, t, s, e, b, n in rows
    ]
    return items, count


async def summary(db: AsyncSession, company_id: uuid.UUID) -> InventorySummary:
    company = await get_company(db, company_id)
    today = today_in_tz(company.timezone)
    t = default_thresholds()
    rows = (
        await db.execute(
            select(Batch.expiry_date, Batch.remaining_quantity, Product.cost_price, Product.selling_price)
            .join(Product, Product.id == Batch.product_id)
            .where(Batch.company_id == company_id, Batch.remaining_quantity > 0, Product.is_active.is_(True))
        )
    ).all()
    buckets = {"EXPIRED": 0, "CRITICAL": 0, "EXPIRING_SOON": 0, "SAFE": 0}
    value = retail = at_risk = Decimal("0")
    for expiry, qty, cost, price in rows:
        status = batch_status(expiry, qty, today, t).value
        buckets[status] += qty
        value += cost * qty
        retail += price * qty
        if status in ("EXPIRED", "CRITICAL"):
            at_risk += cost * qty

    _, sellable, _, _, _ = _stock_columns(today)
    per_product = (
        select(Product.id, sellable.label("sellable"))
        .outerjoin(Batch, Batch.product_id == Product.id)
        .where(Product.company_id == company_id, Product.is_active.is_(True))
        .group_by(Product.id)
        .subquery()
    )
    total_products = await db.scalar(select(func.count()).select_from(per_product)) or 0
    in_stock = await db.scalar(select(func.count()).select_from(per_product).where(per_product.c.sellable > 0)) or 0
    low = await db.scalar(
        select(func.count()).select_from(per_product).where(per_product.c.sellable <= settings.LOW_STOCK_THRESHOLD)
    ) or 0
    total_units = sum(buckets.values())
    return InventorySummary(
        total_products=int(total_products),
        products_in_stock=int(in_stock),
        low_stock_products=int(low),
        total_units=total_units,
        sellable_units=total_units - buckets["EXPIRED"],
        expired_units=buckets["EXPIRED"],
        critical_units=buckets["CRITICAL"],
        expiring_soon_units=buckets["EXPIRING_SOON"],
        safe_units=buckets["SAFE"],
        inventory_value=money(value),
        retail_value=money(retail),
        value_at_risk=money(at_risk),
        currency=company.currency,
    )


async def adjust(db: AsyncSession, user: User, data: InventoryAdjustRequest, ip: str | None) -> InventoryAdjustResponse:
    """Apply a signed stock change to one batch atomically (row lock prevents lost updates)."""
    batch = await get_batch(db, user.company_id, data.batch_id, lock=True)
    previous = batch.remaining_quantity
    new_quantity = previous + data.quantity_change
    if new_quantity < 0 and not settings.ALLOW_NEGATIVE_INVENTORY:
        raise InsufficientStockError(
            "Adjustment would make inventory negative",
            details={"available": previous, "requested_change": data.quantity_change},
        )
    batch.remaining_quantity = new_quantity
    if data.movement_type == MovementType.PURCHASE:
        batch.initial_quantity += data.quantity_change
    company = await get_company(db, user.company_id)
    batch.status = batch_status(batch.expiry_date, new_quantity, today_in_tz(company.timezone))
    movement = InventoryMovement(
        company_id=user.company_id,
        product_id=batch.product_id,
        batch_id=batch.id,
        movement_type=data.movement_type,
        quantity=data.quantity_change,
        reference_type="ADJUSTMENT",
        notes=data.notes,
    )
    db.add(movement)
    await db.flush()
    audit_service.record(
        db, action=AuditAction.INVENTORY_ADJUSTED, company_id=user.company_id, user_id=user.id,
        entity_type="batch", entity_id=batch.id,
        metadata={
            "movement_type": data.movement_type.value, "change": data.quantity_change,
            "previous": previous, "new": new_quantity, "notes": data.notes,
        },
        ip_address=ip,
    )
    await db.commit()
    return InventoryAdjustResponse(
        movement=MovementRead.model_validate(movement),
        batch_id=batch.id,
        previous_quantity=previous,
        new_quantity=new_quantity,
        batch_status=batch.status.value,
    )


async def list_movements(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    *,
    product_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    movement_type: MovementType | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
) -> tuple[list[InventoryMovement], int]:
    stmt = select(InventoryMovement).where(InventoryMovement.company_id == company_id)
    if product_id:
        stmt = stmt.where(InventoryMovement.product_id == product_id)
    if batch_id:
        stmt = stmt.where(InventoryMovement.batch_id == batch_id)
    if movement_type:
        stmt = stmt.where(InventoryMovement.movement_type == movement_type)
    if date_from or date_to:
        tz = (await get_company(db, company_id)).timezone
        if date_from:
            stmt = stmt.where(InventoryMovement.created_at >= start_of_local_day_utc(date_from, tz))
        if date_to:
            stmt = stmt.where(InventoryMovement.created_at < start_of_local_day_utc(date_to + timedelta(days=1), tz))
    return await paginate(db, stmt.order_by(InventoryMovement.created_at.desc()), params)
