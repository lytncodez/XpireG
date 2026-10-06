import io
from datetime import datetime

from openpyxl import Workbook

from tests.helpers import create_product, iso, today


def _csv(rows: list[str]) -> bytes:
    header = "Product Name,SKU,Barcode,Category,Selling Price,Cost Price,Batch Number,Expiry Date,Quantity"
    return ("\n".join([header, *rows]) + "\n").encode()


def _upload(content: bytes, name: str = "stock.csv", ctype: str = "text/csv"):
    return {"file": (name, content, ctype)}


async def test_csv_import_success(client, admin):
    h = admin["headers"]
    content = _csv([
        f"Fresh Milk 1L,MLK-001,6001234567890,Dairy,1.50,1.00,M-1,{iso(1)},30",
        f"Fresh Milk 1L,MLK-001,6001234567890,Dairy,1.50,1.00,M-2,{iso(120)},40",
        f"White Bread,BRD-1,,Bakery,1.20,0.80,B-1,{iso(4)},15",
    ])
    resp = await client.post("/imports/csv", files=_upload(content), headers=h)
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["status"] == "COMPLETED"
    assert (r["total_rows"], r["successful_rows"], r["failed_rows"], r["duplicate_rows"]) == (3, 3, 0, 0)
    assert r["products_created"] == 2 and r["batches_created"] == 3
    assert r["alerts_generated"] == 2  # two CRITICAL batches
    cats = (await client.get("/categories", headers=h)).json()
    assert {c["name"] for c in cats["items"]} == {"Dairy", "Bakery"}
    job = (await client.get(f"/imports/{r['job_id']}", headers=h)).json()
    assert job["summary"]["alerts_generated"] == 2


async def test_csv_reimport_counts_duplicates_and_updates_products(client, admin):
    h = admin["headers"]
    await create_product(client, h, selling_price="1.00")
    content = _csv([f"Fresh Milk 1L,MLK-001,6001234567890,Dairy,1.75,1.00,M-1,{iso(30)},10"])
    first = (await client.post("/imports/csv", files=_upload(content), headers=h)).json()
    assert first["products_updated"] == 1 and first["products_created"] == 0
    second = (await client.post("/imports/csv", files=_upload(content), headers=h)).json()
    assert second["duplicate_rows"] == 1 and second["batches_created"] == 0
    assert second["status"] == "COMPLETED_WITH_ERRORS"


async def test_csv_invalid_rows_reported_valid_rows_kept(client, admin):
    h = admin["headers"]
    content = _csv([
        f"Good Item,GOOD-1,,Misc,1,0.5,G-1,{iso(200)},5",
        f"Bad Qty,BAD-1,,Misc,1,0.5,X-1,{iso(200)},abc",
        f"Bad Date,BAD-2,,Misc,1,0.5,X-2,31/02/2027,5",
        f"No Id,,,Misc,1,0.5,X-3,{iso(200)},5",
        f"Good Item,GOOD-1,,Misc,1,0.5,G-1,{iso(200)},5",
        f"Neg Price,BAD-3,,Misc,-4,0.5,X-4,{iso(200)},5",
    ])
    r = (await client.post("/imports/csv", files=_upload(content), headers=h)).json()
    assert r["status"] == "COMPLETED_WITH_ERRORS"
    assert r["total_rows"] == 6
    assert r["successful_rows"] == 1
    assert r["failed_rows"] == 4
    assert r["duplicate_rows"] == 1
    errors = (await client.get(f"/imports/{r['job_id']}/errors", headers=h)).json()
    rows = {(e["row"], e["field"]) for e in errors["errors"]}
    assert (3, "quantity") in rows and (4, "expiry_date") in rows and (5, "sku/barcode") in rows
    assert (7, "selling_price") in rows
    assert any(e["error_type"] == "DUPLICATE" and e["row"] == 6 for e in errors["errors"])


async def test_strict_mode_writes_nothing(client, admin):
    h = admin["headers"]
    content = _csv([
        f"Good Item,GOOD-1,,Misc,1,0.5,G-1,{iso(200)},5",
        f"Bad Qty,BAD-1,,Misc,1,0.5,X-1,{iso(200)},-1",
    ])
    r = (await client.post("/imports/csv?strict=true", files=_upload(content), headers=h)).json()
    assert r["status"] == "FAILED"
    assert r["batches_created"] == 0
    assert (await client.get("/products", headers=h)).json()["total"] == 0


async def test_missing_columns_fails_cleanly(client, admin):
    h = admin["headers"]
    r = (await client.post("/imports/csv", files=_upload(b"name,qty\nMilk,3\n"), headers=h)).json()
    assert r["status"] == "FAILED"
    assert any("batch_number" in e["message"] for e in r["errors"])
    jobs = (await client.get("/imports", headers=h)).json()
    assert jobs["items"][0]["status"] == "FAILED"


async def test_upload_validation(client, admin):
    h = admin["headers"]
    assert (await client.post("/imports/csv", files=_upload(b"x", "stock.txt"), headers=h)).status_code == 400
    assert (await client.post("/imports/csv", files=_upload(b"", "stock.csv"), headers=h)).status_code == 400
    assert (await client.post("/imports/excel", files=_upload(b"not a zip", "s.xlsx",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"), headers=h)).json()["status"] == "FAILED"


async def test_excel_import(client, admin):
    h = admin["headers"]
    wb = Workbook()
    ws = wb.active
    ws.append(["product_name", "barcode", "category", "price", "cost", "lot", "mfg_date", "expiry_date", "qty"])
    t = today()
    ws.append(["Yoghurt 500g", "7001112223334", "Dairy", 2.5, 1.6, "Y-77", datetime(t.year - 1, 1, 1),
               datetime.combine(t, datetime.min.time()), 12])
    ws.append(["Cheddar", "7001112223335", "Dairy", 6, 4, 1001, None, f"{iso(400)}", 3])
    buf = io.BytesIO()
    wb.save(buf)
    resp = await client.post(
        "/imports/excel",
        files=_upload(buf.getvalue(), "stock.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers=h,
    )
    assert resp.status_code == 200, resp.text
    r = resp.json()
    assert r["status"] == "COMPLETED", r
    assert r["batches_created"] == 2 and r["products_created"] == 2
    assert r["alerts_generated"] == 1  # Yoghurt expires today -> EXPIRED
    lookup = (await client.get("/barcode/lookup/7001112223335", headers=h)).json()
    assert lookup["active_batches"][0]["batch_number"] == "1001"
