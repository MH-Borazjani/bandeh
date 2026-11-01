"""Guest-cart cookie helpers.

Maintained for backwards compatibility. Implementation is located in ``core.cookies``.
"""

from core.cookies import (
    CART_COOKIE_MAX_AGE,
    CART_COOKIE_NAME,
    delete_cart_cookie,
    ensure_cart_id,
)

__all__ = [
    "CART_COOKIE_MAX_AGE",
    "CART_COOKIE_NAME",
    "delete_cart_cookie",
    "ensure_cart_id",
]
