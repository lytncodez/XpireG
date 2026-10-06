"""Date/time helpers. All stored timestamps are timezone-aware UTC."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def safe_zone(tz_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(tz_name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def is_valid_timezone(tz_name: str) -> bool:
    try:
        ZoneInfo(tz_name)
        return True
    except (ZoneInfoNotFoundError, ValueError):
        return False


def today_in_tz(tz_name: str | None) -> date:
    """The calendar date 'today' for a company, in its own timezone."""
    return utcnow().astimezone(safe_zone(tz_name)).date()


def local_date(dt: datetime, tz_name: str | None) -> date:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(safe_zone(tz_name)).date()


def days_until(target: date, today: date) -> int:
    """days_remaining = expiry_date - current_date (whole calendar days)."""
    return (target - today).days


def start_of_local_day_utc(d: date, tz_name: str | None) -> datetime:
    """UTC instant at which local date `d` begins in the given timezone."""
    return datetime(d.year, d.month, d.day, tzinfo=safe_zone(tz_name)).astimezone(timezone.utc)


def week_start(d: date) -> date:
    return d - timedelta(days=d.weekday())


def month_start(d: date) -> date:
    return d.replace(day=1)


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y = d.year + m // 12
    return date(y, m % 12 + 1, 1)


def date_range(start: date, end: date) -> list[date]:
    """Inclusive list of dates from start to end."""
    if end < start:
        return []
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]
