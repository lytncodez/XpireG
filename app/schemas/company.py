from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas import ORMModel
from app.utils.dates import is_valid_timezone
from app.schemas.types import OptionalPhone


class CompanyRead(ORMModel):
    id: uuid.UUID
    name: str
    email: EmailStr
    phone: str | None
    address: str | None
    currency: str
    timezone: str
    created_at: datetime
    updated_at: datetime


class CompanyUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    phone: OptionalPhone = None
    address: str | None = Field(default=None, max_length=1000)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    timezone: str | None = None


    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not v.isalpha():
            raise ValueError("Currency must be a 3-letter ISO code")
        return v.upper()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is not None and not is_valid_timezone(v):
            raise ValueError("Unknown IANA timezone")
        return v


class AuditLogRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    user_id: uuid.UUID | None
    action: str
    entity_type: str | None
    entity_id: str | None
    metadata: dict[str, Any] | None = Field(default=None, validation_alias="meta")
    ip_address: str | None
    created_at: datetime
