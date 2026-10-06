from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field

from app.models.alert import AlertSeverity, AlertType
from app.schemas import ORMModel
from app.schemas.types import Phone


class AlertRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    product_id: uuid.UUID | None
    batch_id: uuid.UUID | None
    alert_type: AlertType
    severity: AlertSeverity
    title: str
    message: str
    days_remaining: int | None
    quantity_at_risk: int | None
    recipient_phone: str | None
    is_read: bool
    is_resolved: bool
    sms_sent: bool
    sms_sent_at: datetime | None
    sms_error: str | None
    created_at: datetime
    resolved_at: datetime | None


class TestSMSRequest(BaseModel):
    phone_number: Phone = Field(description="E.164 recipient, e.g. +254712345678")
    message: str = Field(default="ExpireGuard test message: SMS delivery is working.", max_length=480)

