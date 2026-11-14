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

Runs a background task to periodically invoke ``release_expired_reservations``
so that 15-minute stock holds from abandoned payment attempts are freed automatically.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from db.session import get_session_factory
from services.payment_service import HOLD_MINUTES, release_expired_reservations

logger = logging.getLogger("bandeh.scheduler")


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


__all__ = ["ReservationCleanupTask"]
