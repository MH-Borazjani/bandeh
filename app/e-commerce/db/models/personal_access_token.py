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
"""One personal access token (PAT) for the MCP server.

A customer creates a token in /auth/account and pastes it into their AI
app (ChatGPT / Gemini / Claude) as ``Authorization: Bearer <token>``.
Only the SHA-256 hash is stored — the plaintext is shown once at
creation and never again, like a GitHub PAT. Revoking sets
``revoked_at``; a revoked token never authenticates again.
"""

from __future__ import annotations

import datetime
import uuid

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from db.base import Base, TimestampMixin


class PersonalAccessToken(Base, TimestampMixin):
    """A revocable login token for one customer, used by AI apps only."""

    __tablename__ = "personal_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A short name the customer picks ("ChatGPT on my phone").
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    # First characters of the plaintext, shown in the list so the
    # customer can tell tokens apart without ever seeing the secret.
    token_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    # SHA-256 hex of the plaintext token; unique so one token can never
    # be created twice.
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    last_used_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None
