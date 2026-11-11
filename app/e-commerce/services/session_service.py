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
"""Session service layer: database session persistence, validation, and revocation."""

from __future__ import annotations

import datetime
import secrets
from typing import Any, cast
import uuid

from db.models.session import UserSession
from db.models.user import User
from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

# 30 days session persistence
SESSION_LIFETIME = datetime.timedelta(days=30)


def generate_session_token() -> str:
    """Generate a high-entropy cryptographically secure random session token."""
    return secrets.token_urlsafe(48)


async def create_database_session(
    db: AsyncSession,
    user: User,
    user_agent: str | None = None,
    ip_address: str | None = None,
) -> UserSession:
    """Create and persist a new session record in PostgreSQL."""
    now = datetime.datetime.now(datetime.timezone.utc)
    token = generate_session_token()

    session = UserSession(
        session_token=token,
        user_id=user.id,
        expires_at=now + SESSION_LIFETIME,
        is_revoked=False,
        user_agent=user_agent,
        ip_address=ip_address,
    )
    db.add(session)
    await db.flush()
    return session


async def get_valid_session(db: AsyncSession, session_token: str) -> UserSession | None:
    """Fetch an active session with its associated user.

    Validates that:
    1. Session token matches
    2. Session is not marked revoked
    3. Expiration date is in the future
    4. Attached user account is active
    """
    now = datetime.datetime.now(datetime.timezone.utc)
    stmt = (
        select(UserSession)
        .options(joinedload(UserSession.user))
        .where(
            UserSession.session_token == session_token,
            UserSession.is_revoked.is_(False),
            UserSession.expires_at > now,
        )
    )
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()

    if session and session.user and session.user.is_active:
        return session
    return None


async def revoke_session(db: AsyncSession, session_token: str) -> bool:
    """Mark a specific session as revoked in the database."""
    stmt = (
        update(UserSession)
        .where(UserSession.session_token == session_token)
        .values(is_revoked=True)
    )
    result = cast(CursorResult[Any], await db.execute(stmt))
    return (result.rowcount or 0) > 0


async def revoke_all_user_sessions(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke all sessions for a user (e.g., 'Log out of all devices')."""
    stmt = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.is_revoked.is_(False))
        .values(is_revoked=True)
    )
    result = cast(CursorResult[Any], await db.execute(stmt))
    return result.rowcount or 0


__all__ = [
    "SESSION_LIFETIME",
    "create_database_session",
    "generate_session_token",
    "get_valid_session",
    "revoke_all_user_sessions",
    "revoke_session",
]
