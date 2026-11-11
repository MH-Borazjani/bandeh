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
"""Payment routes (Zibal): start, callback, status, cancel.

- POST /payments/{order_id}/start — logged-in owner only. Reserves
  stock, asks Zibal, returns the gateway start URL.
- GET /payments/callback — PUBLIC. Zibal redirects the browser here
  after payment. The query parameters are a hint only: the service
  verifies server-to-server and decides from that.
- GET /payments/{order_id}/status — logged-in owner only.
- POST /payments/{order_id}/cancel — logged-in owner only.

The callback URL the gateway calls is ``ZIBAL_CALLBACK_URL`` when set,
otherwise this app's own ``/payments/callback`` (from the request).
In production it must be a public URL Zibal can reach.
"""

from __future__ import annotations

import os
import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import TypeAdapter

from core.security import RequireDbUserDep
from db.session import DbSessionDep
from integrations.zibal_client import ZibalClientDep, ZibalError
from schema.payment_api import (
    PaymentAttemptSummary,
    PaymentCallbackResponse,
    PaymentCancelRead,
    PaymentStartRead,
    PaymentStatusRead,
)
from services import payment_service
from services.order_service import OrderError, load_order_for_user

router = APIRouter(tags=["payments"])
_CALLBACK_RESPONSE_ADAPTER: TypeAdapter[PaymentCallbackResponse] = TypeAdapter(
    PaymentCallbackResponse
)


def _callback_url(request: Request) -> str:
    """Where Zibal redirects the browser after payment."""
    configured = os.environ.get("ZIBAL_CALLBACK_URL")
    if configured:
        return configured
    return str(request.base_url).rstrip("/") + "/payments/callback"


@router.get("/payments/callback")
async def payment_callback(
    db: DbSessionDep,
    zibal: ZibalClientDep,
    trackId: Annotated[int, Query()],
    success: Annotated[str, Query()] = "0",
    status: Annotated[str, Query()] = "-1",
    orderId: Annotated[str, Query()] = "",
) -> PaymentCallbackResponse:
    """Zibal's redirect after payment. Verifies; never trusts the params.

    ``success``/``status`` arrive here but are ignored for the decision
    — they are only what the browser claims. The verify call is the truth.
    """
    try:
        result = await payment_service.handle_callback(
            db, track_id=trackId, order_id=orderId, zibal=zibal
        )
    except ZibalError as exc:
        detail = f"payment gateway error: {exc.message or exc.result}"
        raise HTTPException(status_code=502, detail=detail) from exc
    if result["status"] == "unknown":
        raise HTTPException(status_code=404, detail="unknown payment")
    return _CALLBACK_RESPONSE_ADAPTER.validate_python(result)


@router.post("/payments/{order_id}/start")
async def start_payment_endpoint(
    order_id: Annotated[uuid.UUID, Path()],
    request: Request,
    user: RequireDbUserDep,
    db: DbSessionDep,
    zibal: ZibalClientDep,
) -> PaymentStartRead:
    """Start paying for my order. Returns the gateway start URL."""
    try:
        attempt = await payment_service.start_payment(
            db, order_id, user.id, zibal, _callback_url(request)
        )
    except OrderError as exc:
        if exc.detail == "order not found":
            raise HTTPException(status_code=404, detail="order not found") from exc
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ZibalError as exc:
        detail = f"payment gateway error: {exc.message or exc.result}"
        raise HTTPException(status_code=502, detail=detail) from exc
    assert attempt.track_id is not None  # start only returns waiting attempts
    return PaymentStartRead(
        payment_url=zibal.start_url(attempt.track_id),
        track_id=attempt.track_id,
        order_id=order_id,
    )


@router.get("/payments/{order_id}/status")
async def payment_status(
    order_id: Annotated[uuid.UUID, Path()],
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> PaymentStatusRead:
    """My order's payment status: order state + latest attempt."""
    order = await load_order_for_user(db, order_id, user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    latest = max(order.attempts, key=lambda a: a.created_at) if order.attempts else None
    return PaymentStatusRead(
        order_id=order.id,
        order_status=order.status,
        total_toman=order.total_toman,
        total_rial=order.total_rial,
        payment=PaymentAttemptSummary.model_validate(latest) if latest is not None else None,
    )


@router.post("/payments/{order_id}/cancel")
async def cancel_payment_endpoint(
    order_id: Annotated[uuid.UUID, Path()],
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> PaymentCancelRead:
    """Cancel my unpaid order: free the stock hold, fail the order."""
    try:
        cancelled = await payment_service.cancel_payment(db, order_id, user.id)
    except OrderError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc
    if not cancelled:
        raise HTTPException(status_code=409, detail="order cannot be cancelled")
    return PaymentCancelRead(order_id=order_id)


__all__ = ["router"]
