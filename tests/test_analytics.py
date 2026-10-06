import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.models import InventoryMovement, MovementType, Sale
from app.services.anomaly_service import detect_series_anomaly
from tests.helpers import create_batch, create_product


async def _history(db, company_id: str, product: dict, batch_id: str, daily_units: list[int], price: str = "1.50"):
    """Insert one sale per day; daily_units[0] is today, [1] yesterday, ..."""
    now = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    for offset, units in enumerate(daily_units):
        if units <= 0:
            continue
        db.add(Sale(
            company_id=uuid.UUID(company_id), product_id=uuid.UUID(product["id"]), batch_id=uuid.UUID(batch_id),
            quantity=units, unit_price=Decimal(price), total_amount=Decimal(price) * units,
            sold_at=now - timedelta(days=offset),
        ))
    await db.commit()


async def test_overview_and_sales_analytics(client, admin, db):
    h = admin["headers"]
    cid = admin["company"]["id"]
    milk = await create_product(client, h)
    batch = await create_batch(client, h, milk["id"], days=200, qty=500)
    await _history(db, cid, milk, batch["id"], [2] * 30 + [1] * 30)  # 60 units now, 30 prior period

    overview = (await client.get("/analytics/overview?days=30", headers=h)).json()
    assert overview["sales"]["units_sold"] == 60
    assert overview["sales"]["revenue"] == 90.0
    assert overview["sales"]["previous_revenue"] == 45.0
    assert overview["sales"]["revenue_growth_pct"] == 100.0
    assert overview["inventory"]["total_units"] == 500
    assert overview["inventory"]["inventory_value"] == 500.0
    assert overview["inventory"]["safe_units"] == 500

    sales = (await client.get("/analytics/sales", headers=h)).json()
    assert sales["units_sold"] == 60 and sales["revenue"] == 90.0
    assert len(sales["daily"]) == 30 and len(sales["weekly"]) == 12 and len(sales["monthly"]) == 12
    assert sales["by_product"][0]["share_pct"] == 100.0
    assert sales["by_category"][0]["name"] == "Uncategorised"


async def test_product_classification(client, admin, db):
    h = admin["headers"]
    cid = admin["company"]["id"]
    fast = await create_product(client, h, name="Fast", sku="FAST", barcode=None)
    slow = await create_product(client, h, name="Slow", sku="SLOW", barcode=None)
    tight = await create_product(client, h, name="Tight", sku="TIGHT", barcode=None)
    fb = await create_batch(client, h, fast["id"], days=300, qty=200)
    sb = await create_batch(client, h, slow["id"], days=300, qty=400)
    tb = await create_batch(client, h, tight["id"], days=300, qty=20)
    await _history(db, cid, fast, fb["id"], [10] * 30)
    await _history(db, cid, slow, sb["id"], [1] + [0] * 29)
    await _history(db, cid, tight, tb["id"], [10] * 30)

    data = (await client.get("/analytics/products?window_days=30", headers=h)).json()
    by_name = {p["product_name"]: p for p in data["products"]}
    assert by_name["Slow"]["classification"] == "HIGH_STOCK_LOW_SALES"
    assert by_name["Tight"]["classification"] == "LOW_STOCK_HIGH_SALES"
    assert by_name["Tight"]["days_of_cover"] == 2.0
    assert data["fast_movers"][0]["product_name"] in {"Fast", "Tight"}
    assert data["slow_movers"][0]["product_name"] == "Slow"
    assert [p["product_name"] for p in data["high_stock_low_sales"]] == ["Slow"]


async def test_inventory_analytics_expiry_risk(client, admin, db):
    h = admin["headers"]
    cid = admin["company"]["id"]
    product = await create_product(client, h)
    near = await create_batch(client, h, product["id"], days=10, qty=100, batch_number="NEAR")
    await create_batch(client, h, product["id"], days=300, qty=50, batch_number="FAR")
    await _history(db, cid, product, near["id"], [2] * 30)  # 2/day -> ~20 sellable before NEAR expires

    data = (await client.get("/analytics/inventory?window_days=30", headers=h)).json()
    risk = {r["batch_number"]: r for r in data["expiry_risk"]}
    assert risk["NEAR"]["projected_sales_before_expiry"] == 20.0
    assert risk["NEAR"]["units_at_risk"] == 80
    assert risk["NEAR"]["value_at_risk"] == 80.0
    assert data["potential_waste_units"] >= 80
    assert data["stock_turnover"] == round(60 / 150, 3)
    assert data["status_breakdown"]["CRITICAL"] == 100


async def test_trends_endpoint(client, admin):
    data = (await client.get("/analytics/trends?granularity=week&periods=8", headers=admin["headers"])).json()
    assert data["granularity"] == "week" and len(data["points"]) == 8
    bad = await client.get("/analytics/trends?granularity=year", headers=admin["headers"])
    assert bad.status_code == 422


def test_series_anomaly_rule():
    flag, z, pct, recent, base = detect_series_anomaly([5, 6, 4, 5, 5, 6, 4] * 4, [15] * 7, 2.5, 50)
    assert flag and pct > 150 and z > 2.5
    flag, *_ = detect_series_anomaly([5, 6, 4, 5] * 7, [5, 6, 5, 4, 5, 6, 5], 2.5, 50)
    assert not flag
    flag, *_ = detect_series_anomaly([0] * 28, [3] * 7, 2.5, 50)
    assert not flag  # no baseline: new product, not an anomaly


async def test_anomaly_detection(client, admin, db):
    h = admin["headers"]
    cid = admin["company"]["id"]
    spike = await create_product(client, h, name="Spiky", sku="SPK", barcode=None)
    drop = await create_product(client, h, name="Droppy", sku="DRP", barcode=None)
    steady = await create_product(client, h, name="Steady", sku="STD", barcode=None)
    sb = await create_batch(client, h, spike["id"], days=300, qty=5000)
    db_ = await create_batch(client, h, drop["id"], days=300, qty=5000)
    stb = await create_batch(client, h, steady["id"], days=300, qty=300)
    baseline = [5, 6, 4, 5, 5, 6, 4] * 4
    await _history(db, cid, spike, sb["id"], [20] * 7 + baseline)
    await _history(db, cid, drop, db_["id"], [0] * 7 + baseline)
    await _history(db, cid, steady, stb["id"], [5] * 7 + baseline)
    # Abnormal write-off for Steady in the recent window
    db.add(InventoryMovement(
        company_id=uuid.UUID(cid), product_id=uuid.UUID(steady["id"]), batch_id=uuid.UUID(stb["id"]),
        movement_type=MovementType.DAMAGED, quantity=-120, notes="flood",
    ))
    await db.commit()

    data = (await client.get("/analytics/anomalies", headers=h)).json()
    assert "not causation" in data["disclaimer"]
    found = {(a["anomaly_type"], a["entity_name"]) for a in data["anomalies"]}
    assert ("UNUSUAL_SALES_INCREASE", "Spiky") in found
    assert ("UNUSUAL_SALES_DECLINE", "Droppy") in found
    assert ("ABNORMAL_STOCK_MOVEMENT", "Steady") in found
    assert ("UNUSUALLY_HIGH_INVENTORY", "Droppy") in found  # 5000 units at ~5/day
    assert not any(a[1] == "Steady" and a[0].startswith("UNUSUAL_SALES") for a in found)
    for a in data["anomalies"]:
        assert a["explanation"]
