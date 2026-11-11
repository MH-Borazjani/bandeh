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
"""Guest-cart cookie management.

Provides helpers to read, create, and delete the guest cart cookie.
"""

from __future__ import annotations

import uuid

from fastapi import Response

CART_COOKIE_NAME = "bandeh_cart_id"
# 30 days: a guest cart survives across visits
CART_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _is_valid_cart_id(value: str | None) -> bool:
    """Validate whether the value is a valid UUID string."""
    if not value:
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def ensure_cart_id(
    cookie_value: str | None, response: Response, *, secure: bool = False
) -> str:
    """Return the visitor's cart id, creating a new cookie if missing or invalid."""
    if _is_valid_cart_id(cookie_value):
        return cookie_value  # type: ignore[return-value]

    cart_id = str(uuid.uuid4())
    response.set_cookie(
        key=CART_COOKIE_NAME,
        value=cart_id,
        max_age=CART_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=secure,
    )
    return cart_id


def delete_cart_cookie(response: Response) -> None:
    """Tell the client browser to drop the cart cookie."""
    response.delete_cookie(key=CART_COOKIE_NAME)


__all__ = [
    "CART_COOKIE_MAX_AGE",
    "CART_COOKIE_NAME",
    "delete_cart_cookie",
    "ensure_cart_id",
]

