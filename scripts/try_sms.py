"""Send one SMS through ExpireGuard's SMS service. No database needed.

    python scripts/try_sms.py +254712345678
    python scripts/try_sms.py +254712345678 "Custom message"

Uses SMS_MODE / SMS_PROVIDER and credentials from .env:
  * SMS_MODE=mock -> nothing is sent; the message is printed and stored in the mock outbox.
  * SMS_MODE=live -> sent through Africa's Talking or Twilio.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.services.sms_service import MockSMSProvider, get_sms_provider, send_sms  # noqa: E402
from app.utils.validators import normalize_phone  # noqa: E402

DEFAULT_MESSAGE = (
    "[ExpireGuard] Demo Fresh Mart: Fresh Milk 1L batch MLK-01 expires in 1 day. "
    "40 bottle at risk. (test message)"
)


async def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    try:
        phone = normalize_phone(sys.argv[1])
    except ValueError as exc:
        print(f"Invalid phone number: {exc}")
        return 1
    message = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MESSAGE
    provider = get_sms_provider()
    print(f"SMS_MODE={settings.SMS_MODE}  provider={provider.name}")
    if settings.SMS_MODE == "live" and settings.SMS_PROVIDER == "africastalking":
        print(f"Africa's Talking {'SANDBOX' if settings.AT_SANDBOX else 'LIVE'} as user '{settings.AT_USERNAME}'")
    print(f"Sending to {phone}: {message}\n")

    result = await send_sms(phone, message)
    if result.success:
        print(f"SUCCESS  provider={result.provider}  message_id={result.message_id}")
        if isinstance(provider, MockSMSProvider):
            print("Mock mode: nothing left this computer. Set SMS_MODE=live in .env to send a real SMS.")
        return 0
    print(f"FAILED   provider={result.provider}  error={result.error}")
    return 2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
