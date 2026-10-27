"""Runnable self-check for cookies.py. Run from app/e-commerce:

    python check_cookies.py

No server, no database: it calls the helpers directly with plain
Response objects and inspects the Set-Cookie headers they produce.
"""

import uuid

from fastapi import Response

from cookies import (
    CART_COOKIE_NAME,
    delete_cart_cookie,
    ensure_cart_id,
)


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
