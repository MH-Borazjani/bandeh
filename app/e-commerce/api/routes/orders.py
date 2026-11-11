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
"""Order routes: checkout (create) and read one order.

Both need a logged-in user, and a user only ever sees their own
orders — the service returns None for another user's order, and the
route answers 404 either way.
"""

from __future__ import annotations

import uuid

from core.security import RequireDbUserDep
from db.session import DbSessionDep
from fastapi import APIRouter, HTTPException
from schema.order_api import OrderCreate, OrderRead
from services.order_service import OrderError, create_order, load_order_for_user

router = APIRouter(tags=["orders"])


@router.post("/orders", status_code=201, response_model=OrderRead)
async def create_order_endpoint(
    data: OrderCreate,
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> OrderRead:
    """Checkout: freeze the cart into an order (prices fixed at this moment)."""
    try:
        order = await create_order(
            db, user, [item.model_dump() for item in data.items]
        )
    except OrderError as exc:
        raise HTTPException(status_code=400, detail=exc.detail)
    return OrderRead.model_validate(order)


@router.get("/orders/{order_id}", response_model=OrderRead)
async def read_order(
    order_id: uuid.UUID,
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> OrderRead:
    """Read one of my orders."""
    order = await load_order_for_user(db, order_id, user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return OrderRead.model_validate(order)


__all__ = ["router"]
