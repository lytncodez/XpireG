import importlib.util
from pathlib import Path

from app.core.database import SessionLocal
from app.services.sms_service import MockSMSProvider
from tests.helpers import login

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "seed_demo_data.py"


def _load_seed_module():
    spec = importlib.util.spec_from_file_location("seed_demo_data", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_seed_then_full_demo_workflow(client):
    seed = _load_seed_module()
    result = await seed.seed(SessionLocal, reset=True, run_expiry=True)
    assert result["products"] == len(seed.PRODUCTS)
    assert result["sales"] > 500
    check = result["expiry_check"]
    assert check["alerts_created"] > 0 and check["status_counts"]["CRITICAL"] >= 1
    assert check["status_counts"]["EXPIRED"] >= 2
    assert check["sms_sent"] > 0 and len(MockSMSProvider.outbox) > 0

    # Re-seeding is idempotent (demo company is recreated)
    again = await seed.seed(SessionLocal, reset=True, run_expiry=False)
    assert again["products"] == result["products"]

    h = await login(client, seed.ADMIN_EMAIL, seed.PASSWORD)
    await client.post("/expiry/check", headers=h)
    critical = (await client.get("/expiry/critical", headers=h)).json()
    assert any(b["days_remaining"] == 1 for b in critical["items"])  # the batch expiring tomorrow
    assert (await client.get("/alerts/critical", headers=h)).json()["total"] >= 1
    overview = (await client.get("/analytics/overview", headers=h)).json()
    assert overview["sales"]["revenue"] > 0
    products = (await client.get("/analytics/products", headers=h)).json()
    assert any(p["product_name"] == "Infant Formula 400g" for p in products["low_stock_high_sales"])
    assert any(p["product_name"] == "Cheddar Cheese 200g" for p in products["high_stock_low_sales"])
    anomalies = (await client.get("/analytics/anomalies", headers=h)).json()["anomalies"]
    names = {(a["anomaly_type"], a["entity_name"]) for a in anomalies}
    assert ("UNUSUAL_SALES_INCREASE", "Salted Butter 250g") in names
    assert ("UNUSUAL_SALES_DECLINE", "Butter Croissants 4pk") in names
    summary = (await client.get("/dashboard/summary", headers=h)).json()
    assert summary["total_products"] == len(seed.PRODUCTS) and summary["critical_alerts"] >= 1
