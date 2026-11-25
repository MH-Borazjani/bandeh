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
"""Internal endpoints for Directus Flows.

Architecture: ALL logic lives in this code (FastAPI + SQLAlchemy);
Directus is only the shell that shows the data and triggers actions.
A Directus Flow calls these endpoints with a shared secret in the
``X-Directus-Flow-Secret`` header — never a cookie, never a user
session. The shop does the work (status change, tracking SMS,
search re-index) and Directus displays the result. The order status
and tracking-code fields are READONLY in Directus, so a Flow is the
only path that changes them — editing the raw field can never skip
the SMS.

If ``DIRECTUS_FLOW_SECRET`` is unset, every endpoint here returns
503. A missing or wrong secret returns 401. The secret is compared
in constant time and never logged.
"""

from __future__ import annotations

import hmac
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, HTTPException, Path, Request

from db.session import DbSessionDep
from integrations.directus_client import DirectusClientDep
from integrations.meilisearch_client import (
    MeilisearchClientDep,
    MeilisearchError,
)
from schema.admin import (
    AdminOrderItemRead,
    AdminOrderRead,
    AdminOrderUpdate,
    SearchReindexRead,
)
from services.order_service import OrderError, load_order, update_order_fulfillment
from services.search_index_service import sync_products_index

router = APIRouter(prefix="/internal", tags=["internal"])

_FLOW_SECRET_HEADER = "X-Directus-Flow-Secret"


def _require_flow_secret(request: Request) -> None:
    """Check the Directus Flow shared secret (constant-time).

    503 when no secret is configured (the endpoints are off);
    401 when the header is missing or wrong. The settings import is
    lazy (like core/security.py) so importing this router never
    requires the repo root on sys.path.
    """
    from env_settings import get_settings

    settings = get_settings()
    configured = settings.directus_flow_secret
    if configured is None:
        raise HTTPException(
            status_code=503, detail="Directus Flow secret is not configured"
        )
    provided = request.headers.get(_FLOW_SECRET_HEADER, "")
    if not hmac.compare_digest(provided, configured.get_secret_value()):
        raise HTTPException(status_code=401, detail="invalid Directus Flow secret")


@router.post(
    "/directus/orders/{order_id}/status", response_model=AdminOrderRead
)
async def directus_update_order_status(
    request: Request,
    order_id: Annotated[uuid.UUID, Path()],
    payload: AdminOrderUpdate,
    db: DbSessionDep,
) -> AdminOrderRead:
    """Directus Flow: mark an order shipped/delivered + tracking code.

    Same rules as the staff admin endpoint: the tracking SMS goes out
    ONLY the first time a tracking code is set (order had no code
    before this update, and the new status is "shipped").
    """
    _require_flow_secret(request)

    order_before = await load_order(db, order_id)
    if order_before is None:
        raise HTTPException(status_code=404, detail="order not found")

    status_before = order_before.status
    tracking_before = order_before.tracking_code

    try:
        updated_order = await update_order_fulfillment(
            db,
            order_id=order_id,
            new_status=payload.status,
            tracking_code=payload.tracking_code,
        )
    except OrderError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc

    if (
        payload.status == "shipped"
        and status_before != "shipped"
        and not tracking_before
    ):
        try:
            from services.sms_service import send_tracking_sms

            await send_tracking_sms(db, updated_order)
        except Exception:
            pass

    return AdminOrderRead(
        id=updated_order.id,
        user_id=updated_order.user_id,
        status=updated_order.status,
        total_toman=updated_order.total_toman,
        total_rial=updated_order.total_rial,
        recipient_name=updated_order.recipient_name,
        recipient_phone=updated_order.recipient_phone,
        delivery_address=updated_order.delivery_address,
        tracking_code=updated_order.tracking_code,
        created_at=updated_order.created_at,
        items=[
            AdminOrderItemRead(
                product_id=it.product_id,
                product_name=it.product_name,
                quantity=it.quantity,
                unit_price_toman=it.unit_price_toman,
            )
            for it in updated_order.items
        ],
    )


@router.post(
    "/directus/search/reindex", response_model=SearchReindexRead
)
async def directus_reindex_search(
    request: Request,
    directus: DirectusClientDep,
    meilisearch: MeilisearchClientDep,
) -> SearchReindexRead:
    """Directus Flow: rebuild the Meilisearch products index now.

    Same full re-index as the staff admin endpoint and the 5-minute
    timer. 503 when Meilisearch is not configured or unreachable.
    """
    _require_flow_secret(request)

    if meilisearch is None:
        raise HTTPException(
            status_code=503, detail="Meilisearch is not configured"
        )
    try:
        result = await sync_products_index(directus, meilisearch=meilisearch)
    except (MeilisearchError, httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503, detail=f"search index sync failed: {exc}"
        ) from exc
    return SearchReindexRead(indexed=result["indexed"], deleted=result["deleted"])


__all__ = [
    "directus_reindex_search",
    "directus_update_order_status",
    "router",
]
