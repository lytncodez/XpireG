"""Daily expiry job: for every company -> classify batches -> alerts -> SMS -> record results.

Run once from the CLI:  python -m app.jobs.expiry_job
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models import Company
from app.services.expiry_service import run_expiry_check

logger = get_logger(__name__)


async def run_expiry_job(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> list[dict[str, Any]]:
    """Process each company in its own session so one failure never blocks the others."""
    async with session_factory() as db:
        company_ids = list((await db.scalars(select(Company.id))).all())
    results: list[dict[str, Any]] = []
    for company_id in company_ids:
        async with session_factory() as db:
            try:
                result = await run_expiry_check(db, company_id)
                results.append({"status": "ok", **result.as_dict()})
            except Exception as exc:  # noqa: BLE001
                await db.rollback()
                logger.exception("Expiry job failed for company %s", company_id)
                results.append({"status": "error", "company_id": str(company_id), "error": type(exc).__name__})
    logger.info("Expiry job finished for %d companies", len(results))
    return results


if __name__ == "__main__":
    configure_logging()
    for line in asyncio.run(run_expiry_job()):
        print(line)
