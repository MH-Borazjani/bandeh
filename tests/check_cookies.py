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
"""Runnable self-check for cookies.py. Run from repo root:

    python tests/check_cookies.py

No server, no database: it calls the helpers directly with plain
Response objects and inspects the Set-Cookie headers they produce.
"""

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from cookies import (
    CART_COOKIE_NAME,
    delete_cart_cookie,
    ensure_cart_id,
)
from fastapi import Response


def main() -> None:
    # 1. No cookie yet -> a new id is made and set on the response.
    response = Response()
    cart_id = ensure_cart_id(None, response)
    uuid.UUID(cart_id)  # raises if it is not a valid UUID
    header = response.headers["set-cookie"]
    assert f"{CART_COOKIE_NAME}={cart_id}" in header, header
    assert "httponly" in header.lower(), header
    assert "samesite=lax" in header.lower(), header

    # 2. A valid cookie already sent -> kept as-is, no new cookie set.
    response = Response()
    assert ensure_cart_id(cart_id, response) == cart_id
    assert "set-cookie" not in response.headers

    # 3. A garbage cookie -> thrown away and replaced with a fresh id.
    response = Response()
    fresh_id = ensure_cart_id("not-a-cart-id", response)
    assert fresh_id != "not-a-cart-id"
    uuid.UUID(fresh_id)

    # 4. Delete -> the cookie is rewritten with a zero lifetime.
    response = Response()
    delete_cart_cookie(response)
    header = response.headers["set-cookie"]
    assert CART_COOKIE_NAME in header, header
    assert "max-age=0" in header.lower(), header

    print("All cookie checks passed.")


if __name__ == "__main__":
    main()
