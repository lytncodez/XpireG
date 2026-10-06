from datetime import datetime, timedelta, timezone

from tests.helpers import create_batch, create_product


async def test_sale_reduces_stock_and_records_movement(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=100, qty=20)
    resp = await client.post("/sales", json={"product_id": product["id"], "batch_id": batch["id"], "quantity": 3}, headers=h)
    assert resp.status_code == 201
    result = resp.json()
    assert result["total_quantity"] == 3
    assert result["total_amount"] == "4.50"
    assert (await client.get(f"/batches/{batch['id']}", headers=h)).json()["remaining_quantity"] == 17
    moves = (await client.get("/inventory/movements?movement_type=SALE", headers=h)).json()
    assert moves["total"] == 1 and moves["items"][0]["quantity"] == -3
    assert moves["items"][0]["reference_id"] == result["sales"][0]["id"]


async def test_fefo_allocation_splits_across_batches(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    late = await create_batch(client, h, product["id"], days=200, qty=10, batch_number="LATE")
    early = await create_batch(client, h, product["id"], days=20, qty=4, batch_number="EARLY")
    expired = await create_batch(client, h, product["id"], days=-2, qty=50, batch_number="OLD")
    resp = await client.post("/sales", json={"product_id": product["id"], "quantity": 6}, headers=h)
    assert resp.status_code == 201
    allocations = [(s["batch_number"], s["quantity"]) for s in resp.json()["sales"]]
    assert allocations == [("EARLY", 4), ("LATE", 2)]
    assert (await client.get(f"/batches/{early['id']}", headers=h)).json()["status"] == "DEPLETED"
    assert (await client.get(f"/batches/{late['id']}", headers=h)).json()["remaining_quantity"] == 8
    assert (await client.get(f"/batches/{expired['id']}", headers=h)).json()["remaining_quantity"] == 50


async def test_sale_validation(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    other = await create_product(client, h, sku="OTH", barcode="222")
    batch = await create_batch(client, h, product["id"], days=10, qty=2)
    expired = await create_batch(client, h, product["id"], days=0, qty=5, batch_number="EXP")
    other_batch = await create_batch(client, h, other["id"], days=10, qty=5)

    too_many = await client.post("/sales", json={"product_id": product["id"], "quantity": 3}, headers=h)
    assert too_many.status_code == 409 and too_many.json()["error"]["code"] == "INSUFFICIENT_STOCK"
    exp = await client.post("/sales", json={"product_id": product["id"], "batch_id": expired["id"], "quantity": 1}, headers=h)
    assert exp.status_code == 400
    wrong = await client.post("/sales", json={"product_id": product["id"], "batch_id": other_batch["id"], "quantity": 1}, headers=h)
    assert wrong.status_code == 400
    zero = await client.post("/sales", json={"product_id": product["id"], "quantity": 0}, headers=h)
    assert zero.status_code == 422
    future = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    fut = await client.post("/sales", json={"product_id": product["id"], "quantity": 1, "sold_at": future}, headers=h)
    assert fut.status_code == 400
    # Nothing was decremented by the failed attempts
    assert (await client.get(f"/batches/{batch['id']}", headers=h)).json()["remaining_quantity"] == 2


async def test_inventory_adjustments(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=100, qty=10)
    ok = await client.post("/inventory/adjust", json={
        "batch_id": batch["id"], "movement_type": "DAMAGED", "quantity_change": -3, "notes": "Crushed boxes",
    }, headers=h)
    assert ok.status_code == 200
    assert ok.json()["previous_quantity"] == 10 and ok.json()["new_quantity"] == 7
    negative = await client.post("/inventory/adjust", json={
        "batch_id": batch["id"], "movement_type": "ADJUSTMENT", "quantity_change": -8,
    }, headers=h)
    assert negative.status_code == 409
    wrong_sign = await client.post("/inventory/adjust", json={
        "batch_id": batch["id"], "movement_type": "DAMAGED", "quantity_change": 2,
    }, headers=h)
    assert wrong_sign.status_code == 422
    restock = await client.post("/inventory/adjust", json={
        "batch_id": batch["id"], "movement_type": "PURCHASE", "quantity_change": 5,
    }, headers=h)
    assert restock.json()["new_quantity"] == 12


async def test_inventory_list_and_summary(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=-1, qty=5, batch_number="EXP")
    await create_batch(client, h, product["id"], days=10, qty=7, batch_number="CRIT")
    await create_batch(client, h, product["id"], days=60, qty=11, batch_number="SOON")
    await create_batch(client, h, product["id"], days=365, qty=13, batch_number="SAFE")
    items = (await client.get("/inventory", headers=h)).json()["items"]
    assert items[0]["total_stock"] == 36
    assert items[0]["sellable_stock"] == 31
    assert items[0]["expired_stock"] == 5
    assert items[0]["inventory_value"] == "36.00"
    summary = (await client.get("/inventory/summary", headers=h)).json()
    assert summary["expired_units"] == 5
    assert summary["critical_units"] == 7
    assert summary["expiring_soon_units"] == 11
    assert summary["safe_units"] == 13
    assert summary["inventory_value"] == "36.00"
    assert summary["retail_value"] == "54.00"
    assert summary["value_at_risk"] == "12.00"


async def test_sales_list_summary_and_trends(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=200, qty=100)
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    await client.post("/sales", json={"product_id": product["id"], "quantity": 4, "sold_at": yesterday}, headers=h)
    await client.post("/sales", json={"product_id": product["id"], "quantity": 6, "unit_price": "2.00"}, headers=h)
    assert (await client.get("/sales", headers=h)).json()["total"] == 2
    summary = (await client.get("/sales/summary", headers=h)).json()
    assert summary["units_sold"] == 10
    assert summary["revenue"] == "18.00"
    assert summary["gross_profit"] == "8.00"
    assert summary["top_products"][0]["units_sold"] == 10
    trends = (await client.get("/sales/trends?granularity=day&periods=7", headers=h)).json()
    assert len(trends["points"]) == 7
    assert sum(p["units_sold"] for p in trends["points"]) == 10
    assert trends["points"][-1]["units_sold"] == 6
