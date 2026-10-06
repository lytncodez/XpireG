"""Lightweight in-process scheduler (APScheduler).

Jobs are plain async functions taking a session factory, so they can be moved to
Celery/RQ/cron later without changes: just call them from the new runner.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.jobs.alert_job import run_alert_job
from app.jobs.cleanup_job import run_cleanup_job
from app.jobs.expiry_job import run_expiry_job


def create_scheduler() -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone="UTC", job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
    scheduler.add_job(
        run_expiry_job, CronTrigger(hour=settings.EXPIRY_JOB_HOUR, minute=settings.EXPIRY_JOB_MINUTE),
        id="expiry_job", replace_existing=True,
    )
    scheduler.add_job(
        run_alert_job, IntervalTrigger(minutes=settings.ALERT_JOB_INTERVAL_MINUTES),
        id="alert_job", replace_existing=True,
    )
    scheduler.add_job(
        run_cleanup_job, CronTrigger(hour=settings.CLEANUP_JOB_HOUR, minute=30),
        id="cleanup_job", replace_existing=True,
    )
    return scheduler
