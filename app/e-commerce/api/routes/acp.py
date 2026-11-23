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
"""ACP checkout sessions (OpenAI + Stripe, spec 2026-04-17).

Merchant endpoints, mounted under ``/acp``:
- POST /checkout_sessions (201) — create from cart + buyer.
- POST /checkout_sessions/{id} — update items / buyer / fulfillment.
- GET /checkout_sessions/{id} — fetch state.
- POST /checkout_sessions/{id}/complete — create the order, start
  Zibal, return the payment link (never charges a delegated token).
- POST /checkout_sessions/{id}/cancel — cancel (405 when completed).

Every call needs ``Authorization: Bearer <AGENT_API_KEY>`` and an
``Idempotency-Key`` on the POSTs (400 without one, per the spec).
When ``ACP_SIGNING_SECRET`` is set, POSTs must also carry a valid
HMAC ``Signature`` (see services/agent_commerce.py for the canonical
string). Errors are FastAPI-style ``{"detail": ...}``.
"""

from __future__ import annotations

import json
import os

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from db.session import DbSessionDep
from integrations.directus_client import DirectusClientDep
from integrations.zibal_client import ZibalClientDep
from services import agent_commerce
from services.agent_commerce import AgentError

router = APIRouter(prefix="/acp", tags=["acp"])


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
    """ACP response headers: key echo, version echo, replay flag."""
    headers = {"API-Version": request.headers.get("api-version", agent_commerce.ACP_VERSION)}
    if idempotency_key:
        headers["Idempotency-Key"] = idempotency_key
    if replayed:
        headers["Idempotent-Replayed"] = "true"
    return headers


def _idempotency_key(request: Request) -> str:
    """ACP requires Idempotency-Key on every POST (spec: 400 without)."""
    key = request.headers.get("idempotency-key", "")
    if not key:
        raise HTTPException(status_code=400, detail="Idempotency-Key header is required")
    return key


@router.post("/checkout_sessions", status_code=201)
async def create_checkout_session(
    request: Request,
    db: DbSessionDep,
    directus: DirectusClientDep,
) -> JSONResponse:
    """Create a checkout session from items + optional buyer (201)."""
    try:
        agent_commerce.require_agent_key(request)
        payload, raw = await _read_payload(request)
        agent_commerce.verify_agent_signature(request, raw)
        key = _idempotency_key(request)
        status, response, replayed = await agent_commerce.create_session(
            db, directus, "acp", payload, _base_url(request), key, raw
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        status_code=status,
        content=response,
        headers=_response_headers(request, key, replayed),
    )


@router.get("/checkout_sessions/{session_id}")
async def get_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
) -> JSONResponse:
    """Fetch the current state of one checkout session."""
    try:
        agent_commerce.require_agent_key(request)
        row = await agent_commerce.load_session(db, "acp", session_id)
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=agent_commerce.response_for(row, _base_url(request)),
        headers=_response_headers(request, None, False),
    )


@router.post("/checkout_sessions/{session_id}")
async def update_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
    directus: DirectusClientDep,
) -> JSONResponse:
    """Update items / buyer / fulfillment on an open session, reprice."""
    try:
        agent_commerce.require_agent_key(request)
        payload, raw = await _read_payload(request)
        agent_commerce.verify_agent_signature(request, raw)
        key = _idempotency_key(request)
        row = await agent_commerce.load_session(db, "acp", session_id)
        response, replayed = await agent_commerce.update_session(
            db, directus, row, payload, _base_url(request), key, raw
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=response,
        headers=_response_headers(request, key, replayed),
    )


@router.post("/checkout_sessions/{session_id}/complete")
async def complete_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
    directus: DirectusClientDep,
    zibal: ZibalClientDep,
) -> JSONResponse:
    """Complete: create the order, start Zibal, return the payment link.

    A delegated payment token (Stripe SPT) sent by the agent is never
    charged and never forwarded — the response carries the Zibal start
    URL, and the buyer pays on the bank page.
    """
    try:
        agent_commerce.require_agent_key(request)
        payload, raw = await _read_payload(request)
        agent_commerce.verify_agent_signature(request, raw)
        key = _idempotency_key(request)
        row = await agent_commerce.load_session(db, "acp", session_id)
        http_client = getattr(request.app.state, "http_client", None)
        response, replayed = await agent_commerce.complete_session(
            db,
            directus,
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


@router.post("/checkout_sessions/{session_id}/cancel")
async def cancel_checkout_session(
    request: Request,
    session_id: str,
    db: DbSessionDep,
) -> JSONResponse:
    """Cancel an open session (405 when already completed/canceled)."""
    try:
        agent_commerce.require_agent_key(request)
        raw = await request.body()
        agent_commerce.verify_agent_signature(request, raw)
        key = request.headers.get("idempotency-key")
        row = await agent_commerce.load_session(db, "acp", session_id)
        response, replayed = await agent_commerce.cancel_session(
            db, row, _base_url(request), key, raw
        )
    except AgentError as exc:
        raise _agent_error(exc) from exc
    return JSONResponse(
        content=response,
        headers=_response_headers(request, key, replayed),
    )


__all__ = ["router"]
