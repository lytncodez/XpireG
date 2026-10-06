import json

from tests.ai_helpers import stock_store
from tests.helpers import create_product


async def test_context_contains_verified_metrics(client, admin, db):
    await stock_store(client, admin, db)
    h = admin["headers"]
    ctx = (await client.get("/ai/context?intent=GENERAL", headers=h)).json()
    overview = (await client.get("/analytics/overview", headers=h)).json()

    assert ctx["inventory"]["total_units"] == overview["inventory"]["total_units"]
    assert ctx["inventory"]["expired_units"] == overview["inventory"]["expired_units"] == 5
    assert ctx["sales"]["revenue"] == overview["sales"]["revenue"]
    assert ctx["sales"]["units_sold"] == overview["sales"]["units_sold"] == 361
    assert ctx["expiry_risks"][0]["batch_number"] == "MLK-TMRW" and ctx["expiry_risks"][0]["days_remaining"] == 1
    assert ctx["expired_batches"][0]["batch_number"] == "YOG-OLD"
    types = {p["type"] for p in ctx["patterns"]}
    assert "HIGH_STOCK_LOW_SALES_SHORT_EXPIRY" in types and "LOW_STOCK_HIGH_SALES" in types
    assert ctx["alerts"]["open_total"] >= 2
    assert ctx["stock_risks"][0]["product"] == "Infant Formula 400g"


async def test_context_is_scoped_to_intent(client, admin, db):
    await stock_store(client, admin, db)
    ctx = (await client.get("/ai/context?intent=EXPIRY_RISK", headers=admin["headers"])).json()
    assert "expiry_risks" in ctx and "inventory" in ctx
    assert "sales" not in ctx and "anomalies" not in ctx  # only what the question needs


async def test_context_never_includes_another_company(client, admin, other_admin, db):
    store = await stock_store(client, admin, db)
    await create_product(client, other_admin["headers"], name="Globex Soda", sku="GLX-1", barcode="999")
    other = json.dumps((await client.get("/ai/context", headers=other_admin["headers"])).json())
    for product in store.values():
        assert product["id"] not in other
    assert "Infant Formula" not in other and "Cheddar" not in other


async def test_empty_company_reports_missing_evidence(client, admin):
    ctx = (await client.get("/ai/context", headers=admin["headers"])).json()
    assert ctx["expiry_risks"] == [] and ctx["patterns"] == []
    notes = " ".join(ctx["data_quality"]["notes"])
    assert "No sales" in notes and "No stock" in notes


async def test_context_endpoint_requires_manager(client, admin):
    from tests.helpers import create_user

    staff = await create_user(client, admin["headers"], "staff@acme.test", "STAFF")
    assert (await client.get("/ai/context", headers=staff["headers"])).status_code == 403
