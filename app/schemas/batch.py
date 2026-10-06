from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from pydantic import BaseModel, Field, model_validator

from app.models.batch import BatchStatus
from app.schemas import ORMModel
from app.schemas.types import BatchNumber


class BatchCreate(BaseModel):
    product_id: uuid.UUID
    batch_number: BatchNumber
    manufacturing_date: date | None = None
    expiry_date: date
    initial_quantity: int = Field(gt=0, le=1_000_000_000)


    @model_validator(mode="after")
    def validate_dates(self) -> "BatchCreate":
        if self.manufacturing_date and self.expiry_date < self.manufacturing_date:
            raise ValueError("expiry_date cannot be before manufacturing_date")
        if self.manufacturing_date and self.manufacturing_date > date.today() + timedelta(days=1):
            raise ValueError("manufacturing_date cannot be in the future")
        return self


class BatchUpdate(BaseModel):
    """Quantities change only through sales and inventory adjustments (auditable ledger)."""

    batch_number: BatchNumber | None = None
    manufacturing_date: date | None = None
    expiry_date: date | None = None



class BatchRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    product_id: uuid.UUID
    product_name: str | None = None
    batch_number: str
    manufacturing_date: date | None
    expiry_date: date
    initial_quantity: int
    remaining_quantity: int
    status: BatchStatus
    days_remaining: int | None = Field(default=None, description="Calculated live against company 'today'")
    created_at: datetime
    updated_at: datetime
