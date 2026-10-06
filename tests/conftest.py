"""Test fixtures. Requires PostgreSQL (TEST_DATABASE_URL, default: local expireguard_test DB).

The schema is created from the ORM metadata once per session and every table is truncated
before each test, so tests are fully independent.
"""

import asyncio
import os

os.environ["APP_ENV"] = "test"
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-0123456789-abcdefghijklmnopqrstuvwxyz")
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://expireguard:expireguard@localhost:5432/expireguard_test"
)
os.environ["SCHEDULER_ENABLED"] = "false"
os.environ["RATE_LIMIT_ENABLED"] = "false"
os.environ["SMS_MODE"] = "mock"
os.environ["SMS_MIN_SEVERITY"] = "CRITICAL"
os.environ["AI_PROVIDER"] = "mock"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

import app.models  # noqa: E402,F401  (registers tables)
from app.core.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app as fastapi_app  # noqa: E402
from app.services.sms_service import MockSMSProvider  # noqa: E402
from app.utils.helpers import rate_limiter  # noqa: E402
from tests.helpers import register_company  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def database_schema():
    async def _create():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.run_sync(Base.metadata.create_all)

    async def _drop():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)

    asyncio.run(_create())
    yield
    asyncio.run(_drop())


@pytest_asyncio.fixture(autouse=True)
async def clean_state():
    tables = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
    async with engine.begin() as conn:
        await conn.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
    MockSMSProvider.outbox.clear()
    rate_limiter.reset()
    yield


@pytest.fixture(autouse=True)
def reset_dependency_overrides():
    yield
    fastapi_app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=fastapi_app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def db():
    async with SessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def admin(client):
    return await register_company(client, "admin@acme.example.com", "Acme Foods", phone="+254700000001")


@pytest_asyncio.fixture
async def other_admin(client):
    return await register_company(client, "admin@globex.example.com", "Globex Mart", phone="+254700000002")
