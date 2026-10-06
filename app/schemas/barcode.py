from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field

from app.schemas.batch import BatchRead
from app.schemas.product import ProductRead


class BarcodeLookupRequest(BaseModel):
    barcode: str = Field(min_length=1, max_length=64, description="Raw string captured by the scanner")


class BarcodeInventory(BaseModel):
    total_stock: int
    sellable_stock: int
    expired_stock: int
    active_batch_count: int
    nearest_expiry: date | None
    next_fefo_batch_id: str | None = Field(description="Batch that should be sold next (First-Expired-First-Out)")


class BarcodeLookupResponse(BaseModel):
    barcode: str
    product: ProductRead
    active_batches: list[BatchRead]
    inventory: BarcodeInventory
