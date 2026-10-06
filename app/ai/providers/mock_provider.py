"""Deterministic mock provider.

Produces realistic JSON answers built ONLY from the supplied context, so the whole AI layer works with no
external API: local development, tests, demos and CI. Every number it writes is copied from the context,
so its output always passes the guardrails. The same builders are reused as the grounded fallback when a
real model's output fails validation.
"""

from __future__ import annotations

import json
import re
from typing import Any

from app.ai.base import AIProvider, AIResponse, AITask

ALL_CATEGORIES = ["EXPIRY", "WASTE_RISK", "INVENTORY", "STOCK_RISK", "SALES", "PRODUCT_PERFORMANCE", "ANOMALY"]
NO_CAUSE = "ExpireGuard data does not record external factors, so the data does not establish what caused this."


def _days(n: int) -> str:
    return f"{n} day" if abs(n) == 1 else f"{n} days"


def _ev(label: str, value: Any, source: str) -> dict[str, Any]:
    return {"label": label, "value": value, "source": source}


def _first(items: list[dict], pred) -> tuple[int, dict] | None:
    for i, item in enumerate(items):
        if pred(item):
            return i, item
    return None


# --------------------------------------------------------------------------- insights


def build_insights(ctx: dict[str, Any], categories: list[str] | None = None) -> dict[str, Any]:
    cats = categories or ctx.get("requested_categories") or ALL_CATEGORIES
    cur = ctx.get("business", {}).get("currency", "")
    th = ctx.get("thresholds", {})
    out: list[dict[str, Any]] = []

    risks = ctx.get("expiry_risks") or []
    expired = ctx.get("expired_batches") or []
    inv = ctx.get("inventory") or {}

    if "EXPIRY" in cats:
        if expired and inv.get("expired_units"):
            b = expired[0]
            out.append({
                "title": f"Expired stock on hand: {inv['expired_units']} units",
                "summary": f"{ctx['expired_batches_count']} batch(es) are past expiry, including {b['product']} "
                           f"batch {b['batch_number']} (expired {b['expiry_date']}).",
                "explanation": f"Expired units are recorded in stock and should not be sold. "
                               f"{b['product']} batch {b['batch_number']} still holds {b['remaining_quantity']} units.",
                "severity": "URGENT", "category": "EXPIRY",
                "supporting_evidence": [
                    _ev("Expired units", inv["expired_units"], "inventory.expired_units"),
                    _ev("Expired batches", ctx["expired_batches_count"], "expired_batches_count"),
                    _ev("Example batch", b["batch_number"], "expired_batches[0].batch_number"),
                ],
                "recommendation": "Remove the expired batches from sale and record them as EXPIRED inventory "
                                  "adjustments after verification.",
                "product_id": b["product_id"], "batch_id": b["batch_id"],
            })
        if risks:
            r = risks[0]
            sev = "CRITICAL" if r["days_remaining"] <= th.get("critical_days", 30) else "WARNING"
            out.append({
                "title": f"{r['product']} batch {r['batch_number']} expires in {_days(r['days_remaining'])}",
                "summary": f"{r['units_at_risk']} of {r['remaining_quantity']} units are not projected to sell "
                           f"before {r['expiry_date']}.",
                "explanation": f"At the recent sales rate the backend projects {r['projected_sales_before_expiry']} "
                               f"units to sell before expiry, leaving {r['units_at_risk']} units (value "
                               f"{r['value_at_risk']} {cur}) at risk. This is a projection, not a certainty.",
                "severity": sev, "category": "EXPIRY",
                "supporting_evidence": [
                    _ev("Days remaining", r["days_remaining"], "expiry_risks[0].days_remaining"),
                    _ev("Units at risk", r["units_at_risk"], "expiry_risks[0].units_at_risk"),
                    _ev("Remaining quantity", r["remaining_quantity"], "expiry_risks[0].remaining_quantity"),
                    _ev("Expiry date", r["expiry_date"], "expiry_risks[0].expiry_date"),
                ],
                "recommendation": "Prioritise this batch for FEFO movement and review whether a promotion or "
                                  "transfer is appropriate before it expires.",
                "product_id": r["product_id"], "batch_id": r["batch_id"],
            })

    w = ctx.get("waste_risk") or {}
    if "WASTE_RISK" in cats and w.get("projected_waste_units"):
        out.append({
            "title": f"Potential waste: {w['projected_waste_value']} {cur} of stock at risk",
            "summary": f"{w['projected_waste_units']} units across {w['batches_at_risk']} batch(es) are projected "
                       f"not to sell before they expire.",
            "explanation": "The projection applies each product's recent sales velocity to its batches in "
                           "First-Expired-First-Out order; units that cannot sell in time are counted as at risk.",
            "severity": "WARNING", "category": "WASTE_RISK",
            "supporting_evidence": [
                _ev("Projected waste units", w["projected_waste_units"], "waste_risk.projected_waste_units"),
                _ev("Projected waste value", w["projected_waste_value"], "waste_risk.projected_waste_value"),
                _ev("Batches at risk", w["batches_at_risk"], "waste_risk.batches_at_risk"),
            ],
            "recommendation": "Review the at-risk batches, starting with those expiring soonest, for promotion, "
                              "transfer or adjusted ordering.",
            "product_id": None, "batch_id": None,
        })

    patterns = ctx.get("patterns") or []
    if "INVENTORY" in cats:
        hit = _first(patterns, lambda p: p["type"] == "HIGH_STOCK_LOW_SALES_SHORT_EXPIRY") or \
            _first(patterns, lambda p: p["type"] == "HIGH_STOCK_LOW_SALES")
        if hit:
            i, p = hit
            e = p["evidence"]
            short = p["type"].endswith("SHORT_EXPIRY")
            cover = (f"At {e['daily_velocity']} units per day that is about {e['days_of_cover']} days of cover."
                     if e.get("days_of_cover") is not None else "No sales were recorded for it in the analysis period.")
            extra = (f" Its nearest at-risk batch {e['batch_number']} expires in {_days(e['nearest_expiry_days'])} "
                     f"with {e['units_at_risk']} units projected unsold." if short else "")
            evidence = [_ev("Sellable stock", e["stock"], f"patterns[{i}].evidence.stock"),
                        _ev("Units sold", e["units_sold"], f"patterns[{i}].evidence.units_sold")]
            if short:
                evidence.append(_ev("Days to nearest expiry", e["nearest_expiry_days"], f"patterns[{i}].evidence.nearest_expiry_days"))
            out.append({
                "title": f"High stock, low sales: {p['product']}",
                "summary": f"{p['product']} has {e['stock']} sellable units but sold {e['units_sold']} in the analysis period.",
                "explanation": cover + extra,
                "severity": "CRITICAL" if short else "WARNING", "category": "INVENTORY",
                "supporting_evidence": evidence,
                "recommendation": "Prioritise this product for a promotion or operational review (reorder "
                                  "quantities, shelf placement)" + (" and move the short-dated batch first." if short else "."),
                "product_id": p["product_id"], "batch_id": p.get("batch_id"),
            })

    if "STOCK_RISK" in cats:
        hit = _first(patterns, lambda p: p["type"] == "LOW_STOCK_HIGH_SALES")
        if hit:
            i, p = hit
            e = p["evidence"]
            out.append({
                "title": f"Low stock, high sales: {p['product']}",
                "summary": f"{p['product']} has {e['stock']} sellable units and sells about {e['daily_velocity']} per day.",
                "explanation": f"That is about {e['days_of_cover']} days of cover at the recent sales rate.",
                "severity": "CRITICAL" if e["stock"] == 0 else "WARNING", "category": "STOCK_RISK",
                "supporting_evidence": [
                    _ev("Sellable stock", e["stock"], f"patterns[{i}].evidence.stock"),
                    _ev("Daily velocity", e["daily_velocity"], f"patterns[{i}].evidence.daily_velocity"),
                    _ev("Days of cover", e["days_of_cover"], f"patterns[{i}].evidence.days_of_cover"),
                ],
                "recommendation": "Review replenishment for this product so it does not run out.",
                "product_id": p["product_id"], "batch_id": None,
            })

    s = ctx.get("sales") or {}
    if "SALES" in cats and s.get("revenue_growth_pct") is not None:
        g = s["revenue_growth_pct"]
        direction = "lower" if g < 0 else "higher"
        out.append({
            "title": f"Revenue is {direction} than the previous period ({g}%)",
            "summary": f"Revenue was {s['revenue']} {cur} in the analysis period versus {s['previous_revenue']} {cur} "
                       f"in the previous period of equal length.",
            "explanation": f"Units sold changed by {s['units_growth_pct']}% over the same comparison. {NO_CAUSE}"
            if s.get("units_growth_pct") is not None else NO_CAUSE,
            "severity": "WARNING" if g <= -10 else "INFO", "category": "SALES",
            "supporting_evidence": [
                _ev("Revenue", s["revenue"], "sales.revenue"),
                _ev("Previous revenue", s["previous_revenue"], "sales.previous_revenue"),
                _ev("Revenue growth %", g, "sales.revenue_growth_pct"),
            ],
            "recommendation": "Review the products with the largest changes in the sales analytics before drawing "
                              "conclusions about the reasons.",
            "product_id": None, "batch_id": None,
        })

    if "PRODUCT_PERFORMANCE" in cats and s.get("top_products"):
        t = s["top_products"][0]
        out.append({
            "title": f"Top product by revenue: {t['product']}",
            "summary": f"{t['product']} generated {t['revenue']} {cur} from {t['units_sold']} units "
                       f"({t['share_pct']}% of revenue) in the analysis period.",
            "explanation": "This ranking is based on recorded sales in the analysis period.",
            "severity": "INFO", "category": "PRODUCT_PERFORMANCE",
            "supporting_evidence": [
                _ev("Revenue", t["revenue"], "sales.top_products[0].revenue"),
                _ev("Units sold", t["units_sold"], "sales.top_products[0].units_sold"),
                _ev("Revenue share %", t["share_pct"], "sales.top_products[0].share_pct"),
            ],
            "recommendation": "Keep this product well stocked and monitor its batches for expiry.",
            "product_id": t["product_id"], "batch_id": None,
        })

    if "ANOMALY" in cats:
        for i, a in enumerate((ctx.get("anomalies") or [])[:3]):
            label = a["type"].replace("_", " ").lower()
            out.append({
                "title": f"Anomaly: {label} for {a['entity']}",
                "summary": a["explanation"],
                "explanation": f"Observed {a['observed']} versus an expected {a['expected']} for {a['metric']}. "
                               f"This is a statistical deviation from the item's own baseline; the data does not "
                               f"establish its cause.",
                "severity": a["severity"] if a["severity"] in ("INFO", "WARNING", "CRITICAL") else "WARNING",
                "category": "ANOMALY",
                "supporting_evidence": [
                    _ev("Observed", a["observed"], f"anomalies[{i}].observed"),
                    _ev("Expected", a["expected"], f"anomalies[{i}].expected"),
                    _ev("Metric", a["metric"], f"anomalies[{i}].metric"),
                ],
                "recommendation": "Check recent stock records, supply and pricing for this item to understand the change.",
                "product_id": a["entity_id"] if a["entity_type"] == "product" else None, "batch_id": None,
            })

    return {"insights": out[:8], "insufficient_evidence": not out,
            "notes": None if out else "The verified context does not contain evidence for the requested categories."}


# --------------------------------------------------------------------------- recommendations


def build_recommendations(ctx: dict[str, Any]) -> dict[str, Any]:
    th = ctx.get("thresholds", {})
    recs: list[dict[str, Any]] = []
    inv = ctx.get("inventory") or {}
    expired = ctx.get("expired_batches") or []
    if expired:
        b = expired[0]
        recs.append({
            "title": "Remove expired stock from sale",
            "action": "Pull the expired batches from shelves and record them as EXPIRED adjustments after checking them.",
            "rationale": f"{ctx.get('expired_batches_count', len(expired))} batch(es) are past expiry, including "
                         f"{b['product']} batch {b['batch_number']} with {b['remaining_quantity']} units.",
            "priority": "HIGH", "category": "EXPIRY",
            "supporting_evidence": [_ev("Remaining quantity", b["remaining_quantity"], "expired_batches[0].remaining_quantity"),
                                    _ev("Expiry date", b["expiry_date"], "expired_batches[0].expiry_date")],
            "product_id": b["product_id"], "batch_id": b["batch_id"],
        })
    for i, r in enumerate((ctx.get("expiry_risks") or [])[:2]):
        if r["days_remaining"] > th.get("critical_days", 30):
            break
        recs.append({
            "title": f"Prioritise FEFO for {r['product']} batch {r['batch_number']}",
            "action": "Place this batch in front of newer stock and sell it first; consider a promotion if it is not moving.",
            "rationale": f"It expires in {_days(r['days_remaining'])} ({r['expiry_date']}) with {r['units_at_risk']} "
                         f"of {r['remaining_quantity']} units projected unsold.",
            "priority": "HIGH", "category": "EXPIRY",
            "supporting_evidence": [_ev("Days remaining", r["days_remaining"], f"expiry_risks[{i}].days_remaining"),
                                    _ev("Units at risk", r["units_at_risk"], f"expiry_risks[{i}].units_at_risk")],
            "product_id": r["product_id"], "batch_id": r["batch_id"],
        })
    patterns = ctx.get("patterns") or []
    for i, p in enumerate(patterns):
        e = p["evidence"]
        if p["type"] == "MULTIPLE_BATCHES_NEAR_EXPIRY":
            recs.append({
                "title": f"Sequence {p['product']} batches by expiry",
                "action": "Sell these batches strictly First-Expired-First-Out and check shelf rotation.",
                "rationale": f"{e['batch_count']} batches of {p['product']} expire within the critical window.",
                "priority": "HIGH", "category": "EXPIRY",
                "supporting_evidence": [_ev("Batches near expiry", e["batch_count"], f"patterns[{i}].evidence.batch_count")],
                "product_id": p["product_id"], "batch_id": None,
            })
        elif p["type"] == "LOW_STOCK_HIGH_SALES":
            recs.append({
                "title": f"Review replenishment for {p['product']}",
                "action": "Check supplier lead time and consider placing a replenishment order.",
                "rationale": f"{e['stock']} sellable units at about {e['daily_velocity']} units per day "
                             f"({e['days_of_cover']} days of cover).",
                "priority": "HIGH" if e["stock"] == 0 else "MEDIUM", "category": "STOCK_RISK",
                "supporting_evidence": [_ev("Sellable stock", e["stock"], f"patterns[{i}].evidence.stock"),
                                        _ev("Days of cover", e["days_of_cover"], f"patterns[{i}].evidence.days_of_cover")],
                "product_id": p["product_id"], "batch_id": None,
            })
        elif p["type"] in ("HIGH_STOCK_LOW_SALES_SHORT_EXPIRY", "HIGH_STOCK_LOW_SALES"):
            short = p["type"].endswith("SHORT_EXPIRY")
            rationale = f"{e['stock']} sellable units but {e['units_sold']} sold in the analysis period."
            if short:
                rationale += f" Nearest at-risk batch expires in {_days(e['nearest_expiry_days'])}."
            recs.append({
                "title": f"Review promotion for {p['product']}",
                "action": "Consider a promotion or better shelf placement, and reduce the next order quantity.",
                "rationale": rationale,
                "priority": "HIGH" if short else "MEDIUM", "category": "INVENTORY",
                "supporting_evidence": [_ev("Sellable stock", e["stock"], f"patterns[{i}].evidence.stock"),
                                        _ev("Units sold", e["units_sold"], f"patterns[{i}].evidence.units_sold")],
                "product_id": p["product_id"], "batch_id": p.get("batch_id"),
            })
    anomalies = ctx.get("anomalies") or []
    if anomalies:
        a = anomalies[0]
        recs.append({
            "title": f"Investigate unusual activity for {a['entity']}",
            "action": "Check recent stock records, deliveries and pricing for this item.",
            "rationale": a["explanation"],
            "priority": "MEDIUM", "category": "ANOMALY",
            "supporting_evidence": [_ev("Observed", a["observed"], "anomalies[0].observed"),
                                    _ev("Expected", a["expected"], "anomalies[0].expected")],
            "product_id": a["entity_id"] if a["entity_type"] == "product" else None, "batch_id": None,
        })
    s = ctx.get("sales") or {}
    if s.get("revenue_growth_pct") is not None and s["revenue_growth_pct"] < 0:
        recs.append({
            "title": "Review the sales decline by product",
            "action": "Open the sales analytics and compare product-level results with the previous period.",
            "rationale": f"Revenue changed by {s['revenue_growth_pct']}% versus the previous period. {NO_CAUSE}",
            "priority": "LOW", "category": "SALES",
            "supporting_evidence": [_ev("Revenue growth %", s["revenue_growth_pct"], "sales.revenue_growth_pct")],
            "product_id": None, "batch_id": None,
        })
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    recs.sort(key=lambda r: order[r["priority"]])
    _ = inv
    return {"recommendations": recs[:8], "insufficient_evidence": not recs,
            "notes": None if recs else "No grounded recommendation can be made from the current data."}


# --------------------------------------------------------------------------- chat


def build_chat(ctx: dict[str, Any], intent: str, question: str = "") -> dict[str, Any]:
    cur = ctx.get("business", {}).get("currency", "")
    lines: list[str] = []
    evidence: list[dict[str, Any]] = []
    insufficient = False
    follow = ["What should I prioritise today?", "Which products are at highest expiry risk?",
              "Which products have high stock but low sales?"]
    patterns = ctx.get("patterns") or []

    if intent == "EXPIRY_RISK":
        risks = ctx.get("expiry_risks") or []
        if risks:
            lines.append("Batches with the highest expiry risk (soonest first):")
            for i, r in enumerate(risks[:5]):
                lines.append(f"- {r['product']} batch {r['batch_number']}: {_days(r['days_remaining'])} left "
                             f"(expires {r['expiry_date']}), {r['units_at_risk']} of {r['remaining_quantity']} units "
                             f"projected unsold.")
                evidence.append(_ev(f"{r['product']} days remaining", r["days_remaining"], f"expiry_risks[{i}].days_remaining"))
        else:
            lines.append("No in-stock batches are projected to be at expiry risk in the current data.")
        inv = ctx.get("inventory") or {}
        if inv.get("expired_units"):
            lines.append(f"Already expired and still in stock: {inv['expired_units']} units.")
            evidence.append(_ev("Expired units", inv["expired_units"], "inventory.expired_units"))
    elif intent == "SALES_TREND":
        s = ctx.get("sales") or {}
        p = ctx.get("analysis_period", {})
        if s.get("revenue_growth_pct") is None:
            insufficient = True
            lines.append("There is not enough sales history to compare this period with the previous one.")
        else:
            lines.append(f"Revenue from {p.get('start')} to {p.get('end')} was {s['revenue']} {cur} versus "
                         f"{s['previous_revenue']} {cur} in the previous period ({s['revenue_growth_pct']}%).")
            evidence += [_ev("Revenue", s["revenue"], "sales.revenue"),
                         _ev("Revenue growth %", s["revenue_growth_pct"], "sales.revenue_growth_pct")]
        declines = [(i, a) for i, a in enumerate(ctx.get("anomalies") or []) if a["type"] == "UNUSUAL_SALES_DECLINE"]
        if declines:
            lines.append("Products with an unusual recent decline:")
            for i, a in declines[:5]:
                lines.append(f"- {a['explanation']}")
                evidence.append(_ev(f"{a['entity']} observed", a["observed"], f"anomalies[{i}].observed"))
        lines.append(NO_CAUSE)
        if "why" in question.lower():
            insufficient = True
    elif intent in ("SLOW_MOVERS", "HIGH_STOCK_LOW_SALES"):
        wanted = {"HIGH_STOCK_LOW_SALES", "HIGH_STOCK_LOW_SALES_SHORT_EXPIRY"}
        if intent == "SLOW_MOVERS":
            wanted.add("SLOW_MOVER")
        hits = [(i, p) for i, p in enumerate(patterns) if p["type"] in wanted]
        seen: set[str] = set()
        if hits:
            lines.append("Products with high stock relative to sales:" if intent == "HIGH_STOCK_LOW_SALES"
                         else "Slow-moving products:")
            for i, p in hits:
                if p["product_id"] in seen:
                    continue
                seen.add(p["product_id"])
                e = p["evidence"]
                cover = f"about {e['days_of_cover']} days of cover" if e.get("days_of_cover") is not None else "no sales in the period"
                lines.append(f"- {p['product']}: {e['stock']} in stock, {e['units_sold']} sold, {cover}.")
                evidence.append(_ev(f"{p['product']} stock", e["stock"], f"patterns[{i}].evidence.stock"))
        else:
            lines.append("No products currently match that pattern in the analysis period.")
    elif intent == "LOW_STOCK":
        risks = ctx.get("stock_risks") or []
        if risks:
            lines.append("Products that could run out soon at the recent sales rate:")
            for i, r in enumerate(risks[:5]):
                lines.append(f"- {r['product']}: {r['sellable_stock']} sellable units, about {r['days_of_cover']} days of cover.")
                evidence.append(_ev(f"{r['product']} stock", r["sellable_stock"], f"stock_risks[{i}].sellable_stock"))
        else:
            lines.append("No products are close to running out based on recent sales.")
    elif intent == "ANOMALIES":
        anomalies = ctx.get("anomalies") or []
        if anomalies:
            lines.append("Detected anomalies (statistical deviations, not established causes):")
            for i, a in enumerate(anomalies[:5]):
                lines.append(f"- {a['explanation']}")
                evidence.append(_ev(f"{a['entity']} observed", a["observed"], f"anomalies[{i}].observed"))
        else:
            lines.append("No anomalies were detected in the current data.")
    elif intent == "INVENTORY_OVERVIEW":
        inv = ctx.get("inventory") or {}
        if inv:
            lines.append(f"Stock: {inv['total_units']} units across {inv['total_products']} products, valued at "
                         f"{inv['inventory_value']} {cur} at cost.")
            lines.append(f"Expired: {inv['expired_units']} units; critical: {inv['critical_units']}; expiring soon: "
                         f"{inv['expiring_soon_units']}; safe: {inv['safe_units']}.")
            evidence += [_ev("Total units", inv["total_units"], "inventory.total_units"),
                         _ev("Inventory value", inv["inventory_value"], "inventory.inventory_value")]
        else:
            insufficient = True
            lines.append("Inventory data is not available.")
    elif intent == "PRIORITIES":
        recs = build_recommendations(ctx)["recommendations"][:5]
        if recs:
            lines.append("Suggested priorities for today (for your review):")
            for r in recs:
                lines.append(f"- [{r['priority']}] {r['title']}: {r['rationale']}")
                evidence += r["supporting_evidence"][:1]
        else:
            lines.append("Nothing in the current data requires urgent attention.")
    else:
        inv = ctx.get("inventory") or {}
        alerts = ctx.get("alerts") or {}
        if inv:
            lines.append(f"You have {inv['total_units']} units in stock across {inv['total_products']} products.")
            evidence.append(_ev("Total units", inv["total_units"], "inventory.total_units"))
        if alerts:
            lines.append(f"There are {alerts['open_total']} open alerts.")
            evidence.append(_ev("Open alerts", alerts["open_total"], "alerts.open_total"))
        lines.append("I can answer questions about expiry risk, slow-moving or overstocked products, low stock, "
                     "sales trends, anomalies and today's priorities, using ExpireGuard's verified data.")
        if not inv and not alerts:
            insufficient = True

    return {"answer": "\n".join(lines), "evidence": evidence[:30], "insufficient_evidence": insufficient,
            "follow_up_suggestions": follow}


class MockProvider(AIProvider):
    name = "mock"
    model = "expireguard-mock-1"

    async def generate_response(self, context: dict[str, Any], prompt: str, *, system: str, task: AITask,
                                history: list[dict[str, str]] | None = None) -> AIResponse:
        if task in (AITask.INSIGHTS, AITask.ANOMALY_EXPLANATION):
            cats = ["ANOMALY"] if task == AITask.ANOMALY_EXPLANATION else context.get("requested_categories")
            payload = build_insights(context, cats)
        elif task == AITask.RECOMMENDATIONS:
            payload = build_recommendations(context)
        else:
            m = re.search(r"<<<\n(.*)\n>>>", prompt, re.DOTALL)
            payload = build_chat(context, context.get("intent", "GENERAL"), m.group(1) if m else "")
        return AIResponse(text=json.dumps(payload, default=str), provider=self.name, model=self.model)
