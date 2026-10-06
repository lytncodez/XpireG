from fastapi import APIRouter, Depends, Request, status

from app.core.config import settings
from app.core.exceptions import error_responses
from app.dependencies.auth import CurrentUser, TokenPayload, rate_limit
from app.dependencies.database import DbSession
from app.schemas import MessageResponse
from app.schemas.auth import (
    ForgotPasswordRequest,
    ForgotPasswordResponse,
    LoginRequest,
    RegisterRequest,
    RegisterResponse,
    ResetPasswordRequest,
    TokenResponse,
)
from app.schemas.company import CompanyRead
from app.schemas.user import UserRead
from app.services import auth_service
from app.utils.helpers import client_ip

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a company and its admin",
    description="Creates a new tenant (company) plus its first ADMIN user and returns an access token.",
    responses=error_responses(409, 422, 429),
    dependencies=[Depends(rate_limit("register"))],
)
async def register(data: RegisterRequest, request: Request, db: DbSession) -> RegisterResponse:
    user, company = await auth_service.register(db, data, client_ip(request))
    token, expires = auth_service.issue_token(user)
    return RegisterResponse(
        access_token=token, expires_in=expires, user=UserRead.model_validate(user),
        company=CompanyRead.model_validate(company),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Log in",
    description="Exchange email and password for a JWT bearer token.",
    responses=error_responses(401, 422, 429),
    dependencies=[Depends(rate_limit("login"))],
)
async def login(data: LoginRequest, request: Request, db: DbSession) -> TokenResponse:
    user = await auth_service.authenticate(db, data.email, data.password, client_ip(request))
    token, expires = auth_service.issue_token(user)
    return TokenResponse(access_token=token, expires_in=expires, user=UserRead.model_validate(user))


@router.post(
    "/logout",
    response_model=MessageResponse,
    summary="Log out",
    description="Revokes the presented token; it cannot be used again.",
    responses=error_responses(401),
)
async def logout(user: CurrentUser, payload: TokenPayload, request: Request, db: DbSession) -> MessageResponse:
    await auth_service.logout(db, user, payload, client_ip(request))
    return MessageResponse(message="Logged out")


@router.get(
    "/me",
    response_model=UserRead,
    summary="Current user",
    description="Profile of the authenticated user.",
    responses=error_responses(401),
)
async def me(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post(
    "/forgot-password",
    response_model=ForgotPasswordResponse,
    summary="Request a password reset",
    description=(
        "Always returns the same message to avoid revealing which emails exist. The reset token is sent by SMS "
        "if the user has a phone number. Outside production the token is also returned for local testing."
    ),
    responses=error_responses(422, 429),
    dependencies=[Depends(rate_limit("forgot"))],
)
async def forgot_password(data: ForgotPasswordRequest, request: Request, db: DbSession) -> ForgotPasswordResponse:
    token = await auth_service.forgot_password(db, data.email, client_ip(request))
    return ForgotPasswordResponse(
        message="If the account exists, password reset instructions have been sent.",
        reset_token=token if settings.expose_reset_token else None,
    )


@router.post(
    "/reset-password",
    response_model=MessageResponse,
    summary="Reset password",
    description="Set a new password using a reset token. Tokens are single-use and expire.",
    responses=error_responses(401, 422, 429),
    dependencies=[Depends(rate_limit("reset"))],
)
async def reset_password(data: ResetPasswordRequest, request: Request, db: DbSession) -> MessageResponse:
    await auth_service.reset_password(db, data.token, data.new_password, client_ip(request))
    return MessageResponse(message="Password has been reset")
