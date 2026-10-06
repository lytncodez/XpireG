"""Alert maintenance: retry failed SMS deliveries and refresh LOW_STOCK alerts.

Run once from the CLI:  python -m app.jobs.alert_job
"""

from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models import Company
from app.services import alert_service, notification_service

logger = get_logger(__name__)


async def run_alert_job(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> dict[str, Any]:
    summary = {"sms_retried_sent": 0, "sms_retried_failed": 0, "low_stock_created": 0, "low_stock_resolved": 0}
    async with session_factory() as db:
        try:
            outcome = await notification_service.retry_failed(db)
            summary["sms_retried_sent"], summary["sms_retried_failed"] = outcome.sent, outcome.failed
        except Exception:  # noqa: BLE001
            await db.rollback()
            logger.exception("SMS retry pass failed")
        company_ids = list((await db.scalars(select(Company.id))).all())
    for company_id in company_ids:
        async with session_factory() as db:
            try:
                created, resolved = await alert_service.check_low_stock(db, company_id)
                summary["low_stock_created"] += created
                summary["low_stock_resolved"] += resolved
            except Exception:  # noqa: BLE001
                await db.rollback()
                logger.exception("Low-stock check failed for company %s", company_id)
    logger.info("Alert job: %s", summary)
    return summary


if __name__ == "__main__":
    configure_logging()
    print(asyncio.run(run_alert_job()))
