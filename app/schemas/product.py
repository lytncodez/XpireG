from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.schemas import ORMModel
from app.schemas.types import OptionalBarcode, OptionalSKU


class ProductBase(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    category_id: uuid.UUID | None = None
    sku: OptionalSKU = None
    barcode: OptionalBarcode = None
    brand: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    unit: str = Field(default="unit", min_length=1, max_length=32)
    selling_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)
    cost_price: Decimal = Field(default=Decimal("0"), ge=0, max_digits=12, decimal_places=2)



class ProductCreate(ProductBase):
    @model_validator(mode="after")
    def validate_identifier(self) -> "ProductCreate":
        if not self.sku and not self.barcode:
            raise ValueError("A product needs at least a SKU or a barcode")
        return self


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    category_id: uuid.UUID | None = None
    sku: OptionalSKU = None
    barcode: OptionalBarcode = None
    brand: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    unit: str | None = Field(default=None, min_length=1, max_length=32)
    selling_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    cost_price: Decimal | None = Field(default=None, ge=0, max_digits=12, decimal_places=2)
    is_active: bool | None = None



class ProductRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    category_id: uuid.UUID | None
    category_name: str | None = None
    name: str
    sku: str | None
    barcode: str | None
    brand: str | None
    description: str | None
    unit: str
    selling_price: Decimal
    cost_price: Decimal
    is_active: bool
    total_stock: int = Field(default=0, description="All remaining units incl. expired")
    sellable_stock: int = Field(default=0, description="Remaining units in non-expired batches")
    created_at: datetime
    updated_at: datetime
