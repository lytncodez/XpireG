"""Pagination parameters and helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from fastapi import Query
from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class PageParams:
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


def page_params(
    page: int = Query(1, ge=1, description="1-based page number"),
    page_size: int = Query(20, ge=1, le=200, description="Items per page (max 200)"),
) -> PageParams:
    return PageParams(page=page, page_size=page_size)


def page_meta(total: int, params: PageParams) -> dict[str, int]:
    return {
        "total": total,
        "page": params.page,
        "page_size": params.page_size,
        "pages": math.ceil(total / params.page_size) if total else 0,
    }


async def paginate(db: AsyncSession, stmt: Select, params: PageParams) -> tuple[list[Any], int]:
    """Execute `stmt` with limit/offset and return (scalars, total)."""
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.scalars(stmt.limit(params.page_size).offset(params.offset))).all()
    return list(rows), int(total)


async def paginate_rows(db: AsyncSession, stmt: Select, params: PageParams) -> tuple[list[Any], int]:
    """Like paginate() but returns full Row objects (multi-column selects)."""
    total = await db.scalar(select(func.count()).select_from(stmt.order_by(None).subquery())) or 0
    rows = (await db.execute(stmt.limit(params.page_size).offset(params.offset))).all()
    return list(rows), int(total)
