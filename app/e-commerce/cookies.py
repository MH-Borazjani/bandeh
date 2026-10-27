"""Guest-cart cookie: the Cookies topic applied to Bandeh e-commerce.

A visitor without an account still needs a cart that survives page reloads.
This module gives that visitor a cart id in a cookie -- nothing more.

Deliberately NOT here (they belong to later topics):
- No login, no user id, no password, no token, no JWT.
- No protected routes. The auth topic (tomorrow's JWT block) adds those.
- No cart endpoints and no database. This is only the cookie mechanism;
  cart endpoints will use the cart id returned here.

How it works, in one line: read the cookie, or make a new one.
- ``ensure_cart_id``: if the browser already sent a valid cart id, keep it
  (no new cookie is set). Otherwise make a fresh UUID, set it as a cookie
  on the response, and return it.
- ``delete_cart_cookie``: tell the browser to drop the cookie, by
  rewriting it with a zero lifetime -- exactly what the lesson taught.

Security defaults, and why:
- ``httponly=True``: page JavaScript cannot read the cookie, so a bad
  script cannot steal it.
- ``samesite="lax"``: other sites cannot ride along on this cookie.
- ``secure=False`` by default ONLY because local development runs over
  plain http, and a secure cookie is never sent over http. In production,
  behind HTTPS, pass ``secure=True``. Never ship a production cookie
  with ``secure=False``.
"""

import uuid

from fastapi import Response

CART_COOKIE_NAME = "bandeh_cart_id"
# 30 days, in seconds: a guest cart should survive longer than one visit.
CART_COOKIE_MAX_AGE = 60 * 60 * 24 * 30


def _is_valid_cart_id(value: str | None) -> bool:
    """A cart id is only valid if it is a UUID we could have made."""
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
    """Return the visitor's cart id, setting the cookie if it is missing.

    ``cookie_value`` is what the browser sent (the ``Cookie`` parameter in
    a real endpoint). ``response`` is the response that will be sent back:
    the cookie is set on that same response, never on a separate one.
    """
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
    """Drop the cart cookie: rewrite it with a zero lifetime."""
    response.delete_cookie(key=CART_COOKIE_NAME)
