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
"""UCP checkout sessions (Google, spec 2026-08-25).

Merchant endpoints, mounted under ``/ucp`` (discovery lives at
``/.well-known/ucp`` in pages.py):
- POST /checkout-sessions — create from line items + buyer.
- GET /checkout-sessions/{id} — fetch state.
- PUT /checkout-sessions/{id} — update items / buyer / fulfillment.
- POST /checkout-sessions/{id}/complete — create the order, start
  Zibal, return the payment link (never charges a Google Pay token).
- POST /checkout-sessions/{id}/cancel — cancel.
- GET /orders/{id} — read one order in UCP shape.
- POST /catalog/search|lookup|product — the catalog capability.
- GET /schemas/zibal-payment.json — the payment handler schema.

Every call needs ``Authorization: Bearer <AGENT_API_KEY>`` (except
the public handler schema). ``Idempotency-Key`` is supported when
sent, like ACP. Errors are FastAPI-style ``{"detail": ...}``.
"""

from __future__ import annotations

import json
import os
import uuid

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from db.session import DbSessionDep
from integrations.zibal_client import ZibalClientDep
from services import agent_commerce
from services.agent_commerce import AgentError
from services.order_service import load_order

router = APIRouter(prefix="/ucp", tags=["ucp"])


def _base_url(request: Request) -> str:
    """Public base URL of this shop (localhost, funnel, or genabay.ir)."""
    return str(request.base_url).rstrip("/")


def _callback_url(request: Request) -> str:
    """Where Zibal redirects the browser after payment."""
    configured = os.environ.get("ZIBAL_CALLBACK_URL")
    if configured:
        return configured
    return _base_url(request) + "/payments/callback"


def _agent_error(exc: AgentError) -> HTTPException:
    """Translate a service error into the route's HTTP answer."""
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


async def _read_payload(request: Request) -> tuple[dict, bytes]:
    """Read + parse the JSON body (400 on bad JSON, 400 on empty)."""
    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="request body is required")
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="request body is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="request body must be a JSON object")
    return payload, raw


def _response_headers(
    request: Request, idempotency_key: str | None, replayed: bool
) -> dict[str, str]:
    """Response headers: key echo, replay flag, response signature."""
    headers: dict[str, str] = {}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if replayed:
        headers["Idempotent-Replayed"] = "true"
    return headers


@router.post("/checkout-sessions")
async def create_checkout_session(
    request: Request,
    db: DbSessionDep,
) -> JSONResponse:
    """Create a checkout session from line items + optional buyer."""
    try:
        agent_commerce.require_agent_key(request)
        payload, raw = await _read_payload(request)
        agent_commerce.verify_agent_signature(request, raw)
        key = request.headers.get("idempotency-key")
        status, response, replayed = await agent_commerce.create_session(
            db, "ucp", payload, _base_url(request), key, raw
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        status_code=status,
        content=response,
        headers=_response_headers(request, key, replayed),
    )


@router.get("/checkout-sessions/{session_id}")
async def get_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
) -> JSONResponse:
    """Fetch the current state of one checkout session."""
    try:
        agent_commerce.require_agent_key(request)
        row = await agent_commerce.load_session(db, "ucp", session_id)
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=agent_commerce.response_for(row, _base_url(request)),
        headers=_response_headers(request, None, False),
    )


@router.put("/checkout-sessions/{session_id}")
async def update_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
) -> JSONResponse:
    """Update items / buyer / fulfillment on an open session, reprice."""
    try:
        agent_commerce.require_agent_key(request)
        payload, raw = await _read_payload(request)
        agent_commerce.verify_agent_signature(request, raw)
        key = request.headers.get("idempotency-key")
        row = await agent_commerce.load_session(db, "ucp", session_id)
        response, replayed = await agent_commerce.update_session(
            db, row, payload, _base_url(request), key, raw
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=response,
        headers=_response_headers(request, key, replayed),
    )


@router.post("/checkout-sessions/{session_id}/complete")
async def complete_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
    zibal: ZibalClientDep,
) -> JSONResponse:
    """Complete: create the order, start Zibal, return the payment link.

    A Google Pay token sent by the agent is never charged and never
    forwarded — the response carries the Zibal start URL, and the
    buyer pays on the bank page.
    """
    try:
        agent_commerce.require_agent_key(request)
        payload, raw = await _read_payload(request)
        agent_commerce.verify_agent_signature(request, raw)
        key = request.headers.get("idempotency-key")
        row = await agent_commerce.load_session(db, "ucp", session_id)
        http_client = getattr(request.app.state, "http_client", None)
        response, replayed = await agent_commerce.complete_session(
            db,
            row,
            payload,
            zibal,
            _callback_url(request),
            _base_url(request),
            key,
            raw,
            http_client,
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=response,
        headers=_response_headers(request, key, replayed),
    )


@router.post("/checkout-sessions/{session_id}/cancel")
async def cancel_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
) -> JSONResponse:
    """Cancel an open session (409 when already completed/canceled)."""
    try:
        agent_commerce.require_agent_key(request)
        raw = await request.body()
        agent_commerce.verify_agent_signature(request, raw)
        key = request.headers.get("idempotency-key")
        row = await agent_commerce.load_session(db, "ucp", session_id)
        response, replayed = await agent_commerce.cancel_session(
            db, row, _base_url(request), key, raw
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=response,
        headers=_response_headers(request, key, replayed),
    )


@router.get("/orders/{order_id}")
async def get_order(
    request: Request,
    order_id: str,
    db: DbSessionDep,
) -> JSONResponse:
    """Read one order in UCP shape (``ord_<hex>`` from ``complete``)."""
    agent_commerce.require_agent_key(request)
    raw_id = order_id[4:] if order_id.startswith("ord_") else order_id
    try:
        oid = uuid.UUID(raw_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="order not found") from exc
    order = await load_order(db, oid)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    base_url = _base_url(request)
    items = [
        {
            "id": f"li_{index}",
            "item": {
                "id": str(item.product_id),
                "title": item.product_name,
                "price": item.unit_price_toman * 10,
            },
            "quantity": item.quantity,
            "totals": [
                {
                    "type": "subtotal",
                    "amount": item.unit_price_toman * 10 * item.quantity,
                }
            ],
        }
        for index, item in enumerate(order.items, start=1)
    ]
    return JSONResponse(
        content={
            "ucp": {"version": agent_commerce.UCP_VERSION},
            "id": f"ord_{order.id.hex}",
            "label": f"Genabay order {str(order.id)[:8]}",
            "checkout_id": None,
            "permalink_url": f"{base_url}/order/{order.id}",
            "line_items": items,
            "fulfillment": {"status": order.status},
            "currency": agent_commerce.CURRENCY,
            "totals": agent_commerce.totals_for(order.total_rial),
        }
    )


@router.post("/catalog/search")
async def catalog_search(
    request: Request,
    db: DbSessionDep,
) -> JSONResponse:
    """Catalog search: ``{"query": "...", "limit": 20}`` → products."""
    try:
        agent_commerce.require_agent_key(request)
        payload, _ = await _read_payload(request)
        products = await agent_commerce.search_catalog(
            db,
            payload.get("query"),
            _base_url(request),
            int(payload.get("limit", 20)),
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(content={"products": products})


@router.post("/catalog/lookup")
async def catalog_lookup(
    request: Request,
    db: DbSessionDep,
) -> JSONResponse:
    """Catalog lookup: ``{"ids": [...]}`` → the products, in order."""
    try:
        agent_commerce.require_agent_key(request)
        payload, _ = await _read_payload(request)
        products = await agent_commerce.lookup_catalog(
            db, payload.get("ids"), _base_url(request)
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(content={"products": products})


@router.post("/catalog/product")
async def catalog_product(
    request: Request,
    db: DbSessionDep,
) -> JSONResponse:
    """Catalog product: ``{"id": "..."}`` → one product or 404."""
    try:
        agent_commerce.require_agent_key(request)
        payload, _ = await _read_payload(request)
        products = await agent_commerce.lookup_catalog(
            db, [payload.get("id")], _base_url(request)
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    if not products:
        raise HTTPException(status_code=404, detail="product not found")
    return JSONResponse(content=products[0])


@router.get("/schemas/zibal-payment.json")
async def zibal_payment_schema(request: Request) -> JSONResponse:
    """Public JSON Schema for the ``ir.genabay.payments.zibal`` handler."""
    return JSONResponse(content=agent_commerce.zibal_payment_schema(_base_url(request)))


__all__ = ["router"]
