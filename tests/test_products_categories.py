from tests.helpers import create_product


async def test_category_crud(client, admin):
    h = admin["headers"]
    resp = await client.post("/categories", json={"name": "Dairy", "description": "Milk"}, headers=h)
    assert resp.status_code == 201
    cat = resp.json()
    assert (await client.post("/categories", json={"name": "dairy"}, headers=h)).status_code == 409
    upd = await client.patch(f"/categories/{cat['id']}", json={"name": "Dairy & Eggs"}, headers=h)
    assert upd.status_code == 200 and upd.json()["name"] == "Dairy & Eggs"
    product = await create_product(client, h, category_id=cat["id"])
    listed = (await client.get("/categories", headers=h)).json()
    assert listed["items"][0]["product_count"] == 1
    assert (await client.delete(f"/categories/{cat['id']}", headers=h)).status_code == 204
    assert (await client.get(f"/products/{product['id']}", headers=h)).json()["category_id"] is None


async def test_product_crud_and_search(client, admin):
    h = admin["headers"]
    cat = (await client.post("/categories", json={"name": "Bakery"}, headers=h)).json()
    milk = await create_product(client, h, sku="mlk-001")
    assert milk["sku"] == "MLK-001"
    bread = await create_product(client, h, name="White Bread", sku="BRD-1", barcode="5000000000001", category_id=cat["id"])

    assert (await client.get("/products?search=bread", headers=h)).json()["total"] == 1
    assert (await client.get(f"/products?category_id={cat['id']}", headers=h)).json()["items"][0]["id"] == bread["id"]
    assert (await client.get("/products?barcode=6001234567890", headers=h)).json()["items"][0]["id"] == milk["id"]
    assert (await client.get("/products?sku=brd-1", headers=h)).json()["items"][0]["id"] == bread["id"]

    upd = await client.patch(f"/products/{milk['id']}", json={"selling_price": "2.00"}, headers=h)
    assert upd.status_code == 200 and upd.json()["selling_price"] == "2.00"

    assert (await client.delete(f"/products/{milk['id']}", headers=h)).status_code == 204
    assert (await client.get("/products", headers=h)).json()["total"] == 1
    assert (await client.get("/products?include_inactive=true", headers=h)).json()["total"] == 2


async def test_product_uniqueness_is_company_scoped(client, admin):
    h = admin["headers"]
    await create_product(client, h)
    dup_sku = await client.post("/products", json={"name": "Other", "sku": "MLK-001"}, headers=h)
    assert dup_sku.status_code == 409
    dup_bc = await client.post("/products", json={"name": "Other", "barcode": "6001234567890"}, headers=h)
    assert dup_bc.status_code == 409


async def test_product_requires_identifier_and_valid_prices(client, admin):
    h = admin["headers"]
    assert (await client.post("/products", json={"name": "No ids"}, headers=h)).status_code == 422
    assert (await client.post("/products", json={"name": "Neg", "sku": "N1", "selling_price": "-1"}, headers=h)).status_code == 422
    assert (await client.post("/products", json={"name": "Bad", "barcode": "bad code!"}, headers=h)).status_code == 422


async def test_unknown_category_rejected(client, admin):
    resp = await client.post("/products", json={
        "name": "X", "sku": "X1", "category_id": "00000000-0000-0000-0000-000000000000",
    }, headers=admin["headers"])
    assert resp.status_code == 404
