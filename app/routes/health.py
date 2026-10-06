from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app import __version__
from app.core.config import settings
from app.core.database import check_database

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str
    database: str
    sms_mode: str


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Service and database health",
    description="Returns 200 when the API and PostgreSQL are reachable, 503 when the database is not ready.",
    responses={503: {"model": HealthResponse, "description": "Database not ready"}},
)
async def health() -> JSONResponse:
    db_ok = await check_database()
    body = HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        environment=settings.APP_ENV,
        database="ready" if db_ok else "unavailable",
        sms_mode=settings.SMS_MODE,
    )
    return JSONResponse(status_code=200 if db_ok else 503, content=body.model_dump())
