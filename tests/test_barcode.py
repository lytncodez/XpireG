from tests.helpers import create_batch, create_product


async def test_lookup_returns_product_batches_and_inventory(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=90, qty=10, batch_number="LATER")
    soon = await create_batch(client, h, product["id"], days=3, qty=4, batch_number="SOON")
    await create_batch(client, h, product["id"], days=-1, qty=2, batch_number="GONE")

    resp = await client.get("/barcode/lookup/6001234567890", headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert body["product"]["id"] == product["id"]
    assert [b["batch_number"] for b in body["active_batches"]] == ["GONE", "SOON", "LATER"]
    assert body["active_batches"][0]["status"] == "EXPIRED"
    inv = body["inventory"]
    assert inv == {
        "total_stock": 16, "sellable_stock": 14, "expired_stock": 2, "active_batch_count": 3,
        "nearest_expiry": soon["expiry_date"], "next_fefo_batch_id": soon["id"],
    }

    post = await client.post("/barcode/lookup", json={"barcode": " 6001234567890 "}, headers=h)
    assert post.status_code == 200 and post.json()["product"]["id"] == product["id"]


async def test_lookup_not_found(client, admin):
    resp = await client.get("/barcode/lookup/0000000000000", headers=admin["headers"])
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
    assert resp.json()["error"]["details"]["barcode"] == "0000000000000"


async def test_inactive_product_not_found(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await client.delete(f"/products/{product['id']}", headers=h)
    assert (await client.get("/barcode/lookup/6001234567890", headers=h)).status_code == 404
