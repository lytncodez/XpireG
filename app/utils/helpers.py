"""Small cross-cutting helpers: money rounding, client IP, in-memory rate limiter."""

from __future__ import annotations

import time
from collections import defaultdict, deque
from decimal import ROUND_HALF_UP, Decimal
from threading import Lock

from fastapi import Request

TWO_PLACES = Decimal("0.01")


def money(value: Decimal | float | int | None) -> Decimal:
    if value is None:
        return Decimal("0.00")
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def to_float(value: Decimal | float | int | None) -> float:
    return float(money(value))


def pct_change(current: float, previous: float) -> float | None:
    """Percentage change; None when the previous value is zero (undefined growth)."""
    if previous == 0:
        return None
    return round((current - previous) / previous * 100, 2)


def client_ip(request: Request) -> str | None:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:45]
    return request.client.host[:45] if request.client else None


class RateLimiter:
    """Thread-safe sliding-window limiter, keyed per (scope, client).

    In-process only; replace with Redis when running multiple workers.
    """

    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def hit(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] > window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


rate_limiter = RateLimiter()
