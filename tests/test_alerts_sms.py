from app.services.sms_service import MockSMSProvider, SMSResult
from tests.helpers import create_batch, create_product, create_user, register_company


async def test_critical_alert_sends_mock_sms_and_records_notification(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=1, qty=10)
    result = (await client.post("/expiry/check", headers=h)).json()
    assert result["sms_sent"] == 1 and result["sms_failed"] == 0

    assert len(MockSMSProvider.outbox) == 1
    msg = MockSMSProvider.outbox[-1]
    assert msg.to == "+254700000001"
    assert "Fresh Milk 1L" in msg.body and "1 day" in msg.body

    alert = (await client.get("/alerts", headers=h)).json()["items"][0]
    assert alert["sms_sent"] is True and alert["sms_sent_at"] and alert["sms_error"] is None
    assert alert["recipient_phone"] == "+254700000001"
    notes = (await client.get(f"/notifications?alert_id={alert['id']}", headers=h)).json()
    assert notes["total"] == 1
    n = notes["items"][0]
    assert n["status"] == "SENT" and n["provider"] == "mock" and n["provider_message_id"].startswith("mock-")


async def test_warning_alerts_do_not_send_sms_by_default(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=60)
    result = (await client.post("/expiry/check", headers=h)).json()
    assert result["alerts_created"] == 1 and result["sms_sent"] == 0
    assert len(MockSMSProvider.outbox) == 0


async def test_sms_failure_preserves_alert_and_records_error(client, admin, monkeypatch):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=-1, qty=3)

    async def boom(self, to, message):
        raise RuntimeError("provider down")

    monkeypatch.setattr(MockSMSProvider, "send", boom)
    resp = await client.post("/expiry/check", headers=h)
    assert resp.status_code == 200
    result = resp.json()
    assert result["alerts_created"] == 1 and result["sms_failed"] == 1 and result["sms_sent"] == 0

    alert = (await client.get("/alerts/expired", headers=h)).json()["items"][0]
    assert alert["severity"] == "URGENT"
    assert alert["sms_sent"] is False
    assert "provider down" in alert["sms_error"]
    failed = (await client.get("/notifications?status=FAILED", headers=h)).json()["items"][0]
    assert failed["attempts"] == 1 and "provider down" in failed["error_message"]

    # Provider recovers: retry succeeds and updates the alert
    monkeypatch.undo()
    retried = (await client.post(f"/notifications/{failed['id']}/retry", headers=h)).json()
    assert retried["status"] == "SENT" and retried["attempts"] == 2
    alert = (await client.get(f"/alerts/{alert['id']}", headers=h)).json()
    assert alert["sms_sent"] is True and alert["sms_error"] is None


async def test_provider_soft_failure_result(client, admin, monkeypatch):
    h = admin["headers"]

    async def rejected(self, to, message):
        return SMSResult(success=False, provider="mock", error="Invalid phone number")

    monkeypatch.setattr(MockSMSProvider, "send", rejected)
    resp = await client.post("/alerts/test-sms", json={"phone_number": "+254711111111"}, headers=h)
    assert resp.status_code == 200
    assert resp.json()["status"] == "FAILED" and resp.json()["error_message"] == "Invalid phone number"


async def test_test_sms_endpoint(client, admin):
    resp = await client.post("/alerts/test-sms", json={"phone_number": "+254 711 111 111", "message": "hello"},
                             headers=admin["headers"])
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "SENT" and body["recipient"] == "+254711111111"
    assert MockSMSProvider.outbox[-1].body == "hello"
    bad = await client.post("/alerts/test-sms", json={"phone_number": "0711"}, headers=admin["headers"])
    assert bad.status_code == 422


async def test_sms_goes_to_all_admins_and_managers(client, admin):
    h = admin["headers"]
    await create_user(client, h, "mgr@acme.test", "MANAGER", phone="+254700000009")
    await create_user(client, h, "staff@acme.test", "STAFF", phone="+254700000010")
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=3)
    await client.post("/expiry/check", headers=h)
    assert {m.to for m in MockSMSProvider.outbox} == {"+254700000001", "+254700000009"}


async def test_no_recipient_is_recorded_not_raised(client):
    company = await register_company(client, "nophone@acme.test", "No Phone Ltd", phone=None)
    h = company["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=1)
    result = (await client.post("/expiry/check", headers=h)).json()
    assert result["alerts_created"] == 1 and result["sms_failed"] == 1
    alert = (await client.get("/alerts", headers=h)).json()["items"][0]
    assert "No recipient" in alert["sms_error"]


async def test_read_and_resolve_alert(client, admin):
    h = admin["headers"]
    product = await create_product(client, h)
    await create_batch(client, h, product["id"], days=1)
    await client.post("/expiry/check", headers=h)
    alert = (await client.get("/alerts", headers=h)).json()["items"][0]
    read = (await client.patch(f"/alerts/{alert['id']}/read", headers=h)).json()
    assert read["is_read"] is True and read["is_resolved"] is False
    resolved = (await client.patch(f"/alerts/{alert['id']}/resolve", headers=h)).json()
    assert resolved["is_resolved"] is True and resolved["resolved_at"]
    # Condition still holds, so the next check raises a fresh alert (no unresolved duplicate exists)
    result = (await client.post("/expiry/check", headers=h)).json()
    assert result["alerts_created"] == 1
    assert (await client.get("/alerts?is_resolved=false", headers=h)).json()["total"] == 1
