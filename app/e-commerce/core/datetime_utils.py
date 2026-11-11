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
"""Datetime helpers: one rule for the whole app — store UTC, show local.

Why this module exists: datetimes go wrong in exactly three ways, and
all three have bitten shops before —
1. Storing local time in the database, so the same instant means two
   different things on two servers.
2. Comparing a naive datetime (no timezone) with an aware one — Python
   refuses, and the error shows up at runtime, in production.
3. Showing the server's timezone to the user instead of theirs.

So: every timestamp in the database is timezone-aware UTC. Display
converts to Asia/Tehran at the last moment. These helpers make the rule
easy to follow and hard to break: naive datetimes are rejected loudly
instead of silently miscompared.
"""

from __future__ import annotations

import datetime
from zoneinfo import ZoneInfo

TEHRAN = ZoneInfo("Asia/Tehran")
UTC = datetime.timezone.utc

# Lifetimes used across the app (single source of truth for the numbers).
OTP_EXPIRY_SECONDS = 120  # an SMS code lives for 2 minutes
SESSION_EXPIRY_DAYS = 30  # a login session lives for 30 days


def utcnow() -> datetime.datetime:
    """Timezone-aware current time in UTC. Use this for every timestamp."""
    return datetime.datetime.now(UTC)


def now_tehran() -> datetime.datetime:
    """Timezone-aware current time in Asia/Tehran (display only)."""
    return datetime.datetime.now(TEHRAN)


def _require_aware(value: datetime.datetime, name: str) -> None:
    """Refuse naive datetimes loudly: a missing timezone is a bug, not a default."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware, got naive {value!r}")


def to_tehran(value: datetime.datetime) -> datetime.datetime:
    """Convert an aware datetime to Asia/Tehran time."""
    _require_aware(value, "value")
    return value.astimezone(TEHRAN)


def expires_at(seconds: int, now: datetime.datetime | None = None) -> datetime.datetime:
    """Return the expiry timestamp ``seconds`` from ``now`` (default: now)."""
    base = now if now is not None else utcnow()
    _require_aware(base, "now")
    return base + datetime.timedelta(seconds=seconds)


def is_expired(expires_at_value: datetime.datetime, now: datetime.datetime | None = None) -> bool:
    """True when ``expires_at_value`` is in the past (or exactly now)."""
    _require_aware(expires_at_value, "expires_at_value")
    current = now if now is not None else utcnow()
    _require_aware(current, "now")
    return current >= expires_at_value


def otp_is_expired(expires_at_value: datetime.datetime) -> bool:
    """True when an OTP code (120s lifetime) is dead. Thin wrapper, one name to remember."""
    return is_expired(expires_at_value)


def session_is_expired(expires_at_value: datetime.datetime) -> bool:
    """True when a login session (30-day lifetime) is dead."""
    return is_expired(expires_at_value)


def format_tehran(value: datetime.datetime) -> str:
    """Format an aware datetime for display in Tehran time: ``2026-11-11 14:30``."""
    return to_tehran(value).strftime("%Y-%m-%d %H:%M")


__all__ = [
    "OTP_EXPIRY_SECONDS",
    "SESSION_EXPIRY_DAYS",
    "TEHRAN",
    "UTC",
    "expires_at",
    "format_tehran",
    "is_expired",
    "now_tehran",
    "otp_is_expired",
    "session_is_expired",
    "to_tehran",
    "utcnow",
]
