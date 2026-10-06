"""Full Phase 2 workflow on the demo dataset, using the mock provider only (no external credentials)."""

import importlib.util
from pathlib import Path

from app.core.database import SessionLocal
from tests.helpers import login

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_data.py"


def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_end_to_end_ai_workflow(client):
    seed = _seed_module()
    await seed.seed(SessionLocal, reset=True)                                   # 1. seed
    h = await login(client, seed.ADMIN_EMAIL, seed.PASSWORD)
    check = (await client.post("/expiry/check", headers=h)).json()              # 2. expiry check
    assert check["alerts_created"] > 0                                          # 3. alerts
    assert (await client.get("/analytics/overview", headers=h)).json()["sales"]["revenue"] > 0  # 4. analytics

    ctx = (await client.get("/ai/context?intent=PRIORITIES", headers=h)).json()  # 5-6. patterns + context
    assert ctx["patterns"] and ctx["expiry_risks"] and ctx["anomalies"]
    assert any(r["days_remaining"] == 1 for r in ctx["expiry_risks"])

    gen = (await client.post("/insights/generate", json={}, headers=h)).json()   # 7. insights
    assert gen["generated"] and gen["rejected_count"] == 0
    recs = (await client.get("/insights/recommendations", headers=h)).json()     # 8. recommendations
    assert recs["recommendations"] and not recs["fallback_used"]

    first = gen["generated"][0]                                                  # 9. retrieve via API
    assert (await client.get(f"/insights/{first['id']}", headers=h)).json()["id"] == first["id"]

    chat = (await client.post("/ai/chat", json={"message": "What should I prioritize today?"}, headers=h)).json()  # 10
    assert chat["grounded"] and not chat["fallback_used"] and chat["evidence"]   # 11. grounded
    detail = (await client.get(f"/ai/conversations/{chat['conversation_id']}", headers=h)).json()  # 12. stored
    assert len(detail["messages"]) == 2

    panel = (await client.get("/dashboard/insights", headers=h)).json()
    assert panel["total"] == len(gen["generated"])


async def test_swagger_lists_ai_endpoints(client):
    paths = (await client.get("/openapi.json")).json()["paths"]
    for path in ("/insights/generate", "/insights", "/insights/{insight_id}", "/insights/recommendations",
                 "/ai/chat", "/ai/conversations", "/ai/conversations/{conversation_id}", "/ai/status",
                 "/ai/context", "/dashboard/insights"):
        assert path in paths, path
