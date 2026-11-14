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
"""Admin-only endpoints.

The authorization lives in the dependency, never in an if inside the
route: ``RequireAdminDep`` / ``RequireStaffDep`` raises 401 for guests and 403 for customers,
so the handler only ever runs for authorized staff/admin users.
"""

from __future__ import annotations

import datetime
import uuid
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Path, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select

from api.routes.orders import ORDER_STATUS_META
from core.datetime_utils import format_tehran
from core.security import RequireAdminDep, RequireStaffDep
from core.templates import templates
from db.models.order import Order
from db.models.session import UserSession
from db.models.user import User
from db.session import DbSessionDep
from integrations.directus_client import _FA_DIGITS, format_toman_price
from integrations.sms import send_order_shipped
from schema.admin import (
    AdminOrderItemRead,
    AdminOrderListItem,
    AdminOrderRead,
    AdminOrderUpdate,
    AdminOverviewRead,
)
from services.order_service import (
    OrderError,
    list_all_orders,
    load_order,
    update_order_fulfillment,
)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/overview")
async def admin_overview(admin: RequireAdminDep, db: DbSessionDep) -> AdminOverviewRead:
    """Admin dashboard numbers: total users and live (unrevoked) sessions."""
    total_users = await db.scalar(select(func.count()).select_from(User))
    live_sessions = await db.scalar(
        select(func.count())
        .select_from(UserSession)
        .where(UserSession.is_revoked.is_(False))
    )
    return AdminOverviewRead(
        admin=admin.phone,
        total_users=total_users or 0,
        live_sessions=live_sessions or 0,
    )


def _render_admin_orders_page(
    request: Request,
    orders: list[Order],
    phone: str,
    current_status: str | None = None,
) -> HTMLResponse:
    """Render the admin order fulfillment listing page."""
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
                "recipient_phone": o.recipient_phone,
                "tracking_code": o.tracking_code,
            }
        )
    return templates.TemplateResponse(
        request=request,
        name="admin_orders.html",
        context={
            "phone": phone,
            "orders": view_orders,
            "current_status": current_status,
        },
    )


def _render_admin_order_detail_page(
    request: Request,
    order: Order,
    phone: str,
    success_message: str | None = None,
    error_message: str | None = None,
) -> HTMLResponse:
    """Render the admin order fulfillment detail page."""
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

    order_dict = {
        "id": str(order.id),
        "status": order.status,
        "status_label": label,
        "status_badge_class": badge,
        "total_toman_formatted": format_toman_price(order.total_toman),
        "created_at_formatted": formatted_date,
        "recipient_name": order.recipient_name,
        "recipient_phone": order.recipient_phone,
        "delivery_address": order.delivery_address,
        "tracking_code": order.tracking_code,
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_order_detail.html",
        context={
            "phone": phone,
            "order": order_dict,
            "items": items_view,
            "success_message": success_message,
            "error_message": error_message,
        },
    )


@router.get("/orders", response_model=None)
async def admin_list_orders(
    request: Request,
    staff: RequireStaffDep,
    db: DbSessionDep,
    status: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[AdminOrderListItem] | HTMLResponse:
    """List orders for administrative fulfillment (HTML or JSON)."""
    accept = request.headers.get("accept", "")
    fmt = request.query_params.get("format")
    is_json = fmt == "json" or (accept == "application/json" and fmt != "html")

    orders_seq = await list_all_orders(db, status=status, limit=limit, offset=offset)
    orders = list(orders_seq)

    if is_json:
        return [
            AdminOrderListItem(
                id=o.id,
                user_id=o.user_id,
                status=o.status,
                total_toman=o.total_toman,
                recipient_name=o.recipient_name,
                recipient_phone=o.recipient_phone,
                tracking_code=o.tracking_code,
                items_count=len(o.items),
                created_at=o.created_at,
            )
            for o in orders
        ]

    return _render_admin_orders_page(
        request=request,
        orders=orders,
        phone=staff.phone,
        current_status=status,
    )


@router.get("/orders/{order_id}", response_model=None)
async def admin_read_order(
    request: Request,
    order_id: Annotated[uuid.UUID, Path()],
    staff: RequireStaffDep,
    db: DbSessionDep,
) -> AdminOrderRead | HTMLResponse:
    """Read full order details for administrative fulfillment view (HTML or JSON)."""
    accept = request.headers.get("accept", "")
    fmt = request.query_params.get("format")
    is_json = fmt == "json" or (accept == "application/json" and fmt != "html")

    order = await load_order(db, order_id)
    if order is None:
        raise HTTPException(status_code=404, detail="order not found")

    if is_json:
        return AdminOrderRead(
            id=order.id,
            user_id=order.user_id,
            status=order.status,
            total_toman=order.total_toman,
            total_rial=order.total_rial,
            recipient_name=order.recipient_name,
            recipient_phone=order.recipient_phone,
            delivery_address=order.delivery_address,
            tracking_code=order.tracking_code,
            created_at=order.created_at,
            items=[
                AdminOrderItemRead(
                    product_id=it.product_id,
                    product_name=it.product_name,
                    quantity=it.quantity,
                    unit_price_toman=it.unit_price_toman,
                )
                for it in order.items
            ],
        )

    return _render_admin_order_detail_page(request, order, staff.phone)


@router.post("/orders/{order_id}/status", response_model=None)
async def admin_update_order_status(
    request: Request,
    order_id: Annotated[uuid.UUID, Path()],
    staff: RequireStaffDep,
    db: DbSessionDep,
    status: Annotated[str | None, Form()] = None,
    tracking_code: Annotated[str | None, Form()] = None,
) -> AdminOrderRead | HTMLResponse | RedirectResponse:
    """Update order fulfillment status (paid -> shipped -> delivered) and tracking code."""
    accept = request.headers.get("accept", "")
    content_type = request.headers.get("content-type", "")
    is_json_request = "application/json" in content_type

    target_status = status
    target_tracking_code = tracking_code

    if is_json_request:
        try:
            body = await request.json()
            payload = AdminOrderUpdate.model_validate(body)
            target_status = payload.status
            target_tracking_code = payload.tracking_code
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid JSON payload: {exc}") from exc

    order_before = await load_order(db, order_id)
    if order_before is None:
        raise HTTPException(status_code=404, detail="order not found")

    status_before = order_before.status

    try:
        updated_order = await update_order_fulfillment(
            db,
            order_id=order_id,
            new_status=target_status,
            tracking_code=target_tracking_code,
        )
    except OrderError as exc:
        if is_json_request or ("text/html" not in accept):
            raise HTTPException(status_code=400, detail=exc.detail) from exc
        return _render_admin_order_detail_page(
            request, order_before, staff.phone, error_message=exc.detail
        )

    # If order moved to shipped, send SMS notification
    if target_status == "shipped" and status_before != "shipped":
        notify_phone = updated_order.recipient_phone or (
            updated_order.user.phone if updated_order.user else None
        )
        if notify_phone:
            try:
                send_order_shipped(
                    notify_phone,
                    updated_order.id,
                    updated_order.tracking_code,
                )
            except Exception:
                pass

    if is_json_request or (
        "application/json" in accept and "text/html" not in accept
    ):
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

    return RedirectResponse(
        url=f"/admin/orders/{order_id}",
        status_code=303,
    )


@router.patch("/orders/{order_id}", response_model=AdminOrderRead)
async def admin_patch_order_fulfillment(
    order_id: Annotated[uuid.UUID, Path()],
    payload: AdminOrderUpdate,
    staff: RequireStaffDep,
    db: DbSessionDep,
) -> AdminOrderRead:
    """RESTful API to update order fulfillment status and tracking code."""
    order_before = await load_order(db, order_id)
    if order_before is None:
        raise HTTPException(status_code=404, detail="order not found")

    status_before = order_before.status

    try:
        updated_order = await update_order_fulfillment(
            db,
            order_id=order_id,
            new_status=payload.status,
            tracking_code=payload.tracking_code,
        )
    except OrderError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc

    if payload.status == "shipped" and status_before != "shipped":
        notify_phone = updated_order.recipient_phone or (
            updated_order.user.phone if updated_order.user else None
        )
        if notify_phone:
            try:
                send_order_shipped(
                    notify_phone,
                    updated_order.id,
                    updated_order.tracking_code,
                )
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


__all__ = [
    "admin_list_orders",
    "admin_overview",
    "admin_patch_order_fulfillment",
    "admin_read_order",
    "admin_update_order_status",
    "router",
]
