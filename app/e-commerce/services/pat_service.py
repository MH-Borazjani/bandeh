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
"""Personal access tokens (PATs) for the MCP server.

A customer creates a token in /auth/account and pastes it into their AI
app, which sends it as ``Authorization: Bearer <token>`` on every MCP
request. The design copies GitHub PATs: the plaintext is shown once and
never stored — only its SHA-256 hash lives in the database — and the
customer can revoke a token at any time. A token proves *who* the AI
acts for; it never authorises a payment on its own (the AI only ever
receives a Zibal payment link, and the person pays on the bank page).
"""

from __future__ import annotations

import datetime
import hashlib
import secrets
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.models.personal_access_token import PersonalAccessToken
from db.models.user import User

TOKEN_PREFIX = "gbpat_"
# Shown in the account page so the customer can tell tokens apart.
PREFIX_LEN = 12


def _hash_token(plaintext: str) -> str:
    """SHA-256 hex of the plaintext token (what the database stores)."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def _new_plaintext() -> str:
    """A fresh token value, never stored anywhere but the customer's screen."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


async def create_token(
    db: AsyncSession, user: User, name: str
) -> tuple[PersonalAccessToken, str]:
    """Create a token and return (record, plaintext). Show the plaintext once.

    The caller must commit the session (the route does it via get_db).
    """
    plaintext = _new_plaintext()
    record = PersonalAccessToken(
        user_id=user.id,
        name=name.strip()[:80] or "MCP token",
        token_prefix=plaintext[:PREFIX_LEN],
        token_hash=_hash_token(plaintext),
    )
    db.add(record)
    await db.flush()
    return record, plaintext


async def verify_token(db: AsyncSession, plaintext: str) -> User | None:
    """Return the token's owner, or None when the token is bad/revoked.

    A verified token also updates ``last_used_at`` (one flush; the
    caller commits).
    """
    if not plaintext or not plaintext.startswith(TOKEN_PREFIX):
        return None
    stmt = select(PersonalAccessToken).where(
        PersonalAccessToken.token_hash == _hash_token(plaintext)
    )
    record = (await db.execute(stmt)).scalar_one_or_none()
    if record is None or record.is_revoked:
        return None
    record.last_used_at = datetime.datetime.now(datetime.UTC)
    user = await db.get(User, record.user_id)
    if user is None or not user.is_active:
        return None
    return user


async def list_tokens(db: AsyncSession, user: User) -> list[PersonalAccessToken]:
    """All tokens of one customer, newest first (revoked ones included)."""
    stmt = (
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user.id)
        .order_by(PersonalAccessToken.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


async def revoke_token(db: AsyncSession, user: User, token_id: uuid.UUID) -> bool:
    """Revoke one of the customer's own tokens. False when it is not theirs."""
    record = await db.get(PersonalAccessToken, token_id)
    if record is None or record.user_id != user.id or record.is_revoked:
        return False
    record.revoked_at = datetime.datetime.now(datetime.UTC)
    await db.flush()
    return True
