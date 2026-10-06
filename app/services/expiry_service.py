"""Deterministic expiry engine. No AI is ever involved in expiry calculations.

    days_remaining = expiry_date - today (company timezone)

    days <= 0                 -> EXPIRED
    1 .. CRITICAL_DAYS        -> CRITICAL
    CRITICAL_DAYS+1 .. SOON   -> EXPIRING_SOON
    > SOON_DAYS               -> SAFE
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.models import AlertSeverity, AlertType, AuditAction, Batch, BatchStatus, Product
from app.schemas.batch import BatchRead
from app.services import audit_service
from app.services.auth_service import get_company
from app.utils.dates import days_until, today_in_tz
from app.utils.pagination import PageParams, paginate_rows

logger = get_logger(__name__)


@dataclass(frozen=True)
class ExpiryThresholds:
    critical_days: int = 30
    soon_days: int = 90

    def __post_init__(self) -> None:
        if self.critical_days < 1 or self.soon_days <= self.critical_days:
            raise ValueError("Require 1 <= critical_days < soon_days")


def default_thresholds() -> ExpiryThresholds:
    return ExpiryThresholds(settings.EXPIRY_CRITICAL_DAYS, settings.EXPIRY_SOON_DAYS)


def classify(days_remaining: int, thresholds: ExpiryThresholds | None = None) -> BatchStatus:
    t = thresholds or default_thresholds()
    if days_remaining <= 0:
        return BatchStatus.EXPIRED
    if days_remaining <= t.critical_days:
        return BatchStatus.CRITICAL
    if days_remaining <= t.soon_days:
        return BatchStatus.EXPIRING_SOON
    return BatchStatus.SAFE


def batch_status(
    expiry_date: date, remaining_quantity: int, today: date, thresholds: ExpiryThresholds | None = None
) -> BatchStatus:
    """Status for a stored batch: empty batches are DEPLETED regardless of date."""
    if remaining_quantity <= 0:
        return BatchStatus.DEPLETED
    return classify(days_until(expiry_date, today), thresholds)


# status -> (alert type, severity)
STATUS_ALERTS: dict[BatchStatus, tuple[AlertType, AlertSeverity]] = {
    BatchStatus.EXPIRED: (AlertType.EXPIRED, AlertSeverity.URGENT),
    BatchStatus.CRITICAL: (AlertType.CRITICAL_EXPIRY, AlertSeverity.CRITICAL),
    BatchStatus.EXPIRING_SOON: (AlertType.EXPIRING_SOON, AlertSeverity.WARNING),
}


def alert_text(status: BatchStatus, product: Product, batch: Batch, days: int) -> tuple[str, str]:
    qty = f"{batch.remaining_quantity} {product.unit}"
    if status == BatchStatus.EXPIRED:
        ago = abs(days)
        when = "today" if ago == 0 else f"{ago} day{'s' if ago != 1 else ''} ago"
        return (
            f"EXPIRED: {product.name} (batch {batch.batch_number})",
            f"{product.name} batch {batch.batch_number} expired {when} ({batch.expiry_date.isoformat()}). "
            f"{qty} must be removed from sale.",
        )
    label = "Critical expiry" if status == BatchStatus.CRITICAL else "Expiring soon"
    return (
        f"{label}: {product.name} (batch {batch.batch_number})",
        f"{product.name} batch {batch.batch_number} expires in {days} day{'s' if days != 1 else ''} "
        f"({batch.expiry_date.isoformat()}). {qty} at risk.",
    )


@dataclass
class ExpiryCheckResult:
    company_id: uuid.UUID
    as_of: date
    batches_checked: int = 0
    statuses_updated: int = 0
    status_counts: dict[str, int] = field(default_factory=dict)
    alerts_created: int = 0
    duplicates_prevented: int = 0
    alerts_auto_resolved: int = 0
    sms_sent: int = 0
    sms_failed: int = 0
    new_alert_ids: list[uuid.UUID] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "company_id": str(self.company_id),
            "as_of": self.as_of.isoformat(),
            "batches_checked": self.batches_checked,
            "statuses_updated": self.statuses_updated,
            "status_counts": self.status_counts,
            "alerts_created": self.alerts_created,
            "duplicates_prevented": self.duplicates_prevented,
            "alerts_auto_resolved": self.alerts_auto_resolved,
            "sms_sent": self.sms_sent,
            "sms_failed": self.sms_failed,
            "new_alert_ids": [str(a) for a in self.new_alert_ids],
        }


async def run_expiry_check(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    today: date | None = None,
    notify: bool = True,
    actor_id: uuid.UUID | None = None,
    batch_ids: list[uuid.UUID] | None = None,
) -> ExpiryCheckResult:
    """Classify batches, persist statuses, create de-duplicated alerts, then notify.

    Alerts and statuses commit in one transaction; SMS delivery happens afterwards so a
    provider failure can never roll back or crash expiry processing.
    """
    from app.services import alert_service, notification_service

    company = await get_company(db, company_id)
    as_of = today or today_in_tz(company.timezone)
    thresholds = default_thresholds()
    result = ExpiryCheckResult(company_id=company_id, as_of=as_of)

    stmt = (
        select(Batch, Product)
        .join(Product, Product.id == Batch.product_id)
        .where(Batch.company_id == company_id)
        .where((Batch.remaining_quantity > 0) | (Batch.status != BatchStatus.DEPLETED))
        .order_by(Batch.expiry_date)
    )
    if batch_ids is not None:
        stmt = stmt.where(Batch.id.in_(batch_ids))
    rows = (await db.execute(stmt)).all()
    recipient = await alert_service.primary_recipient(db, company)

    for batch, product in rows:
        result.batches_checked += 1
        days = days_until(batch.expiry_date, as_of)
        new_status = batch_status(batch.expiry_date, batch.remaining_quantity, as_of, thresholds)
        result.status_counts[new_status.value] = result.status_counts.get(new_status.value, 0) + 1
        if batch.status != new_status:
            batch.status = new_status
            result.statuses_updated += 1

        if new_status == BatchStatus.DEPLETED or not product.is_active:
            # Nothing left at risk: close any open expiry alerts for this batch.
            result.alerts_auto_resolved += await alert_service.resolve_open_expiry_alerts(db, batch.id)
            continue

        spec = STATUS_ALERTS.get(new_status)
        if spec is None:  # SAFE (e.g. expiry date was corrected)
            result.alerts_auto_resolved += await alert_service.resolve_open_expiry_alerts(db, batch.id)
            continue
        alert_type, severity = spec
        result.alerts_auto_resolved += await alert_service.resolve_open_expiry_alerts(
            db, batch.id, keep_type=alert_type
        )
        title, message = alert_text(new_status, product, batch, days)
        alert, created = await alert_service.ensure_alert(
            db,
            company_id=company_id,
            alert_type=alert_type,
            severity=severity,
            title=title,
            message=message,
            product_id=product.id,
            batch_id=batch.id,
            days_remaining=days,
            quantity_at_risk=batch.remaining_quantity,
            recipient_phone=recipient,
            actor_id=actor_id,
        )
        if created:
            result.alerts_created += 1
            result.new_alert_ids.append(alert.id)
        else:
            result.duplicates_prevented += 1

    audit_service.record(
        db,
        action=AuditAction.EXPIRY_CHECK_RUN,
        company_id=company_id,
        user_id=actor_id,
        entity_type="company",
        entity_id=company_id,
        metadata={k: v for k, v in result.as_dict().items() if k != "new_alert_ids"},
    )
    await db.commit()

    if notify and result.new_alert_ids:
        outcome = await notification_service.notify_alerts(db, result.new_alert_ids)
        result.sms_sent, result.sms_failed = outcome.sent, outcome.failed

    logger.info(
        "Expiry check company=%s checked=%d created=%d duplicates=%d sms_sent=%d sms_failed=%d",
        company_id, result.batches_checked, result.alerts_created, result.duplicates_prevented,
        result.sms_sent, result.sms_failed,
    )
    return result


async def list_expiry(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    statuses: list[BatchStatus] | None = None,
    within_days: int | None = None,
) -> tuple[list[BatchRead], int]:
    """Live classification of in-stock batches (does not depend on the last stored status)."""
    company = await get_company(db, company_id)
    today = today_in_tz(company.timezone)
    t = default_thresholds()
    stmt = (
        select(Batch, Product.name)
        .join(Product, Product.id == Batch.product_id)
        .where(Batch.company_id == company_id, Batch.remaining_quantity > 0, Product.is_active.is_(True))
    )
    if statuses:
        ranges = []
        for s in statuses:
            if s == BatchStatus.EXPIRED:
                ranges.append(Batch.expiry_date <= today)
            elif s == BatchStatus.CRITICAL:
                ranges.append((Batch.expiry_date > today) & (Batch.expiry_date <= today + timedelta(days=t.critical_days)))
            elif s == BatchStatus.EXPIRING_SOON:
                ranges.append(
                    (Batch.expiry_date > today + timedelta(days=t.critical_days))
                    & (Batch.expiry_date <= today + timedelta(days=t.soon_days))
                )
            elif s == BatchStatus.SAFE:
                ranges.append(Batch.expiry_date > today + timedelta(days=t.soon_days))
        if ranges:
            stmt = stmt.where(or_(*ranges))
    if within_days is not None:
        stmt = stmt.where(Batch.expiry_date <= today + timedelta(days=within_days))
    rows, total = await paginate_rows(db, stmt.order_by(Batch.expiry_date, Batch.batch_number), params)
    reads = []
    for batch, product_name in rows:
        read = BatchRead.model_validate(batch)
        read.product_name = product_name
        read.days_remaining = days_until(batch.expiry_date, today)
        read.status = batch_status(batch.expiry_date, batch.remaining_quantity, today, t)
        reads.append(read)
    return reads, total
