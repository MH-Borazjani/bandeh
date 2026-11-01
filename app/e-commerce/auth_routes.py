"""Auth routes re-export for backwards compatibility.

Implementation is located in ``api.routes.auth``.
"""

from api.routes.auth import (
    account_page,
    login_page,
    logout,
    otp_request,
    otp_verify,
    router,
)

__all__ = ["account_page", "login_page", "logout", "otp_request", "otp_verify", "router"]
