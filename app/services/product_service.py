"""Categories and products (company-scoped CRUD, search and stock enrichment)."""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import case, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ConflictError, NotFoundError
from app.models import AuditAction, Batch, Category, Product, User
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.schemas.product import ProductCreate, ProductRead, ProductUpdate
from app.services import audit_service
from app.services.auth_service import get_company
from app.utils.dates import today_in_tz
from app.utils.pagination import PageParams, paginate_rows

# --------------------------------------------------------------------------- categories


async def _category_name_taken(
    db: AsyncSession, company_id: uuid.UUID, name: str, exclude_id: uuid.UUID | None = None
) -> bool:
    stmt = select(Category.id).where(Category.company_id == company_id, func.lower(Category.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Category.id != exclude_id)
    return bool(await db.scalar(stmt))


def _product_count_subquery():
    return (
        select(Product.category_id, func.count(Product.id).label("cnt"))
        .where(Product.is_active.is_(True))
        .group_by(Product.category_id)
        .subquery()
    )


def _category_read(category: Category, count: int | None) -> CategoryRead:
    read = CategoryRead.model_validate(category)
    read.product_count = int(count or 0)
    return read


async def list_categories(
    db: AsyncSession, company_id: uuid.UUID, params: PageParams, search: str | None = None
) -> tuple[list[CategoryRead], int]:
    counts = _product_count_subquery()
    stmt = (
        select(Category, counts.c.cnt)
        .outerjoin(counts, counts.c.category_id == Category.id)
        .where(Category.company_id == company_id)
    )
    if search:
        stmt = stmt.where(Category.name.ilike(f"%{search.strip()}%"))
    rows, total = await paginate_rows(db, stmt.order_by(Category.name), params)
    return [_category_read(c, n) for c, n in rows], total


async def get_category(db: AsyncSession, company_id: uuid.UUID, category_id: uuid.UUID) -> Category:
    category = await db.scalar(
        select(Category).where(Category.id == category_id, Category.company_id == company_id)
    )
    if category is None:
        raise NotFoundError("Category not found")
    return category


async def get_category_read(db: AsyncSession, company_id: uuid.UUID, category_id: uuid.UUID) -> CategoryRead:
    category = await get_category(db, company_id, category_id)
    count = await db.scalar(
        select(func.count()).select_from(Product).where(
            Product.category_id == category.id, Product.is_active.is_(True)
        )
    )
    return _category_read(category, count)


async def create_category(db: AsyncSession, user: User, data: CategoryCreate, ip: str | None) -> CategoryRead:
    if await _category_name_taken(db, user.company_id, data.name):
        raise ConflictError("A category with this name already exists")
    category = Category(company_id=user.company_id, name=data.name, description=data.description)
    db.add(category)
    await db.flush()
    audit_service.record(
        db, action=AuditAction.CATEGORY_CREATED, company_id=user.company_id, user_id=user.id,
        entity_type="category", entity_id=category.id, metadata={"name": category.name}, ip_address=ip,
    )
    await db.commit()
    await db.refresh(category)
    return _category_read(category, 0)


async def update_category(
    db: AsyncSession, user: User, category_id: uuid.UUID, data: CategoryUpdate, ip: str | None
) -> CategoryRead:
    category = await get_category(db, user.company_id, category_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("name") and await _category_name_taken(db, user.company_id, changes["name"], category.id):
        raise ConflictError("A category with this name already exists")
    for field, value in changes.items():
        if field == "name" and value is None:
            continue
        setattr(category, field, value)
    audit_service.record(
        db, action=AuditAction.CATEGORY_UPDATED, company_id=user.company_id, user_id=user.id,
        entity_type="category", entity_id=category.id, metadata=changes, ip_address=ip,
    )
    await db.commit()
    return await get_category_read(db, user.company_id, category.id)


async def delete_category(db: AsyncSession, user: User, category_id: uuid.UUID, ip: str | None) -> None:
    """Products keep existing; their category_id becomes NULL (FK ON DELETE SET NULL)."""
    category = await get_category(db, user.company_id, category_id)
    audit_service.record(
        db, action=AuditAction.CATEGORY_DELETED, company_id=user.company_id, user_id=user.id,
        entity_type="category", entity_id=category.id, metadata={"name": category.name}, ip_address=ip,
    )
    await db.delete(category)
    await db.commit()


async def get_or_create_category(db: AsyncSession, company_id: uuid.UUID, name: str) -> Category:
    """Used by imports. Flushes but does not commit."""
    category = await db.scalar(
        select(Category).where(Category.company_id == company_id, func.lower(Category.name) == name.lower())
    )
    if category is None:
        category = Category(company_id=company_id, name=name.strip())
        db.add(category)
        await db.flush()
    return category


# --------------------------------------------------------------------------- stock helpers


async def stock_by_product(
    db: AsyncSession, company_id: uuid.UUID, product_ids: list[uuid.UUID], today: date
) -> dict[uuid.UUID, tuple[int, int]]:
    """Return {product_id: (total_stock, sellable_stock)} counting only positive remaining quantities."""
    if not product_ids:
        return {}
    positive = case((Batch.remaining_quantity > 0, Batch.remaining_quantity), else_=0)
    stmt = (
        select(
            Batch.product_id,
            func.coalesce(func.sum(positive), 0),
            func.coalesce(func.sum(case((Batch.expiry_date > today, positive), else_=0)), 0),
        )
        .where(Batch.company_id == company_id, Batch.product_id.in_(product_ids))
        .group_by(Batch.product_id)
    )
    return {pid: (int(total), int(sellable)) for pid, total, sellable in (await db.execute(stmt)).all()}


async def to_product_reads(
    db: AsyncSession, company_id: uuid.UUID, rows: list[tuple[Product, str | None]], today: date
) -> list[ProductRead]:
    stock = await stock_by_product(db, company_id, [p.id for p, _ in rows], today)
    reads = []
    for product, category_name in rows:
        read = ProductRead.model_validate(product)
        read.category_name = category_name
        read.total_stock, read.sellable_stock = stock.get(product.id, (0, 0))
        reads.append(read)
    return reads


# --------------------------------------------------------------------------- products


async def list_products(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    *,
    search: str | None = None,
    category_id: uuid.UUID | None = None,
    barcode: str | None = None,
    sku: str | None = None,
    include_inactive: bool = False,
) -> tuple[list[ProductRead], int]:
    stmt = (
        select(Product, Category.name)
        .outerjoin(Category, Category.id == Product.category_id)
        .where(Product.company_id == company_id)
    )
    if not include_inactive:
        stmt = stmt.where(Product.is_active.is_(True))
    if search:
        term = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(Product.name.ilike(term), Product.sku.ilike(term), Product.barcode.ilike(term), Product.brand.ilike(term))
        )
    if category_id:
        stmt = stmt.where(Product.category_id == category_id)
    if barcode:
        stmt = stmt.where(Product.barcode == barcode.strip())
    if sku:
        stmt = stmt.where(Product.sku == sku.strip().upper())
    rows, total = await paginate_rows(db, stmt.order_by(Product.name), params)
    company = await get_company(db, company_id)
    return await to_product_reads(db, company_id, [(r[0], r[1]) for r in rows], today_in_tz(company.timezone)), total


async def get_product(
    db: AsyncSession, company_id: uuid.UUID, product_id: uuid.UUID, *, active_only: bool = False
) -> Product:
    stmt = select(Product).where(Product.id == product_id, Product.company_id == company_id)
    if active_only:
        stmt = stmt.where(Product.is_active.is_(True))
    product = await db.scalar(stmt)
    if product is None:
        raise NotFoundError("Product not found")
    return product


async def get_product_read(db: AsyncSession, company_id: uuid.UUID, product_id: uuid.UUID) -> ProductRead:
    product = await get_product(db, company_id, product_id)
    category_name = None
    if product.category_id:
        category_name = await db.scalar(select(Category.name).where(Category.id == product.category_id))
    company = await get_company(db, company_id)
    return (await to_product_reads(db, company_id, [(product, category_name)], today_in_tz(company.timezone)))[0]


async def _check_identifiers(
    db: AsyncSession, company_id: uuid.UUID, sku: str | None, barcode: str | None, exclude_id: uuid.UUID | None = None
) -> None:
    for field, value in (("sku", sku), ("barcode", barcode)):
        if not value:
            continue
        column = getattr(Product, field)
        stmt = select(Product.id, Product.is_active).where(Product.company_id == company_id, column == value)
        if exclude_id:
            stmt = stmt.where(Product.id != exclude_id)
        existing = (await db.execute(stmt)).first()
        if existing:
            hint = "" if existing.is_active else " (an inactive product uses it; reactivate it instead)"
            raise ConflictError(
                f"A product with this {field} already exists{hint}",
                details={"field": field, "value": value, "product_id": str(existing.id)},
            )


async def find_by_identifier(
    db: AsyncSession, company_id: uuid.UUID, *, sku: str | None = None, barcode: str | None = None
) -> Product | None:
    conditions = []
    if sku:
        conditions.append(Product.sku == sku)
    if barcode:
        conditions.append(Product.barcode == barcode)
    if not conditions:
        return None
    return await db.scalar(select(Product).where(Product.company_id == company_id, or_(*conditions)).limit(1))


async def create_product(db: AsyncSession, user: User, data: ProductCreate, ip: str | None) -> ProductRead:
    if data.category_id:
        await get_category(db, user.company_id, data.category_id)
    await _check_identifiers(db, user.company_id, data.sku, data.barcode)
    product = Product(company_id=user.company_id, **data.model_dump())
    db.add(product)
    await db.flush()
    audit_service.record(
        db, action=AuditAction.PRODUCT_CREATED, company_id=user.company_id, user_id=user.id,
        entity_type="product", entity_id=product.id,
        metadata={"name": product.name, "sku": product.sku, "barcode": product.barcode}, ip_address=ip,
    )
    await db.commit()
    return await get_product_read(db, user.company_id, product.id)


async def update_product(
    db: AsyncSession, user: User, product_id: uuid.UUID, data: ProductUpdate, ip: str | None
) -> ProductRead:
    product = await get_product(db, user.company_id, product_id)
    changes = data.model_dump(exclude_unset=True)
    if changes.get("category_id"):
        await get_category(db, user.company_id, changes["category_id"])
    await _check_identifiers(db, user.company_id, changes.get("sku"), changes.get("barcode"), exclude_id=product.id)
    new_sku = changes.get("sku", product.sku)
    new_barcode = changes.get("barcode", product.barcode)
    if not new_sku and not new_barcode:
        raise ConflictError("A product needs at least a SKU or a barcode")
    for field, value in changes.items():
        if field in {"name", "unit", "selling_price", "cost_price", "is_active"} and value is None:
            continue
        setattr(product, field, value)
    audit_service.record(
        db, action=AuditAction.PRODUCT_UPDATED, company_id=user.company_id, user_id=user.id,
        entity_type="product", entity_id=product.id,
        metadata={k: str(v) if v is not None else None for k, v in changes.items()}, ip_address=ip,
    )
    await db.commit()
    return await get_product_read(db, user.company_id, product.id)


async def delete_product(db: AsyncSession, user: User, product_id: uuid.UUID, ip: str | None) -> None:
    """Soft delete: keeps sales/inventory history intact."""
    product = await get_product(db, user.company_id, product_id, active_only=True)
    product.is_active = False
    audit_service.record(
        db, action=AuditAction.PRODUCT_DELETED, company_id=user.company_id, user_id=user.id,
        entity_type="product", entity_id=product.id, metadata={"name": product.name}, ip_address=ip,
    )
    await db.commit()
