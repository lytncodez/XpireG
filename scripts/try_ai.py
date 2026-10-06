"""Try ExpireGuard's AI layer on a built-in sample store. No database needed.

    python scripts/try_ai.py                                   # insights + recommendations
    python scripts/try_ai.py "What should I prioritize today?" # ...plus a chat answer
    python scripts/try_ai.py --show-context                    # print what the AI receives

Uses AI_PROVIDER from .env (mock by default). With openai/anthropic set up, the real model answers and
every output still goes through schema validation and the guardrails, exactly as in the API.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ai.base import AIProviderError, AITask  # noqa: E402
from app.ai.context_builder import detect_intent  # noqa: E402
from app.ai.factory import get_provider_selection  # noqa: E402
from app.ai.guardrails import (  # noqa: E402
    AIOutputError,
    collect_facts,
    parse_json_object,
    validate_chat,
    validate_insight,
    validate_model,
    validate_recommendation,
)
from app.ai.prompts import RECOMMENDATION_PROMPT, SYSTEM_RULES, chat_prompt, insight_prompt  # noqa: E402
from app.ai.providers.mock_provider import ALL_CATEGORIES, build_chat  # noqa: E402
from app.schemas.ai import ModelChatOutput  # noqa: E402
from app.schemas.insights import ModelInsightOutput, ModelRecommendationOutput  # noqa: E402

MILK, YOG, CHEESE, FORMULA, CROISSANT = (f"0000000{i}-0000-0000-0000-00000000000{i}" for i in range(1, 6))
B_MILK, B_YOG, B_CHEESE = (f"1000000{i}-0000-0000-0000-00000000000{i}" for i in range(1, 4))

# A verified context in exactly the shape app/ai/context_builder.py produces from the database.
SAMPLE_CONTEXT = {
    "context_version": 1, "intent": "GENERAL",
    "business": {"name": "Demo Fresh Mart", "currency": "KES", "timezone": "Africa/Nairobi", "as_of": "2026-10-05"},
    "analysis_period": {"start": "2026-09-06", "end": "2026-10-05", "days": 30},
    "thresholds": {"critical_days": 30, "expiring_soon_days": 90, "low_stock_threshold": 10,
                   "anomaly_recent_days": 7, "anomaly_baseline_days": 28},
    "inventory": {"total_products": 17, "total_units": 3963, "inventory_value": 7740.85, "retail_value": 12001.5,
                  "expired_units": 35, "critical_units": 400, "expiring_soon_units": 600, "safe_units": 2928,
                  "expired_value": 37.0, "critical_value": 450.25, "stock_turnover": 1.2, "annualized_turnover": 14.6},
    "expiry_risks": [
        {"product_id": MILK, "batch_id": B_MILK, "product": "Fresh Milk 1L", "batch_number": "MLK-2610-01",
         "expiry_date": "2026-10-06", "days_remaining": 1, "remaining_quantity": 40,
         "projected_sales_before_expiry": 23.4, "units_at_risk": 17, "value_at_risk": 17.85},
        {"product_id": CHEESE, "batch_id": B_CHEESE, "product": "Cheddar Cheese 200g", "batch_number": "CHD-2610-01",
         "expiry_date": "2026-12-04", "days_remaining": 60, "remaining_quantity": 400,
         "projected_sales_before_expiry": 8, "units_at_risk": 392, "value_at_risk": 1254.4},
    ],
    "expiry_risks_count": 2,
    "expired_batches": [
        {"product_id": YOG, "batch_id": B_YOG, "product": "Natural Yoghurt 500g", "batch_number": "YOG-2610-01",
         "expiry_date": "2026-10-02", "days_remaining": -3, "remaining_quantity": 15,
         "projected_sales_before_expiry": 0, "units_at_risk": 15, "value_at_risk": 24},
    ],
    "expired_batches_count": 1,
    "waste_risk": {"projected_waste_units": 409, "projected_waste_value": 1272.25, "batches_at_risk": 2,
                   "expired_units": 15, "expired_value": 24},
    "sales": {"revenue": 12450.5, "units_sold": 3400, "transactions": 900, "previous_revenue": 13561,
              "revenue_growth_pct": -8.19, "units_growth_pct": -5.5, "average_daily_revenue": 415.02,
              "top_products": [{"product_id": MILK, "product": "Fresh Milk 1L", "units_sold": 720, "revenue": 1080,
                                "share_pct": 8.67}],
              "top_categories": [{"category": "Dairy", "units_sold": 1500, "revenue": 4200, "share_pct": 33.73}]},
    "patterns": [
        {"type": "HIGH_STOCK_LOW_SALES_SHORT_EXPIRY", "product_id": CHEESE, "batch_id": B_CHEESE,
         "product": "Cheddar Cheese 200g",
         "evidence": {"stock": 400, "units_sold": 4, "daily_velocity": 0.13, "days_of_cover": 3000,
                      "nearest_expiry_days": 60, "nearest_expiry_date": "2026-12-04", "units_at_risk": 392,
                      "batch_number": "CHD-2610-01"}},
        {"type": "LOW_STOCK_HIGH_SALES", "product_id": FORMULA, "product": "Infant Formula 400g",
         "evidence": {"stock": 8, "units_sold": 315, "daily_velocity": 10.5, "days_of_cover": 0.8}},
    ],
    "pattern_counts": {"HIGH_STOCK_LOW_SALES_SHORT_EXPIRY": 1, "LOW_STOCK_HIGH_SALES": 1},
    "stock_risks": [{"product_id": FORMULA, "product": "Infant Formula 400g", "sellable_stock": 8,
                     "daily_velocity": 10.5, "days_of_cover": 0.8}],
    "stock_risks_count": 1,
    "anomalies": [
        {"type": "UNUSUAL_SALES_DECLINE", "severity": "CRITICAL", "entity_type": "product", "entity_id": CROISSANT,
         "entity": "Butter Croissants 4pk", "metric": "units_per_day", "observed": 0.43, "expected": 10.04,
         "pct_change": -95.7, "deviation_score": -40.1,
         "explanation": "Butter Croissants 4pk sold 0.4 units/day in the last 7 days versus 10.0 in the prior "
                        "28 days (-96%, z=-40.1)."},
    ],
    "anomalies_count": 1,
    "alerts": {"open_total": 9, "by_type": {"CRITICAL_EXPIRY": 4, "EXPIRED": 1, "EXPIRING_SOON": 4},
               "critical_and_urgent": []},
    "data_quality": {"notes": ["ExpireGuard data contains stock, expiry, sales and adjustments only; it does not "
                               "record external factors such as prices of competitors, weather or promotions."]},
}


def line(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


async def run_batch(provider, task, prompt, key, schema, validator, facts) -> None:
    try:
        resp = await provider.generate_response(SAMPLE_CONTEXT, prompt, system=SYSTEM_RULES, task=task)
        items = parse_json_object(resp.text).get(key) or []
    except (AIProviderError, AIOutputError) as exc:
        print(f"  Provider/parse failure: {exc}")
        return
    if not items:
        print("  (no items returned)")
    for item in items:
        try:
            out = validate_model(item, schema)
        except AIOutputError as exc:
            print(f"  REJECTED (schema): {exc}")
            continue
        result = validator(out, SAMPLE_CONTEXT, facts)
        label = getattr(out, "severity", None) or getattr(out, "priority", None)
        print(f"\n  [{'PASS' if result.ok else 'REJECTED'}] [{label.value}] [{out.category.value}] {out.title}")
        if result.ok:
            body = getattr(out, "summary", None) or getattr(out, "rationale", "")
            print(f"     {body}")
            print(f"     -> {getattr(out, 'recommendation', None) or out.action}")
        else:
            for v in result.violations[:5]:
                print(f"     ! {v}")


async def main() -> None:
    args = sys.argv[1:]
    if "--show-context" in args:
        print(json.dumps(SAMPLE_CONTEXT, indent=2))
        return
    sel = get_provider_selection()
    provider = sel.provider
    print(f"AI provider: {provider.name}  model: {provider.model}  (configured: {sel.configured})")
    if sel.fallback_reason:
        print(f"Using mock because: {sel.fallback_reason}")
    facts = collect_facts(SAMPLE_CONTEXT)

    line("INSIGHTS")
    SAMPLE_CONTEXT["requested_categories"] = ALL_CATEGORIES
    await run_batch(provider, AITask.INSIGHTS, insight_prompt(ALL_CATEGORIES), "insights",
                    ModelInsightOutput, validate_insight, facts)

    line("RECOMMENDATIONS")
    await run_batch(provider, AITask.RECOMMENDATIONS, RECOMMENDATION_PROMPT, "recommendations",
                    ModelRecommendationOutput, validate_recommendation, facts)

    question = " ".join(a for a in args if not a.startswith("--"))
    if question:
        intent = detect_intent(question)
        SAMPLE_CONTEXT["intent"] = intent
        line(f"CHAT  (intent: {intent})\nQ: {question}")
        fallback_reason = None
        try:
            resp = await provider.generate_response(SAMPLE_CONTEXT, chat_prompt(intent, question),
                                                    system=SYSTEM_RULES, task=AITask.CHAT)
            out = validate_model(parse_json_object(resp.text), ModelChatOutput)
            result = validate_chat(out, SAMPLE_CONTEXT, facts)
            if not result.ok:
                fallback_reason = "; ".join(result.violations[:3])
        except (AIProviderError, AIOutputError) as exc:
            fallback_reason = str(exc)
        if fallback_reason:
            print(f"AI answer rejected ({fallback_reason}); grounded fallback shown instead:\n")
            out = validate_model(build_chat(SAMPLE_CONTEXT, intent, question), ModelChatOutput)
        print(out.answer)
        if out.insufficient_evidence:
            print("\n(insufficient_evidence = true)")


if __name__ == "__main__":
    asyncio.run(main())
