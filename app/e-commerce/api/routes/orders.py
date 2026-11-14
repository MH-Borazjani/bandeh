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
"""Order routes: checkout (create), list my orders, and read one order.

Both need a logged-in user, and a user only ever sees their own
orders — the service returns None for another user's order, and the
route answers 404 either way.
"""

from __future__ import annotations

import datetime
import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from core.datetime_utils import format_tehran
from core.security import CurrentDbUserDep, RequireDbUserDep
from core.templates import templates
from db.models.order import Order
from db.session import DbSessionDep
from integrations.directus_client import _FA_DIGITS, format_toman_price
from schema.order_api import OrderCreate, OrderRead
from services.order_service import (
    OrderError,
    create_order,
    list_orders_for_user,
    load_order_for_user,
)

router = APIRouter(tags=["orders"])

ORDER_STATUS_META: dict[str, tuple[str, str]] = {
    "created": ("ثبت شده (در انتظار پرداخت)", "theme-badge"),
    "waiting_payment": ("در انتظار پرداخت", "theme-badge"),
    "paid": ("پرداخت شده", "theme-badge-success"),
    "failed": ("ناموفق", "theme-badge-danger"),
    "expired": ("منقضی شده", "theme-badge-danger"),
}


def _render_orders_page(
    request: Request,
    orders: Sequence[Order],
    phone: str | None,
) -> HTMLResponse:
    """Render the My Orders listing HTML page."""
    view_orders: list[dict] = []
    for o in orders:
        label, badge = ORDER_STATUS_META.get(o.status, (o.status, "theme-badge"))
        dt = o.created_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=datetime.UTC)
        formatted_date = format_tehran(dt).translate(_FA_DIGITS)
        view_orders.append(
            {
                "id": str(o.id),
                "status": o.status,
                "status_label": label,
                "status_badge_class": badge,
                "total_toman_formatted": format_toman_price(o.total_toman),
                "created_at_formatted": formatted_date,
                "items_count": str(len(o.items)).translate(_FA_DIGITS),
                "recipient_name": o.recipient_name,
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="orders.html",
        context={"phone": phone, "orders": view_orders},
    )


def _render_order_detail_page(
    request: Request,
    order: Order,
    phone: str | None,
) -> HTMLResponse:
    """Render the Order Detail HTML page."""
    label, badge = ORDER_STATUS_META.get(order.status, (order.status, "theme-badge"))
    dt = order.created_at
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    formatted_date = format_tehran(dt).translate(_FA_DIGITS)

    items_view: list[dict] = []
    for item in order.items:
        items_view.append(
            {
                "product_id": str(item.product_id),
                "product_name": item.product_name,
                "quantity": item.quantity,
                "quantity_formatted": str(item.quantity).translate(_FA_DIGITS),
                "unit_price_formatted": format_toman_price(item.unit_price_toman),
                "line_total_formatted": format_toman_price(
                    item.unit_price_toman * item.quantity
                ),
            }
        )

    payment_status_label = label
    payment_badge_class = badge
    latest_attempt = None
    if order.attempts:
        att = order.attempts[-1]
        gateway_name = "زیبال" if att.gateway == "zibal" else att.gateway
        latest_attempt = {
            "gateway_label": gateway_name,
            "track_id": att.track_id,
            "status": att.status,
        }
        if att.status == "paid":
            payment_status_label = "پرداخت موفق و تایید شده"
            payment_badge_class = "theme-badge-success"
        elif att.status == "failed":
            payment_status_label = "پرداخت ناموفق"
            payment_badge_class = "theme-badge-danger"

    can_pay = order.status in ("created", "waiting_payment")

    order_dict = {
        "id": str(order.id),
        "status": order.status,
        "status_label": label,
        "status_badge_class": badge,
        "payment_status_label": payment_status_label,
        "payment_badge_class": payment_badge_class,
        "payment_attempt": latest_attempt,
        "can_pay": can_pay,
        "total_toman_formatted": format_toman_price(order.total_toman),
        "created_at_formatted": formatted_date,
        "recipient_name": order.recipient_name,
        "recipient_phone": order.recipient_phone,
        "delivery_address": order.delivery_address,
        "items": items_view,
    }

    return templates.TemplateResponse(
        request=request,
        name="order_detail.html",
        context={"phone": phone, "order": order_dict, "items": items_view},
    )


@router.post("/orders", status_code=201)
async def create_order_endpoint(
    data: OrderCreate,
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> OrderRead:
    """Checkout: freeze the cart into an order (prices fixed at this moment)."""
    try:
        order = await create_order(
            db,
            user,
            data.items,
            recipient_name=data.recipient_name,
            recipient_phone=data.recipient_phone,
            delivery_address=data.delivery_address,
        )
    except OrderError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return OrderRead.model_validate(order)


@router.post("/checkout", status_code=201)
async def checkout_endpoint(
    data: OrderCreate,
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> OrderRead:
    """Checkout: create order with delivery address."""
    return await create_order_endpoint(data, user, db)


@router.get("/orders", response_model=None)
async def list_orders_endpoint(
    request: Request,
    db: DbSessionDep,
    user: CurrentDbUserDep,
) -> list[OrderRead] | HTMLResponse | RedirectResponse:
    """List orders for the signed-in buyer.

    Redirects guest to login page. Supports Accept: application/json or format=json for API callers.
    """
    accept = request.headers.get("accept", "")
    fmt = request.query_params.get("format")
    is_json = fmt == "json" or (accept == "application/json" and fmt != "html")

    if user is None:
        if is_json:
            raise HTTPException(
                status_code=401, detail="ورود به حساب کاربری الزامی است."
            )
        return RedirectResponse("/auth/login", status_code=303)

    orders = await list_orders_for_user(db, user.id)

    if is_json:
        return [OrderRead.model_validate(o) for o in orders]

    return _render_orders_page(request, orders, user.phone)


@router.get("/orders/{order_id}", response_model=None)
async def read_order(
    request: Request,
    order_id: Annotated[uuid.UUID, Path()],
    user: CurrentDbUserDep,
    db: DbSessionDep,
) -> OrderRead | HTMLResponse | RedirectResponse:
    """Read one of my orders.

    Returns HTML detail view for browsers, or JSON OrderRead for API callers.
    """
    accept = request.headers.get("accept", "")
    fmt = request.query_params.get("format")
    is_html = (
        "text/html" in accept
        or fmt == "html"
        or request.url.path.startswith("/order/")
    )

    if user is None:
        if is_html:
            return RedirectResponse("/auth/login", status_code=303)
        raise HTTPException(
            status_code=401, detail="ورود به حساب کاربری الزامی است."
        )

    order = await load_order_for_user(db, order_id, user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    if is_html:
        return _render_order_detail_page(request, order, user.phone)
    return OrderRead.model_validate(order)


@router.get("/order/{order_id}", response_class=HTMLResponse, response_model=None)
async def order_page_endpoint(
    request: Request,
    order_id: Annotated[uuid.UUID, Path()],
    user: CurrentDbUserDep,
    db: DbSessionDep,
) -> HTMLResponse | RedirectResponse:
    """HTML order detail page for one order."""
    if user is None:
        return RedirectResponse("/auth/login", status_code=303)
    order = await load_order_for_user(db, order_id, user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")
    return _render_order_detail_page(request, order, user.phone)


__all__ = [
    "checkout_endpoint",
    "create_order_endpoint",
    "list_orders_endpoint",
    "order_page_endpoint",
    "read_order",
    "router",
]

