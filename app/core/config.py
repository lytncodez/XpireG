"""Application configuration loaded from environment variables / .env."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Severity = Literal["INFO", "WARNING", "CRITICAL", "URGENT"]


class Settings(BaseSettings):
    """All runtime settings. Secrets are SecretStr so they never leak into logs/reprs."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    APP_NAME: str = "ExpireGuard"
    APP_ENV: Literal["development", "test", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    LOG_JSON: bool = False
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:5173"

    # --- Database ----------------------------------------------------------
    DATABASE_URL: str = "postgresql+asyncpg://expireguard:expireguard@localhost:5432/expireguard"
    DB_POOL_SIZE: int = 10
    DB_MAX_OVERFLOW: int = 20
    DB_ECHO: bool = False

    # --- Auth --------------------------------------------------------------
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    RESET_TOKEN_EXPIRE_MINUTES: int = 30

    # --- Rate limiting -----------------------------------------------------
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_AUTH_PER_MINUTE: int = 10
    RATE_LIMIT_UPLOAD_PER_MINUTE: int = 5

    # --- Expiry engine -----------------------------------------------------
    EXPIRY_CRITICAL_DAYS: int = 30
    EXPIRY_SOON_DAYS: int = 90

    # --- Inventory ---------------------------------------------------------
    ALLOW_NEGATIVE_INVENTORY: bool = False
    LOW_STOCK_THRESHOLD: int = 10

    # --- SMS ---------------------------------------------------------------
    SMS_MODE: Literal["mock", "live"] = "mock"
    SMS_PROVIDER: Literal["africastalking", "twilio"] = "africastalking"
    SMS_MIN_SEVERITY: Severity = "CRITICAL"
    SMS_TIMEOUT_SECONDS: float = 10.0
    SMS_MAX_ATTEMPTS: int = 3
    SMS_SENDER_ID: str | None = None
    AT_USERNAME: str | None = None
    AT_API_KEY: SecretStr | None = None
    AT_SANDBOX: bool = True
    TWILIO_ACCOUNT_SID: str | None = None
    TWILIO_AUTH_TOKEN: SecretStr | None = None
    TWILIO_FROM_NUMBER: str | None = None

    # --- Imports -----------------------------------------------------------
    MAX_UPLOAD_SIZE_MB: int = 10
    MAX_IMPORT_ROWS: int = 50_000
    IMPORT_DATE_DAYFIRST: bool = True

    # --- Analytics / anomalies ----------------------------------------------
    ANALYTICS_DEFAULT_DAYS: int = 30
    ANOMALY_BASELINE_DAYS: int = 28
    ANOMALY_RECENT_DAYS: int = 7
    ANOMALY_Z_THRESHOLD: float = 2.5
    ANOMALY_MIN_PCT_CHANGE: float = 50.0
    ANOMALY_HIGH_COVER_DAYS: int = 180
    ANOMALY_LOW_COVER_DAYS: int = 3

    # --- AI intelligence layer (Phase 2) -----------------------------------
    AI_PROVIDER: Literal["mock", "openai", "anthropic"] = "mock"
    OPENAI_API_KEY: SecretStr | None = None
    OPENAI_MODEL: str | None = None
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    ANTHROPIC_API_KEY: SecretStr | None = None
    ANTHROPIC_MODEL: str | None = None
    ANTHROPIC_BASE_URL: str = "https://api.anthropic.com/v1"
    AI_TIMEOUT_SECONDS: float = 45.0
    AI_MAX_TOKENS: int = 2000
    AI_TEMPERATURE: float = 0.2
    AI_CHAT_HISTORY_MESSAGES: int = 10
    AI_CONTEXT_LIST_LIMIT: int = 10
    AI_RATE_LIMIT_PER_MINUTE: int = 20

    # --- Background jobs ---------------------------------------------------
    SCHEDULER_ENABLED: bool = True
    EXPIRY_JOB_HOUR: int = 6
    EXPIRY_JOB_MINUTE: int = 0
    ALERT_JOB_INTERVAL_MINUTES: int = 30
    CLEANUP_JOB_HOUR: int = 3
    ALERT_RETENTION_DAYS: int = 180

    @field_validator("EXPIRY_CRITICAL_DAYS")
    @classmethod
    def _critical_positive(cls, v: int) -> int:
        if v < 1:
            raise ValueError("EXPIRY_CRITICAL_DAYS must be >= 1")
        return v

    @model_validator(mode="after")
    def _validate(self) -> "Settings":
        if self.EXPIRY_SOON_DAYS <= self.EXPIRY_CRITICAL_DAYS:
            raise ValueError("EXPIRY_SOON_DAYS must be greater than EXPIRY_CRITICAL_DAYS")
        secret = self.JWT_SECRET_KEY.get_secret_value()
        if self.APP_ENV == "production":
            if len(secret) < 32 or secret.startswith("change-me"):
                raise ValueError("JWT_SECRET_KEY must be a strong random value (>=32 chars) in production")
        if self.SMS_MODE == "live":
            if self.SMS_PROVIDER == "africastalking" and not (self.AT_USERNAME and secret_value(self.AT_API_KEY)):
                raise ValueError("SMS_MODE=live with africastalking requires AT_USERNAME and AT_API_KEY")
            if self.SMS_PROVIDER == "twilio" and not (
                self.TWILIO_ACCOUNT_SID and secret_value(self.TWILIO_AUTH_TOKEN) and self.TWILIO_FROM_NUMBER
            ):
                raise ValueError(
                    "SMS_MODE=live with twilio requires TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER"
                )
        return self

    @property
    def cors_origins(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        return self.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    @property
    def expose_reset_token(self) -> bool:
        """Return reset tokens in API responses only outside production (local testing)."""
        return self.APP_ENV != "production"


def secret_value(value: SecretStr | None) -> str | None:
    """Return the secret, treating an empty/blank value (e.g. `KEY=` in .env) as not set."""
    if value is None:
        return None
    raw = value.get_secret_value().strip()
    return raw or None


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
