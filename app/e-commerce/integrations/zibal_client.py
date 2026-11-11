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
"""Zibal payment-gateway client (server-to-server, httpx).

Verified facts (from Zibal's own OpenAPI doc):
- Base https://gateway.zibal.ir. Test merchant is the literal string
  "zibal" — same host as production.
- POST /v1/request {merchant, amount (int Rial), callbackUrl, orderId,
  description} -> {result, message, trackId (int)}.
  Result 100 = ok. Amount min 1,000 Rial, max 4,000,000,000 Rial.
- Redirect the browser to https://gateway.zibal.ir/start/{trackId}.
- Callback GET to callbackUrl with ?trackId&success&status&orderId.
  The callback is a HINT, never the truth.
- POST /v1/verify {merchant, trackId} (NO amount) -> {result, paidAt,
  cardNumber (masked), status, orderId, refNumber, amount}.
  Result 100 = verified, 201 = already verified, 202 = not paid /
  failed, 203 = bad trackId.
- Zibal has NO idempotency-key field. Do not invent one: the
  idempotency key lives in OUR PaymentAttempt row, never on the wire.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Annotated

import httpx
from fastapi import Depends

from integrations.http_client import HttpClientDep

BASE_URL = "https://gateway.zibal.ir"
MIN_AMOUNT_RIAL = 1_000
MAX_AMOUNT_RIAL = 4_000_000_000

# Result codes (request + verify share 100; verify adds 201/202/203).
RESULT_OK = 100
RESULT_ALREADY_VERIFIED = 201
RESULT_NOT_PAID = 202
RESULT_BAD_TRACK_ID = 203


class ZibalError(Exception):
    """The gateway said no (or the request was invalid before sending).

    ``result`` is Zibal's numeric code (None for client-side problems
    like an out-of-range amount). ``message`` is Zibal's own text.
    """

    def __init__(self, result: int | None, message: str = "") -> None:
        super().__init__(message or f"Zibal error {result}")
        self.result = result
        self.message = message


@dataclass
class VerifyResult:
    """What POST /v1/verify returns, typed. Amount is int Rial."""

    result: int
    paid_at: str | None = None
    card_number: str | None = None
    status: int | None = None
    order_id: str | None = None
    ref_number: str | None = None
    amount: int | None = None


class ZibalClient:
    """Thin wrapper around one shared httpx client. No state of its own."""

    def __init__(self, client: httpx.AsyncClient, merchant: str = "zibal") -> None:
        self._client = client
        self.merchant = merchant

    def start_url(self, track_id: int) -> str:
        """Where the browser goes to pay: the gateway's start page."""
        return f"{BASE_URL}/start/{track_id}"

    async def request_payment(
        self,
        amount_rial: int,
        callback_url: str,
        order_id: str,
        description: str = "",
    ) -> int:
        """Ask Zibal for a payment session. Returns the trackId (int)."""
        if not MIN_AMOUNT_RIAL <= amount_rial <= MAX_AMOUNT_RIAL:
            raise ValueError(
                f"amount {amount_rial} Rial is outside Zibal's range "
                f"({MIN_AMOUNT_RIAL:,}–{MAX_AMOUNT_RIAL:,} Rial)"
            )
        try:
            response = await self._client.post(
                f"{BASE_URL}/v1/request",
                json={
                    "merchant": self.merchant,
                    "amount": amount_rial,
                    "callbackUrl": callback_url,
                    "orderId": order_id,
                    "description": description,
                },
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ZibalError(None, f"gateway transport error: {exc}") from exc
        if not isinstance(data, dict):
            raise ZibalError(None, "unexpected gateway response")
        raw_result = data.get("result")
        result = (
            raw_result
            if isinstance(raw_result, int) and not isinstance(raw_result, bool)
            else None
        )
        if result != RESULT_OK:
            raise ZibalError(result, str(data.get("message", "")))
        try:
            return int(data["trackId"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ZibalError(result, "missing or invalid trackId") from exc

    async def verify_payment(self, track_id: int) -> VerifyResult:
        """Verify a payment server-to-server. The result is the truth.

        100/201/202/203 are all returned normally — the caller decides
        what each means. Transport/HTTP errors raise ZibalError.
        """
        try:
            response = await self._client.post(
                f"{BASE_URL}/v1/verify",
                json={"merchant": self.merchant, "trackId": track_id},
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise ZibalError(None, f"gateway transport error: {exc}") from exc
        if not isinstance(data, dict):
            raise ZibalError(None, "unexpected gateway verify response")
        raw_result = data.get("result", 0)
        result = (
            raw_result
            if isinstance(raw_result, int) and not isinstance(raw_result, bool)
            else 0
        )
        raw_paid_at = data.get("paidAt")
        raw_card_number = data.get("cardNumber")
        raw_status = data.get("status")
        raw_order_id = data.get("orderId")
        raw_ref = data.get("refNumber")
        raw_amount = data.get("amount")
        return VerifyResult(
            result=result,
            paid_at=raw_paid_at if isinstance(raw_paid_at, str) else None,
            card_number=raw_card_number if isinstance(raw_card_number, str) else None,
            status=(
                raw_status
                if isinstance(raw_status, int) and not isinstance(raw_status, bool)
                else None
            ),
            order_id=str(raw_order_id) if raw_order_id is not None else None,
            ref_number=str(raw_ref) if raw_ref is not None else None,
            amount=(
                raw_amount
                if isinstance(raw_amount, int) and not isinstance(raw_amount, bool)
                else None
            ),
        )


def get_zibal_client(http_client: HttpClientDep) -> ZibalClient:
    """Build the client from the app's shared httpx client.

    Merchant comes from ``ZIBAL_MERCHANT`` (default "zibal", the test
    merchant). Override this dependency in tests with a MockTransport.
    """
    return ZibalClient(http_client, merchant=os.environ.get("ZIBAL_MERCHANT", "zibal"))


ZibalClientDep = Annotated[ZibalClient, Depends(get_zibal_client)]

__all__ = [
    "BASE_URL",
    "MAX_AMOUNT_RIAL",
    "MIN_AMOUNT_RIAL",
    "RESULT_ALREADY_VERIFIED",
    "RESULT_BAD_TRACK_ID",
    "RESULT_NOT_PAID",
    "RESULT_OK",
    "VerifyResult",
    "ZibalClient",
    "ZibalClientDep",
    "ZibalError",
    "get_zibal_client",
]
