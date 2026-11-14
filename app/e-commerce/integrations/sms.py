# Copyright (C) 2026 Mohammadhossein Borazjani
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <https://www.gnu.org/licenses/>.
"""SMS delivery integration supporting Kavenegar and dev fallback.

When KAVENEGAR_API_KEY is configured in settings, messages are dispatched
via Kavenegar's REST API. When no key is set (development and tests),
messages fall back to stdout logging and an in-memory test log.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated, Any

import httpx
from fastapi import Depends

logger = logging.getLogger("bandeh.sms")

# Maps phone -> last sent OTP code for local development and automated testing
_DEV_LAST_CODE: dict[str, str] = {}
# Log of all sent messages in development/testing
_DEV_SENT_MESSAGES: list[dict[str, Any]] = []

KAVENEGAR_BASE_URL = "https://api.kavenegar.com/v1"


class KavenegarError(Exception):
    """Exception raised when Kavenegar API call fails."""

    def __init__(self, status: int | None, message: str) -> None:
        super().__init__(message or f"Kavenegar error {status}")
        self.status = status
        self.message = message


class KavenegarClient:
    """Kavenegar SMS client with automatic dev fallback when unconfigured."""

    def __init__(
        self,
        api_key: str | None = None,
        sender: str | None = None,
        base_url: str = KAVENEGAR_BASE_URL,
    ) -> None:
        self.api_key = api_key
        self.sender = sender
        self.base_url = base_url.rstrip("/")

    @property
    def is_configured(self) -> bool:
        """Return True if an API key is available."""
        return bool(self.api_key and self.api_key.strip())

    def _format_send_url(self) -> str:
        return f"{self.base_url}/{self.api_key}/sms/send.json"

    def send_sms(self, phone: str, message: str) -> bool:
        """Send an SMS synchronously (uses dev fallback if unconfigured)."""
        if not self.is_configured:
            print(f"[DEV SMS] to {phone}: {message}")
            _DEV_SENT_MESSAGES.append({"phone": phone, "message": message, "type": "general"})
            return True

        url = self._format_send_url()
        data: dict[str, str] = {"receptor": phone, "message": message}
        if self.sender:
            data["sender"] = self.sender

        try:
            with httpx.Client(timeout=10.0, trust_env=False) as client:
                resp = client.post(url, data=data)
                resp.raise_for_status()
                payload = resp.json()
                ret = payload.get("return", {})
                if ret.get("status") != 200:
                    raise KavenegarError(ret.get("status"), ret.get("message", "API error"))
                return True
        except Exception as exc:
            logger.error("Kavenegar SMS dispatch failed for %s: %s", phone, exc)
            if isinstance(exc, KavenegarError):
                raise
            raise KavenegarError(None, f"Transport error: {exc}") from exc

    async def send_sms_async(
        self,
        phone: str,
        message: str,
        http_client: httpx.AsyncClient | None = None,
    ) -> bool:
        """Send an SMS asynchronously (uses dev fallback if unconfigured)."""
        if not self.is_configured:
            print(f"[DEV SMS] to {phone}: {message}")
            _DEV_SENT_MESSAGES.append({"phone": phone, "message": message, "type": "general"})
            return True

        url = self._format_send_url()
        data: dict[str, str] = {"receptor": phone, "message": message}
        if self.sender:
            data["sender"] = self.sender

        try:
            if http_client is not None:
                resp = await http_client.post(url, data=data)
            else:
                async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                    resp = await client.post(url, data=data)
            resp.raise_for_status()
            payload = resp.json()
            ret = payload.get("return", {})
            if ret.get("status") != 200:
                raise KavenegarError(ret.get("status"), ret.get("message", "API error"))
            return True
        except Exception as exc:
            logger.error("Kavenegar async SMS dispatch failed for %s: %s", phone, exc)
            if isinstance(exc, KavenegarError):
                raise
            raise KavenegarError(None, f"Transport error: {exc}") from exc

    def send_otp(self, phone: str, code: str) -> None:
        """Deliver OTP verification code."""
        if not self.is_configured:
            print(f"[DEV SMS] code for {phone}: {code}")
            _DEV_LAST_CODE[phone] = code
            _DEV_SENT_MESSAGES.append({"phone": phone, "code": code, "type": "otp"})
            return

        message = f"کد تایید ورود به بنده: {code}\nاین کد تا ۲ دقیقه معتبر است."
        self.send_sms(phone, message)

    def send_order_paid(
        self,
        phone: str,
        order_id: str | uuid.UUID,
        total_toman: int | None = None,
    ) -> None:
        """Deliver order paid notification."""
        order_str = str(order_id)
        msg = (
            f"سفارش شما به شناسه {order_str} با موفقیت پرداخت شد و وارد مرحله پردازش گردید.\n"
            "فروشگاه بنده"
        )
        if not self.is_configured:
            print(f"[DEV SMS] to {phone}: {msg}")
            _DEV_SENT_MESSAGES.append(
                {"phone": phone, "order_id": order_str, "type": "order_paid", "message": msg}
            )
            return
        self.send_sms(phone, msg)

    async def send_order_paid_async(
        self,
        phone: str,
        order_id: str | uuid.UUID,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Deliver order paid notification asynchronously."""
        order_str = str(order_id)
        msg = (
            f"سفارش شما به شناسه {order_str} با موفقیت پرداخت شد و وارد مرحله پردازش گردید.\n"
            "فروشگاه بنده"
        )
        if not self.is_configured:
            print(f"[DEV SMS] to {phone}: {msg}")
            _DEV_SENT_MESSAGES.append(
                {"phone": phone, "order_id": order_str, "type": "order_paid", "message": msg}
            )
            return
        await self.send_sms_async(phone, msg, http_client=http_client)

    def send_order_shipped(
        self,
        phone: str,
        order_id: str | uuid.UUID,
        tracking_code: str | None = None,
    ) -> None:
        """Deliver order shipped notification with tracking code."""
        order_str = str(order_id)
        code_str = tracking_code or "در انتظار صدور"
        msg = (
            f"سفارش شما به شناسه {order_str} ارسال شد.\n"
            f"کد رهگیری پستی: {code_str}\n"
            "فروشگاه بنده"
        )
        if not self.is_configured:
            print(f"[DEV SMS] to {phone}: {msg}")
            _DEV_SENT_MESSAGES.append(
                {
                    "phone": phone,
                    "order_id": order_str,
                    "tracking_code": tracking_code,
                    "type": "order_shipped",
                    "message": msg,
                }
            )
            return
        self.send_sms(phone, msg)

    async def send_order_shipped_async(
        self,
        phone: str,
        order_id: str | uuid.UUID,
        tracking_code: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        """Deliver order shipped notification asynchronously."""
        order_str = str(order_id)
        code_str = tracking_code or "در انتظار صدور"
        msg = (
            f"سفارش شما به شناسه {order_str} ارسال شد.\n"
            f"کد رهگیری پستی: {code_str}\n"
            "فروشگاه بنده"
        )
        if not self.is_configured:
            print(f"[DEV SMS] to {phone}: {msg}")
            _DEV_SENT_MESSAGES.append(
                {
                    "phone": phone,
                    "order_id": order_str,
                    "tracking_code": tracking_code,
                    "type": "order_shipped",
                    "message": msg,
                }
            )
            return
        await self.send_sms_async(phone, msg, http_client=http_client)


def get_sms_client() -> KavenegarClient:
    """Instantiate KavenegarClient reading credentials from settings."""
    try:
        from env_settings import get_settings

        settings = get_settings()
        key = (
            settings.kavenegar_api_key.get_secret_value()
            if settings.kavenegar_api_key is not None
            else None
        )
        sender = settings.kavenegar_sender
    except Exception:
        key = None
        sender = None
    return KavenegarClient(api_key=key, sender=sender)


SmsClientDep = Annotated[KavenegarClient, Depends(get_sms_client)]


def send_otp(phone: str, code: str) -> None:
    """Deliver a 5-digit OTP verification code to the target phone number."""
    get_sms_client().send_otp(phone, code)


def send_order_paid(
    phone: str,
    order_id: str | uuid.UUID,
    total_toman: int | None = None,
) -> None:
    """Deliver order payment confirmation via SMS."""
    get_sms_client().send_order_paid(phone, order_id, total_toman)


def send_order_shipped(
    phone: str,
    order_id: str | uuid.UUID,
    tracking_code: str | None = None,
) -> None:
    """Deliver order shipment notification with tracking code via SMS."""
    get_sms_client().send_order_shipped(phone, order_id, tracking_code)


__all__ = [
    "KAVENEGAR_BASE_URL",
    "KavenegarClient",
    "KavenegarError",
    "SmsClientDep",
    "_DEV_LAST_CODE",
    "_DEV_SENT_MESSAGES",
    "get_sms_client",
    "send_order_paid",
    "send_order_shipped",
    "send_otp",
]
