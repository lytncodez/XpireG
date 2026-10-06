"""ExpireGuard FastAPI application factory."""

from __future__ import annotations

import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging, get_logger, request_id_ctx
from app.routes import api_router

logger = get_logger("app")

DESCRIPTION = """
**ExpireGuard** is an inventory expiry monitoring and business intelligence backend.

Flow: Business → Products → Batches → Inventory → Sales → Expiry Engine → Alerts → SMS → Analytics → Dashboard.

* Authenticate with `POST /auth/login`, then click **Authorize** and paste the `access_token`.
* All data is isolated per company. Roles: ADMIN ⊇ MANAGER ⊇ STAFF.
* Errors always use `{"error": {"code", "message", "details"}}`.
"""


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    scheduler = None
    if settings.SCHEDULER_ENABLED:
        from app.jobs import create_scheduler

        scheduler = create_scheduler()
        scheduler.start()
        logger.info("Background scheduler started")
    logger.info("ExpireGuard %s started (env=%s, sms=%s)", __version__, settings.APP_ENV, settings.SMS_MODE)
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ExpireGuard API",
        version=__version__,
        description=DESCRIPTION,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        request_id = (request.headers.get("x-request-id") or uuid.uuid4().hex[:16])[:64]
        token = request_id_ctx.set(request_id)
        started = time.perf_counter()
        try:
            content_length = request.headers.get("content-length")
            if content_length and content_length.isdigit() and int(content_length) > settings.max_upload_bytes + 1_048_576:
                response = JSONResponse(
                    status_code=413,
                    content={"error": {"code": "PAYLOAD_TOO_LARGE", "message": "Request body too large", "details": None}},
                )
            else:
                response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "no-referrer"
            logger.info(
                "%s %s -> %s (%.1f ms)", request.method, request.url.path, response.status_code,
                (time.perf_counter() - started) * 1000,
            )
            return response
        finally:
            request_id_ctx.reset(token)

    register_exception_handlers(app)
    app.include_router(api_router)
    return app


app = create_app()
