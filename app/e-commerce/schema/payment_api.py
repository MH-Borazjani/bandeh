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
"""Payment API response schemas."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PaymentStartRead(BaseModel):
    """Response when starting a Zibal payment session."""

    payment_url: str
    track_id: int
    order_id: uuid.UUID


class PaymentAttemptSummary(BaseModel):
    """Latest payment attempt snapshot on an order."""

    model_config = ConfigDict(from_attributes=True)

    status: str
    track_id: int | None
    ref_number: str | None


class PaymentStatusRead(BaseModel):
    """Order payment status and latest attempt summary."""

    order_id: uuid.UUID
    order_status: str
    total_toman: int
    total_rial: int
    payment: PaymentAttemptSummary | None = None


class PaymentCancelRead(BaseModel):
    """Response after cancelling an unpaid order."""

    status: Literal["failed"] = "failed"
    order_id: uuid.UUID


class PaymentCallbackPaidRead(BaseModel):
    """Callback response when verification succeeded."""

    status: Literal["paid"] = "paid"
    order_id: uuid.UUID
    ref_number: str | None


class PaymentCallbackAlreadyPaidRead(BaseModel):
    """Callback response when payment was already verified."""

    status: Literal["already_paid"] = "already_paid"
    order_id: uuid.UUID
    ref_number: str | None


class PaymentCallbackAmountMismatchRead(BaseModel):
    """Callback response when Zibal paid amount differs from order amount."""

    status: Literal["failed"] = "failed"
    order_id: uuid.UUID
    reason: Literal["amount_mismatch"] = "amount_mismatch"
    expected_rial: int
    received_rial: int


class PaymentCallbackNotPaidRead(BaseModel):
    """Callback response when Zibal reports payment failed/unpaid."""

    status: Literal["failed"] = "failed"
    order_id: uuid.UUID
    reason: Literal["not_paid"] = "not_paid"


class PaymentCallbackOrderMissingRead(BaseModel):
    """Callback response when the attempt's order row no longer exists."""

    status: Literal["error"] = "error"
    track_id: int
    reason: Literal["order_missing"] = "order_missing"


class PaymentCallbackGatewayErrorRead(BaseModel):
    """Callback response when Zibal returned a non-final gateway error code."""

    status: Literal["error"] = "error"
    track_id: int
    result: int


PaymentCallbackResponse = (
    PaymentCallbackPaidRead
    | PaymentCallbackAlreadyPaidRead
    | PaymentCallbackAmountMismatchRead
    | PaymentCallbackNotPaidRead
    | PaymentCallbackOrderMissingRead
    | PaymentCallbackGatewayErrorRead
)

__all__ = [
    "PaymentAttemptSummary",
    "PaymentCallbackAlreadyPaidRead",
    "PaymentCallbackAmountMismatchRead",
    "PaymentCallbackGatewayErrorRead",
    "PaymentCallbackNotPaidRead",
    "PaymentCallbackOrderMissingRead",
    "PaymentCallbackPaidRead",
    "PaymentCallbackResponse",
    "PaymentCancelRead",
    "PaymentStartRead",
    "PaymentStatusRead",
]
