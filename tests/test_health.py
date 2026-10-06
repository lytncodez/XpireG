async def test_health_reports_database_ready(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "ready"
    assert body["sms_mode"] == "mock"


async def test_docs_and_openapi_available(client):
    assert (await client.get("/docs")).status_code == 200
    assert (await client.get("/redoc")).status_code == 200
    spec = (await client.get("/openapi.json")).json()
    for path in ("/auth/login", "/products", "/batches", "/expiry/check", "/alerts/test-sms", "/dashboard/summary"):
        assert path in spec["paths"]


async def test_unknown_route_uses_error_envelope(client):
    resp = await client.get("/does-not-exist")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "NOT_FOUND"
