"""Barcode lookup. A barcode identifies a PRODUCT; batch numbers and expiry dates are separate data."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import BadRequestError, NotFoundError
from app.models import Batch, Category, Product
from app.schemas.barcode import BarcodeInventory, BarcodeLookupResponse
from app.services.auth_service import get_company
from app.services.batch_service import to_read
from app.services.expiry_service import batch_status
from app.services.product_service import to_product_reads
from app.utils.dates import today_in_tz
from app.utils.validators import normalize_barcode


async def lookup(db: AsyncSession, company_id: uuid.UUID, raw_barcode: str) -> BarcodeLookupResponse:
    try:
        barcode = normalize_barcode(raw_barcode)
    except ValueError as exc:
        raise BadRequestError(str(exc)) from exc
    if not barcode:
        raise BadRequestError("Barcode is empty")

    row = (
        await db.execute(
            select(Product, Category.name)
            .outerjoin(Category, Category.id == Product.category_id)
            .where(Product.company_id == company_id, Product.barcode == barcode, Product.is_active.is_(True))
        )
    ).first()
    if row is None:
        raise NotFoundError("No product found for this barcode", details={"barcode": barcode})
    product, category_name = row

    company = await get_company(db, company_id)
    today = today_in_tz(company.timezone)
    batches = (
        await db.scalars(
            select(Batch)
            .where(Batch.product_id == product.id, Batch.company_id == company_id, Batch.remaining_quantity > 0)
            .order_by(Batch.expiry_date, Batch.created_at)
        )
    ).all()
    batch_reads = []
    for b in batches:
        read = to_read(b, product.name, today)
        read.status = batch_status(b.expiry_date, b.remaining_quantity, today)
        batch_reads.append(read)
    sellable = [b for b in batches if b.expiry_date > today]
    product_read = (await to_product_reads(db, company_id, [(product, category_name)], today))[0]
    return BarcodeLookupResponse(
        barcode=barcode,
        product=product_read,
        active_batches=batch_reads,
        inventory=BarcodeInventory(
            total_stock=sum(b.remaining_quantity for b in batches),
            sellable_stock=sum(b.remaining_quantity for b in sellable),
            expired_stock=sum(b.remaining_quantity for b in batches if b.expiry_date <= today),
            active_batch_count=len(batches),
            nearest_expiry=sellable[0].expiry_date if sellable else None,
            next_fefo_batch_id=str(sellable[0].id) if sellable else None,
        ),
    )
