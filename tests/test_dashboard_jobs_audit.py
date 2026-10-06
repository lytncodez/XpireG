import uuid
from datetime import timedelta

from sqlalchemy import select

from app.core.database import SessionLocal
from app.jobs.alert_job import run_alert_job
from app.jobs.cleanup_job import run_cleanup_job
from app.jobs.expiry_job import run_expiry_job
from app.models import RevokedToken
from app.utils.dates import utcnow
from tests.helpers import create_batch, create_product


async def test_dashboard_endpoints(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=1, qty=10, batch_number="TMRW")
    await create_batch(client, h, product["id"], days=-2, qty=4, batch_number="OLD")
    await create_batch(client, h, product["id"], days=60, qty=6, batch_number="LATER")
    await client.post("/sales", json={"product_id": product["id"], "quantity": 2}, headers=h)
    await client.post("/expiry/check", headers=h)

    summary = (await client.get("/dashboard/summary", headers=h)).json()
    assert summary["total_products"] == 1
    assert summary["total_stock"] == 18
    assert summary["expired_units"] == 4 and summary["expired_batches"] == 1
    assert summary["critical_alerts"] == 2  # CRITICAL_EXPIRY + EXPIRED
    assert summary["expiring_soon"] == 14
    assert summary["sales_today"] == 2 and summary["revenue_today"] == 3.0
    assert summary["top_products"][0]["name"] == "Fresh Milk 1L"
    assert len(summary["recent_alerts"]) == 3

    alerts = (await client.get("/dashboard/alerts", headers=h)).json()
    assert alerts["open_total"] == 3 and alerts["by_severity"]["URGENT"] == 1 and alerts["sms_sent"] == 2
    sales = (await client.get("/dashboard/sales", headers=h)).json()
    assert sales["revenue_30d"] == 3.0 and len(sales["daily"]) == 30
    inventory = (await client.get("/dashboard/inventory", headers=h)).json()
    assert inventory["status_breakdown"]["EXPIRED"] == 4
    assert inventory["expiring_next_30_days"][0]["batch_number"] == "TMRW"


async def test_background_expiry_job_processes_every_company(client, admin, other_admin):
    for company in (admin, other_admin):
        product = await create_product(client, company["headers"])
        await create_batch(client, company["headers"], product["id"], days=1)
    results = await run_expiry_job(SessionLocal)
    assert len(results) == 2
    assert all(r["status"] == "ok" and r["alerts_created"] == 1 for r in results)
    again = await run_expiry_job(SessionLocal)
    assert all(r["alerts_created"] == 0 and r["duplicates_prevented"] == 1 for r in again)


async def test_alert_job_creates_low_stock_alerts(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=200, qty=3)
    summary = await run_alert_job(SessionLocal)
    assert summary["low_stock_created"] == 1
    alerts = (await client.get("/alerts?alert_type=LOW_STOCK", headers=h)).json()
    assert alerts["total"] == 1 and alerts["items"][0]["quantity_at_risk"] == 3
    assert (await run_alert_job(SessionLocal))["low_stock_created"] == 0  # deduplicated


async def test_cleanup_job_purges_expired_tokens(client, admin, db):
    db.add(RevokedToken(jti="old-token", user_id=uuid.UUID(admin["user"]["id"]), expires_at=utcnow() - timedelta(hours=1)))
    await db.commit()
    summary = await run_cleanup_job(SessionLocal)
    assert summary["revoked_tokens_purged"] == 1
    assert (await db.scalar(select(RevokedToken).where(RevokedToken.jti == "old-token"))) is None


async def test_audit_log_records_key_actions(client, admin):
    h = admin["headers"]
    await client.post("/auth/login", json={"email": "admin@acme.test", "password": "Secret123!"})
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=1)
    await client.patch(f"/batches/{batch['id']}", json={"batch_number": "RENAMED"}, headers=h)
    await client.patch(f"/products/{product['id']}", json={"brand": "Acme"}, headers=h)
    await client.post("/sales", json={"product_id": product["id"], "quantity": 1}, headers=h)
    await client.post("/expiry/check", headers=h)
    alert = (await client.get("/alerts", headers=h)).json()["items"][0]
    await client.patch(f"/alerts/{alert['id']}/resolve", headers=h)
    await client.post("/imports/csv", files={"file": ("x.csv", b"name,qty\nA,1\n", "text/csv")}, headers=h)

    logs = (await client.get("/companies/me/audit-logs?page_size=200", headers=h)).json()
    actions = {entry["action"] for entry in logs["items"]}
    for expected in (
        "USER_LOGIN", "PRODUCT_CREATED", "PRODUCT_UPDATED", "BATCH_CREATED", "BATCH_UPDATED", "SALE_CREATED",
        "ALERT_CREATED", "ALERT_RESOLVED", "SMS_SENT", "IMPORT_STARTED", "IMPORT_FAILED",
    ):
        assert expected in actions, expected
    await client.post("/auth/logout", headers=h)
