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
- POST /auth/refresh           Mint a fresh session from a refresh token (rotation)
- POST /auth/logout            Log out user (revoke session/refresh token, clear cookies) -> /
- POST /auth/logout-everywhere Revoke every session + refresh token of the user, on all devices
- GET  /auth/account           Protected account page
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from core.security import (
    CurrentUserDep,
    RequireDbUserDep,
    RequireUserDep,
    create_session,
    delete_refresh_cookie,
    delete_session,
    read_refresh_token,
    read_session_token,
    set_refresh_cookie,
)
from core.templates import templates
from db.session import DbSessionDep
from services.auth_service import (
    OtpError,
    mask_phone,
    normalize_phone,
    request_otp,
    verify_otp,
)
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
from services.user_service import get_or_create_user_by_phone, record_user_login

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(
    request: Request, phone: CurrentUserDep
) -> HTMLResponse | RedirectResponse:
    """Render the login page. Redirect already authenticated users to /auth/account."""
    if phone is not None:
        return RedirectResponse("/auth/account", status_code=303)
    return templates.TemplateResponse(request=request, name="auth/login.html", context={})


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
def account_page(request: Request, phone: RequireUserDep) -> HTMLResponse:
    """Protected user account dashboard."""
    return templates.TemplateResponse(
        request=request,
        name="account.html",
        context={"phone": phone, "masked": mask_phone(phone)},
    )


__all__ = ["router"]
