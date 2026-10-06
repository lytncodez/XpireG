"""Authentication, role-based authorisation and rate-limit dependencies."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select

from app.core.config import settings
from app.core.exceptions import AuthenticationError, PermissionDeniedError, RateLimitError
from app.core.security import decode_token
from app.dependencies.database import DbSession
from app.models import RevokedToken, User, UserRole
from app.utils.helpers import client_ip, rate_limiter

bearer_scheme = HTTPBearer(auto_error=False, description="JWT from POST /auth/login")


async def get_token_payload(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: DbSession,
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AuthenticationError("Not authenticated")
    payload = decode_token(credentials.credentials, expected_type="access")
    jti = payload.get("jti")
    if not jti:
        raise AuthenticationError("Invalid token")
    if await db.scalar(select(RevokedToken.jti).where(RevokedToken.jti == jti)):
        raise AuthenticationError("Token has been revoked")
    return payload


async def get_current_user(
    payload: Annotated[dict[str, Any], Depends(get_token_payload)],
    db: DbSession,
) -> User:
    try:
        user_id = uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise AuthenticationError("Invalid token") from exc
    user = await db.get(User, user_id)
    if user is None or not user.is_active:
        raise AuthenticationError("User not found or inactive")
    # Guard against a token minted for another tenant (e.g. user moved/recreated).
    if str(user.company_id) != payload.get("cid"):
        raise AuthenticationError("Invalid token")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
TokenPayload = Annotated[dict[str, Any], Depends(get_token_payload)]


def require_roles(*roles: UserRole) -> Callable[..., Any]:
    allowed = set(roles)

    async def _checker(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise PermissionDeniedError(
                "You do not have permission to perform this action",
                details={"required_roles": sorted(r.value for r in allowed)},
            )
        return user

    return _checker


AdminUser = Annotated[User, Depends(require_roles(UserRole.ADMIN))]
ManagerUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.MANAGER))]
StaffUser = Annotated[User, Depends(require_roles(UserRole.ADMIN, UserRole.MANAGER, UserRole.STAFF))]


def rate_limit(scope: str, per_minute: int | None = None) -> Callable[..., Any]:
    """Per-IP sliding window limit for sensitive endpoints."""

    async def _limiter(request: Request) -> None:
        if not settings.RATE_LIMIT_ENABLED:
            return
        limit = per_minute or settings.RATE_LIMIT_AUTH_PER_MINUTE
        key = f"{scope}:{client_ip(request) or 'unknown'}"
        if not rate_limiter.hit(key, limit):
            raise RateLimitError("Too many requests, please try again in a minute")

    return _limiter
