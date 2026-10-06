"""SMS provider abstraction.

    SMS_MODE=mock  -> MockSMSProvider (logs + in-memory outbox, full local demo)
    SMS_MODE=live  -> Africa's Talking or Twilio over HTTPS (credentials from env only)

`send_sms` never raises: failures come back as SMSResult(success=False, error=...).
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache

import httpx

from app.core.config import secret_value, settings
from app.core.logging import get_logger
from app.utils.dates import utcnow

logger = get_logger(__name__)


@dataclass(frozen=True)
class SMSResult:
    success: bool
    provider: str
    message_id: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class MockMessage:
    to: str
    body: str
    message_id: str
    sent_at: datetime


class SMSProvider(ABC):
    name: str = "base"

    @abstractmethod
    async def send(self, to: str, message: str) -> SMSResult:  # pragma: no cover - interface
        raise NotImplementedError


class MockSMSProvider(SMSProvider):
    name = "mock"
    outbox: deque[MockMessage] = deque(maxlen=1000)

    async def send(self, to: str, message: str) -> SMSResult:
        message_id = f"mock-{uuid.uuid4().hex[:16]}"
        self.outbox.append(MockMessage(to=to, body=message, message_id=message_id, sent_at=utcnow()))
        logger.info("[MOCK SMS] to=%s id=%s body=%s", to, message_id, message)
        return SMSResult(success=True, provider=self.name, message_id=message_id)


class AfricasTalkingSMSProvider(SMSProvider):
    name = "africastalking"

    def __init__(self, username: str, api_key: str, sandbox: bool, sender_id: str | None, timeout: float) -> None:
        self.username = username
        self._api_key = api_key
        self.sender_id = sender_id
        self.timeout = timeout
        host = "api.sandbox.africastalking.com" if sandbox else "api.africastalking.com"
        self.url = f"https://{host}/version1/messaging"

    async def send(self, to: str, message: str) -> SMSResult:
        data = {"username": self.username, "to": to, "message": message}
        if self.sender_id:
            data["from"] = self.sender_id
        headers = {"apiKey": self._api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(self.url, data=data, headers=headers)
        if resp.status_code >= 400:
            return SMSResult(False, self.name, error=f"HTTP {resp.status_code}: {resp.text[:300]}")
        try:
            recipients = resp.json()["SMSMessageData"]["Recipients"]
        except (ValueError, KeyError, TypeError):
            return SMSResult(False, self.name, error=f"Unexpected response: {resp.text[:300]}")
        if not recipients:
            return SMSResult(False, self.name, error="Provider accepted no recipients")
        first = recipients[0]
        if str(first.get("status", "")).lower() != "success":
            return SMSResult(False, self.name, error=f"Delivery rejected: {first.get('status')}")
        return SMSResult(True, self.name, message_id=first.get("messageId"))


class TwilioSMSProvider(SMSProvider):
    name = "twilio"

    def __init__(self, account_sid: str, auth_token: str, from_number: str, timeout: float) -> None:
        self.account_sid = account_sid
        self._auth_token = auth_token
        self.from_number = from_number
        self.timeout = timeout
        self.url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"

    async def send(self, to: str, message: str) -> SMSResult:
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            resp = await client.post(
                self.url,
                data={"To": to, "From": self.from_number, "Body": message},
                auth=(self.account_sid, self._auth_token),
            )
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("message", resp.text[:300])
            except ValueError:
                detail = resp.text[:300]
            return SMSResult(False, self.name, error=f"HTTP {resp.status_code}: {detail}")
        try:
            sid = resp.json().get("sid")
        except ValueError:
            sid = None
        return SMSResult(True, self.name, message_id=sid)


@lru_cache
def get_sms_provider() -> SMSProvider:
    if settings.SMS_MODE == "mock":
        return MockSMSProvider()
    if settings.SMS_PROVIDER == "twilio":
        return TwilioSMSProvider(
            settings.TWILIO_ACCOUNT_SID or "",
            secret_value(settings.TWILIO_AUTH_TOKEN) or "",
            settings.TWILIO_FROM_NUMBER or "",
            settings.SMS_TIMEOUT_SECONDS,
        )
    return AfricasTalkingSMSProvider(
        settings.AT_USERNAME or "",
        secret_value(settings.AT_API_KEY) or "",
        settings.AT_SANDBOX,
        settings.SMS_SENDER_ID,
        settings.SMS_TIMEOUT_SECONDS,
    )


async def send_sms(to: str, message: str, provider: SMSProvider | None = None) -> SMSResult:
    provider = provider or get_sms_provider()
    try:
        result = await provider.send(to, message)
    except httpx.TimeoutException:
        result = SMSResult(False, provider.name, error="Provider timed out")
    except Exception as exc:  # noqa: BLE001 - provider errors must never propagate
        logger.exception("SMS provider %s raised", provider.name)
        result = SMSResult(False, provider.name, error=f"{type(exc).__name__}: {str(exc)[:300]}")
    if not result.success:
        logger.warning("SMS to %s via %s failed: %s", to, provider.name, result.error)
    return result
