"""Explainable, deterministic anomaly detection.

Method (all thresholds configurable):
  * Sales: compare the mean daily units of the recent window (default 7 days) with the
    baseline window before it (default 28 days). Flag when the z-score of the recent mean
    (baseline std / sqrt(n_recent)) exceeds ANOMALY_Z_THRESHOLD *and* the relative change
    exceeds ANOMALY_MIN_PCT_CHANGE. Evaluated per product and for the whole company.
  * Inventory: days of cover (sellable stock / baseline daily velocity) above
    ANOMALY_HIGH_COVER_DAYS or below ANOMALY_LOW_COVER_DAYS.
  * Stock movement: non-sale outflows (adjustments, damage, expiry write-offs, transfers) in
    the recent window far above their own baseline, or above 10% of stock on hand.

These are statistical observations. They do not establish causes.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import InventoryMovement, MovementType, Product
from app.schemas.analytics import Anomaly, AnomalyResponse
from app.services.analytics_service import _context, load_batches, load_sales
from app.utils.dates import date_range, local_date, start_of_local_day_utc

DISCLAIMER = (
    "Anomalies are statistical deviations from each item's own recent baseline. "
    "They indicate where to look, not why it happened; correlation is not causation."
)
MIN_BASELINE_DAILY_UNITS = 1.0
OUTFLOW_TYPES = (MovementType.ADJUSTMENT, MovementType.DAMAGED, MovementType.EXPIRED, MovementType.TRANSFER)


def _severity(z: float | None, pct: float | None) -> str:
    score = max(abs(z or 0) / max(settings.ANOMALY_Z_THRESHOLD, 0.1), abs(pct or 0) / 100)
    if score >= 3:
        return "CRITICAL"
    if score >= 1.5:
        return "WARNING"
    return "INFO"


def detect_series_anomaly(
    baseline: list[float], recent: list[float], z_threshold: float, min_pct: float
) -> tuple[bool, float | None, float | None, float, float]:
    """Return (is_anomaly, z, pct_change, recent_mean, baseline_mean). Pure function."""
    base_mean = statistics.fmean(baseline) if baseline else 0.0
    recent_mean = statistics.fmean(recent) if recent else 0.0
    if base_mean == 0 and recent_mean == 0:
        return False, None, None, recent_mean, base_mean
    pct = ((recent_mean - base_mean) / base_mean * 100) if base_mean else None
    std = statistics.pstdev(baseline) if len(baseline) > 1 else 0.0
    if std > 0 and recent:
        z = (recent_mean - base_mean) / (std / math.sqrt(len(recent)))
    else:
        z = None
    if pct is None:
        return False, z, pct, recent_mean, base_mean  # no baseline: new item, not an anomaly
    significant = abs(pct) >= min_pct and (z is None or abs(z) >= z_threshold)
    return significant, (round(z, 2) if z is not None else None), round(pct, 1), recent_mean, base_mean


async def detect(db: AsyncSession, company_id: uuid.UUID) -> AnomalyResponse:
    company, today = await _context(db, company_id)
    recent_days, baseline_days = settings.ANOMALY_RECENT_DAYS, settings.ANOMALY_BASELINE_DAYS
    recent_start = today - timedelta(days=recent_days - 1)
    baseline_start = recent_start - timedelta(days=baseline_days)
    baseline_end = recent_start - timedelta(days=1)
    window_label = (
        f"recent {recent_start.isoformat()}..{today.isoformat()} vs "
        f"baseline {baseline_start.isoformat()}..{baseline_end.isoformat()}"
    )
    z_thr, min_pct = settings.ANOMALY_Z_THRESHOLD, settings.ANOMALY_MIN_PCT_CHANGE
    anomalies: list[Anomaly] = []

    sales = await load_sales(db, company, baseline_start, today)
    daily: dict[uuid.UUID, dict[date, int]] = defaultdict(lambda: defaultdict(int))
    totals: dict[date, int] = defaultdict(int)
    names: dict[uuid.UUID, str] = {}
    for pid, name, _, qty, _, _, sold_at in sales:
        d = local_date(sold_at, company.timezone)
        daily[pid][d] += qty
        totals[d] += qty
        names[pid] = name
    base_dates = date_range(baseline_start, baseline_end)
    recent_dates = date_range(recent_start, today)

    # --- company-level sales ---------------------------------------------------------
    flag, z, pct, r_mean, b_mean = detect_series_anomaly(
        [totals[d] for d in base_dates], [totals[d] for d in recent_dates], z_thr, min_pct
    )
    if flag:
        direction = "increase" if r_mean > b_mean else "decline"
        anomalies.append(Anomaly(
            anomaly_type=f"UNUSUAL_SALES_{'INCREASE' if direction == 'increase' else 'DECLINE'}",
            severity=_severity(z, pct), entity_type="company", entity_id=company.id, entity_name=company.name,
            metric="units_per_day", observed=round(r_mean, 2), expected=round(b_mean, 2), deviation_score=z,
            pct_change=pct, window=window_label,
            explanation=f"Total units sold per day averaged {r_mean:.1f} recently versus {b_mean:.1f} in the baseline "
                        f"({pct:+.0f}%).",
        ))

    # --- product-level sales ----------------------------------------------------------
    baseline_velocity: dict[uuid.UUID, float] = {}
    for pid, by_day in daily.items():
        base = [by_day[d] for d in base_dates]
        recent = [by_day[d] for d in recent_dates]
        baseline_velocity[pid] = statistics.fmean(base) if base else 0.0
        if max(statistics.fmean(base), statistics.fmean(recent)) < MIN_BASELINE_DAILY_UNITS:
            continue  # too little volume to judge
        flag, z, pct, r_mean, b_mean = detect_series_anomaly(base, recent, z_thr, min_pct)
        if not flag:
            continue
        up = r_mean > b_mean
        anomalies.append(Anomaly(
            anomaly_type="UNUSUAL_SALES_INCREASE" if up else "UNUSUAL_SALES_DECLINE",
            severity=_severity(z, pct), entity_type="product", entity_id=pid, entity_name=names[pid],
            metric="units_per_day", observed=round(r_mean, 2), expected=round(b_mean, 2), deviation_score=z,
            pct_change=pct, window=window_label,
            explanation=f"{names[pid]} sold {r_mean:.1f} units/day in the last {recent_days} days versus "
                        f"{b_mean:.1f} in the prior {baseline_days} days ({pct:+.0f}%"
                        + (f", z={z:+.1f}" if z is not None else "") + ").",
        ))

    # --- inventory levels (days of cover) ---------------------------------------------
    batches = await load_batches(db, company_id)
    stock: dict[uuid.UUID, int] = defaultdict(int)
    for b in batches:
        if b.expiry_date > today:
            stock[b.product_id] += b.remaining
            names.setdefault(b.product_id, b.product_name)
    for pid, units in stock.items():
        velocity = baseline_velocity.get(pid, 0.0)
        if velocity <= 0:
            continue
        cover = units / velocity
        if cover > settings.ANOMALY_HIGH_COVER_DAYS:
            anomalies.append(Anomaly(
                anomaly_type="UNUSUALLY_HIGH_INVENTORY", severity="WARNING", entity_type="product",
                entity_id=pid, entity_name=names[pid], metric="days_of_cover", observed=round(cover, 1),
                expected=float(settings.ANOMALY_HIGH_COVER_DAYS), deviation_score=None, pct_change=None,
                window=f"baseline velocity {baseline_start.isoformat()}..{baseline_end.isoformat()}",
                explanation=f"{names[pid]} has {units} sellable units, about {cover:.0f} days of cover at "
                            f"{velocity:.2f} units/day (threshold {settings.ANOMALY_HIGH_COVER_DAYS} days).",
            ))
        elif cover < settings.ANOMALY_LOW_COVER_DAYS:
            anomalies.append(Anomaly(
                anomaly_type="UNUSUALLY_LOW_INVENTORY", severity="CRITICAL" if units == 0 else "WARNING",
                entity_type="product", entity_id=pid, entity_name=names[pid], metric="days_of_cover",
                observed=round(cover, 1), expected=float(settings.ANOMALY_LOW_COVER_DAYS), deviation_score=None,
                pct_change=None, window=f"baseline velocity {baseline_start.isoformat()}..{baseline_end.isoformat()}",
                explanation=f"{names[pid]} has {units} sellable units, under {settings.ANOMALY_LOW_COVER_DAYS} days "
                            f"of cover at {velocity:.2f} units/day.",
            ))
    for pid, velocity in baseline_velocity.items():
        if velocity >= MIN_BASELINE_DAILY_UNITS and stock.get(pid, 0) == 0:
            anomalies.append(Anomaly(
                anomaly_type="UNUSUALLY_LOW_INVENTORY", severity="CRITICAL", entity_type="product",
                entity_id=pid, entity_name=names[pid], metric="sellable_stock", observed=0.0,
                expected=round(velocity * settings.ANOMALY_LOW_COVER_DAYS, 1), deviation_score=None,
                pct_change=None, window=window_label,
                explanation=f"{names[pid]} usually sells {velocity:.1f} units/day but has no sellable stock.",
            ))

    # --- abnormal stock movements ------------------------------------------------------
    movements = (
        await db.execute(
            select(InventoryMovement.product_id, InventoryMovement.quantity, InventoryMovement.created_at, Product.name)
            .join(Product, Product.id == InventoryMovement.product_id)
            .where(
                InventoryMovement.company_id == company_id,
                InventoryMovement.movement_type.in_(OUTFLOW_TYPES),
                InventoryMovement.quantity < 0,
                InventoryMovement.created_at >= start_of_local_day_utc(baseline_start, company.timezone),
            )
        )
    ).all()
    out_recent: dict[uuid.UUID, int] = defaultdict(int)
    out_base: dict[uuid.UUID, int] = defaultdict(int)
    for pid, qty, created_at, name in movements:
        names.setdefault(pid, name)
        if local_date(created_at, company.timezone) >= recent_start:
            out_recent[pid] += -qty
        else:
            out_base[pid] += -qty
    for pid, recent_out in out_recent.items():
        expected = out_base[pid] / baseline_days * recent_days
        on_hand = stock.get(pid, 0)
        share = recent_out / (on_hand + recent_out) * 100 if (on_hand + recent_out) else 0
        if recent_out > max(3 * expected, 0) and share >= 10:
            anomalies.append(Anomaly(
                anomaly_type="ABNORMAL_STOCK_MOVEMENT", severity="CRITICAL" if share >= 30 else "WARNING",
                entity_type="product", entity_id=pid, entity_name=names[pid], metric="non_sale_outflow_units",
                observed=float(recent_out), expected=round(expected, 1), deviation_score=None,
                pct_change=round((recent_out - expected) / expected * 100, 1) if expected else None,
                window=window_label,
                explanation=f"{recent_out} units of {names[pid]} left stock through adjustments/damage/write-offs "
                            f"in the last {recent_days} days ({share:.0f}% of stock), versus about {expected:.1f} "
                            f"expected from the baseline.",
            ))

    severity_rank = {"CRITICAL": 0, "WARNING": 1, "INFO": 2}
    anomalies.sort(key=lambda a: (severity_rank.get(a.severity, 3), -(abs(a.pct_change or 0))))
    return AnomalyResponse(
        as_of=today,
        method=f"z-score of {recent_days}-day mean vs {baseline_days}-day baseline (|z|>={z_thr}, "
               f"|change|>={min_pct}%); days-of-cover bounds; non-sale outflow spikes",
        disclaimer=DISCLAIMER,
        anomalies=anomalies,
    )
