import json

from sqlalchemy import func, select

from app.models import Insight
from tests.ai_helpers import FailingProvider, ScriptedProvider, stock_store, tamper, use_provider
from tests.helpers import create_user


async def _count(db) -> int:
    return int(await db.scalar(select(func.count()).select_from(Insight)) or 0)


async def test_generate_insights_with_mock_provider(client, admin, db):
    store = await stock_store(client, admin, db)
    h = admin["headers"]
    resp = await client.post("/insights/generate", json={}, headers=h)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["provider"] == "mock" and body["rejected_count"] == 0
    assert body["generation_status"] == "SUCCESS"
    categories = {i["category"] for i in body["generated"]}
    assert {"EXPIRY", "WASTE_RISK", "INVENTORY", "STOCK_RISK", "PRODUCT_PERFORMANCE"} <= categories
    for i in body["generated"]:
        assert i["title"] and i["summary"] and i["explanation"] and i["recommendation"]
        assert i["supporting_data"]["evidence"], i
        assert i["supporting_data"]["guardrails"]["passed"] is True
    expiry = next(i for i in body["generated"] if i["batch_id"] == store["tomorrow"]["id"])
    assert expiry["category"] == "EXPIRY" and expiry["severity"] == "CRITICAL"
    assert any(i["severity"] == "URGENT" and i["category"] == "EXPIRY" for i in body["generated"])  # expired yoghurt

    listed = (await client.get("/insights", headers=h)).json()
    assert listed["total"] == len(body["generated"])
    one = (await client.get(f"/insights/{expiry['id']}", headers=h)).json()
    assert one["title"] == expiry["title"]
    assert (await client.get("/insights?category=STOCK_RISK", headers=h)).json()["total"] == 1


async def test_category_filter_and_anomaly_prompt(client, admin, db):
    await stock_store(client, admin, db)
    body = (await client.post("/insights/generate", json={"categories": ["EXPIRY"]}, headers=admin["headers"])).json()
    assert body["generated"] and all(i["category"] == "EXPIRY" for i in body["generated"])


async def test_insufficient_evidence_creates_nothing(client, admin, db):
    body = (await client.post("/insights/generate", json={}, headers=admin["headers"])).json()
    assert body["generated"] == [] and body["insufficient_evidence"] is True
    assert body["generation_status"] == "NO_EVIDENCE"
    assert await _count(db) == 0


async def test_malformed_ai_output_is_not_stored(client, admin, db):
    await stock_store(client, admin, db)
    use_provider(ScriptedProvider(lambda ctx, task: "Here are your insights: totally not json"))
    body = (await client.post("/insights/generate", json={}, headers=admin["headers"])).json()
    assert body["generated"] == [] and body["rejected_count"] == 1
    assert body["generation_status"] == "REJECTED"
    assert "JSON" in body["rejected"][0]["reasons"][0]
    assert await _count(db) == 0


async def test_invented_numbers_rejected_per_item(client, admin, db):
    await stock_store(client, admin, db)

    def invent(payload, task):
        payload["insights"][0]["summary"] += " Revenue will reach 987654 next month."

    use_provider(tamper(invent))
    body = (await client.post("/insights/generate", json={}, headers=admin["headers"])).json()
    assert body["rejected_count"] == 1
    assert body["generation_status"] == "PARTIAL"
    assert any("987654" in r for r in body["rejected"][0]["reasons"])
    assert body["generated"]  # the other, grounded insights are kept
    assert all("987654" not in i["summary"] for i in body["generated"])
    assert await _count(db) == len(body["generated"])


async def test_causal_claims_and_fake_entities_rejected(client, admin, db):
    await stock_store(client, admin, db)

    def corrupt(payload, task):
        payload["insights"][0]["explanation"] = "Inflation caused the sales decline."
        payload["insights"][1]["product_id"] = "99999999-9999-9999-9999-999999999999"
        payload["insights"][2]["supporting_evidence"] = [{"label": "Stock", "value": 5000, "source": "inventory.total_units"}]
        payload["insights"][3]["category"] = "WEATHER"

    use_provider(tamper(corrupt))
    body = (await client.post("/insights/generate", json={}, headers=admin["headers"])).json()
    assert body["rejected_count"] == 4
    reasons = json.dumps(body["rejected"])
    assert "Unhedged causal claim" in reasons and "Unknown id" in reasons
    assert "does not match context" in reasons and "Schema validation failed" in reasons


async def test_provider_outage_returns_503(client, admin, db):
    await stock_store(client, admin, db)
    use_provider(FailingProvider())
    resp = await client.post("/insights/generate", json={}, headers=admin["headers"])
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "AI_UNAVAILABLE"


async def test_recommendations_grounded_and_prioritised(client, admin, db):
    await stock_store(client, admin, db)
    h = admin["headers"]
    await client.post("/insights/generate", json={}, headers=h)
    body = (await client.get("/insights/recommendations", headers=h)).json()
    assert body["fallback_used"] is False and body["recommendations"]
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    ranks = [order[r["priority"]] for r in body["recommendations"]]
    assert ranks == sorted(ranks)
    titles = " ".join(r["title"] for r in body["recommendations"])
    assert "FEFO" in titles and "Remove expired stock" in titles and "Infant Formula" in titles
    assert all(r["supporting_evidence"] for r in body["recommendations"])
    assert "not executed automatically" in body["disclaimer"]


async def test_recommendations_fall_back_when_ai_output_invalid(client, admin, db):
    await stock_store(client, admin, db)
    use_provider(ScriptedProvider(lambda ctx, task: '{"recommendations": [{"title": "Buy 5000 units now"}]}'))
    body = (await client.get("/insights/recommendations", headers=admin["headers"])).json()
    assert body["fallback_used"] is True and body["recommendations"]
    assert all("5000" not in r["title"] for r in body["recommendations"])


async def test_insight_access_control_and_isolation(client, admin, other_admin, db):
    await stock_store(client, admin, db)
    generated = (await client.post("/insights/generate", json={}, headers=admin["headers"])).json()["generated"]
    insight_id = generated[0]["id"]
    assert (await client.get(f"/insights/{insight_id}", headers=other_admin["headers"])).status_code == 404
    assert (await client.get("/insights", headers=other_admin["headers"])).json()["total"] == 0
    staff = await create_user(client, admin["headers"], "staff@acme.example.com", "STAFF")
    assert (await client.get("/insights", headers=staff["headers"])).json()["total"] == len(generated)
    assert (await client.post("/insights/generate", json={}, headers=staff["headers"])).status_code == 403


async def test_dashboard_insights_panel(client, admin, db):
    await stock_store(client, admin, db)
    generated = (await client.post("/insights/generate", json={}, headers=admin["headers"])).json()["generated"]
    panel = (await client.get("/dashboard/insights", headers=admin["headers"])).json()
    assert panel["total"] == len(generated) and panel["latest"] and panel["last_generated_at"]
    assert sum(panel["by_category"].values()) == len(generated)
