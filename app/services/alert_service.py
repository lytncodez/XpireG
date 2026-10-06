"""Alert lifecycle: de-duplicated creation, auto-resolution, read/resolve, recipients."""

from __future__ import annotations

import uuid

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import NotFoundError
from app.models import (
    EXPIRY_ALERT_TYPES,
    Alert,
    AlertSeverity,
    AlertType,
    AuditAction,
    Batch,
    Company,
    Product,
    User,
    UserRole,
)
from app.services import audit_service
from app.utils.dates import today_in_tz, utcnow
from app.utils.pagination import PageParams, paginate


async def recipients(db: AsyncSession, company_id: uuid.UUID) -> list[str]:
    """Active admins/managers with a phone number; falls back to the company phone."""
    phones = (
        await db.scalars(
            select(User.phone_number)
            .where(
                User.company_id == company_id,
                User.is_active.is_(True),
                User.role.in_([UserRole.ADMIN, UserRole.MANAGER]),
                User.phone_number.is_not(None),
            )
            .order_by(User.role, User.created_at)
        )
    ).all()
    unique = list(dict.fromkeys(p for p in phones if p))
    if unique:
        return unique
    company_phone = await db.scalar(select(Company.phone).where(Company.id == company_id))
    return [company_phone] if company_phone else []


async def primary_recipient(db: AsyncSession, company: Company) -> str | None:
    phones = await recipients(db, company.id)
    return phones[0] if phones else None


async def _find_open(
    db: AsyncSession, alert_type: AlertType, batch_id: uuid.UUID | None, product_id: uuid.UUID | None,
    company_id: uuid.UUID,
) -> Alert | None:
    stmt = select(Alert).where(
        Alert.company_id == company_id, Alert.alert_type == alert_type, Alert.is_resolved.is_(False)
    )
    if batch_id is not None:
        stmt = stmt.where(Alert.batch_id == batch_id)
    else:
        stmt = stmt.where(Alert.batch_id.is_(None), Alert.product_id == product_id)
    return await db.scalar(stmt.limit(1))


async def ensure_alert(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    alert_type: AlertType,
    severity: AlertSeverity,
    title: str,
    message: str,
    product_id: uuid.UUID | None = None,
    batch_id: uuid.UUID | None = None,
    days_remaining: int | None = None,
    quantity_at_risk: int | None = None,
    recipient_phone: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> tuple[Alert, bool]:
    """Create an alert unless an unresolved one of the same type exists for the batch/product.

    Returns (alert, created). Existing alerts get their live figures refreshed. The partial
    unique indexes make this race-safe: a concurrent duplicate insert is caught and reused.
    """
    existing = await _find_open(db, alert_type, batch_id, product_id, company_id)
    if existing is not None:
        existing.days_remaining = days_remaining
        existing.quantity_at_risk = quantity_at_risk
        existing.message = message
        existing.title = title
        return existing, False

    alert = Alert(
        company_id=company_id,
        product_id=product_id,
        batch_id=batch_id,
        alert_type=alert_type,
        severity=severity,
        title=title[:255],
        message=message,
        days_remaining=days_remaining,
        quantity_at_risk=quantity_at_risk,
        recipient_phone=recipient_phone,
    )
    try:
        async with db.begin_nested():
            db.add(alert)
            await db.flush()
    except IntegrityError:
        existing = await _find_open(db, alert_type, batch_id, product_id, company_id)
        if existing is None:
            raise
        return existing, False

    audit_service.record(
        db,
        action=AuditAction.ALERT_CREATED,
        company_id=company_id,
        user_id=actor_id,
        entity_type="alert",
        entity_id=alert.id,
        metadata={"alert_type": alert_type.value, "severity": severity.value, "batch_id": str(batch_id) if batch_id else None},
    )
    return alert, True


async def resolve_open_expiry_alerts(
    db: AsyncSession, batch_id: uuid.UUID, keep_type: AlertType | None = None
) -> int:
    """Resolve superseded expiry alerts for a batch (e.g. EXPIRING_SOON once it becomes CRITICAL)."""
    types = [t for t in EXPIRY_ALERT_TYPES if t != keep_type]
    open_alerts = (
        await db.scalars(
            select(Alert).where(
                Alert.batch_id == batch_id, Alert.alert_type.in_(types), Alert.is_resolved.is_(False)
            )
        )
    ).all()
    now = utcnow()
    for alert in open_alerts:
        alert.is_resolved = True
        alert.resolved_at = now
    return len(open_alerts)


async def list_alerts(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    *,
    alert_types: list[AlertType] | None = None,
    severity: AlertSeverity | None = None,
    is_read: bool | None = None,
    is_resolved: bool | None = None,
    product_id: uuid.UUID | None = None,
) -> tuple[list[Alert], int]:
    stmt = select(Alert).where(Alert.company_id == company_id)
    if alert_types:
        stmt = stmt.where(Alert.alert_type.in_(alert_types))
    if severity:
        stmt = stmt.where(Alert.severity == severity)
    if is_read is not None:
        stmt = stmt.where(Alert.is_read.is_(is_read))
    if is_resolved is not None:
        stmt = stmt.where(Alert.is_resolved.is_(is_resolved))
    if product_id:
        stmt = stmt.where(Alert.product_id == product_id)
    return await paginate(db, stmt.order_by(Alert.created_at.desc()), params)


async def get_alert(db: AsyncSession, company_id: uuid.UUID, alert_id: uuid.UUID) -> Alert:
    alert = await db.scalar(select(Alert).where(Alert.id == alert_id, Alert.company_id == company_id))
    if alert is None:
        raise NotFoundError("Alert not found")
    return alert


async def mark_read(db: AsyncSession, user: User, alert_id: uuid.UUID) -> Alert:
    alert = await get_alert(db, user.company_id, alert_id)
    alert.is_read = True
    await db.commit()
    return alert


async def resolve(db: AsyncSession, user: User, alert_id: uuid.UUID, ip: str | None) -> Alert:
    alert = await get_alert(db, user.company_id, alert_id)
    if not alert.is_resolved:
        alert.is_resolved = True
        alert.is_read = True
        alert.resolved_at = utcnow()
        audit_service.record(
            db, action=AuditAction.ALERT_RESOLVED, company_id=user.company_id, user_id=user.id,
            entity_type="alert", entity_id=alert.id, metadata={"alert_type": alert.alert_type.value}, ip_address=ip,
        )
        await db.commit()
    return alert


async def check_low_stock(db: AsyncSession, company_id: uuid.UUID) -> tuple[int, int]:
    """Create LOW_STOCK alerts for active products at/below the threshold; resolve recovered ones.

    Returns (created, resolved). Commits.
    """
    company = await db.get(Company, company_id)
    if company is None:
        return 0, 0
    today = today_in_tz(company.timezone)
    sellable = func.coalesce(
        func.sum(
            case(((Batch.expiry_date > today) & (Batch.remaining_quantity > 0), Batch.remaining_quantity), else_=0)
        ),
        0,
    )
    rows = (
        await db.execute(
            select(Product, sellable.label("stock"))
            .outerjoin(Batch, Batch.product_id == Product.id)
            .where(Product.company_id == company_id, Product.is_active.is_(True))
            .group_by(Product.id)
        )
    ).all()
    threshold = settings.LOW_STOCK_THRESHOLD
    recipient = await primary_recipient(db, company)
    created = resolved = 0
    for product, stock in rows:
        stock = int(stock or 0)
        if stock <= threshold:
            _, was_created = await ensure_alert(
                db,
                company_id=company_id,
                alert_type=AlertType.LOW_STOCK,
                severity=AlertSeverity.WARNING if stock > 0 else AlertSeverity.CRITICAL,
                title=f"Low stock: {product.name}",
                message=f"{product.name} has {stock} sellable {product.unit} left (threshold {threshold}).",
                product_id=product.id,
                quantity_at_risk=stock,
                recipient_phone=recipient,
            )
            created += int(was_created)
        else:
            open_alert = await _find_open(db, AlertType.LOW_STOCK, None, product.id, company_id)
            if open_alert:
                open_alert.is_resolved = True
                open_alert.resolved_at = utcnow()
                resolved += 1
    await db.commit()
    return created, resolved
