from __future__ import annotations

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.company import CompanyRead
from app.schemas.user import UserRead
from app.utils.dates import is_valid_timezone
from app.schemas.types import OptionalPhone, Password


class RegisterRequest(BaseModel):
    """Creates a new company and its first ADMIN user."""

    company_name: str = Field(min_length=2, max_length=200)
    company_email: EmailStr
    company_phone: OptionalPhone = None
    company_address: str | None = Field(default=None, max_length=1000)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    timezone: str = "UTC"
    name: str = Field(min_length=2, max_length=200, description="Admin full name")
    email: EmailStr
    password: Password
    phone_number: OptionalPhone = Field(default=None, description="E.164, receives SMS alerts")


    @field_validator("currency")
    @classmethod
    def validate_currency(cls, v: str) -> str:
        if not v.isalpha():
            raise ValueError("Currency must be a 3-letter ISO code")
        return v.upper()

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        if not is_valid_timezone(v):
            raise ValueError("Unknown IANA timezone, e.g. 'Africa/Nairobi'")
        return v


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Seconds until expiry")
    user: UserRead


class RegisterResponse(TokenResponse):
    company: CompanyRead


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ForgotPasswordResponse(BaseModel):
    message: str
    reset_token: str | None = Field(
        default=None, description="Only returned outside production, to allow local testing"
    )


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=10)
    new_password: Password

