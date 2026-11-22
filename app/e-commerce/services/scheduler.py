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
"""Background scheduler for recurring tasks.

Runs background tasks to periodically:
- invoke ``release_expired_reservations`` so that 15-minute stock
  holds from abandoned payment attempts are freed automatically;
- send a one-time SMS reminder for orders whose payment failed
  (``FailedPaymentReminderTask``).
"""

from __future__ import annotations

import asyncio
import datetime
import logging
from contextlib import suppress

from sqlalchemy import select

from core.datetime_utils import utcnow
from db.models.order import Order, OrderStatus
from db.models.sms_log import SmsLog, SmsMessageType
from db.session import get_session_factory
from integrations.sms import MeliPayamakClient
from services.payment_service import HOLD_MINUTES, release_expired_reservations

logger = logging.getLogger("bandeh.scheduler")

# A failed payment gets ONE reminder SMS, no sooner than this after
# the failure and no later than this (the upper bound stops a first
# run from flooding owners of ancient failed orders).
REMINDER_DELAY_MINUTES = 15
REMINDER_MAX_AGE_HOURS = 24


class ReservationCleanupTask:
    """Periodic timer that frees expired stock reservations."""

    def __init__(
        self,
        interval_seconds: float = 60.0,
        hold_minutes: int = HOLD_MINUTES,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.hold_minutes = hold_minutes
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def run_once(self) -> int:
        """Execute one cleanup pass and return count of freed holds."""
        factory = get_session_factory()
        async with factory() as session:
            count = await release_expired_reservations(session, hold_minutes=self.hold_minutes)
            if count > 0:
                logger.info("Released %d expired stock reservations", count)
            return count

    async def _loop(self) -> None:
        """Continuous execution loop with error recovery."""
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("Reservation cleanup task failed: %s", exc)

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def start(self) -> asyncio.Task[None]:
        """Start the background timer loop if not already running."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        """Gracefully stop and cancel the background task."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None


class FailedPaymentReminderTask:
    """Periodic timer that reminds customers of failed payments, once each.

    Each pass finds orders with status "failed" whose last update is
    between REMINDER_DELAY_MINUTES and REMINDER_MAX_AGE_HOURS ago and
    that have no payment_reminder sms_log row yet, then sends the
    reminder through ``services.sms_service`` (which logs before
    sending, so a second pass is a no-op for the same order). Orders
    with no phone are skipped.

    NOTE: status "failed" also covers user-cancelled orders — the
    payment-attempt records carry no reason that distinguishes a user
    cancel from a gateway failure (``cancel_payment`` marks the
    attempt FAILED just like a refused payment), so the reminder goes
    to all failed orders. If that proves noisy, the fix is a reason
    field on PaymentAttempt, not a filter here.
    """

    def __init__(
        self,
        interval_seconds: float = 60.0,
        client: MeliPayamakClient | None = None,
    ) -> None:
        self.interval_seconds = interval_seconds
        self.client = client
        self._task: asyncio.Task[None] | None = None
        self._running = False

    async def run_once(self) -> int:
        """Execute one reminder pass and return how many reminders went out."""
        from services.sms_service import send_failed_payment_reminder_sms

        factory = get_session_factory()
        now = utcnow()
        earliest = now - datetime.timedelta(hours=REMINDER_MAX_AGE_HOURS)
        latest = now - datetime.timedelta(minutes=REMINDER_DELAY_MINUTES)
        count = 0
        async with factory() as session:
            result = await session.execute(
                select(Order).where(Order.status == OrderStatus.FAILED.value)
            )
            for order in result.scalars().all():
                updated_at = order.updated_at
                if updated_at.tzinfo is None:
                    # SQLite stores no timezone; values were written as UTC.
                    updated_at = updated_at.replace(tzinfo=datetime.UTC)
                if not earliest <= updated_at <= latest:
                    continue
                already = await session.scalar(
                    select(SmsLog.id).where(
                        SmsLog.message_type == SmsMessageType.PAYMENT_REMINDER,
                        SmsLog.reference == str(order.id),
                    )
                )
                if already is not None:
                    continue
                phone = order.recipient_phone or (
                    order.user.phone if order.user else None
                )
                if not phone:
                    continue
                await send_failed_payment_reminder_sms(
                    session, order, client=self.client
                )
                count += 1
        if count > 0:
            logger.info("Sent %d failed-payment reminders", count)
        return count

    async def _loop(self) -> None:
        """Continuous execution loop with error recovery."""
        while self._running:
            try:
                await self.run_once()
            except Exception as exc:
                logger.error("Failed-payment reminder task failed: %s", exc)

            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break

    def start(self) -> asyncio.Task[None]:
        """Start the background timer loop if not already running."""
        if self._task is None or self._task.done():
            self._running = True
            self._task = asyncio.create_task(self._loop())
        return self._task

    async def stop(self) -> None:
        """Gracefully stop and cancel the background task."""
        self._running = False
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None


__all__ = ["FailedPaymentReminderTask", "ReservationCleanupTask"]
