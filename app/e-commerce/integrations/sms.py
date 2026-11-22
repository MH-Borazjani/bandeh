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
"""SMS delivery integration: Meli Payamak shared service line + dev fallback.

PRIMARY PATH — patterns over the shared service line
(see docs/sms-melipayamak.md and the panel guide "راهنمای استفاده از
خط خدماتی اشتراکی"):

- The shop's messages are SERVICE messages. They go through the
  shared service line via a PATTERN (template) created by the user in
  the panel (Special tools -> Service web service -> add new text).
  The pattern text uses {0}, {1}, {2} for variables and MUST end with
  the site address (genabay.ir). After approval ("تأیید شده") each
  pattern gets a unique code: the bodyId.
- Pattern send (REST): POST /api/SendSMS/BaseServiceNumber, form
  username, password (the ApiKey), to, text (the pattern's variable
  values joined by ';', e.g. 'msg;test123'), bodyId (int).
  Verified sample: username=...&password=...&to=912000
  &text=msg%3Btest123&bodyId=123.
- The service line reaches even numbers on the telecom blacklist;
  the advertising line cannot. Service lines may only send service
  content — never advertising.
- Delivery status for pattern sends: POST /SendSMS/GetDeliveries2
  with the returned id.

FALLBACK ONLY — plain SendSMS (the advertising line):
POST /api/SendSMS/SendSMS {username, password, to, from, text,
isflash=false}. Kept in code and documented, NOT the primary path:
it cannot reach blacklisted numbers, and its texts must end with
"11" (error 15). The service helpers never call it by default.

Auth on every call is form fields ``username`` (panel username) +
``password`` = the **ApiKey** from the panel developer menu -> web
service settings. It is NOT the panel login password.

When no credentials are configured (development and tests), sends fall
back to stdout logging and an in-memory test log, and return the
placeholder provider id "dev" — the shop works without SMS.

The ApiKey is NEVER logged: no log line, error, or sms_log row ever
contains the password field.

Request format mirrors the official Python library
(``melipayamak/sms/restAsync.py`` in ~/workspace/meli-docs, read as
the reference — NOT added as a dependency: the shop already uses
httpx everywhere, and the library pulls in aiohttp). Same URLs
(PATH % 'BaseServiceNumber', etc.), same form-encoded params
(username/password merged into the form data), same JSON response
shape (Value / RetStatus / StrRetStatus), parsed by us. Two library
behaviours are deliberately NOT copied: its ``post`` returns None on
any non-200 status (swallowing the error — ours raises
MeliPayamakError so the sms_log records failures), and its bugs
(``base_service_number_schedule`` builds ``data`` as a set instead of
a dict; ``get_send_speech_text_statuses`` calls ``self.get_data``
without parens). Pattern CRUD via the ShareService API exists in the
library but is NOT built into the shop — pattern creation stays the
user's panel action.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any

import httpx
from fastapi import Depends

logger = logging.getLogger("bandeh.sms")

# Maps phone -> last sent OTP code for local development and automated testing
_DEV_LAST_CODE: dict[str, str] = {}
# Log of all sent messages in development/testing
_DEV_SENT_MESSAGES: list[dict[str, Any]] = []

MELI_BASE_URL = "https://rest.payamak-panel.com/api"

# RetStatus / Value error codes (from the panel docs + the service-line
# guide; 53 is the REST-only "recipient is blacklisted" code).
MELI_ERROR_MESSAGES: dict[int, str] = {
    0: "wrong username or password",
    2: "insufficient credit",
    3: "daily limit reached",
    4: "volume limit reached",
    5: "sender number invalid",
    6: "system busy",
    7: "filtered word in text",
    9: "cannot send from public lines via web service",
    10: "user not active",
    11: "not sent",
    12: "incomplete documents",
    14: "text contains a link",
    15: 'text does not end with "11"',
    16: "recipient not found",
    17: "empty text",
    18: "invalid mobile number",
    53: "recipient is in the telecom blacklist",
    -108: "IP blocked",
    -109: "IP not allowed for API",
    -110: "must use ApiKey as password",
    -111: "requester IP invalid",
}

# Delivery status codes returned as Value by GetDeliveries2.
DELIVERY_STATUS_DESCRIPTIONS: dict[int, str] = {
    0: "sent to operator",
    1: "delivered",
    2: "not delivered",
    3: "operator error",
    5: "unknown error",
    8: "delivered",
    16: "not delivered",
    35: "blacklist",
    100: "unknown",
    200: "sent",
    300: "filtered",
    400: "in send list",
    500: "operator rejected",
}

# Placeholder provider id returned by the dev fallback (no real send).
DEV_PROVIDER_ID = "dev"


class MeliPayamakError(Exception):
    """The panel said no (or the request was invalid before sending).

    ``status`` is Meli Payamak's numeric RetStatus code (None for
    client-side problems like a transport failure or a missing
    pattern bodyId). ``message`` is a readable explanation; see
    MELI_ERROR_MESSAGES for the code map.
    """

    def __init__(self, status: int | None, message: str = "") -> None:
        super().__init__(message or f"Meli Payamak error {status}")
        self.status = status
        self.message = message


def _error_message(status: int | None, str_status: str = "") -> str:
    """Human-readable message for a RetStatus code."""
    if status is None:
        return str_status or "unknown error"
    base = MELI_ERROR_MESSAGES.get(status, f"unknown error code {status}")
    if str_status and str_status.lower() != "ok":
        return f"{base} ({str_status})"
    return base


class MeliPayamakClient:
    """Meli Payamak SMS client with dev fallback when unconfigured.

    Async only (httpx). Pass an ``httpx.AsyncClient`` (e.g. one built
    on a MockTransport) for tests; otherwise a short-lived client is
    created per call with timeout 10.0 and trust_env=False.

    The primary send is :meth:`send_pattern` (shared service line).
    :meth:`send_sms` (plain SendSMS, advertising line) is a documented
    fallback only — the service layer never calls it by default.
    """

    def __init__(
        self,
        username: str | None = None,
        api_key: str | None = None,
        sender: str | None = None,
        base_url: str = MELI_BASE_URL,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.username = username
        self.api_key = api_key
        self.sender = sender
        self.base_url = base_url.rstrip("/")
        self._http_client = http_client

    @property
    def is_configured(self) -> bool:
        """Return True when panel credentials are available."""
        return bool(
            self.username
            and self.username.strip()
            and self.api_key
            and self.api_key.strip()
        )

    def _auth_fields(self) -> dict[str, str]:
        """Auth form fields. The ApiKey goes here and nowhere else."""
        assert self.username is not None and self.api_key is not None
        return {"username": self.username, "password": self.api_key}

    async def _post(
        self,
        path: str,
        data: dict[str, str],
    ) -> dict[str, Any] | str | int:
        """POST one form-encoded call and return the parsed JSON body."""
        url = f"{self.base_url}{path}"
        try:
            if self._http_client is not None:
                response = await self._http_client.post(url, data=data)
            else:
                async with httpx.AsyncClient(timeout=10.0, trust_env=False) as client:
                    response = await client.post(url, data=data)
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            raise MeliPayamakError(None, f"transport error: {exc}") from exc
        except ValueError as exc:
            raise MeliPayamakError(None, f"unexpected provider response: {exc}") from exc
        if not isinstance(payload, (dict, str, int)) or isinstance(payload, bool):
            raise MeliPayamakError(None, "unexpected provider response")
        return payload

    def _dev_fallback(
        self, phone: str, text: str, kind: str, body_id: int | None = None
    ) -> str:
        """Record a send that never left the machine (unconfigured)."""
        if kind == "pattern":
            print(f"[DEV SMS] pattern {body_id} to {phone}: {text}")
        else:
            print(f"[DEV SMS] to {phone}: {text}")
        entry: dict[str, Any] = {"phone": phone, "message": text, "type": kind}
        if body_id is not None:
            entry["body_id"] = body_id
        _DEV_SENT_MESSAGES.append(entry)
        return DEV_PROVIDER_ID

    @staticmethod
    def _provider_id(payload: dict[str, Any] | str | int) -> str:
        """Pull the provider message id (recId) out of a send response.

        The panel answers either a dict {"Value": "recId", "RetStatus":
        1, "StrRetStatus": "Ok"} or a bare recId string. RetStatus 1 =
        ok; anything else raises with the code and the panel's own
        StrRetStatus text. A short bare number is an error code from
        the service-line guide (0, 2, 6, 7, 10, 11, 12, 16, 17, 53).
        """
        if isinstance(payload, (str, int)) and not isinstance(payload, bool):
            text = str(payload).strip()
            if text.lstrip("-").isdigit() and len(text) <= 3:
                code = int(text)
                if code in MELI_ERROR_MESSAGES:
                    raise MeliPayamakError(code, _error_message(code))
            if not text:
                raise MeliPayamakError(None, "missing provider message id")
            return text
        raw_status = payload.get("RetStatus")
        status = (
            raw_status
            if isinstance(raw_status, int) and not isinstance(raw_status, bool)
            else None
        )
        if status is not None and status != 1:
            str_status = payload.get("StrRetStatus")
            raise MeliPayamakError(
                status,
                _error_message(status, str(str_status) if str_status else ""),
            )
        value = payload.get("Value")
        if value is None or str(value).strip() == "":
            raise MeliPayamakError(status, "missing provider message id")
        return str(value).strip()

    async def send_pattern(
        self,
        phone: str,
        body_id: int | None,
        variables: list[str],
    ) -> str:
        """Send a pattern over the shared service line. Returns the provider id.

        ``variables`` fills the pattern's {0}, {1}, ... placeholders in
        order; they travel as one ';'-joined ``text`` field, exactly as
        the panel's REST sample shows (text=msg%3Btest123&bodyId=123).
        A missing bodyId with a configured client raises instead of
        sending with a wrong id; unconfigured clients take the dev
        fallback.
        """
        text = ";".join(str(v) for v in variables)
        if not self.is_configured:
            return self._dev_fallback(phone, text, "pattern", body_id)
        if body_id is None:
            raise MeliPayamakError(
                None,
                "no pattern bodyId configured: create the pattern in the "
                "panel (Special tools -> Service web service) and set its "
                "MELI_SMS_*_BODY_ID env var",
            )
        data = {
            **self._auth_fields(),
            "to": phone,
            "text": text,
            "bodyId": str(body_id),
        }
        try:
            payload = await self._post("/SendSMS/BaseServiceNumber", data)
            return self._provider_id(payload)
        except MeliPayamakError as exc:
            logger.error("Meli Payamak pattern send failed for %s: %s", phone, exc)
            raise

    async def send_sms(self, phone: str, text: str) -> str:
        """Send via plain SendSMS (advertising line). FALLBACK ONLY.

        Cannot reach numbers on the telecom blacklist, and texts must
        end with "11" (error 15). The service layer never calls this
        by default; it exists for the documented fallback path.
        """
        if not self.is_configured:
            return self._dev_fallback(phone, text, "general")
        data = {**self._auth_fields(), "to": phone, "text": text, "isFlash": "false"}
        if self.sender:
            data["from"] = self.sender
        try:
            payload = await self._post("/SendSMS/SendSMS", data)
            return self._provider_id(payload)
        except MeliPayamakError as exc:
            logger.error("Meli Payamak SendSMS failed for %s: %s", phone, exc)
            raise

    async def get_delivery_status(self, provider_id: str) -> int:
        """Delivery status code for a sent message (see the status map)."""
        if not self.is_configured:
            raise MeliPayamakError(None, "SMS client is not configured")
        payload = await self._post(
            "/SendSMS/GetDeliveries2",
            {**self._auth_fields(), "recId": provider_id},
        )
        if not isinstance(payload, dict):
            raise MeliPayamakError(None, "unexpected delivery response")
        raw_status = payload.get("RetStatus")
        status = (
            raw_status
            if isinstance(raw_status, int) and not isinstance(raw_status, bool)
            else None
        )
        if status is not None and status != 1:
            str_status = payload.get("StrRetStatus")
            raise MeliPayamakError(
                status,
                _error_message(status, str(str_status) if str_status else ""),
            )
        try:
            return int(str(payload.get("Value")).strip())
        except (TypeError, ValueError) as exc:
            raise MeliPayamakError(None, "unexpected delivery response") from exc

    async def get_credit(self) -> int:
        """Remaining SMS credit.

        NOTE: the response shape (Value holding the credit, RetStatus
        1 = ok) follows the same pattern as the send/delivery calls;
        confirm it against the live panel on the first live test.
        """
        if not self.is_configured:
            raise MeliPayamakError(None, "SMS client is not configured")
        payload = await self._post("/SendSMS/GetCredit", self._auth_fields())
        if not isinstance(payload, dict):
            raise MeliPayamakError(None, "unexpected credit response")
        raw_status = payload.get("RetStatus")
        status = (
            raw_status
            if isinstance(raw_status, int) and not isinstance(raw_status, bool)
            else None
        )
        if status is not None and status != 1:
            str_status = payload.get("StrRetStatus")
            raise MeliPayamakError(
                status,
                _error_message(status, str(str_status) if str_status else ""),
            )
        try:
            return int(str(payload.get("Value")).strip())
        except (TypeError, ValueError) as exc:
            raise MeliPayamakError(None, "unexpected credit response") from exc


def get_sms_client() -> MeliPayamakClient:
    """Instantiate MeliPayamakClient reading credentials from settings."""
    try:
        from env_settings import get_settings

        settings = get_settings()
        username = settings.meli_sms_username
        key = (
            settings.meli_sms_api_key.get_secret_value()
            if settings.meli_sms_api_key is not None
            else None
        )
        sender = settings.meli_sms_from
    except Exception:
        username = None
        key = None
        sender = None
    return MeliPayamakClient(username=username, api_key=key, sender=sender)


SmsClientDep = Annotated[MeliPayamakClient, Depends(get_sms_client)]

__all__ = [
    "DELIVERY_STATUS_DESCRIPTIONS",
    "DEV_PROVIDER_ID",
    "MELI_BASE_URL",
    "MELI_ERROR_MESSAGES",
    "MeliPayamakClient",
    "MeliPayamakError",
    "SmsClientDep",
    "_DEV_LAST_CODE",
    "_DEV_SENT_MESSAGES",
    "get_sms_client",
]
