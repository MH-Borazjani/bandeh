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
"""SMS service: async orchestration for every SMS the shop sends.

This is the ONLY place that writes ``sms_log`` rows. Every send goes
through :func:`log_and_send`: insert a row as ``queued`` and commit,
call the provider, then mark the row ``sent`` (with the provider
message id) or ``failed`` and re-raise. A row therefore exists for
every attempted send, even when the provider call blows up.

PATTERN PATH (the only path the helpers use): every message is a
panel pattern sent over the shared service line via
``MeliPayamakClient.send_pattern`` (POST /SendSMS/BaseServiceNumber).
The code sends only VARIABLES — the pattern text lives in the panel.
One bodyId per message type comes from env (never hardcoded):

- otp -> MELI_SMS_OTP_BODY_ID, variables [code]
- order_paid -> MELI_SMS_ORDER_BODY_ID, variables [order id, total toman]
- order_tracking -> MELI_SMS_TRACKING_BODY_ID, variables [order id, tracking code]
- owner_order_paid -> MELI_SMS_OWNER_BODY_ID, variables [order id, total toman]
- low_stock -> MELI_SMS_OWNER_BODY_ID (owner pattern), variables [product name, remaining]
- payment_reminder -> MELI_SMS_ORDER_BODY_ID (order pattern), variables [order id]

The ``sms_log.text`` column stores the ';'-joined variables sent on
the wire, and ``sms_log.body_id`` stores the pattern code used.
A missing bodyId with a configured client raises :class:`SmsConfigError`
(a clear error naming the env var) instead of sending with a wrong id;
with an unconfigured client the dev fallback applies instead (no real
send, row logged as sent with provider id "dev").

The plain-SendSMS advertising line is a DOCUMENTED FALLBACK only
(see docs/sms-melipayamak.md): it cannot reach blacklisted numbers
and its texts must end with "11". The helpers never call it.

ENAMAD GATE: OTP via the service line needs no documents (only the
site content must be approved) — OTP is unblocked. The other service
messages need Enamad + a payment gateway; the shop has no Enamad yet,
so live use of the non-OTP patterns waits on that (see the doc).

Sync constraint: :func:`send_otp_sync` is the sync entry point for
the OTP path (``request_otp`` in ``services.auth_service`` is sync,
and so is the ``/auth/otp/request`` route). When SMS is configured it
runs the async send with ``asyncio.run()`` on its OWN session from
``get_session_factory()``. That is only safe because ``request_otp``
is ever called from sync code — the sync OTP route and the sync
tests. Never call ``send_otp_sync`` from inside a running event loop
(``asyncio.run()`` raises there); async callers use
:func:`send_otp_sms` directly with their own session.

Dev fallback: when the client is unconfigured, the provider calls
print "[DEV SMS]" and record in ``_DEV_SENT_MESSAGES`` (see
``integrations.sms``) and no real SMS leaves the machine. The OTP dev
path also populates ``_DEV_LAST_CODE`` so the OTP tests keep working
— and writes NO sms_log row, because there is no database session in
the sync OTP path when unconfigured.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.order import Order
from db.models.sms_log import SmsLog, SmsMessageType, SmsStatus
from services import stock_service
from integrations.sms import (
    _DEV_LAST_CODE,
    _DEV_SENT_MESSAGES,
    MeliPayamakClient,
    get_sms_client,
)

logger = logging.getLogger("bandeh.sms_service")

# Alert the owner when a product's sellable units drop to this or fewer.
LOW_STOCK_THRESHOLD = 2

# Pattern kind -> env var holding its bodyId (for clear error messages).
_BODY_ID_ENV_VARS: dict[str, str] = {
    "otp": "MELI_SMS_OTP_BODY_ID",
    "order": "MELI_SMS_ORDER_BODY_ID",
    "tracking": "MELI_SMS_TRACKING_BODY_ID",
    "owner": "MELI_SMS_OWNER_BODY_ID",
}


class SmsConfigError(Exception):
    """A pattern bodyId the shop needs is not configured.

    Raised instead of sending with a wrong (or guessed) pattern id:
    create the pattern in the panel (Special tools -> Service web
    service), wait for approval, and set its MELI_SMS_*_BODY_ID env var.
    """


def _get_body_id(kind: str) -> int | None:
    """Pattern bodyId for a message kind, from settings (env)."""
    try:
        from env_settings import get_settings

        settings = get_settings()
        return {
            "otp": settings.meli_sms_otp_body_id,
            "order": settings.meli_sms_order_body_id,
            "tracking": settings.meli_sms_tracking_body_id,
            "owner": settings.meli_sms_owner_body_id,
        }[kind]
    except Exception:
        return None


def _resolve_body_id(kind: str, client: MeliPayamakClient) -> int | None:
    """BodyId for ``kind``, or None on the dev path.

    Raises SmsConfigError when the client IS configured (a real send
    would go out) but the pattern code is missing. Unconfigured
    clients fall through to the dev fallback, which needs no bodyId.
    """
    body_id = _get_body_id(kind)
    if body_id is None and client.is_configured:
        raise SmsConfigError(
            f"No pattern bodyId configured for {kind}: create the pattern "
            f"in the panel and set {_BODY_ID_ENV_VARS[kind]}"
        )
    return body_id


def _get_owner_phone() -> str | None:
    """Shop owner phone (MELI_SMS_OWNER_PHONE) or None when unset."""
    try:
        from env_settings import get_settings

        return get_settings().meli_sms_owner_phone
    except Exception:
        return None


def _customer_phone(order: Order) -> str | None:
    """The phone an order's customer SMS goes to."""
    if order.recipient_phone:
        return order.recipient_phone
    user = order.user
    return user.phone if user is not None else None


async def _already_logged(
    db: AsyncSession, message_type: str, reference: str
) -> bool:
    """True when a row of this type already exists for this reference."""
    row_id = await db.scalar(
        select(SmsLog.id).where(
            SmsLog.message_type == message_type,
            SmsLog.reference == reference,
        )
    )
    return row_id is not None


async def log_and_send(
    db: AsyncSession,
    *,
    phone: str,
    message_type: str,
    text: str,
    reference: str | None = None,
    body_id: int | None = None,
    send: Callable[[], Awaitable[str]],
) -> SmsLog:
    """Log first, then send: the row is "queued" before the provider call.

    Commits the queued row, awaits ``send()`` (which returns the
    provider message id), then marks the row "sent" with that id and
    commits. On any exception the row is marked "failed", committed,
    and the exception re-raised.
    """
    row = SmsLog(
        phone=phone,
        message_type=message_type,
        text=text,
        reference=reference,
        body_id=body_id,
        status=SmsStatus.QUEUED,
    )
    db.add(row)
    await db.commit()
    try:
        provider_id = await send()
    except Exception:
        row.status = SmsStatus.FAILED
        await db.commit()
        raise
    row.status = SmsStatus.SENT
    row.provider_message_id = str(provider_id)
    await db.commit()
    return row


async def send_otp_sms(
    db: AsyncSession,
    phone: str,
    code: str,
    *,
    client: MeliPayamakClient | None = None,
) -> SmsLog:
    """Send the login OTP via the OTP pattern and log it."""
    sms_client = client or get_sms_client()
    body_id = _resolve_body_id("otp", sms_client)
    variables = [code]
    return await log_and_send(
        db,
        phone=phone,
        message_type=SmsMessageType.OTP,
        text=";".join(variables),
        body_id=body_id,
        send=lambda: sms_client.send_pattern(phone, body_id, variables),
    )


async def send_order_paid_sms(
    db: AsyncSession,
    order: Order,
    *,
    client: MeliPayamakClient | None = None,
) -> SmsLog | None:
    """Tell the customer their order is paid (order pattern)."""
    phone = _customer_phone(order)
    if not phone:
        logger.warning("No phone for paid order %s; order-paid SMS skipped", order.id)
        return None
    sms_client = client or get_sms_client()
    body_id = _resolve_body_id("order", sms_client)
    variables = [str(order.id), f"{order.total_toman:,}"]
    return await log_and_send(
        db,
        phone=phone,
        message_type=SmsMessageType.ORDER_PAID,
        text=";".join(variables),
        reference=str(order.id),
        body_id=body_id,
        send=lambda: sms_client.send_pattern(phone, body_id, variables),
    )


async def send_tracking_sms(
    db: AsyncSession,
    order: Order,
    *,
    client: MeliPayamakClient | None = None,
) -> SmsLog | None:
    """Tell the customer their order shipped, with the tracking code."""
    phone = _customer_phone(order)
    if not phone:
        logger.warning("No phone for shipped order %s; tracking SMS skipped", order.id)
        return None
    sms_client = client or get_sms_client()
    body_id = _resolve_body_id("tracking", sms_client)
    variables = [str(order.id), order.tracking_code or "در انتظار صدور"]
    return await log_and_send(
        db,
        phone=phone,
        message_type=SmsMessageType.ORDER_TRACKING,
        text=";".join(variables),
        reference=str(order.id),
        body_id=body_id,
        send=lambda: sms_client.send_pattern(phone, body_id, variables),
    )


async def send_owner_order_paid_sms(
    db: AsyncSession,
    order: Order,
    *,
    client: MeliPayamakClient | None = None,
) -> SmsLog | None:
    """Alert the owner that a new order was paid (owner pattern).

    Skipped (warning logged, no crash, no row) when no owner phone is
    configured.
    """
    phone = _get_owner_phone()
    if not phone:
        logger.warning(
            "No owner phone configured; owner order-paid SMS skipped for %s",
            order.id,
        )
        return None
    sms_client = client or get_sms_client()
    body_id = _resolve_body_id("owner", sms_client)
    variables = [str(order.id), f"{order.total_toman:,}"]
    return await log_and_send(
        db,
        phone=phone,
        message_type=SmsMessageType.OWNER_ORDER_PAID,
        text=";".join(variables),
        reference=str(order.id),
        body_id=body_id,
        send=lambda: sms_client.send_pattern(phone, body_id, variables),
    )


async def send_low_stock_sms(
    db: AsyncSession,
    product_id: uuid.UUID,
    product_name: str,
    available: int,
    *,
    client: MeliPayamakClient | None = None,
) -> SmsLog | None:
    """Alert the owner that a product is nearly out of stock — ONCE per product.

    Uses the OWNER pattern (no separate low-stock pattern). Returns
    None (no row, no send) when an alert for this product was already
    logged, or when no owner phone is configured. The name comes from
    the frozen order item and the count from the stock table — the
    caller already looked both up.
    """
    phone = _get_owner_phone()
    if not phone:
        logger.warning(
            "No owner phone configured; low-stock SMS skipped for product %s",
            product_id,
        )
        return None
    if await _already_logged(db, SmsMessageType.LOW_STOCK, str(product_id)):
        return None
    sms_client = client or get_sms_client()
    body_id = _resolve_body_id("owner", sms_client)
    variables = [product_name, str(available)]
    return await log_and_send(
        db,
        phone=phone,
        message_type=SmsMessageType.LOW_STOCK,
        text=";".join(variables),
        reference=str(product_id),
        body_id=body_id,
        send=lambda: sms_client.send_pattern(phone, body_id, variables),
    )


async def send_failed_payment_reminder_sms(
    db: AsyncSession,
    order: Order,
    *,
    client: MeliPayamakClient | None = None,
) -> SmsLog | None:
    """Remind the customer their payment did not complete — ONCE per order.

    Uses the ORDER pattern (no separate reminder pattern). Returns None
    (no row, no send) when a reminder for this order was already
    logged, or when the order has no phone.
    """
    if await _already_logged(
        db, SmsMessageType.PAYMENT_REMINDER, str(order.id)
    ):
        return None
    phone = _customer_phone(order)
    if not phone:
        logger.warning(
            "No phone for failed order %s; payment reminder skipped", order.id
        )
        return None
    sms_client = client or get_sms_client()
    body_id = _resolve_body_id("order", sms_client)
    variables = [str(order.id)]
    return await log_and_send(
        db,
        phone=phone,
        message_type=SmsMessageType.PAYMENT_REMINDER,
        text=";".join(variables),
        reference=str(order.id),
        body_id=body_id,
        send=lambda: sms_client.send_pattern(phone, body_id, variables),
    )


async def check_low_stock_after_payment(
    db: AsyncSession,
    order: Order,
    *,
    client: MeliPayamakClient | None = None,
) -> None:
    """After a payment, alert the owner for each just-bought product at/below threshold.

    The name is the frozen order-item name; the count comes from the
    stock table (the payment just moved reserved units to sold).
    """
    for item in order.items:
        available = await stock_service.available_for(db, item.product_id)
        if available <= LOW_STOCK_THRESHOLD:
            await send_low_stock_sms(
                db,
                item.product_id,
                item.product_name,
                available,
                client=client,
            )


def send_otp_sync(phone: str, code: str) -> None:
    """Sync entry point for the OTP path (see module docstring).

    Unconfigured client -> dev fallback: populate ``_DEV_LAST_CODE``
    and ``_DEV_SENT_MESSAGES``, write NO database row. Configured
    client -> run the async send on a fresh session of its own.
    Only ever call this from sync code (the sync OTP route, sync
    tests); async callers use :func:`send_otp_sms`.
    """
    sms_client = get_sms_client()
    if not sms_client.is_configured:
        print(f"[DEV SMS] code for {phone}: {code}")
        _DEV_LAST_CODE[phone] = code
        _DEV_SENT_MESSAGES.append({"phone": phone, "code": code, "type": "otp"})
        return

    async def _run() -> None:
        from db.session import get_session_factory

        factory = get_session_factory()
        async with factory() as session:
            await send_otp_sms(session, phone, code, client=sms_client)

    asyncio.run(_run())


__all__ = [
    "LOW_STOCK_THRESHOLD",
    "SmsConfigError",
    "check_low_stock_after_payment",
    "log_and_send",
    "send_failed_payment_reminder_sms",
    "send_low_stock_sms",
    "send_order_paid_sms",
    "send_otp_sms",
    "send_otp_sync",
    "send_owner_order_paid_sms",
    "send_tracking_sms",
]
