"""Turns alerts into delivered notifications: Alert -> Notification records -> SMS service."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.exceptions import BadRequestError, NotFoundError
from app.core.logging import get_logger
from app.models import (
    SEVERITY_RANK,
    Alert,
    AlertSeverity,
    AuditAction,
    Company,
    Notification,
    NotificationChannel,
    NotificationStatus,
    User,
)
from app.services import audit_service
from app.services.alert_service import recipients as alert_recipients
from app.services.sms_service import SMSResult, send_sms
from app.utils.dates import utcnow
from app.utils.pagination import PageParams, paginate

logger = get_logger(__name__)

SMS_MAX_LENGTH = 459  # 3 concatenated GSM segments


@dataclass
class NotifyOutcome:
    sent: int = 0
    failed: int = 0
    skipped: int = 0


def should_send_sms(alert: Alert) -> bool:
    return SEVERITY_RANK[alert.severity] >= SEVERITY_RANK[AlertSeverity(settings.SMS_MIN_SEVERITY)]


def format_sms(alert: Alert, company_name: str) -> str:
    text = f"[ExpireGuard] {company_name}: {alert.message}"
    return text if len(text) <= SMS_MAX_LENGTH else text[: SMS_MAX_LENGTH - 3] + "..."


def _apply_result(notification: Notification, result: SMSResult) -> None:
    notification.attempts += 1
    notification.provider = result.provider
    if result.success:
        notification.status = NotificationStatus.SENT
        notification.provider_message_id = result.message_id
        notification.error_message = None
        notification.sent_at = utcnow()
    else:
        notification.status = NotificationStatus.FAILED
        notification.error_message = (result.error or "Unknown error")[:1000]


async def notify_alert(db: AsyncSession, alert: Alert, *, force: bool = False) -> NotifyOutcome:
    """Send SMS for one alert to every recipient. Records each attempt; commits; never raises."""
    outcome = NotifyOutcome()
    if not force and not should_send_sms(alert):
        outcome.skipped += 1
        return outcome
    company_name = await db.scalar(select(Company.name).where(Company.id == alert.company_id)) or "ExpireGuard"
    phones = await alert_recipients(db, alert.company_id)
    if not phones:
        alert.sms_error = "No recipient phone number configured (add one to an admin/manager or the company)"
        await db.commit()
        outcome.failed += 1
        return outcome

    body = format_sms(alert, company_name)
    errors: list[str] = []
    for phone in phones:
        notification = Notification(
            company_id=alert.company_id,
            alert_id=alert.id,
            channel=NotificationChannel.SMS,
            recipient=phone,
            message=body,
            status=NotificationStatus.PENDING,
            attempts=0,
        )
        db.add(notification)
        await db.flush()
        result = await send_sms(phone, body)
        _apply_result(notification, result)
        if result.success:
            outcome.sent += 1
            alert.sms_sent = True
            alert.sms_sent_at = notification.sent_at
            audit_service.record(
                db, action=AuditAction.SMS_SENT, company_id=alert.company_id, entity_type="notification",
                entity_id=notification.id, metadata={"alert_id": str(alert.id), "provider": result.provider},
            )
        else:
            outcome.failed += 1
            errors.append(f"{phone}: {result.error}")
            audit_service.record(
                db, action=AuditAction.SMS_FAILED, company_id=alert.company_id, entity_type="notification",
                entity_id=notification.id, metadata={"alert_id": str(alert.id), "error": result.error},
            )
    alert.recipient_phone = alert.recipient_phone or phones[0]
    alert.sms_error = "; ".join(errors)[:1000] if errors else None
    await db.commit()
    return outcome


async def notify_alerts(db: AsyncSession, alert_ids: list[uuid.UUID]) -> NotifyOutcome:
    total = NotifyOutcome()
    for alert_id in alert_ids:
        alert = await db.get(Alert, alert_id)
        if alert is None or alert.is_resolved:
            continue
        try:
            o = await notify_alert(db, alert)
        except Exception:  # noqa: BLE001 - one bad alert must not stop the batch
            logger.exception("Notification dispatch failed for alert %s", alert_id)
            await db.rollback()
            total.failed += 1
            continue
        total.sent += o.sent
        total.failed += o.failed
        total.skipped += o.skipped
    return total


async def retry_notification(db: AsyncSession, notification: Notification) -> Notification:
    result = await send_sms(notification.recipient, notification.message)
    _apply_result(notification, result)
    if notification.alert_id:
        alert = await db.get(Alert, notification.alert_id)
        if alert is not None and result.success:
            alert.sms_sent = True
            alert.sms_sent_at = notification.sent_at
            alert.sms_error = None
    audit_service.record(
        db,
        action=AuditAction.SMS_SENT if result.success else AuditAction.SMS_FAILED,
        company_id=notification.company_id,
        entity_type="notification",
        entity_id=notification.id,
        metadata={"retry": True, "attempts": notification.attempts, "error": result.error},
    )
    await db.commit()
    return notification


async def retry_failed(db: AsyncSession, company_id: uuid.UUID | None = None) -> NotifyOutcome:
    """Retry FAILED notifications below the attempt limit (used by the alert job)."""
    outcome = NotifyOutcome()
    stmt = select(Notification).where(
        Notification.status == NotificationStatus.FAILED, Notification.attempts < settings.SMS_MAX_ATTEMPTS
    )
    if company_id:
        stmt = stmt.where(Notification.company_id == company_id)
    for notification in (await db.scalars(stmt.limit(200))).all():
        await retry_notification(db, notification)
        if notification.status == NotificationStatus.SENT:
            outcome.sent += 1
        else:
            outcome.failed += 1
    return outcome


async def send_test_sms(db: AsyncSession, user: User, phone: str, message: str) -> Notification:
    notification = Notification(
        company_id=user.company_id,
        alert_id=None,
        channel=NotificationChannel.SMS,
        recipient=phone,
        message=message,
        status=NotificationStatus.PENDING,
        attempts=0,
    )
    db.add(notification)
    await db.flush()
    result = await send_sms(phone, message)
    _apply_result(notification, result)
    audit_service.record(
        db,
        action=AuditAction.SMS_SENT if result.success else AuditAction.SMS_FAILED,
        company_id=user.company_id,
        user_id=user.id,
        entity_type="notification",
        entity_id=notification.id,
        metadata={"test": True, "provider": result.provider, "error": result.error},
    )
    await db.commit()
    await db.refresh(notification)
    return notification


async def list_notifications(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    status: NotificationStatus | None = None,
    alert_id: uuid.UUID | None = None,
) -> tuple[list[Notification], int]:
    stmt = select(Notification).where(Notification.company_id == company_id)
    if status:
        stmt = stmt.where(Notification.status == status)
    if alert_id:
        stmt = stmt.where(Notification.alert_id == alert_id)
    return await paginate(db, stmt.order_by(Notification.created_at.desc()), params)


async def get_notification(db: AsyncSession, company_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
    n = await db.scalar(
        select(Notification).where(Notification.id == notification_id, Notification.company_id == company_id)
    )
    if n is None:
        raise NotFoundError("Notification not found")
    return n


async def retry_one(db: AsyncSession, company_id: uuid.UUID, notification_id: uuid.UUID) -> Notification:
    n = await get_notification(db, company_id, notification_id)
    if n.status == NotificationStatus.SENT:
        raise BadRequestError("Notification was already delivered")
    await retry_notification(db, n)
    await db.refresh(n)
    return n
