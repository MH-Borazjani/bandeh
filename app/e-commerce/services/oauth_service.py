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
"""Link a Google identity to a shop user (Google OAuth login).

One Google account links to exactly one shop user (``google_sub`` is
unique). Phone stays the shop's identity: a Google login whose account
has no phone yet must finish the phone + OTP step once, and the OTP
verify step links the Google identity to that phone user.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.user import User
from integrations.google_oauth import GoogleUserInfo


async def get_user_by_google_sub(
    db: AsyncSession, google_sub: str
) -> User | None:
    """Retrieve the shop user linked to this Google account, if any."""
    stmt = select(User).where(User.google_sub == google_sub)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_user_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    """Retrieve a user by unique UUID primary key."""
    stmt = select(User).where(User.id == user_id)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def link_google_account(
    db: AsyncSession, user: User, info: GoogleUserInfo
) -> User:
    """Link a Google identity to an existing shop user.

    Refuses when the Google account is already linked to a DIFFERENT
    user — one Google account must never merge two shop accounts.
    """
    existing = await get_user_by_google_sub(db, info.sub)
    if existing is not None and existing.id != user.id:
        raise ValueError("This Google account is linked to another user")
    user.google_sub = info.sub
    user.google_email = info.email
    if user.first_name is None and info.first_name:
        user.first_name = info.first_name
    if user.last_name is None and info.last_name:
        user.last_name = info.last_name
    await db.flush()
    return user


__all__ = [
    "get_user_by_google_sub",
    "get_user_by_id",
    "link_google_account",
]
