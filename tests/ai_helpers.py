"""Test doubles for the AI layer."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from app.ai.base import AIProvider, AIProviderError, AIResponse, AITask
from app.ai.factory import get_ai_provider
from app.ai.providers.mock_provider import MockProvider
from app.main import app as fastapi_app


class ScriptedProvider(AIProvider):
    """Returns whatever `respond(context, task)` produces; records every call."""

    name = "scripted"
    model = "scripted-1"

    def __init__(self, respond: Callable[[dict[str, Any], AITask], str]) -> None:
        self.respond = respond
        self.calls: list[dict[str, Any]] = []

    async def generate_response(self, context, prompt, *, system, task, history=None) -> AIResponse:
        self.calls.append({"context": context, "prompt": prompt, "system": system, "task": task, "history": history or []})
        return AIResponse(text=self.respond(context, task), provider=self.name, model=self.model)


class FailingProvider(AIProvider):
    name = "failing"
    model = "failing-1"

    async def generate_response(self, context, prompt, *, system, task, history=None) -> AIResponse:
        raise AIProviderError("simulated outage")


def mock_payload(context: dict[str, Any], task: AITask, prompt: str = "") -> dict[str, Any]:
    from app.ai.providers.mock_provider import build_chat, build_insights, build_recommendations

    if task in (AITask.INSIGHTS, AITask.ANOMALY_EXPLANATION):
        return build_insights(context, context.get("requested_categories"))
    if task == AITask.RECOMMENDATIONS:
        return build_recommendations(context)
    return build_chat(context, context.get("intent", "GENERAL"), prompt)


def use_provider(provider: AIProvider) -> AIProvider:
    fastapi_app.dependency_overrides[get_ai_provider] = lambda: provider
    return provider


def tamper(mutator: Callable[[dict[str, Any], AITask], None]) -> ScriptedProvider:
    """Start from the grounded mock output and apply a mutation (to simulate a hallucinating model)."""

    def respond(context: dict[str, Any], task: AITask) -> str:
        payload = mock_payload(context, task)
        mutator(payload, task)
        return json.dumps(payload, default=str)

    return ScriptedProvider(respond)


__all__ = ["FailingProvider", "MockProvider", "ScriptedProvider", "mock_payload", "tamper", "use_provider"]


async def stock_store(client, admin: dict, db) -> dict[str, Any]:
    """Create a small store with expiry risk, high-stock/low-sales, low-stock/high-sales and expired stock."""
    import uuid
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal

    from app.models import Sale
    from tests.helpers import create_batch, create_product

    h = admin["headers"]
    cid = uuid.UUID(admin["company"]["id"])
    milk = await create_product(client, h)
    cheese = await create_product(client, h, name="Cheddar 200g", sku="CHS-1", barcode="6001234567891",
                                  selling_price="4.80", cost_price="3.20")
    formula = await create_product(client, h, name="Infant Formula 400g", sku="FRM-1", barcode="6001234567892",
                                   selling_price="12.00", cost_price="8.50")
    yoghurt = await create_product(client, h, name="Yoghurt 500g", sku="YOG-1", barcode="6001234567893")
    tomorrow = await create_batch(client, h, milk["id"], days=1, qty=40, batch_number="MLK-TMRW")
    milk_later = await create_batch(client, h, milk["id"], days=200, qty=100, batch_number="MLK-LATER")
    cheese_b = await create_batch(client, h, cheese["id"], days=60, qty=400, batch_number="CHS-60")
    formula_b = await create_batch(client, h, formula["id"], days=200, qty=8, batch_number="FRM-200")
    await create_batch(client, h, yoghurt["id"], days=-2, qty=5, batch_number="YOG-OLD")

    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)

    def add(product: dict, batch: dict, units: int, offset: int) -> None:
        price = Decimal(product["selling_price"])
        db.add(Sale(company_id=cid, product_id=uuid.UUID(product["id"]), batch_id=uuid.UUID(batch["id"]),
                    quantity=units, unit_price=price, total_amount=price * units, sold_at=now - timedelta(days=offset)))

    for d in range(30):
        add(milk, milk_later, 2, d)
        add(formula, formula_b, 10, d)
    add(cheese, cheese_b, 1, 3)
    await db.commit()
    await client.post("/expiry/check", headers=h)
    return {"milk": milk, "cheese": cheese, "formula": formula, "yoghurt": yoghurt, "tomorrow": tomorrow}
