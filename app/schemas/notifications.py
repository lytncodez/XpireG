from __future__ import annotations

import uuid
from datetime import datetime

from app.models.notification import NotificationChannel, NotificationStatus
from app.schemas import ORMModel


class NotificationRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    alert_id: uuid.UUID | None
    channel: NotificationChannel
    recipient: str
    message: str
    provider: str | None
    status: NotificationStatus
    attempts: int
    provider_message_id: str | None
    error_message: str | None
    sent_at: datetime | None
    created_at: datetime
