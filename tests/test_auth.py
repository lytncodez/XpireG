from tests.helpers import PASSWORD, login, register_company


async def test_register_creates_company_and_admin(client, admin):
    assert admin["user"]["role"] == "ADMIN"
    assert admin["company"]["name"] == "Acme Foods"
    assert "password_hash" not in admin["user"]
    me = await client.get("/auth/me", headers=admin["headers"])
    assert me.status_code == 200
    assert me.json()["email"] == "admin@acme.test"


async def test_duplicate_registration_conflicts(client, admin):
    resp = await client.post("/auth/register", json={
        "company_name": "Other", "company_email": "x@other.test", "name": "Someone",
        "email": "ADMIN@acme.test", "password": PASSWORD,
    })
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "CONFLICT"


async def test_weak_password_rejected(client):
    resp = await client.post("/auth/register", json={
        "company_name": "Weak Co", "company_email": "w@weak.test", "name": "Weak", "email": "w@w.test",
        "password": "short",
    })
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_login_success_and_failure(client, admin):
    assert "Authorization" in await login(client, "admin@acme.test")
    bad = await client.post("/auth/login", json={"email": "admin@acme.test", "password": "WrongPass1"})
    assert bad.status_code == 401
    assert bad.json()["error"]["code"] == "UNAUTHORIZED"


async def test_requires_authentication(client):
    resp = await client.get("/products")
    assert resp.status_code == 401
    resp = await client.get("/products", headers={"Authorization": "Bearer not-a-token"})
    assert resp.status_code == 401


async def test_logout_revokes_token(client, admin):
    resp = await client.post("/auth/logout", headers=admin["headers"])
    assert resp.status_code == 200
    assert (await client.get("/auth/me", headers=admin["headers"])).status_code == 401


async def test_forgot_and_reset_password(client, admin):
    resp = await client.post("/auth/forgot-password", json={"email": "admin@acme.test"})
    assert resp.status_code == 200
    token = resp.json()["reset_token"]
    assert token
    # Unknown email gets the same generic answer
    unknown = await client.post("/auth/forgot-password", json={"email": "nobody@acme.test"})
    assert unknown.status_code == 200 and unknown.json()["reset_token"] is None

    reset = await client.post("/auth/reset-password", json={"token": token, "new_password": "NewSecret456"})
    assert reset.status_code == 200
    assert (await client.post("/auth/login", json={"email": "admin@acme.test", "password": PASSWORD})).status_code == 401
    assert "Authorization" in await login(client, "admin@acme.test", "NewSecret456")
    # Single use
    again = await client.post("/auth/reset-password", json={"token": token, "new_password": "Another789x"})
    assert again.status_code == 401


async def test_two_companies_register_independently(client, admin):
    other = await register_company(client, "boss@second.test", "Second Co")
    assert other["company"]["id"] != admin["company"]["id"]
