"""Builds the ONLY data the AI ever sees: a compact, verified, company-scoped context.

Steps: authenticate company (caller passes the authenticated user's company_id) -> pick the sections the
task needs -> call deterministic analytics / anomaly detection / alert queries -> detect patterns ->
round and trim -> return a JSON-serialisable dict. The database itself is never exposed to the model.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import Alert, AlertSeverity, InsightCategory, Product
from app.services import analytics_service, anomaly_service
from app.services.analytics_service import _context
from app.utils.dates import utcnow

CONTEXT_VERSION = 1
ALL_SECTIONS = frozenset({"inventory", "sales", "expiry", "products", "patterns", "anomalies", "alerts", "stock_risks"})

INTENT_SECTIONS: dict[str, frozenset[str]] = {
    "EXPIRY_RISK": frozenset({"inventory", "expiry", "alerts"}),
    "SALES_TREND": frozenset({"sales", "anomalies", "patterns", "expiry"}),
    "SLOW_MOVERS": frozenset({"products", "patterns", "expiry"}),
    "HIGH_STOCK_LOW_SALES": frozenset({"products", "patterns", "expiry"}),
    "LOW_STOCK": frozenset({"stock_risks", "patterns", "alerts", "expiry"}),
    "ANOMALIES": frozenset({"anomalies", "sales"}),
    "INVENTORY_OVERVIEW": frozenset({"inventory", "expiry", "alerts"}),
    "PRIORITIES": ALL_SECTIONS,
    "GENERAL": ALL_SECTIONS,
}

CATEGORY_SECTIONS: dict[InsightCategory, frozenset[str]] = {
    InsightCategory.EXPIRY: frozenset({"inventory", "expiry", "alerts"}),
    InsightCategory.WASTE_RISK: frozenset({"inventory", "expiry"}),
    InsightCategory.INVENTORY: frozenset({"inventory", "products", "patterns", "expiry"}),
    InsightCategory.STOCK_RISK: frozenset({"stock_risks", "patterns", "expiry"}),
    InsightCategory.SALES: frozenset({"sales", "anomalies"}),
    InsightCategory.PRODUCT_PERFORMANCE: frozenset({"sales", "products", "patterns", "expiry"}),
    InsightCategory.ANOMALY: frozenset({"anomalies", "sales"}),
}

_INTENT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("PRIORITIES", ("prioriti", "priority", "today", "focus", "first", "urgent", "what should i do", "action")),
    ("HIGH_STOCK_LOW_SALES", ("high stock", "overstock", "too much stock", "excess", "low sales")),
    ("EXPIRY_RISK", ("expir", "expire", "waste", "spoil", "shelf life", "fefo", "best before")),
    ("SALES_TREND", ("sales", "revenue", "declin", "growth", "trend", "selling less", "selling more", "drop")),
    ("SLOW_MOVERS", ("slow", "not selling", "moving slowly", "dead stock", "stagnant")),
    ("LOW_STOCK", ("low stock", "run out", "running out", "stock out", "stockout", "reorder", "replenish", "restock")),
    ("ANOMALIES", ("anomal", "unusual", "strange", "abnormal", "spike", "odd")),
    ("INVENTORY_OVERVIEW", ("inventory", "stock level", "how much stock", "stock value", "units in stock")),
]


def detect_intent(question: str) -> str:
    """Deterministic keyword intent classifier (no AI)."""
    q = re.sub(r"\s+", " ", question.lower())
    scores: dict[str, int] = defaultdict(int)
    for intent, words in _INTENT_KEYWORDS:
        for w in words:
            if w in q:
                scores[intent] += len(w)
    if not scores:
        return "GENERAL"
    return max(scores.items(), key=lambda kv: kv[1])[0]


def sections_for_categories(categories: list[InsightCategory]) -> frozenset[str]:
    out: set[str] = set()
    for c in categories:
        out |= CATEGORY_SECTIONS[c]
    return frozenset(out)


def _n(value: Any) -> Any:
    """Round floats to 2 dp; keep ints; leave everything else."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, float):
        r = round(value, 2)
        return int(r) if r.is_integer() else r
    return value


def _s(value: Any) -> str | None:
    return str(value) if value is not None else None


async def build_context(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    sections: frozenset[str] | set[str] | None = None,
    period_days: int = 30,
    intent: str = "GENERAL",
    limit: int | None = None,
) -> dict[str, Any]:
    sections = frozenset(sections or ALL_SECTIONS)
    limit = limit or settings.AI_CONTEXT_LIST_LIMIT
    company, today = await _context(db, company_id)
    start = today - timedelta(days=period_days - 1)

    ctx: dict[str, Any] = {
        "context_version": CONTEXT_VERSION,
        "generated_at": utcnow().isoformat(timespec="seconds"),
        "intent": intent,
        "business": {"name": company.name, "currency": company.currency, "timezone": company.timezone,
                     "as_of": today.isoformat()},
        "analysis_period": {"start": start.isoformat(), "end": today.isoformat(), "days": period_days},
        "thresholds": {
            "critical_days": settings.EXPIRY_CRITICAL_DAYS,
            "expiring_soon_days": settings.EXPIRY_SOON_DAYS,
            "low_stock_threshold": settings.LOW_STOCK_THRESHOLD,
            "anomaly_recent_days": settings.ANOMALY_RECENT_DAYS,
            "anomaly_baseline_days": settings.ANOMALY_BASELINE_DAYS,
        },
        "sections": sorted(sections),
    }
    notes: list[str] = ["ExpireGuard data contains stock, expiry, sales and adjustments only; it does not "
                        "record external factors such as prices of competitors, weather or promotions."]

    need_inventory = sections & {"inventory", "expiry", "patterns", "stock_risks"}
    need_products = sections & {"products", "patterns", "stock_risks"}
    inv = await analytics_service.inventory_analytics(db, company_id, period_days) if need_inventory else None
    prod = await analytics_service.product_analytics(db, company_id, period_days) if need_products else None

    if "inventory" in sections and inv is not None:
        m = inv.metrics
        ctx["inventory"] = {
            "total_products": m.total_products, "total_units": m.total_units,
            "inventory_value": _n(m.inventory_value), "retail_value": _n(m.retail_value),
            "expired_units": m.expired_units, "critical_units": m.critical_units,
            "expiring_soon_units": m.expiring_units, "safe_units": m.safe_units,
            "expired_value": _n(m.expired_value), "critical_value": _n(m.critical_value),
            "stock_turnover": _n(inv.stock_turnover), "annualized_turnover": _n(inv.annualized_turnover),
        }
        if m.total_units == 0:
            notes.append("No stock is currently recorded.")

    if "expiry" in sections and inv is not None:
        upcoming = [r for r in inv.expiry_risk if r.days_remaining > 0]
        expired = [r for r in inv.expiry_risk if r.days_remaining <= 0]
        upcoming.sort(key=lambda r: (r.days_remaining, -r.units_at_risk))

        def risk(r) -> dict[str, Any]:
            return {
                "product_id": str(r.product_id), "batch_id": str(r.batch_id), "product": r.product_name,
                "batch_number": r.batch_number, "expiry_date": r.expiry_date.isoformat(),
                "days_remaining": r.days_remaining, "remaining_quantity": r.remaining_quantity,
                "projected_sales_before_expiry": _n(r.projected_sales_before_expiry),
                "units_at_risk": r.units_at_risk, "value_at_risk": _n(r.value_at_risk),
            }

        ctx["expiry_risks"] = [risk(r) for r in upcoming[:limit]]
        ctx["expiry_risks_count"] = len(upcoming)
        ctx["expired_batches"] = [risk(r) for r in expired[:limit]]
        ctx["expired_batches_count"] = len(expired)
        ctx["waste_risk"] = {
            "projected_waste_units": sum(r.units_at_risk for r in upcoming),
            "projected_waste_value": _n(sum(r.value_at_risk for r in upcoming)),
            "batches_at_risk": len(upcoming),
            "expired_units": sum(r.remaining_quantity for r in expired),
            "expired_value": _n(sum(r.value_at_risk for r in expired)),
        }

    if "sales" in sections:
        ov = await analytics_service.overview(db, company_id, period_days)
        sa = await analytics_service.sales_analytics(db, company_id, start, today)
        ctx["sales"] = {
            "revenue": _n(ov.sales.revenue), "units_sold": ov.sales.units_sold, "transactions": ov.sales.transactions,
            "previous_revenue": _n(ov.sales.previous_revenue),
            "revenue_growth_pct": _n(ov.sales.revenue_growth_pct), "units_growth_pct": _n(ov.sales.units_growth_pct),
            "average_daily_revenue": _n(sa.average_daily_revenue),
            "top_products": [
                {"product_id": _s(p.id), "product": p.name, "units_sold": p.units_sold, "revenue": _n(p.revenue),
                 "share_pct": _n(p.share_pct)}
                for p in sa.by_product[:limit]
            ],
            "top_categories": [
                {"category": c.name, "units_sold": c.units_sold, "revenue": _n(c.revenue), "share_pct": _n(c.share_pct)}
                for c in sa.by_category[:limit]
            ],
        }
        if ov.sales.units_sold == 0:
            notes.append("No sales were recorded in the analysis period.")
        if ov.sales.revenue_growth_pct is None:
            notes.append("Growth versus the previous period is undefined because the previous period had no revenue.")

    if prod is not None and "products" in sections:
        ctx["product_performance"] = [
            {"product_id": str(p.product_id), "product": p.product_name, "classification": p.classification,
             "stock": p.stock, "units_sold": p.units_sold, "revenue": _n(p.revenue),
             "daily_velocity": _n(p.daily_velocity), "days_of_cover": _n(p.days_of_cover)}
            for p in prod.products[:limit]
        ]

    if "patterns" in sections and prod is not None:
        ctx["patterns"], ctx["pattern_counts"] = _detect_patterns(prod, inv, limit)

    if "stock_risks" in sections and prod is not None:
        risks = [p for p in prod.products if p.daily_velocity > 0 and (p.days_of_cover or 0) < 7]
        risks.sort(key=lambda p: p.days_of_cover or 0)
        ctx["stock_risks"] = [
            {"product_id": str(p.product_id), "product": p.product_name, "sellable_stock": p.stock,
             "daily_velocity": _n(p.daily_velocity), "days_of_cover": _n(p.days_of_cover)}
            for p in risks[:limit]
        ]
        ctx["stock_risks_count"] = len(risks)

    if "anomalies" in sections:
        an = await anomaly_service.detect(db, company_id)
        ctx["anomalies"] = [
            {"type": a.anomaly_type, "severity": a.severity, "entity_type": a.entity_type,
             "entity_id": _s(a.entity_id), "entity": a.entity_name, "metric": a.metric,
             "observed": _n(a.observed), "expected": _n(a.expected), "pct_change": _n(a.pct_change),
             "deviation_score": _n(a.deviation_score), "explanation": a.explanation}
            for a in an.anomalies[:limit]
        ]
        ctx["anomalies_count"] = len(an.anomalies)
        ctx["anomaly_method"] = an.method

    if "alerts" in sections:
        by_type = {
            t.value: int(c) for t, c in (await db.execute(
                select(Alert.alert_type, func.count())
                .where(Alert.company_id == company_id, Alert.is_resolved.is_(False))
                .group_by(Alert.alert_type)
            )).all()
        }
        rows = (await db.execute(
            select(Alert, Product.name)
            .outerjoin(Product, Product.id == Alert.product_id)
            .where(Alert.company_id == company_id, Alert.is_resolved.is_(False),
                   Alert.severity.in_([AlertSeverity.CRITICAL, AlertSeverity.URGENT]))
            .order_by(Alert.days_remaining.asc().nulls_last(), Alert.created_at.desc())
            .limit(limit)
        )).all()
        ctx["alerts"] = {
            "open_total": sum(by_type.values()),
            "by_type": by_type,
            "critical_and_urgent": [
                {"alert_type": a.alert_type.value, "severity": a.severity.value, "title": a.title,
                 "product": name, "product_id": _s(a.product_id), "batch_id": _s(a.batch_id),
                 "days_remaining": a.days_remaining, "quantity_at_risk": a.quantity_at_risk}
                for a, name in rows
            ],
        }

    ctx["data_quality"] = {"notes": notes}
    return ctx


def _detect_patterns(prod, inv, limit: int) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Deterministic pattern detection combining product performance with expiry risk."""
    soon_days = settings.EXPIRY_SOON_DAYS
    critical_days = settings.EXPIRY_CRITICAL_DAYS
    risks_by_product: dict[uuid.UUID, list] = defaultdict(list)
    if inv is not None:
        for r in inv.expiry_risk:
            if 0 < r.days_remaining <= soon_days:
                risks_by_product[r.product_id].append(r)

    def evidence(p) -> dict[str, Any]:
        return {"stock": p.stock, "units_sold": p.units_sold, "daily_velocity": _n(p.daily_velocity),
                "days_of_cover": _n(p.days_of_cover)}

    patterns: list[dict[str, Any]] = []
    for p in prod.high_stock_low_sales:
        near = sorted(risks_by_product.get(p.product_id, []), key=lambda r: r.days_remaining)
        if near:
            ev = evidence(p) | {"nearest_expiry_days": near[0].days_remaining,
                                "nearest_expiry_date": near[0].expiry_date.isoformat(),
                                "units_at_risk": near[0].units_at_risk, "batch_number": near[0].batch_number}
            patterns.append({"type": "HIGH_STOCK_LOW_SALES_SHORT_EXPIRY", "product_id": str(p.product_id),
                             "batch_id": str(near[0].batch_id), "product": p.product_name, "evidence": ev})
        else:
            patterns.append({"type": "HIGH_STOCK_LOW_SALES", "product_id": str(p.product_id),
                             "product": p.product_name, "evidence": evidence(p)})
    for p in prod.low_stock_high_sales:
        patterns.append({"type": "LOW_STOCK_HIGH_SALES", "product_id": str(p.product_id),
                         "product": p.product_name, "evidence": evidence(p)})
    for pid, risks in risks_by_product.items():
        critical = [r for r in risks if r.days_remaining <= critical_days]
        if len(critical) >= 2:
            patterns.append({
                "type": "MULTIPLE_BATCHES_NEAR_EXPIRY", "product_id": str(pid), "product": critical[0].product_name,
                "evidence": {"batch_count": len(critical), "batch_numbers": [r.batch_number for r in critical],
                             "units_at_risk": sum(r.units_at_risk for r in critical),
                             "remaining_quantity": sum(r.remaining_quantity for r in critical)},
            })
    for p in prod.fast_movers:
        patterns.append({"type": "FAST_MOVER", "product_id": str(p.product_id), "product": p.product_name,
                         "evidence": evidence(p)})
    for p in prod.slow_movers:
        patterns.append({"type": "SLOW_MOVER", "product_id": str(p.product_id), "product": p.product_name,
                         "evidence": evidence(p)})
    counts: dict[str, int] = defaultdict(int)
    for pat in patterns:
        counts[pat["type"]] += 1
    priority = {"HIGH_STOCK_LOW_SALES_SHORT_EXPIRY": 0, "MULTIPLE_BATCHES_NEAR_EXPIRY": 1, "LOW_STOCK_HIGH_SALES": 2,
                "HIGH_STOCK_LOW_SALES": 3, "SLOW_MOVER": 4, "FAST_MOVER": 5}
    patterns.sort(key=lambda x: priority.get(x["type"], 9))
    return patterns[: limit * 2], dict(counts)
