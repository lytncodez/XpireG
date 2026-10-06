"""Audit trail writes/reads. Writes join the caller's transaction (no commit here)."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditLog
from app.utils.pagination import PageParams, paginate


def record(
    db: AsyncSession,
    *,
    action: str,
    company_id: uuid.UUID | None,
    user_id: uuid.UUID | None = None,
    entity_type: str | None = None,
    entity_id: uuid.UUID | str | None = None,
    metadata: dict[str, Any] | None = None,
    ip_address: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        company_id=company_id,
        user_id=user_id,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        meta=metadata,
        ip_address=ip_address,
    )
    db.add(entry)
    return entry


async def list_logs(
    db: AsyncSession,
    company_id: uuid.UUID,
    params: PageParams,
    action: str | None = None,
    entity_type: str | None = None,
) -> tuple[list[AuditLog], int]:
    stmt = select(AuditLog).where(AuditLog.company_id == company_id)
    if action:
        stmt = stmt.where(AuditLog.action == action.upper())
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    return await paginate(db, stmt.order_by(AuditLog.created_at.desc()), params)
