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
"""Refresh-token service: issue, verify, rotate, revoke.

The flow, in one paragraph: login issues a long-lived refresh token
(30 days) alongside the session. When the browser needs a fresh session
it presents the refresh token; the service verifies it, revokes it, and
issues a brand-new one (rotation). A token is therefore useful at most
once — if a stolen token is ever presented after rotation, the token is
already revoked, and that reuse is treated as theft: every refresh token
of that user is revoked at once.

Only SHA-256 hashes are stored. The plaintext token exists only in the
browser's HttpOnly cookie and in the one response that issued it — the
database can never leak a usable token.
"""

from __future__ import annotations

import datetime
import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.refresh_token import RefreshToken
from db.models.user import User

# A refresh token lives 30 days — the same horizon as the session cookie.
# Rotation (not expiry) is what bounds the damage of a stolen token.
REFRESH_TOKEN_LIFETIME = datetime.timedelta(days=30)


def generate_refresh_token() -> str:
    """One fresh plaintext refresh token. 384 bits of entropy."""
    return secrets.token_urlsafe(48)


def hash_refresh_token(token: str) -> str:
    """SHA-256 hex of a token — the only form ever stored or looked up."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def issue_refresh_token(
    db: AsyncSession, user: User
) -> tuple[str, RefreshToken]:
    """Create a refresh token for ``user``. Returns (plaintext, record).

    Callers must commit (the ``get_db`` dependency does that) — the
    plaintext is returned now because it can never be read back.
    """
    token = generate_refresh_token()
    record = RefreshToken(
        token_hash=hash_refresh_token(token),
        user_id=user.id,
        expires_at=datetime.datetime.now(datetime.UTC) + REFRESH_TOKEN_LIFETIME,
    )
    db.add(record)
    await db.flush()
    return token, record


async def find_refresh_token(
    db: AsyncSession, token: str, *, for_update: bool = False
) -> RefreshToken | None:
    """Look a token up by hash, whatever its state (valid, revoked, expired).

    The route needs the record itself — not just "valid or not" — because
    a revoked token presented again is a theft signal, not a plain miss.
    """
    stmt = select(RefreshToken).where(
        RefreshToken.token_hash == hash_refresh_token(token)
    )
    if for_update:
        stmt = stmt.with_for_update()
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def rotate_refresh_token(
    db: AsyncSession, record: RefreshToken
) -> tuple[str, RefreshToken]:
    """Revoke ``record`` and issue its replacement. Returns (plaintext, record).

    Must only be called on a record that is still valid — the caller
    checks expiry/revocation first. The old and new rows are one
    transaction (the ``get_db`` dependency commits), so there is no
    window where both tokens work.
    """
    record.is_revoked = True
    record.last_used_at = datetime.datetime.now(datetime.UTC)
    return await issue_refresh_token(db, record.user)


async def revoke_refresh_token(db: AsyncSession, token: str) -> bool:
    """Revoke one token by its plaintext. True when a row was revoked."""
    record = await find_refresh_token(db, token)
    if record is None or record.is_revoked:
        return False
    record.is_revoked = True
    await db.flush()
    return True


async def revoke_all_refresh_tokens(db: AsyncSession, user_id: uuid.UUID) -> int:
    """Revoke every live refresh token of one user (logout-everywhere / theft).

    Returns how many tokens were revoked.
    """
    result = await db.execute(
        select(RefreshToken).where(
            RefreshToken.user_id == user_id,
            RefreshToken.is_revoked.is_(False),
        )
    )
    count = 0
    for record in result.scalars():
        record.is_revoked = True
        count += 1
    if count:
        await db.flush()
    return count


__all__ = [
    "REFRESH_TOKEN_LIFETIME",
    "find_refresh_token",
    "generate_refresh_token",
    "hash_refresh_token",
    "issue_refresh_token",
    "revoke_all_refresh_tokens",
    "revoke_refresh_token",
    "rotate_refresh_token",
]
