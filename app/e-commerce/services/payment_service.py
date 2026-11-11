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
"""Payment service: start, callback, cancel, release expired holds.

The rules, all in one place:
- Stock is reserved in ONE transaction with SELECT ... FOR UPDATE:
  lock the product rows, re-check availability, bump ``reserved``,
  create the attempt with a fresh uuid4 idempotency key. Only then
  call Zibal. If Zibal refuses, the reservation is given back in the
  same breath (compensate, then re-raise) — never leave stock held
  for a payment that never started.
- Starting twice for one order reuses the live waiting attempt (same
  start URL). A new try only begins after the old one failed/expired.
- The callback parameters are a HINT, never the truth. Every callback
  verifies server-to-server. Paid happens exactly once: the attempt
  row is locked, an already-paid attempt returns at once with no
  changes, and the paid transition (attempt + order + stock move)
  commits in one transaction.
- Amount mismatch after a verify-100: do NOT mark paid. Release the
  hold and mark failed — the money question goes to manual review.
- Verify-201 (already verified): success without changes when the
  attempt is already paid; when it is still waiting, the same
  exactly-once paid transition runs (the money IS verified paid).
- The service owns its writes and commits them, so a failed gateway
  call can never roll back its own compensation through the request
  dependency's rollback.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.datetime_utils import utcnow
from db.models.order import Order, OrderStatus
from db.models.payment_attempt import PaymentAttempt, PaymentStatus
from db.models.product import Product
from integrations.zibal_client import (
    RESULT_ALREADY_VERIFIED,
    RESULT_NOT_PAID,
    RESULT_OK,
    ZibalClient,
    ZibalError,
)
from services.order_service import OrderError, load_order, load_order_for_user

# How long a stock hold lives before the release job frees it.
HOLD_MINUTES = 15


async def _lock_order_products(
    db: AsyncSession, order: Order
) -> dict[uuid.UUID, Product]:
    """Load the order's products with a row lock (FOR UPDATE).

    On Postgres this serialises two checkouts racing for the same
    stock. On SQLite the clause is ignored (SQLite has no row locks),
    which is why tests use one checkout at a time.
    """
    product_ids = [item.product_id for item in order.items]
    result = await db.execute(
        select(Product)
        .where(Product.id.in_(product_ids))
        .with_for_update()
    )
    return {p.id: p for p in result.scalars().all()}


async def _live_attempt(db: AsyncSession, order_id: uuid.UUID) -> PaymentAttempt | None:
    """The order's live waiting attempt, if one exists."""
    result = await db.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.order_id == order_id,
            PaymentAttempt.status == PaymentStatus.WAITING.value,
        )
    )
    return result.scalar_one_or_none()


async def _release_reservation(db: AsyncSession, order: Order) -> None:
    """Give reserved units back (reserved -= quantity). No commit here."""
    products = await _lock_order_products(db, order)
    for item in order.items:
        product = products.get(item.product_id)
        if product is not None:
            product.reserved = max(0, product.reserved - item.quantity)


async def _move_reserved_to_sold(db: AsyncSession, order: Order) -> None:
    """Payment confirmed: reserved units become sold (stock -= qty too)."""
    products = await _lock_order_products(db, order)
    for item in order.items:
        product = products.get(item.product_id)
        if product is not None:
            product.stock -= item.quantity
            product.reserved = max(0, product.reserved - item.quantity)


def _parse_paid_at(value: str | None) -> datetime.datetime | None:
    """Parse Zibal's paidAt defensively: a bad date must never lose a payment."""
    if not value:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    return parsed


async def start_payment(
    db: AsyncSession,
    order_id: uuid.UUID,
    user_id: uuid.UUID,
    zibal: ZibalClient,
    callback_url: str,
) -> PaymentAttempt:
    """Reserve stock, create the attempt, ask Zibal. Returns the attempt.

    Raises OrderError (order missing / not yours / not payable / short
    on stock) or ZibalError/ValueError (gateway refused — the hold is
    already released when those reach the caller).
    """
    order = await load_order_for_user(db, order_id, user_id)
    if order is None:
        raise OrderError("order not found")

    # Idempotent start first: a waiting order with a live attempt reuses
    # it (same start URL). Only an order with no live attempt must be
    # in CREATED state to start a new try.
    existing = await _live_attempt(db, order_id)
    if existing is not None:
        return existing
    if order.status != OrderStatus.CREATED.value:
        raise OrderError(f"order is {order.status}, not payable")

    products = await _lock_order_products(db, order)
    required_by_product: dict[uuid.UUID, tuple[int, str]] = {}
    for item in order.items:
        prev_qty, _ = required_by_product.get(item.product_id, (0, item.product_name))
        required_by_product[item.product_id] = (prev_qty + item.quantity, item.product_name)

    for product_id, (needed, product_name) in required_by_product.items():
        product = products.get(product_id)
        if product is None or needed > product.available:
            raise OrderError(
                f"only {product.available if product else 0} of "
                f"{product_name} available"
            )
    for product_id, (needed, _) in required_by_product.items():
        products[product_id].reserved += needed

    attempt = PaymentAttempt(
        order_id=order.id,
        idempotency_key=uuid.uuid4(),
        gateway="zibal",
        amount_rial=order.total_rial,
        status=PaymentStatus.CREATED.value,
    )
    db.add(attempt)
    await db.flush()

    try:
        track_id = await zibal.request_payment(
            amount_rial=order.total_rial,
            callback_url=callback_url,
            order_id=str(order.id),
            description=f"Bandeh order {order.id}",
        )
    except (ZibalError, ValueError):
        # Gateway refused: give the stock back, mark the try failed.
        await _release_reservation(db, order)
        attempt.status = PaymentStatus.FAILED.value
        order.status = OrderStatus.FAILED.value
        await db.commit()
        raise

    attempt.track_id = track_id
    attempt.status = PaymentStatus.WAITING.value
    order.status = OrderStatus.WAITING_PAYMENT.value
    await db.commit()
    return attempt


async def handle_callback(
    db: AsyncSession,
    track_id: int,
    order_id: str,
    zibal: ZibalClient,
) -> dict:
    """Handle Zibal's callback. Verifies server-to-server; params are hints.

    Returns a dict: {"status": paid | already_paid | failed | unknown |
    error, ...}. The browser's ?success&status are never used to decide.
    """
    result = await db.execute(
        select(PaymentAttempt)
        .where(PaymentAttempt.track_id == track_id)
        .with_for_update()
    )
    attempt = result.scalar_one_or_none()
    if attempt is None or str(attempt.order_id) != order_id:
        return {"status": "unknown", "track_id": track_id}

    # Exactly-once: an already-paid attempt changes nothing, ever.
    if attempt.status == PaymentStatus.PAID.value:
        return {
            "status": "already_paid",
            "order_id": str(attempt.order_id),
            "ref_number": attempt.ref_number,
        }

    verify = await zibal.verify_payment(track_id)
    order = await load_order(db, attempt.order_id)
    if order is None:  # the order vanished; nothing sane to do
        return {"status": "error", "track_id": track_id, "reason": "order_missing"}

    if verify.result in (RESULT_OK, RESULT_ALREADY_VERIFIED):
        if verify.amount is not None and verify.amount != attempt.amount_rial:
            # Paid, but the wrong amount: do NOT mark paid. Free the hold.
            await _release_reservation(db, order)
            attempt.status = PaymentStatus.FAILED.value
            order.status = OrderStatus.FAILED.value
            await db.commit()
            return {
                "status": "failed",
                "order_id": str(order.id),
                "reason": "amount_mismatch",
                "expected_rial": attempt.amount_rial,
                "received_rial": verify.amount,
            }
        attempt.status = PaymentStatus.PAID.value
        attempt.ref_number = verify.ref_number
        attempt.card_number = verify.card_number
        attempt.paid_at = _parse_paid_at(verify.paid_at)
        order.status = OrderStatus.PAID.value
        await _move_reserved_to_sold(db, order)
        await db.commit()
        return {
            "status": "paid",
            "order_id": str(order.id),
            "ref_number": attempt.ref_number,
        }

    if verify.result == RESULT_NOT_PAID:
        await _release_reservation(db, order)
        attempt.status = PaymentStatus.FAILED.value
        order.status = OrderStatus.FAILED.value
        await db.commit()
        return {"status": "failed", "order_id": str(order.id), "reason": "not_paid"}

    # 203 or anything else: gateway error — hold stays until expiry.
    return {"status": "error", "track_id": track_id, "result": verify.result}


async def cancel_payment(
    db: AsyncSession, order_id: uuid.UUID, user_id: uuid.UUID
) -> bool:
    """Cancel a payment in progress: free the hold, fail the order.

    Only a created or waiting order can be cancelled. Raises OrderError
    when the order is missing or not yours; returns False when it
    exists but cannot be cancelled (already paid, failed, expired).
    """
    order = await load_order_for_user(db, order_id, user_id)
    if order is None:
        raise OrderError("order not found")
    if order.status not in (OrderStatus.CREATED.value, OrderStatus.WAITING_PAYMENT.value):
        return False
    if order.status == OrderStatus.WAITING_PAYMENT.value:
        await _release_reservation(db, order)
    result = await db.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.order_id == order.id,
            PaymentAttempt.status.in_(
                [PaymentStatus.CREATED.value, PaymentStatus.WAITING.value]
            ),
        )
    )
    for attempt in result.scalars():
        attempt.status = PaymentStatus.FAILED.value
    order.status = OrderStatus.FAILED.value
    await db.commit()
    return True


async def release_expired_reservations(
    db: AsyncSession, hold_minutes: int = HOLD_MINUTES
) -> int:
    """Free stock holds older than ``hold_minutes``. Returns how many.

    Run on a schedule (see docs/payments-zibal.md for the cron line).
    The cutoff is compared in Python, not SQL, so naive SQLite
    timestamps and aware Postgres ones behave the same.
    """
    cutoff = utcnow() - datetime.timedelta(minutes=hold_minutes)
    result = await db.execute(
        select(PaymentAttempt).where(
            PaymentAttempt.status == PaymentStatus.WAITING.value
        )
    )
    count = 0
    for attempt in result.scalars().all():
        created_at = attempt.created_at
        if created_at.tzinfo is None:
            # SQLite stores no timezone; values were written as UTC.
            created_at = created_at.replace(tzinfo=datetime.timezone.utc)
        if created_at >= cutoff:
            continue
        order = await load_order(db, attempt.order_id)
        if order is None or order.status != OrderStatus.WAITING_PAYMENT.value:
            continue
        await _release_reservation(db, order)
        attempt.status = PaymentStatus.EXPIRED.value
        order.status = OrderStatus.EXPIRED.value
        count += 1
    if count:
        await db.commit()
    return count


__all__ = [
    "HOLD_MINUTES",
    "cancel_payment",
    "handle_callback",
    "release_expired_reservations",
    "start_payment",
]
