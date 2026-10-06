"""Housekeeping: purge expired revoked tokens and old resolved alerts.

Run once from the CLI:  python -m app.jobs.cleanup_job
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.database import SessionLocal
from app.core.logging import configure_logging, get_logger
from app.models import Alert, RevokedToken
from app.utils.dates import utcnow

logger = get_logger(__name__)


async def run_cleanup_job(session_factory: async_sessionmaker[AsyncSession] = SessionLocal) -> dict[str, Any]:
    now = utcnow()
    async with session_factory() as db:
        tokens = await db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now))
        alerts = await db.execute(
            delete(Alert).where(
                Alert.is_resolved.is_(True),
                Alert.resolved_at < now - timedelta(days=settings.ALERT_RETENTION_DAYS),
            )
        )
        await db.commit()
    summary = {"revoked_tokens_purged": tokens.rowcount or 0, "resolved_alerts_purged": alerts.rowcount or 0}
    logger.info("Cleanup job: %s", summary)
    return summary


if __name__ == "__main__":
    configure_logging()
    print(asyncio.run(run_cleanup_job()))
