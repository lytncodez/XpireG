"""Shared test helpers (API-level)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from httpx import AsyncClient

from app.utils.dates import today_in_tz

PASSWORD = "Secret123!"


def today() -> date:
    return today_in_tz("UTC")


def iso(days_from_today: int) -> str:
    return (today() + timedelta(days=days_from_today)).isoformat()


async def register_company(
    client: AsyncClient, email: str, company: str, phone: str | None = "+254700000001", password: str = PASSWORD
) -> dict[str, Any]:
    payload = {
        "company_name": company,
        "company_email": f"office.{email}",
        "name": "Admin User",
        "email": email,
        "password": password,
        "timezone": "UTC",
        "currency": "KES",
    }
    if phone:
        payload["phone_number"] = phone
    resp = await client.post("/auth/register", json=payload)
    assert resp.status_code == 201, resp.text
    data = resp.json()
    return {
        "headers": {"Authorization": f"Bearer {data['access_token']}"},
        "token": data["access_token"],
        "user": data["user"],
        "company": data["company"],
        "email": email,
    }


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> dict[str, str]:
    resp = await client.post("/auth/login", json={"email": email, "password": password})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def create_user(client: AsyncClient, admin_headers: dict, email: str, role: str, phone: str | None = None) -> dict:
    body = {"name": f"{role.title()} User", "email": email, "password": PASSWORD, "role": role}
    if phone:
        body["phone_number"] = phone
    resp = await client.post("/users", json=body, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    return {"user": resp.json(), "headers": await login(client, email)}


async def create_product(client: AsyncClient, headers: dict, **overrides) -> dict:
    body = {
        "name": "Fresh Milk 1L",
        "sku": "MLK-001",
        "barcode": "6001234567890",
        "unit": "bottle",
        "selling_price": "1.50",
        "cost_price": "1.00",
    }
    body.update(overrides)
    resp = await client.post("/products", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_batch(
    client: AsyncClient, headers: dict, product_id: str, days: int, qty: int = 50, batch_number: str | None = None
) -> dict:
    body = {
        "product_id": product_id,
        "batch_number": batch_number or f"B{days:+d}".replace("+", "P").replace("-", "M"),
        "expiry_date": iso(days),
        "initial_quantity": qty,
    }
    resp = await client.post("/batches", json=body, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()
