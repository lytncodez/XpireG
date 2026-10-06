from tests.helpers import create_batch, create_product, iso


async def test_create_batch_sets_status_and_purchase_movement(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=1, qty=40, batch_number="lot-a1")
    assert batch["batch_number"] == "LOT-A1"
    assert batch["status"] == "CRITICAL"
    assert batch["days_remaining"] == 1
    assert batch["remaining_quantity"] == 40
    moves = (await client.get(f"/inventory/movements?batch_id={batch['id']}", headers=h)).json()
    assert moves["total"] == 1 and moves["items"][0]["movement_type"] == "PURCHASE"
    assert moves["items"][0]["quantity"] == 40


async def test_batch_number_unique_per_product(client, admin):
    h = admin["headers"]
    p1 = await create_product(client, h)
    p2 = await create_product(client, h, sku="P2", barcode="111")
    await create_batch(client, h, p1["id"], days=100, batch_number="L1")
    resp = await client.post("/batches", json={
        "product_id": p1["id"], "batch_number": "l1", "expiry_date": iso(100), "initial_quantity": 1,
    }, headers=h)
    assert resp.status_code == 409
    await create_batch(client, h, p2["id"], days=100, batch_number="L1")  # same number, other product: fine


async def test_batch_validation(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    bad_dates = await client.post("/batches", json={
        "product_id": product["id"], "batch_number": "X", "manufacturing_date": iso(-1),
        "expiry_date": iso(-5), "initial_quantity": 10,
    }, headers=h)
    assert bad_dates.status_code == 422
    zero_qty = await client.post("/batches", json={
        "product_id": product["id"], "batch_number": "Y", "expiry_date": iso(10), "initial_quantity": 0,
    }, headers=h)
    assert zero_qty.status_code == 422
    future_mfg = await client.post("/batches", json={
        "product_id": product["id"], "batch_number": "Z", "manufacturing_date": iso(30),
        "expiry_date": iso(60), "initial_quantity": 5,
    }, headers=h)
    assert future_mfg.status_code == 422


async def test_update_batch_recalculates_status(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=200)
    assert batch["status"] == "SAFE"
    upd = await client.patch(f"/batches/{batch['id']}", json={"expiry_date": iso(45)}, headers=h)
    assert upd.status_code == 200
    assert upd.json()["status"] == "EXPIRING_SOON"
    bad = await client.patch(f"/batches/{batch['id']}", json={"manufacturing_date": iso(100)}, headers=h)
    assert bad.status_code == 400


async def test_delete_batch_rules(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    sold = await create_batch(client, h, product["id"], days=50, qty=5, batch_number="SOLD")
    unsold = await create_batch(client, h, product["id"], days=60, qty=5, batch_number="UNSOLD")
    await client.post("/sales", json={"product_id": product["id"], "batch_id": sold["id"], "quantity": 1}, headers=h)
    assert (await client.delete(f"/batches/{sold['id']}", headers=h)).status_code == 409
    assert (await client.delete(f"/batches/{unsold['id']}", headers=h)).status_code == 204
    assert (await client.get(f"/batches/{unsold['id']}", headers=h)).status_code == 404


async def test_list_batches_filters(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=5)
    await create_batch(client, h, product["id"], days=300)
    resp = await client.get("/batches?status=CRITICAL", headers=h)
    assert resp.json()["total"] == 1
    resp = await client.get(f"/batches?product_id={product['id']}", headers=h)
    assert [b["days_remaining"] for b in resp.json()["items"]] == [5, 300]
