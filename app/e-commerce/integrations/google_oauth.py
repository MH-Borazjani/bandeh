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
"""Google OAuth login ("Sign in with Google") for the shop website.

Standard authorization-code flow, no extra library: the shop redirects
the browser to Google, Google redirects back with a one-time code, the
shop exchanges the code for tokens server-to-server and reads the
verified Google identity (``sub`` + email) from the userinfo endpoint.

Credentials come from the environment (``GOOGLE_CLIENT_ID`` /
``GOOGLE_CLIENT_SECRET``); without them the Google button stays hidden
and the routes redirect back to the phone login. The secret never
leaves the server: it is only sent to Google's token endpoint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"

# Only identity is requested: who the customer is, never their data.
GOOGLE_SCOPES = "openid email profile"


@dataclass
class GoogleUserInfo:
    """The verified Google identity for one customer."""

    sub: str
    email: str
    email_verified: bool
    first_name: str | None = None
    last_name: str | None = None


class GoogleOAuthError(Exception):
    """Google refused a step of the login (bad code, bad token, ...)."""


class GoogleOAuth:
    """Small Google OAuth client (authorization code flow)."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        client_id: str,
        client_secret: str,
    ) -> None:
        self._client = client
        self.client_id = client_id
        self._client_secret = client_secret

    def authorization_url(self, redirect_uri: str, state: str) -> str:
        """The Google login page the browser is sent to."""
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": GOOGLE_SCOPES,
            "state": state,
            "access_type": "offline",
            "prompt": "select_account",
        }
        return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, redirect_uri: str) -> str:
        """Trade the one-time code for an access token (server-side only)."""
        response = await self._client.post(
            GOOGLE_TOKEN_URL,
            data={
                "code": code,
                "client_id": self.client_id,
                "client_secret": self._client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
            },
            headers={"Accept": "application/json"},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOAuthError("Google token reply was not JSON") from exc
        if response.status_code != 200 or not isinstance(body, dict):
            raise GoogleOAuthError(
                f"Google token exchange failed: {response.status_code}"
            )
        access_token = body.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise GoogleOAuthError("Google gave no access token")
        return access_token

    async def get_userinfo(self, access_token: str) -> GoogleUserInfo:
        """Read the verified Google identity for the access token."""
        response = await self._client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise GoogleOAuthError("Google userinfo reply was not JSON") from exc
        if response.status_code != 200 or not isinstance(body, dict):
            raise GoogleOAuthError(
                f"Google userinfo failed: {response.status_code}"
            )
        sub = body.get("sub")
        email = body.get("email")
        if not isinstance(sub, str) or not sub:
            raise GoogleOAuthError("Google gave no user id")
        if not isinstance(email, str) or not email:
            raise GoogleOAuthError("Google gave no email")
        given = body.get("given_name")
        family = body.get("family_name")
        return GoogleUserInfo(
            sub=sub,
            email=email,
            email_verified=body.get("email_verified") is True,
            first_name=given if isinstance(given, str) else None,
            last_name=family if isinstance(family, str) else None,
        )


def is_google_oauth_configured() -> bool:
    """True when the Google client id + secret are both set."""
    return bool(
        os.environ.get("GOOGLE_CLIENT_ID")
        and os.environ.get("GOOGLE_CLIENT_SECRET")
    )


def get_google_oauth(client: httpx.AsyncClient) -> GoogleOAuth | None:
    """Build the Google client from env, or None when not configured."""
    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if not client_id or not client_secret:
        return None
    return GoogleOAuth(client, client_id, client_secret)


__all__ = [
    "GOOGLE_AUTH_URL",
    "GOOGLE_SCOPES",
    "GOOGLE_TOKEN_URL",
    "GOOGLE_USERINFO_URL",
    "GoogleOAuth",
    "GoogleOAuthError",
    "GoogleUserInfo",
    "get_google_oauth",
    "is_google_oauth_configured",
]
