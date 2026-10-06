"""Batch CRUD. Creation records a PURCHASE movement so the inventory ledger stays complete."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, ConflictError, NotFoundError
from app.models import AuditAction, Batch, BatchStatus, InventoryMovement, MovementType, Product, Sale, User
from app.schemas.batch import BatchCreate, BatchRead, BatchUpdate
from app.services import audit_service
from app.services.auth_service import get_company
from app.services.expiry_service import batch_status
from app.services.product_service import get_product
from app.utils.dates import days_until, today_in_tz
from app.utils.pagination import PageParams, paginate_rows


def to_read(batch: Batch, product_name: str | None, today: date) -> BatchRead:
    read = BatchRead.model_validate(batch)
    read.product_name = product_name
    read.days_remaining = days_until(batch.expiry_date, today)
    return read


async def _today(db: AsyncSession, company_id: uuid.UUID) -> date:
    return today_in_tz((await get_company(db, company_id)).timezone)


async def list_batches(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    *,
    product_id: uuid.UUID | None = None,
    status: BatchStatus | None = None,
    in_stock: bool | None = None,
) -> tuple[list[BatchRead], int]:
    stmt = (
        select(Batch, Product.name)
        .join(Product, Product.id == Batch.product_id)
        .where(Batch.company_id == company_id)
    )
    if product_id:
        stmt = stmt.where(Batch.product_id == product_id)
    if status:
        stmt = stmt.where(Batch.status == status)
    if in_stock is True:
        stmt = stmt.where(Batch.remaining_quantity > 0)
    elif in_stock is False:
        stmt = stmt.where(Batch.remaining_quantity <= 0)
    rows, total = await paginate_rows(db, stmt.order_by(Batch.expiry_date, Batch.batch_number), params)
    today = await _today(db, company_id)
    return [to_read(b, name, today) for b, name in rows], total


async def get_batch(db: AsyncSession, company_id: uuid.UUID, batch_id: uuid.UUID, *, lock: bool = False) -> Batch:
    stmt = select(Batch).where(Batch.id == batch_id, Batch.company_id == company_id)
    if lock:
        stmt = stmt.with_for_update()
    batch = await db.scalar(stmt)
    if batch is None:
        raise NotFoundError("Batch not found")
    return batch


async def get_batch_read(db: AsyncSession, company_id: uuid.UUID, batch_id: uuid.UUID) -> BatchRead:
    batch = await get_batch(db, company_id, batch_id)
    name = await db.scalar(select(Product.name).where(Product.id == batch.product_id))
    return to_read(batch, name, await _today(db, company_id))


async def _batch_number_taken(
    db: AsyncSession, product_id: uuid.UUID, batch_number: str, exclude_id: uuid.UUID | None = None
) -> bool:
    stmt = select(Batch.id).where(Batch.product_id == product_id, Batch.batch_number == batch_number)
    if exclude_id:
        stmt = stmt.where(Batch.id != exclude_id)
    return bool(await db.scalar(stmt))


def build_batch(
    product: Product,
    batch_number: str,
    expiry_date: date,
    quantity: int,
    today: date,
    manufacturing_date: date | None = None,
) -> tuple[Batch, InventoryMovement]:
    """Create (unflushed) batch + PURCHASE movement. Shared by the API and imports."""
    batch = Batch(
        id=uuid.uuid4(),
        company_id=product.company_id,
        product_id=product.id,
        batch_number=batch_number,
        manufacturing_date=manufacturing_date,
        expiry_date=expiry_date,
        initial_quantity=quantity,
        remaining_quantity=quantity,
        status=batch_status(expiry_date, quantity, today),
    )
    movement = InventoryMovement(
        company_id=product.company_id,
        product_id=product.id,
        batch_id=batch.id,
        movement_type=MovementType.PURCHASE,
        quantity=quantity,
        reference_type="BATCH",
        reference_id=batch.id,
        notes="Initial batch stock",
    )
    return batch, movement


async def create_batch(db: AsyncSession, user: User, data: BatchCreate, ip: str | None) -> BatchRead:
    product = await get_product(db, user.company_id, data.product_id, active_only=True)
    if await _batch_number_taken(db, product.id, data.batch_number):
        raise ConflictError(
            "This batch number already exists for the product",
            details={"batch_number": data.batch_number, "product_id": str(product.id)},
        )
    today = await _today(db, user.company_id)
    batch, movement = build_batch(
        product, data.batch_number, data.expiry_date, data.initial_quantity, today, data.manufacturing_date
    )
    db.add(batch)
    await db.flush()
    db.add(movement)
    audit_service.record(
        db, action=AuditAction.BATCH_CREATED, company_id=user.company_id, user_id=user.id,
        entity_type="batch", entity_id=batch.id,
        metadata={
            "product_id": str(product.id), "batch_number": batch.batch_number,
            "expiry_date": batch.expiry_date.isoformat(), "quantity": batch.initial_quantity,
        },
        ip_address=ip,
    )
    await db.commit()
    return to_read(batch, product.name, today)


async def update_batch(
    db: AsyncSession, user: User, batch_id: uuid.UUID, data: BatchUpdate, ip: str | None
) -> BatchRead:
    batch = await get_batch(db, user.company_id, batch_id, lock=True)
    changes = data.model_dump(exclude_unset=True)
    if "expiry_date" in changes and changes["expiry_date"] is None:
        raise BadRequestError("expiry_date cannot be null")
    if changes.get("batch_number") and await _batch_number_taken(
        db, batch.product_id, changes["batch_number"], exclude_id=batch.id
    ):
        raise ConflictError("This batch number already exists for the product")
    mfg = changes.get("manufacturing_date", batch.manufacturing_date)
    exp = changes.get("expiry_date", batch.expiry_date)
    if mfg and exp < mfg:
        raise BadRequestError("expiry_date cannot be before manufacturing_date")
    for field, value in changes.items():
        if field == "batch_number" and value is None:
            continue
        setattr(batch, field, value)
    today = await _today(db, user.company_id)
    batch.status = batch_status(batch.expiry_date, batch.remaining_quantity, today)
    audit_service.record(
        db, action=AuditAction.BATCH_UPDATED, company_id=user.company_id, user_id=user.id,
        entity_type="batch", entity_id=batch.id,
        metadata={k: (v.isoformat() if isinstance(v, date) else v) for k, v in changes.items()}, ip_address=ip,
    )
    await db.commit()
    await db.refresh(batch)
    name = await db.scalar(select(Product.name).where(Product.id == batch.product_id))
    return to_read(batch, name, today)


async def delete_batch(db: AsyncSession, user: User, batch_id: uuid.UUID, ip: str | None) -> None:
    batch = await get_batch(db, user.company_id, batch_id, lock=True)
    sales = await db.scalar(select(func.count()).select_from(Sale).where(Sale.batch_id == batch.id))
    if sales:
        raise ConflictError(
            "Batch has recorded sales and cannot be deleted; use an inventory adjustment instead",
            details={"sales": int(sales)},
        )
    audit_service.record(
        db, action=AuditAction.BATCH_DELETED, company_id=user.company_id, user_id=user.id,
        entity_type="batch", entity_id=batch.id,
        metadata={"batch_number": batch.batch_number, "product_id": str(batch.product_id)}, ip_address=ip,
    )
    await db.delete(batch)
    await db.commit()
