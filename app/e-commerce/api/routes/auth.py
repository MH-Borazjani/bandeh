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
"""Authentication web endpoints and HTMX interactions.

All auth URLs are prefixed with /auth:
- GET  /auth/login             The login page (phone-number form)
- POST /auth/otp/request       HTMX: generate and dispatch OTP, swap in code form
- POST /auth/otp/verify        HTMX: verify code, save user/session in DB, set cookies
- GET  /auth/google            Start Google login (redirect to Google)
- GET  /auth/google/callback   Google returns here: log in, or ask for the
                               phone once when the Google account is new
- POST /auth/refresh           Mint a fresh session from a refresh token (rotation)
- POST /auth/logout            Log out user (revoke session/refresh token, clear cookies) -> /
- POST /auth/logout-everywhere Revoke every session + refresh token of the user, on all devices
- GET  /auth/account           Protected account page
"""

from __future__ import annotations

import os
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.security import (
    COOKIE_SECURE,
    CurrentUserDep,
    RequireDbUserDep,
    RequireUserDep,
    create_session,
    delete_refresh_cookie,
    delete_session,
    read_oauth_data,
    read_refresh_token,
    read_session_token,
    set_refresh_cookie,
    sign_oauth_data,
)
from core.templates import templates
from db.models.user import User
from db.session import DbSessionDep
from integrations.google_oauth import (
    GoogleOAuthError,
    GoogleUserInfo,
    get_google_oauth,
    is_google_oauth_configured,
)
from integrations.http_client import HttpClientDep
from services.auth_service import (
    OtpError,
    mask_phone,
    normalize_phone,
    request_otp,
    verify_otp,
)
from services.oauth_service import (
    get_user_by_google_sub,
    link_google_account,
)
from services.pat_service import create_token, list_tokens, revoke_token
from services.refresh_service import (
    find_refresh_token,
    issue_refresh_token,
    revoke_all_refresh_tokens,
    revoke_refresh_token,
    rotate_refresh_token,
)
from services.session_service import (
    create_database_session,
    revoke_all_user_sessions,
    revoke_session,
)
from services.user_service import (
    get_or_create_user_by_phone,
    get_user_by_phone,
    record_user_login,
)

# Short-lived cookies for the Google flow: the CSRF ``state`` and the
# pending Google identity waiting for the phone + OTP step (10 min).
_GOOGLE_STATE_COOKIE = "google_oauth_state"
_GOOGLE_PENDING_COOKIE = "google_oauth_pending"
_GOOGLE_STATE_SALT = "google-oauth-state"
_GOOGLE_PENDING_SALT = "google-oauth-pending"
_GOOGLE_COOKIE_MAX_AGE = 600


def _google_redirect_uri(request: Request) -> str:
    """The callback Google must return to (env override for production)."""
    override = os.environ.get("GOOGLE_REDIRECT_URI")
    if override:
        return override
    return f"{request.base_url}auth/google/callback"


async def _issue_login(
    request: Request, db: DbSessionDep, user: User
) -> tuple[str | None, str | None]:
    """Create the DB session + refresh token for a logged-in user.

    Returns (session_token, refresh_token); either may be None when the
    database is briefly unavailable — the signed cookie still logs the
    user in, exactly like the OTP path's fallback.
    """
    session_token: str | None = None
    refresh_token: str | None = None
    try:
        await record_user_login(db, user)
        user_agent = request.headers.get("user-agent")
        ip_address = request.client.host if request.client else None
        db_session = await create_database_session(
            db, user, user_agent=user_agent, ip_address=ip_address
        )
        session_token = db_session.session_token
        refresh_token, _ = await issue_refresh_token(db, user)
    except Exception:
        pass
    return session_token, refresh_token


router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(
    request: Request, phone: CurrentUserDep
) -> HTMLResponse | RedirectResponse:
    """Render the login page. Redirect already authenticated users to /auth/account."""
    if phone is not None:
        return RedirectResponse("/auth/account", status_code=303)
    params = request.query_params
    return templates.TemplateResponse(
        request=request,
        name="auth/login.html",
        context={
            # Google button shows only when the keys are configured.
            "google_enabled": is_google_oauth_configured(),
            # A new Google account lands here to add its phone number once.
            "google_notice": params.get("google") == "phone",
            "google_error": params.get("error") == "google",
        },
    )


@router.get("/google")
async def google_login(
    request: Request, http_client: HttpClientDep
) -> RedirectResponse:
    """Start Google login: send the browser to Google's login page."""
    google = get_google_oauth(http_client)
    if google is None:
        return RedirectResponse("/auth/login", status_code=303)
    state = secrets.token_urlsafe(32)
    response = RedirectResponse(
        google.authorization_url(_google_redirect_uri(request), state),
        status_code=303,
    )
    response.set_cookie(
        _GOOGLE_STATE_COOKIE,
        sign_oauth_data({"state": state}, _GOOGLE_STATE_SALT),
        max_age=_GOOGLE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/auth",
    )
    return response


@router.get("/google/callback")
async def google_callback(
    request: Request,
    db: DbSessionDep,
    http_client: HttpClientDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google returns here after login.

    Known Google account → log in. New Google account → remember the
    Google identity for 10 minutes and ask for the phone number once;
    the OTP verify step links it to the phone user.
    """
    google = get_google_oauth(http_client)
    if google is None or error is not None or not code or not state:
        return RedirectResponse(
            "/auth/login?error=google", status_code=303
        )
    saved = read_oauth_data(
        request.cookies.get(_GOOGLE_STATE_COOKIE, ""),
        _GOOGLE_STATE_SALT,
        _GOOGLE_COOKIE_MAX_AGE,
    )
    if saved is None or saved.get("state") != state:
        return RedirectResponse(
            "/auth/login?error=google", status_code=303
        )
    try:
        access_token = await google.exchange_code(
            code, _google_redirect_uri(request)
        )
        info = await google.get_userinfo(access_token)
    except GoogleOAuthError:
        return RedirectResponse(
            "/auth/login?error=google", status_code=303
        )
    if not info.email_verified:
        return RedirectResponse(
            "/auth/login?error=google", status_code=303
        )
    user = await get_user_by_google_sub(db, info.sub)
    if user is not None and user.is_active:
        response = RedirectResponse("/auth/account", status_code=303)
        session_token, refresh_token = await _issue_login(request, db, user)
        create_session(response, user.phone, session_token=session_token)
        if refresh_token is not None:
            set_refresh_cookie(response, refresh_token)
        response.delete_cookie(_GOOGLE_STATE_COOKIE, path="/auth")
        return response
    # New Google account: keep the identity, ask for the phone once.
    pending: dict[str, str] = {"sub": info.sub, "email": info.email}
    if info.first_name:
        pending["first_name"] = info.first_name
    if info.last_name:
        pending["last_name"] = info.last_name
    response = RedirectResponse(
        "/auth/login?google=phone", status_code=303
    )
    response.set_cookie(
        _GOOGLE_PENDING_COOKIE,
        sign_oauth_data(pending, _GOOGLE_PENDING_SALT),
        max_age=_GOOGLE_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=COOKIE_SECURE,
        path="/auth",
    )
    response.delete_cookie(_GOOGLE_STATE_COOKIE, path="/auth")
    return response


@router.post("/otp/request", response_class=HTMLResponse)
def otp_request(
    request: Request, phone: Annotated[str, Form()]
) -> HTMLResponse:
    """HTMX endpoint: receives phone number and dispatches verification code."""
    try:
        normalised = request_otp(phone)
    except (ValueError, OtpError) as exc:
        return templates.TemplateResponse(
            request=request,
            name="auth/_phone_form.html",
            context={"error": str(exc), "phone": phone},
        )
    return templates.TemplateResponse(
        request=request,
        name="auth/_otp_form.html",
        context={"phone": normalised},
    )


@router.post("/otp/verify")
async def otp_verify(
    request: Request,
    phone: Annotated[str, Form()],
    code: Annotated[str, Form()],
    db: DbSessionDep,
) -> Response:
    """HTMX endpoint: validates OTP code.

    On success: creates user (if first time), persists server-side session in DB,
    sets signed session cookie, and returns HX-Redirect header.
    On failure: re-renders the OTP form with error message.
    """
    if verify_otp(phone, code):
        norm_phone = normalize_phone(phone)
        response = Response(status_code=200)

        session_token: str | None = None
        refresh_token: str | None = None
        try:
            user, _ = await get_or_create_user_by_phone(db, norm_phone)
            # A Google login waiting for its phone links here: the OTP
            # just proved this phone belongs to the same person. A
            # Google account already linked elsewhere never merges —
            # the link is skipped and the phone login still succeeds.
            pending = read_oauth_data(
                request.cookies.get(_GOOGLE_PENDING_COOKIE, ""),
                _GOOGLE_PENDING_SALT,
                _GOOGLE_COOKIE_MAX_AGE,
            )
            if pending is not None and pending.get("sub") and pending.get("email"):
                try:
                    await link_google_account(
                        db,
                        user,
                        GoogleUserInfo(
                            sub=pending["sub"],
                            email=pending["email"],
                            email_verified=True,
                            first_name=pending.get("first_name"),
                            last_name=pending.get("last_name"),
                        ),
                    )
                except ValueError:
                    pass
                response.delete_cookie(_GOOGLE_PENDING_COOKIE, path="/auth")
            await record_user_login(db, user)

            user_agent = request.headers.get("user-agent")
            ip_address = request.client.host if request.client else None
            db_session = await create_database_session(
                db, user, user_agent=user_agent, ip_address=ip_address
            )
            session_token = db_session.session_token
            # The refresh token is the long-lived credential that mints
            # fresh sessions later without another OTP.
            refresh_token, _ = await issue_refresh_token(db, user)
        except Exception:
            # If database is temporarily unavailable, proceed with signed cookie
            pass

        create_session(response, norm_phone, session_token=session_token)
        if refresh_token is not None:
            set_refresh_cookie(response, refresh_token)
        response.headers["HX-Redirect"] = "/auth/account"
        return response

    return templates.TemplateResponse(
        request=request,
        name="auth/_otp_form.html",
        context={"phone": phone, "error": "کد اشتباه است یا منقضی شده؛ دوباره تلاش کن"},
    )


@router.post("/refresh")
async def refresh_session(request: Request, db: DbSessionDep) -> JSONResponse:
    """Mint a fresh session from a refresh token (rotation).

    - Unknown token -> 401, both cookies cleared.
    - Revoked token presented again -> possible theft: revoke the whole
      token family, 401, both cookies cleared.
    - Expired token -> 401 (a plain old token, no theft signal).
    - Valid token -> rotate (old revoked, new issued), mint a fresh DB
      session, set both cookies, 200.
    """
    def _unauthorized() -> JSONResponse:
        resp = JSONResponse({"detail": "refresh token invalid"}, status_code=401)
        delete_session(resp)
        delete_refresh_cookie(resp)
        return resp

    token = read_refresh_token(request)
    if not token:
        return _unauthorized()

    record = await find_refresh_token(db, token, for_update=True)
    if record is None:
        return _unauthorized()
    if record.is_revoked:
        # Reuse of a dead token: someone may hold a stolen copy.
        # Kill every refresh token and active session of this user at once.
        await revoke_all_refresh_tokens(db, record.user_id)
        await revoke_all_user_sessions(db, record.user_id)
        return _unauthorized()
    if not record.is_valid or not record.user.is_active:
        return _unauthorized()

    new_token, _ = await rotate_refresh_token(db, record)
    user_agent = request.headers.get("user-agent")
    ip_address = request.client.host if request.client else None
    db_session = await create_database_session(
        db, record.user, user_agent=user_agent, ip_address=ip_address
    )
    resp = JSONResponse({"ok": True})
    create_session(resp, record.user.phone, session_token=db_session.session_token)
    set_refresh_cookie(resp, new_token)
    return resp


@router.post("/logout")
async def logout(
    request: Request,
    db: DbSessionDep,
) -> RedirectResponse:
    """Revoke database session + refresh token, clear both cookies."""
    token = read_session_token(request)
    refresh_token = read_refresh_token(request)
    if token:
        try:
            await revoke_session(db, token)
        except Exception:
            pass
    if refresh_token:
        try:
            await revoke_refresh_token(db, refresh_token)
        except Exception:
            pass

    response = RedirectResponse("/", status_code=303)
    delete_session(response)
    delete_refresh_cookie(response)
    return response


@router.post("/logout-everywhere")
async def logout_everywhere(
    user: RequireDbUserDep,
    db: DbSessionDep,
) -> RedirectResponse:
    """Revoke every session and refresh token of this user, on all devices.

    The role check is the dependency, not an if in the route: any logged-in
    user may kill their own sessions; the dependency already proved who
    they are (401 when anonymous).
    """
    try:
        await revoke_all_user_sessions(db, user.id)
        await revoke_all_refresh_tokens(db, user.id)
    except Exception:
        pass

    response = RedirectResponse("/", status_code=303)
    delete_session(response)
    delete_refresh_cookie(response)
    return response


@router.get("/account", response_class=HTMLResponse)
async def account_page(
    request: Request, phone: RequireUserDep, db: DbSessionDep
) -> HTMLResponse:
    """Protected user account dashboard (with the MCP token list).

    The token list is best-effort: when the database is briefly
    unreachable the page still renders (masked number, orders, logout)
    and the list simply comes back empty — the page never depended on
    the database before, and it still must not.
    """
    tokens = []
    try:
        user = await get_user_by_phone(db, phone) if phone else None
        if user is not None:
            tokens = await list_tokens(db, user)
    except Exception:  # noqa: BLE001 - a dead DB must not break the page
        tokens = []
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={"phone": phone, "masked": mask_phone(phone), "tokens": tokens},
    )


@router.post("/tokens", response_class=HTMLResponse, response_model=None)
async def create_pat_token(
    request: Request,
    phone: RequireUserDep,
    db: DbSessionDep,
    name: Annotated[str, Form()] = "",
) -> HTMLResponse | RedirectResponse:
    """Create a personal access token and show it once, on this page only."""
    user = await get_user_by_phone(db, phone) if phone else None
    if user is None:
        return RedirectResponse("/auth/login", status_code=303)
    _, plaintext = await create_token(db, user, name or "MCP token")
    tokens = await list_tokens(db, user)
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone),
            "tokens": tokens,
            "new_token": plaintext,
        },
    )


@router.post("/tokens/{token_id}/revoke")
async def revoke_pat_token(
    request: Request, phone: RequireUserDep, db: DbSessionDep, token_id: str
) -> RedirectResponse:
    """Revoke one of the customer's own tokens, then back to the account."""
    user = await get_user_by_phone(db, phone) if phone else None
    if user is not None:
        try:
            await revoke_token(db, user, uuid.UUID(token_id))
        except ValueError:
            pass
    return RedirectResponse("/auth/account", status_code=303)


__all__ = ["router"]
