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
"""Refresh-token model: the long-lived credential that mints new sessions.

Why a second token at all? The signed session cookie identifies the
browser for 30 days, but when it is gone (cleared, expired, another
device) the user would have to type an OTP again. A refresh token lets
the browser silently mint a fresh session instead — one credential whose
only job is "give me a new session".

Security rules, all enforced in services/refresh_service.py:
- Only the SHA-256 hash is stored here; the plaintext token exists only
  in the browser's HttpOnly cookie and in the one response that issued it.
- Every use ROTATES the token: the old one is revoked, a new one is
  issued. A stolen token is useful at most once.
- A revoked token presented again is a theft signal: the whole token
  family for that user is revoked (logout-everywhere, automatically).
"""

from __future__ import annotations

import datetime
from typing import TYPE_CHECKING
import uuid

from sqlalchemy import Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.base import Base, TimestampMixin

if TYPE_CHECKING:
    from db.models.user import User


class RefreshToken(Base, TimestampMixin):
    """One refresh token. One row per login device, roughly."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        primary_key=True,
        default=uuid.uuid4,
    )
    # SHA-256 hex of the plaintext token. Unique: one hash, one row.
    token_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        index=True,
        nullable=False,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    is_revoked: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    user: Mapped[User] = relationship(
        "User",
        back_populates="refresh_tokens",
        lazy="joined",
    )

    @property
    def is_valid(self) -> bool:
        """True when the token is neither revoked nor expired."""
        now = datetime.datetime.now(datetime.timezone.utc)
        expires_at = self.expires_at
        if expires_at.tzinfo is None:
            # SQLite stores no timezone; values were written as UTC.
            expires_at = expires_at.replace(tzinfo=datetime.timezone.utc)
        return not self.is_revoked and expires_at > now

    def __repr__(self) -> str:
        return f"<RefreshToken user_id={self.user_id} revoked={self.is_revoked}>"


__all__ = ["RefreshToken"]
