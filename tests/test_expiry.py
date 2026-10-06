import pytest

from app.models import BatchStatus
from app.services.expiry_service import ExpiryThresholds, batch_status, classify
from tests.helpers import create_batch, create_product, iso, today


# ---------------------------------------------------------------- pure rule tests (boundaries)
@pytest.mark.parametrize(
    "days,expected",
    [
        (-10, BatchStatus.EXPIRED),
        (-1, BatchStatus.EXPIRED),
        (0, BatchStatus.EXPIRED),
        (1, BatchStatus.CRITICAL),
        (30, BatchStatus.CRITICAL),
        (31, BatchStatus.EXPIRING_SOON),
        (90, BatchStatus.EXPIRING_SOON),
        (91, BatchStatus.SAFE),
        (1000, BatchStatus.SAFE),
    ],
)
def test_classify_boundaries(days, expected):
    assert classify(days, ExpiryThresholds(30, 90)) == expected


def test_custom_thresholds():
    t = ExpiryThresholds(critical_days=7, soon_days=14)
    assert classify(7, t) == BatchStatus.CRITICAL
    assert classify(8, t) == BatchStatus.EXPIRING_SOON
    assert classify(15, t) == BatchStatus.SAFE
    with pytest.raises(ValueError):
        ExpiryThresholds(critical_days=10, soon_days=10)


def test_depleted_batch_status():
    assert batch_status(today(), 0, today()) == BatchStatus.DEPLETED


# ---------------------------------------------------------------- engine integration
async def test_expiry_check_classifies_boundary_batches(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    expected = {0: "EXPIRED", 1: "CRITICAL", 30: "CRITICAL", 31: "EXPIRING_SOON", 90: "EXPIRING_SOON", 91: "SAFE"}
    ids = {}
    for days in expected:
        ids[days] = (await create_batch(client, h, product["id"], days=days, qty=5))["id"]
    resp = await client.post("/expiry/check", headers=h)
    assert resp.status_code == 200
    result = resp.json()
    assert result["batches_checked"] == 6
    assert result["status_counts"] == {"EXPIRED": 1, "CRITICAL": 2, "EXPIRING_SOON": 2, "SAFE": 1}
    assert result["alerts_created"] == 5  # SAFE produces no alert
    for days, status in expected.items():
        batch = (await client.get(f"/batches/{ids[days]}", headers=h)).json()
        assert batch["status"] == status, (days, batch)
        assert batch["days_remaining"] == days


async def test_batch_expiring_tomorrow_is_critical_with_one_alert(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=1, qty=24)
    first = (await client.post("/expiry/check", headers=h)).json()
    assert first["alerts_created"] == 1
    alerts = (await client.get("/alerts/critical", headers=h)).json()
    assert alerts["total"] == 1
    alert = alerts["items"][0]
    assert alert["alert_type"] == "CRITICAL_EXPIRY"
    assert alert["severity"] == "CRITICAL"
    assert alert["days_remaining"] == 1
    assert alert["quantity_at_risk"] == 24
    assert alert["batch_id"] == batch["id"]

    second = (await client.post("/expiry/check", headers=h)).json()
    assert second["alerts_created"] == 0
    assert second["duplicates_prevented"] == 1
    assert (await client.get("/alerts", headers=h)).json()["total"] == 1


async def test_escalation_resolves_superseded_alert(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=60, qty=5)
    await client.post("/expiry/check", headers=h)
    assert (await client.get("/alerts/expiring-soon", headers=h)).json()["total"] == 1
    await client.patch(f"/batches/{batch['id']}", json={"expiry_date": iso(2)}, headers=h)
    result = (await client.post("/expiry/check", headers=h)).json()
    assert result["alerts_created"] == 1 and result["alerts_auto_resolved"] == 1
    assert (await client.get("/alerts/expiring-soon", headers=h)).json()["total"] == 0
    assert (await client.get("/alerts/critical", headers=h)).json()["total"] == 1


async def test_depleted_batch_alert_auto_resolves(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    batch = await create_batch(client, h, product["id"], days=5, qty=2)
    await client.post("/expiry/check", headers=h)
    await client.post("/sales", json={"product_id": product["id"], "quantity": 2}, headers=h)
    result = (await client.post("/expiry/check", headers=h)).json()
    assert result["alerts_auto_resolved"] == 1
    assert (await client.get("/alerts?is_resolved=false", headers=h)).json()["total"] == 0
    assert (await client.get(f"/batches/{batch['id']}", headers=h)).json()["status"] == "DEPLETED"


async def test_expiry_listing_endpoints(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    for d in (-3, 7, 45, 200):
        await create_batch(client, h, product["id"], days=d)
    assert (await client.get("/expiry/expired", headers=h)).json()["total"] == 1
    assert (await client.get("/expiry/critical", headers=h)).json()["items"][0]["days_remaining"] == 7
    assert (await client.get("/expiry/soon", headers=h)).json()["total"] == 1
    assert (await client.get("/expiry", headers=h)).json()["total"] == 4
    assert (await client.get("/expiry?status=SAFE&status=EXPIRED", headers=h)).json()["total"] == 2
