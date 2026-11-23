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
"""Agent commerce service: ACP (OpenAI + Stripe, spec 2026-04-17) and
UCP (Google, spec 2026-08-25) checkout sessions for the shop.

What each protocol asks the merchant to run, and what we run:
- ACP: POST /checkout_sessions (201), POST /checkout_sessions/{id}
  (update), GET /checkout_sessions/{id}, POST .../complete (order),
  POST .../cancel. Session fields: id, status, currency, line_items,
  totals, fulfillment_options, messages, links. Payment via a
  delegated token (Stripe SPT).
- UCP: GET /.well-known/ucp (profile), POST /checkout-sessions,
  GET/PUT /checkout-sessions/{id}, POST .../complete, POST .../cancel,
  GET /orders/{id}, POST /catalog/search|lookup|product. Payment via
  a Google Pay token through a declared payment handler.

The honest adaptation for THIS shop (see docs/agent-commerce-protocols.md):
- Money is int Rial, currency ``irr`` — Toman has no ISO code and the
  protocols price in the smallest currency unit.
- No Stripe account and no Google Pay merchant exist (Iranian shop,
  Zibal only). A delegated token sent by an agent is NEVER charged
  and NEVER forwarded anywhere: only its provider name is recorded,
  and the answer carries the Zibal payment link instead. The buyer
  pays on the bank page. ``complete`` creates a real order + a real
  Zibal payment request (test merchant ``zibal`` until the real
  merchant account exists) and returns the start URL.
- The buyer is a guest user keyed by phone (``get_or_create_user_by_phone``):
  agents cannot do the shop's OTP login, and the order is unpaid
  until the buyer themself pays on the bank page.
- Order webhooks (ACP ``order_create`` / ``order_update``) are POSTed
  to ``ACP_WEBHOOK_URL`` / ``UCP_WEBHOOK_URL`` when configured, HMAC
  signed with ``ACP_SIGNING_SECRET``; otherwise the event is recorded
  on the session and delivery waits for configuration.
"""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import os
import uuid
from typing import Any

import httpx
from fastapi import Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.datetime_utils import utcnow
from core.money import toman_to_rial
from db.models.agent_session import AgentCheckoutSession
from db.models.order import Order, OrderStatus
from integrations.directus_client import DirectusClient
from integrations.zibal_client import ZibalClient, ZibalError
from services import search_service, stock_service
from services.auth_service import normalize_phone
from services.order_service import OrderError, OrderItemInput, create_order, load_order
from services.payment_service import cancel_payment, start_payment
from services.user_service import get_or_create_user_by_phone

ACP_VERSION = "2026-04-17"
UCP_VERSION = "2026-08-25"
CURRENCY = "irr"
SESSION_TTL_HOURS = 6
ZIBAL_HANDLER_ID = "ir.genabay.payments.zibal"

# ACP session statuses (spec 2026-04-17, CheckoutSessionBase.status).
ACP_OPEN = ("incomplete", "not_ready_for_payment", "ready_for_payment")
# UCP session statuses (spec 2026-08-25, checkout.status).
UCP_OPEN = ("incomplete", "ready_for_complete")


class AgentError(Exception):
    """An agent request cannot be served: HTTP status + human detail."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


# ---------------------------------------------------------------- auth


def require_agent_key(request: Request) -> None:
    """Bearer API key for /acp/* and /ucp/* (discovery stays public).

    The key is ``AGENT_API_KEY``. When it is unset the endpoints refuse
    everything (401) — an agent API must never be open by accident on
    a public shop link. Tests set the key in the environment.
    """
    key = os.environ.get("AGENT_API_KEY", "")
    if not key:
        raise AgentError(401, "agent API key is not configured")
    presented = request.headers.get("authorization", "")
    if presented != f"Bearer {key}":
        raise AgentError(401, "invalid agent API key")


def _hmac_signature(secret: str, timestamp: str, request_id: str, body: bytes) -> str:
    """base64 HMAC-SHA256 over ``timestamp.request_id.body``.

    This is OUR canonical string, documented here and in
    docs/agent-commerce-protocols.md: the ACP spec requires a
    ``Signature`` header but leaves the canonical form to the merchant.
    """
    message = f"{timestamp}.{request_id}.".encode() + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def verify_agent_signature(request: Request, raw_body: bytes) -> None:
    """Verify the HMAC ``Signature`` header when a secret is configured.

    ``ACP_SIGNING_SECRET`` unset means no verification (dev mode).
    Timestamp must be within 5 minutes (replay protection).
    """
    secret = os.environ.get("ACP_SIGNING_SECRET", "")
    if not secret:
        return
    signature = request.headers.get("signature", "")
    timestamp = request.headers.get("timestamp", "")
    request_id = request.headers.get("request-id", "")
    if not signature or not timestamp or not request_id:
        raise AgentError(401, "missing Signature, Timestamp or Request-Id header")
    try:
        sent = datetime.datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError as exc:
        raise AgentError(401, "bad Timestamp header") from exc
    if sent.tzinfo is None:
        sent = sent.replace(tzinfo=datetime.UTC)
    if abs((utcnow() - sent).total_seconds()) > 300:
        raise AgentError(401, "stale Timestamp header")
    expected = _hmac_signature(secret, timestamp, request_id, raw_body)
    if not hmac.compare_digest(expected, signature):
        raise AgentError(401, "bad Signature header")


def sign_response_headers(secret: str, body: bytes) -> dict[str, str]:
    """Response signature headers (same HMAC scheme as requests)."""
    if not secret:
        return {}
    timestamp = utcnow().isoformat().replace("+00:00", "Z")
    request_id = uuid.uuid4().hex
    created = int(utcnow().timestamp())
    return {
        "Signature": _hmac_signature(secret, timestamp, request_id, body),
        "Signature-Input": f'sig1=("@authority" "content-digest");created={created}',
        "Timestamp": timestamp,
        "Request-Id": request_id,
    }


# ---------------------------------------------------------------- helpers


def _fingerprint(body: bytes) -> str:
    """sha256 of the raw request body, for idempotency replay checks."""
    return hashlib.sha256(body).hexdigest()


def _new_session_id(protocol: str) -> str:
    """Public session id: ``cs_<hex>`` (ACP) or ``chk_<hex>`` (UCP)."""
    prefix = "cs_" if protocol == "acp" else "chk_"
    return prefix + uuid.uuid4().hex


def _parse_product_id(raw: object) -> uuid.UUID | None:
    """A catalog id is a product UUID string; anything else is not one."""
    if not isinstance(raw, str):
        return None
    try:
        return uuid.UUID(raw)
    except ValueError:
        return None


async def _resolve_items(
    db: AsyncSession, raw_items: object, directus: DirectusClient
) -> list[tuple[dict, int]]:
    """Turn agent cart lines into (Directus row, quantity) pairs.

    Raises AgentError 422 when the cart is empty, an id is not a
    product, a product is inactive, or the quantity beats availability.
    Availability is checked here AND again at ``complete`` (the order
    service re-checks under the payment lock). Each row carries its
    real stock as ``row["available"]`` (from the stock table).
    """
    if not isinstance(raw_items, list) or not raw_items:
        raise AgentError(422, "at least one line item is required")
    merged: dict[uuid.UUID, int] = {}
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise AgentError(422, "each line item must be an object")
        pid = _parse_product_id(raw.get("id"))
        if pid is None:
            raise AgentError(422, f"unknown product id: {raw.get('id')!r}")
        quantity = raw.get("quantity", 1)
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise AgentError(422, "quantity must be an integer of at least 1")
        merged[pid] = merged.get(pid, 0) + quantity
    resolved: list[tuple[dict, int]] = []
    for pid, quantity in merged.items():
        row = await directus.get_product(pid)
        if row is None or not row.get("is_active", True):
            raise AgentError(422, f"product {pid} is not available")
        available = await stock_service.available_for(db, pid)
        if quantity > available:
            raise AgentError(
                422, f"only {available} of {row.get('name')} available"
            )
        row["available"] = available
        resolved.append((row, quantity))
    return resolved


def _as_utc(value: datetime.datetime | None) -> datetime.datetime | None:
    """SQLite returns naive datetimes; treat them as UTC (the app's rule)."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=datetime.UTC)
    return value


def _availability(available: int) -> str:
    """ACP availability_status for a free-units count."""
    if available <= 0:
        return "out_of_stock"
    if available <= 2:
        return "low_stock"
    return "in_stock"


def _totals(subtotal_rial: int) -> list[dict[str, Any]]:
    """Cart totals in Rial: subtotal + total (no shipping fee, no tax)."""
    return [
        {"type": "subtotal", "display_text": "Subtotal", "amount": subtotal_rial},
        {"type": "total", "display_text": "Total", "amount": subtotal_rial},
    ]


def _buyer_name(buyer: dict | None) -> str | None:
    """Full name from a buyer dict (ACP/UCP share the field names)."""
    if not buyer:
        return None
    first = str(buyer.get("first_name") or "").strip()
    last = str(buyer.get("last_name") or "").strip()
    name = f"{first} {last}".strip()
    return name or None


def _buyer_phone(buyer: dict | None, fulfillment: dict | None) -> str | None:
    """Buyer phone: buyer.phone_number, else fulfillment phone."""
    if buyer and buyer.get("phone_number"):
        return str(buyer["phone_number"])
    if fulfillment and fulfillment.get("phone_number"):
        return str(fulfillment["phone_number"])
    return None


def _delivery_address(buyer: dict | None, fulfillment: dict | None) -> str | None:
    """One address line from fulfillment details or the buyer address."""
    address = None
    if fulfillment and isinstance(fulfillment.get("address"), dict):
        address = fulfillment["address"]
    elif buyer and isinstance(buyer.get("address"), dict):
        address = buyer["address"]
    if not address:
        return None
    parts = [
        str(address.get("line_one") or address.get("street_address") or "").strip(),
        str(address.get("line_two") or "").strip(),
        str(address.get("city") or address.get("address_locality") or "").strip(),
        str(address.get("state") or address.get("address_region") or "").strip(),
        str(address.get("postal_code") or "").strip(),
        str(address.get("country") or address.get("address_country") or "").strip(),
    ]
    joined = ", ".join(p for p in parts if p)
    return joined or None


def _links(base_url: str) -> list[dict[str, str]]:
    """Legal links every session carries (both protocols require them)."""
    return [
        {"type": "terms_of_service", "url": f"{base_url}/terms"},
        {"type": "privacy_policy", "url": f"{base_url}/privacy"},
    ]


def _fulfillment_option() -> dict[str, Any]:
    """The shop's one fulfillment option: delivery, no fee configured."""
    return {
        "type": "shipping",
        "id": "delivery",
        "title": "Delivery",
        "description": "Delivery to the buyer address.",
        "amount": 0,
    }


def _zibal_payment_terms(total_rial: int) -> list[dict[str, Any]]:
    """UCP payment terms for the Zibal redirect handler."""
    return [
        {
            "id": "pt_zibal",
            "title": "Pay with Zibal",
            "description": {
                "plain": "Pay on the Zibal bank page. The shop never charges a card or a token."
            },
            "schedules": [
                {
                    "id": "sched_zibal",
                    "type": "immediate",
                    "description": {"plain": "Due now, on the bank page."},
                    "amount": total_rial,
                }
            ],
        }
    ]


def _zibal_payment_instrument(total_rial: int) -> list[dict[str, Any]]:
    """UCP payment instruments: one redirect instrument, Zibal handler."""
    return [
        {
            "id": "pi_zibal",
            "handler_id": ZIBAL_HANDLER_ID,
            "type": "redirect",
            "selected": True,
            "display": {"description": "Pay on the Zibal bank page"},
        }
    ]


def _acp_line_items(state: dict) -> list[dict[str, Any]]:
    """ACP LineItem list from the canonical stored state."""
    items = []
    for index, line in enumerate(state["items"], start=1):
        items.append(
            {
                "id": f"li_{index}",
                "item": {"id": line["product_id"], "quantity": line["quantity"]},
                "product_id": line["product_id"],
                "name": line["name"],
                "description": line["description"],
                "images": [line["image_url"]] if line.get("image_url") else [],
                "unit_amount": line["unit_rial"],
                "quantity": line["quantity"],
                "availability_status": line["availability"],
            }
        )
    return items


def _ucp_line_items(state: dict) -> list[dict[str, Any]]:
    """UCP line_item list from the canonical stored state."""
    items = []
    for index, line in enumerate(state["items"], start=1):
        items.append(
            {
                "id": f"li_{index}",
                "item": {
                    "id": line["product_id"],
                    "title": line["name"],
                    "price": line["unit_rial"],
                },
                "quantity": line["quantity"],
                "totals": [
                    {
                        "type": "subtotal",
                        "amount": line["unit_rial"] * line["quantity"],
                    }
                ],
            }
        )
    return items


def _canonical_state(
    resolved: list[tuple[dict, int]],
    buyer: dict | None,
    fulfillment: dict | None,
    base_url: str,
) -> dict[str, Any]:
    """The stored cart: product snapshots + buyer + totals + messages."""
    items = []
    subtotal = 0
    for row, quantity in resolved:
        unit_rial = toman_to_rial(row.get("price_toman") or 0)
        subtotal += unit_rial * quantity
        items.append(
            {
                "product_id": str(row.get("id")),
                "name": row.get("name"),
                "description": row.get("description") or "",
                "image_url": (
                    f"{base_url}/uploads/{row.get('image_path')}"
                    if row.get("image_path")
                    else None
                ),
                "unit_rial": unit_rial,
                "quantity": quantity,
                "availability": _availability(row.get("available", 0)),
            }
        )
    return {
        "items": items,
        "buyer": buyer,
        "fulfillment": fulfillment,
        "subtotal_rial": subtotal,
        "order": None,
        "payment": None,
        "token_offered": None,
        "messages": [],
        "webhook_events": [],
    }


def _acp_response(row: AgentCheckoutSession, base_url: str) -> dict[str, Any]:
    """ACP CheckoutSession (or CheckoutSessionWithOrder) from a row."""
    state = row.state_json
    subtotal = state["subtotal_rial"]
    response: dict[str, Any] = {
        "id": row.session_id,
        "protocol": {"version": ACP_VERSION},
        "status": row.status,
        "currency": row.currency,
        "buyer": state.get("buyer"),
        "line_items": _acp_line_items(state),
        "fulfillment_options": [_fulfillment_option()],
        "totals": _totals(subtotal),
        "messages": state.get("messages", []),
        "links": _links(base_url),
        "capabilities": {},
    }
    if state.get("order"):
        response["order"] = state["order"]
    return response


def _ucp_response(row: AgentCheckoutSession, base_url: str) -> dict[str, Any]:
    """UCP checkout object from a row."""
    state = row.state_json
    subtotal = state["subtotal_rial"]
    expires_at = _as_utc(row.expires_at)
    response: dict[str, Any] = {
        "ucp": {"version": UCP_VERSION},
        "id": row.session_id,
        "status": row.status,
        "currency": row.currency,
        "buyer": state.get("buyer"),
        "line_items": _ucp_line_items(state),
        "totals": _totals(subtotal),
        "fulfillment": {
            "methods": [
                {
                    "id": "method_delivery",
                    "type": "delivery",
                    "options": [
                        {
                            "id": "opt_standard",
                            "title": "Standard Delivery",
                            "description": "Delivery to the buyer address.",
                            "totals": [{"type": "total", "amount": 0}],
                        }
                    ],
                    "selected_option_id": "opt_standard",
                }
            ]
        },
        "links": _links(base_url),
        "messages": state.get("messages", []),
        "expires_at": (
            expires_at.isoformat().replace("+00:00", "Z") if expires_at else None
        ),
    }
    if state.get("payment"):
        response["payment"] = state["payment"]
    if row.payment_url:
        response["continue_url"] = row.payment_url
    if state.get("order"):
        response["order"] = state["order"]
    return response


def _response_for(row: AgentCheckoutSession, base_url: str) -> dict[str, Any]:
    """The protocol-shaped response for a stored session row."""
    if row.protocol == "acp":
        return _acp_response(row, base_url)
    return _ucp_response(row, base_url)


def response_for(row: AgentCheckoutSession, base_url: str) -> dict[str, Any]:
    """The protocol-shaped response for a stored session row."""
    return _response_for(row, base_url)


def totals_for(subtotal_rial: int) -> list[dict[str, Any]]:
    """Cart totals in Rial: subtotal + total (no shipping fee, no tax)."""
    return _totals(subtotal_rial)


def _ready_status(protocol: str, buyer: dict | None) -> str:
    """Open-session status: ready when a buyer is known, else waiting."""
    if protocol == "acp":
        return "ready_for_payment" if buyer else "not_ready_for_payment"
    return "ready_for_complete" if buyer else "incomplete"


def _is_open(row: AgentCheckoutSession) -> bool:
    """True while the session can still be updated or completed."""
    open_statuses = ACP_OPEN if row.protocol == "acp" else UCP_OPEN
    return row.status in open_statuses


async def _expire_if_needed(db: AsyncSession, row: AgentCheckoutSession) -> None:
    """Close a session past its 6-hour TTL (ACP: expired, UCP: canceled)."""
    if row.expires_at is None or not _is_open(row):
        return
    expires_at = _as_utc(row.expires_at)
    assert expires_at is not None
    if utcnow() <= expires_at:
        return
    row.status = "expired" if row.protocol == "acp" else "canceled"
    state = row.state_json
    state["messages"] = [
        {
            "type": "info",
            "code": "session_expired",
            "content": "This checkout session expired after 6 hours.",
        }
    ]
    row.state_json = state
    await db.commit()


async def load_session(
    db: AsyncSession, protocol: str, session_id: str
) -> AgentCheckoutSession:
    """Load one session row (404 when unknown) and expire it if stale."""
    result = await db.execute(
        select(AgentCheckoutSession).where(
            AgentCheckoutSession.protocol == protocol,
            AgentCheckoutSession.session_id == session_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise AgentError(404, "checkout session not found")
    await _expire_if_needed(db, row)
    return row


# ---------------------------------------------------------------- sessions


def _replay_check(
    row: AgentCheckoutSession | None, key: str | None, raw_body: bytes
) -> dict[str, Any] | None:
    """Idempotency replay: same key + same body returns the cached answer.

    Returns the cached response dict, or None when this is a fresh
    request. Raises 422 when the key was used with a different body.
    """
    if not key or row is None or row.idempotency_key != key:
        return None
    if row.request_fingerprint != _fingerprint(raw_body):
        raise AgentError(
            422, "Idempotency-Key was already used with a different request"
        )
    return row.last_response_json


async def _find_create_replay(
    db: AsyncSession, protocol: str, key: str | None, raw_body: bytes
) -> dict[str, Any] | None:
    """Replay lookup for session creation (no session id yet)."""
    if not key:
        return None
    result = await db.execute(
        select(AgentCheckoutSession).where(
            AgentCheckoutSession.protocol == protocol,
            AgentCheckoutSession.idempotency_key == key,
        )
    )
    return _replay_check(result.scalar_one_or_none(), key, raw_body)


def _store_response(
    row: AgentCheckoutSession, key: str | None, raw_body: bytes, response: dict
) -> None:
    """Remember the response for idempotent replay (no commit here)."""
    if key:
        row.idempotency_key = key
        row.request_fingerprint = _fingerprint(raw_body)
        row.last_response_json = response


def _cart_lines(payload: dict) -> object:
    """Cart lines: ACP/UCP both use ``line_items``; accept ``items`` too."""
    return payload.get("line_items", payload.get("items"))


def _fulfillment_details(payload: dict) -> dict | None:
    """Fulfillment input: ACP ``fulfillment_details`` or UCP ``fulfillment``."""
    value = payload.get("fulfillment_details", payload.get("fulfillment"))
    return value if isinstance(value, dict) else None


async def create_session(
    db: AsyncSession,
    directus: DirectusClient,
    protocol: str,
    payload: dict,
    base_url: str,
    idempotency_key: str | None,
    raw_body: bytes,
) -> tuple[int, dict[str, Any], bool]:
    """Create a checkout session. ACP answers 201, UCP answers 200."""
    replayed = await _find_create_replay(db, protocol, idempotency_key, raw_body)
    if replayed is not None:
        return (200, replayed, True)
    resolved = await _resolve_items(db, _cart_lines(payload), directus)
    buyer = payload.get("buyer") if isinstance(payload.get("buyer"), dict) else None
    fulfillment = _fulfillment_details(payload)
    state = _canonical_state(resolved, buyer, fulfillment, base_url)
    row = AgentCheckoutSession(
        protocol=protocol,
        session_id=_new_session_id(protocol),
        status=_ready_status(protocol, buyer),
        currency=CURRENCY,
        buyer_json=buyer,
        items_json=[
            {"product_id": str(item_row["id"]), "quantity": q}
            for item_row, q in resolved
        ],
        state_json=state,
        expires_at=utcnow() + datetime.timedelta(hours=SESSION_TTL_HOURS),
    )
    db.add(row)
    await db.flush()
    response = _response_for(row, base_url)
    _store_response(row, idempotency_key, raw_body, response)
    await db.commit()
    return (201 if protocol == "acp" else 200, response, False)


async def update_session(
    db: AsyncSession,
    directus: DirectusClient,
    row: AgentCheckoutSession,
    payload: dict,
    base_url: str,
    idempotency_key: str | None,
    raw_body: bytes,
) -> tuple[dict[str, Any], bool]:
    """Update items / buyer / fulfillment on an open session, reprice."""
    replayed = _replay_check(row, idempotency_key, raw_body)
    if replayed is not None:
        return replayed, True
    if not _is_open(row):
        raise AgentError(409, f"checkout session is {row.status}")
    state = row.state_json
    if _cart_lines(payload) is not None:
        resolved = await _resolve_items(db, _cart_lines(payload), directus)
        fresh = _canonical_state(
            resolved, state.get("buyer"), state.get("fulfillment"), base_url
        )
        state["items"] = fresh["items"]
        state["subtotal_rial"] = fresh["subtotal_rial"]
        row.items_json = [
            {"product_id": str(item_row["id"]), "quantity": q}
            for item_row, q in resolved
        ]
    if isinstance(payload.get("buyer"), dict):
        buyer = dict(state.get("buyer") or {})
        buyer.update(payload["buyer"])
        state["buyer"] = buyer
        row.buyer_json = buyer
    fulfillment = _fulfillment_details(payload)
    if fulfillment is not None:
        state["fulfillment"] = fulfillment
    row.status = _ready_status(row.protocol, state.get("buyer"))
    row.state_json = state
    response = _response_for(row, base_url)
    _store_response(row, idempotency_key, raw_body, response)
    await db.commit()
    return response, False


def _payment_offer(payload: dict) -> dict[str, Any] | None:
    """The payment the agent offers at ``complete`` (ACP/UCP shapes).

    Returns a small description (provider + handler, never any token
    value) or None when the agent offers nothing — the Zibal link is
    the payment method either way.
    """
    offered = payload.get("payment_data", payload.get("payment"))
    if not isinstance(offered, dict):
        return None
    info: dict[str, Any] = {}
    handler = offered.get("handler_id")
    if isinstance(handler, str):
        info["handler_id"] = handler
    # Flat ACP shape: {provider: "stripe", token: "spt_..."}.
    if isinstance(offered.get("provider"), str):
        info["provider"] = offered["provider"]
    if isinstance(offered.get("token"), str):
        info["token_present"] = True
        info["credential_type"] = "spt"
    instrument = offered.get("instrument")
    if isinstance(instrument, dict):
        if isinstance(instrument.get("type"), str):
            info["instrument_type"] = instrument["type"]
        credential = instrument.get("credential")
        if isinstance(credential, dict) and isinstance(credential.get("type"), str):
            info["credential_type"] = credential["type"]
            info["token_present"] = bool(credential.get("token"))
    instruments = offered.get("instruments")
    if isinstance(instruments, list) and instruments:
        first = instruments[0]
        if isinstance(first, dict):
            if isinstance(first.get("handler_id"), str):
                info["handler_id"] = first["handler_id"]
            if isinstance(first.get("type"), str):
                info["instrument_type"] = first["type"]
            info["token_present"] = bool(first.get("token") or first.get("data"))
    return info or None


async def complete_session(
    db: AsyncSession,
    directus: DirectusClient,
    row: AgentCheckoutSession,
    payload: dict,
    zibal: ZibalClient,
    callback_url: str,
    base_url: str,
    idempotency_key: str | None,
    raw_body: bytes,
    http_client: httpx.AsyncClient | None,
) -> tuple[dict[str, Any], bool]:
    """Complete: create the real order, start Zibal, return the pay link.

    The agent's delegated token (Stripe SPT / Google Pay) is NEVER
    charged and NEVER forwarded: only its provider is recorded, and
    the response carries the Zibal start URL. The buyer pays on the
    bank page; the order stays waiting until the callback verifies.
    """
    replayed = _replay_check(row, idempotency_key, raw_body)
    if replayed is not None:
        return replayed, True
    if row.status == "completed":
        raise AgentError(409, "checkout session is already completed")
    if not _is_open(row):
        raise AgentError(409, f"checkout session is {row.status}")
    ready = "ready_for_payment" if row.protocol == "acp" else "ready_for_complete"
    if row.status != ready:
        raise AgentError(
            409, "checkout session is not ready for payment (buyer is missing)"
        )
    offered = _payment_offer(payload)
    if offered is not None:
        handler = offered.get("handler_id")
        if handler is not None and handler != ZIBAL_HANDLER_ID:
            raise AgentError(
                422,
                f"unsupported payment handler {handler!r} — the shop takes "
                "Zibal only, by payment link",
            )
    state = row.state_json
    buyer = state.get("buyer") or {}
    raw_phone = _buyer_phone(buyer, state.get("fulfillment"))
    if not raw_phone:
        raise AgentError(
            422, "buyer phone_number is required — the shop identifies buyers by phone"
        )
    try:
        phone = normalize_phone(str(raw_phone))
    except (ValueError, TypeError) as exc:
        raise AgentError(422, f"buyer phone_number is not a valid phone: {raw_phone}") from exc
    user, _ = await get_or_create_user_by_phone(db, phone)
    items: list[OrderItemInput] = [
        {
            "product_id": uuid.UUID(line["product_id"]),
            "quantity": int(line["quantity"]),
        }
        for line in state["items"]
    ]
    try:
        order = await create_order(
            db,
            user,
            items,
            directus,
            recipient_name=_buyer_name(buyer),
            recipient_phone=phone,
            delivery_address=_delivery_address(buyer, state.get("fulfillment")),
        )
    except OrderError as exc:
        raise AgentError(409, exc.detail) from exc
    try:
        attempt = await start_payment(db, order.id, user.id, zibal, callback_url)
    except OrderError as exc:
        raise AgentError(409, exc.detail) from exc
    except (ZibalError, ValueError) as exc:
        raise AgentError(502, f"payment gateway error: {exc}") from exc
    assert attempt.track_id is not None  # start only returns waiting attempts
    payment_url = zibal.start_url(attempt.track_id)
    subtotal = state["subtotal_rial"]
    order_payload: dict[str, Any] = {
        "id": f"ord_{order.id.hex}",
        "order_number": str(order.id),
        "status": "confirmed",
        "currency": CURRENCY,
        "total": order.total_rial,
        "line_items": (
            _acp_line_items(state)
            if row.protocol == "acp"
            else _ucp_line_items(state)
        ),
        "created_at": utcnow().isoformat().replace("+00:00", "Z"),
        "payment_url": payment_url,
    }
    if row.protocol == "ucp":
        order_payload = {
            "ucp": {"version": UCP_VERSION},
            "id": f"ord_{order.id.hex}",
            "label": f"Genabay order {str(order.id)[:8]}",
            "checkout_id": row.session_id,
            "permalink_url": f"{base_url}/order/{order.id}",
            "line_items": _ucp_line_items(state),
            "fulfillment": _ucp_response(row, base_url)["fulfillment"],
            "currency": CURRENCY,
            "totals": _totals(subtotal),
            "payment_url": payment_url,
        }
    state["order"] = order_payload
    state["token_offered"] = offered
    state["payment"] = {
        "handler_id": ZIBAL_HANDLER_ID,
        "payment_url": payment_url,
        "terms": _zibal_payment_terms(subtotal),
        "instruments": _zibal_payment_instrument(subtotal),
    }
    state["messages"] = [
        {
            "type": "info",
            "code": "payment_link",
            "content": (
                "Pay here on the Zibal bank page: " + payment_url + ". "
                "The shop never charges a card or a token."
            ),
        }
    ]
    row.status = "completed"
    row.order_id = order.id
    row.payment_url = payment_url
    row.state_json = state
    response = _response_for(row, base_url)
    _store_response(row, idempotency_key, raw_body, response)
    await db.commit()
    event = await emit_order_event(
        row.protocol, "order_create", order, row, base_url, http_client
    )
    state["webhook_events"].append(event)
    row.state_json = state
    await db.commit()
    return response, False


async def cancel_session(
    db: AsyncSession,
    row: AgentCheckoutSession,
    base_url: str,
    idempotency_key: str | None,
    raw_body: bytes,
) -> tuple[dict[str, Any], bool]:
    """Cancel an open session; a started order's stock hold is freed."""
    replayed = _replay_check(row, idempotency_key, raw_body)
    if replayed is not None:
        return replayed, True
    if row.status == "completed":
        # ACP: 405 when already completed/canceled; UCP: 409.
        raise AgentError(
            405 if row.protocol == "acp" else 409,
            "a completed checkout session cannot be canceled",
        )
    if not _is_open(row):
        raise AgentError(
            405 if row.protocol == "acp" else 409,
            f"checkout session is already {row.status}",
        )
    if row.order_id is not None:
        order = await load_order(db, row.order_id)
        if order is not None and order.status in (
            OrderStatus.CREATED.value,
            OrderStatus.WAITING_PAYMENT.value,
        ):
            try:
                await cancel_payment(db, order.id, order.user_id)
            except OrderError:
                pass
    row.status = "canceled"
    state = row.state_json
    state["messages"] = [
        {
            "type": "info",
            "code": "session_canceled",
            "content": "This checkout session was canceled.",
        }
    ]
    row.state_json = state
    response = _response_for(row, base_url)
    _store_response(row, idempotency_key, raw_body, response)
    await db.commit()
    return response, False


# ---------------------------------------------------------------- webhooks


async def emit_order_event(
    protocol: str,
    event_type: str,
    order: Order,
    row: AgentCheckoutSession,
    base_url: str,
    http_client: httpx.AsyncClient | None,
) -> dict[str, Any]:
    """Send one order event to the agent platform (best effort).

    ACP posts ``order_create`` / ``order_update`` to the agent's
    webhook endpoint; UCP pushes post-purchase updates the same way.
    The URL comes from ``ACP_WEBHOOK_URL`` / ``UCP_WEBHOOK_URL`` and
    the body is HMAC signed with ``ACP_SIGNING_SECRET`` when set.
    With no URL configured the event is recorded, not sent — delivery
    waits for configuration. Never raises.
    """
    url = os.environ.get(
        "ACP_WEBHOOK_URL" if protocol == "acp" else "UCP_WEBHOOK_URL", ""
    )
    record: dict[str, Any] = {
        "type": event_type,
        "order_id": str(order.id),
        "at": utcnow().isoformat().replace("+00:00", "Z"),
        "delivered": False,
    }
    if not url:
        record["reason"] = "no webhook URL configured"
        return record
    if http_client is None:
        record["reason"] = "no HTTP client available"
        return record
    payload = {
        "type": event_type,
        "order": row.state_json.get("order") or {"id": str(order.id)},
        "created_at": record["at"],
    }
    body = json.dumps(payload).encode()
    timestamp = record["at"]
    request_id = uuid.uuid4().hex
    headers = {
        "Content-Type": "application/json",
        "Timestamp": timestamp,
        "Request-Id": request_id,
    }
    secret = os.environ.get("ACP_SIGNING_SECRET", "")
    if secret:
        headers["Signature"] = _hmac_signature(secret, timestamp, request_id, body)
    try:
        response = await http_client.post(url, content=body, headers=headers, timeout=10.0)
    except httpx.HTTPError as exc:
        record["reason"] = f"webhook transport error: {exc}"
        return record
    record["delivered"] = 200 <= response.status_code < 300
    record["status_code"] = response.status_code
    return record


# ---------------------------------------------------------------- catalog


def _catalog_product(row: dict, available: int, base_url: str) -> dict[str, Any]:
    """One catalog entry: price in Rial, availability, shop URL."""
    return {
        "id": str(row.get("id")),
        "title": row.get("name"),
        "description": row.get("description") or "",
        "price": toman_to_rial(row.get("price_toman") or 0),
        "currency": CURRENCY,
        "availability": _availability(available),
        "url": f"{base_url}/products/{row.get('id')}",
    }


async def search_catalog(
    directus: DirectusClient,
    db: AsyncSession,
    query: str | None,
    base_url: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Catalog search: name match or the whole list (Directus)."""
    if query:
        rows = await search_service.search_products(
            directus, query, limit=max(1, min(limit, 50))
        )
    else:
        rows = await search_service.list_products(
            directus, limit=max(1, min(limit, 50))
        )
    await stock_service.attach_stock(db, rows)
    return [
        _catalog_product(row, row.get("available", 0), base_url) for row in rows
    ]


async def lookup_catalog(
    directus: DirectusClient,
    db: AsyncSession,
    raw_ids: object,
    base_url: str,
) -> list[dict[str, Any]]:
    """Catalog lookup: the products for the given ids, in request order."""
    if not isinstance(raw_ids, list):
        raise AgentError(422, "ids must be an array of product ids")
    wanted = [pid for pid in (_parse_product_id(v) for v in raw_ids) if pid is not None]
    if not wanted:
        return []
    rows = await directus.get_products_by_ids(wanted)
    await stock_service.attach_stock(db, rows)
    by_id = {str(row.get("id")): row for row in rows}
    return [
        _catalog_product(by_id[str(pid)], by_id[str(pid)].get("available", 0), base_url)
        for pid in wanted
        if str(pid) in by_id
    ]


# ---------------------------------------------------------------- discovery


def ucp_profile(base_url: str) -> dict[str, Any]:
    """The ``/.well-known/ucp`` merchant profile (UCP spec, discovery).

    Services and capabilities use reverse-domain names under the
    spec's ``dev.ucp.*`` namespace; the payment handler lives under
    our own domain (``ir.genabay.*``) with its schema served by the
    shop, so the authority binding holds.
    """
    return {
        "ucp_version": UCP_VERSION,
        "business": {
            "name": "گنابِی",
            "display_name": "Genabay",
            "url": base_url,
            "description": "Genabay — home appliances store (فروشگاه لوازم خانگی گنابِی).",
        },
        "services": {
            "dev.ucp.shopping": [
                {
                    "version": UCP_VERSION,
                    "transport": "rest",
                    "endpoint": f"{base_url}/ucp",
                    "spec": "https://ucp.dev/latest/specification/overview/",
                    "schema": "https://ucp.dev/2026-08-25/services/shopping/rest.openapi.json",
                }
            ]
        },
        "capabilities": {
            "dev.ucp.shopping.checkout": {
                "version": UCP_VERSION,
                "spec": "https://ucp.dev/latest/specification/overview/",
                "schema": "https://ucp.dev/2026-08-25/schemas/shopping/checkout.json",
            },
            "dev.ucp.shopping.catalog": {
                "version": UCP_VERSION,
                "spec": "https://ucp.dev/latest/specification/overview/",
                "schema": "https://ucp.dev/2026-08-25/schemas/shopping/catalog.json",
            },
            "dev.ucp.shopping.orders": {
                "version": UCP_VERSION,
                "spec": "https://ucp.dev/latest/specification/overview/",
                "schema": "https://ucp.dev/2026-08-25/schemas/shopping/order.json",
            },
        },
        "payment_handlers": [
            {
                "name": ZIBAL_HANDLER_ID,
                "schema": f"{base_url}/ucp/schemas/zibal-payment.json",
                "spec": f"{base_url}/terms",
                "description": "Payment by Zibal redirect link — the buyer pays on the bank page.",
            }
        ],
    }


def zibal_payment_schema(base_url: str) -> dict[str, Any]:
    """JSON Schema for the ``ir.genabay.payments.zibal`` handler.

    It declares what the handler is: a redirect to a Zibal payment
    page. No card fields, no token fields — the handler carries a
    payment URL, never payment credentials.
    """
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{base_url}/ucp/schemas/zibal-payment.json",
        "title": "Zibal payment handler",
        "description": (
            "The buyer pays on the Zibal bank page. The merchant returns "
            "a payment_url in the checkout response; no card or token is "
            "collected or charged by the shop."
        ),
        "type": "object",
        "properties": {
            "handler_id": {"const": ZIBAL_HANDLER_ID},
            "payment_url": {"type": "string", "format": "uri"},
        },
        "required": ["handler_id", "payment_url"],
    }


__all__ = [
    "ACP_VERSION",
    "AgentError",
    "CURRENCY",
    "UCP_VERSION",
    "ZIBAL_HANDLER_ID",
    "cancel_session",
    "complete_session",
    "create_session",
    "emit_order_event",
    "load_session",
    "lookup_catalog",
    "require_agent_key",
    "search_catalog",
    "sign_response_headers",
    "ucp_profile",
    "update_session",
    "verify_agent_signature",
    "zibal_payment_schema",
]
