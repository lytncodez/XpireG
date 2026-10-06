from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from app.models.import_job import ImportFileType, ImportStatus
from app.schemas import ORMModel


class ImportRowError(BaseModel):
    row: int | None = Field(description="Spreadsheet row number (header = row 1)")
    field: str | None = None
    value: Any | None = None
    error_type: str = Field(description="INVALID | MISSING | DUPLICATE | FILE")
    message: str


class ImportJobRead(ORMModel):
    id: uuid.UUID
    company_id: uuid.UUID
    filename: str
    file_type: ImportFileType
    status: ImportStatus
    total_rows: int
    successful_rows: int
    failed_rows: int
    duplicate_rows: int
    summary: dict[str, Any] | None
    created_at: datetime
    completed_at: datetime | None


class ImportResult(BaseModel):
    job_id: uuid.UUID
    status: ImportStatus
    total_rows: int
    successful_rows: int
    failed_rows: int
    duplicate_rows: int
    products_created: int
    products_updated: int
    batches_created: int
    alerts_generated: int
    errors: list[ImportRowError] = Field(description="First 100 errors; full list at /imports/{id}/errors")


class ImportErrorsResponse(BaseModel):
    job_id: uuid.UUID
    total_errors: int
    errors: list[ImportRowError]
