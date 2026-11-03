"""Authentication web endpoints and HTMX interactions.

All auth URLs are prefixed with /auth:
- GET  /auth/login        The login page (phone-number form)
- POST /auth/otp/request  HTMX: generate and dispatch OTP, swap in code form
- POST /auth/otp/verify   HTMX: verify code, persist user/session in DB, set cookie
- POST /auth/logout       Log out user (revoke DB session + clear cookie), redirect to /
- GET  /auth/account      Protected account page
"""

from __future__ import annotations

from typing import Annotated

from core.security import (
    CurrentUserDep,
    RequireUserDep,
    create_session,
    delete_session,
    read_session_token,
)
from core.templates import templates
from db.session import DbSessionDep
from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from services.auth_service import (
    OtpError,
    mask_phone,
    normalize_phone,
    request_otp,
    verify_otp,
)
from services.session_service import create_database_session, revoke_session
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
        try:
            user, _ = await get_or_create_user_by_phone(db, norm_phone)
            await record_user_login(db, user)

            user_agent = request.headers.get("user-agent")
            ip_address = request.client.host if request.client else None
            db_session = await create_database_session(
                db, user, user_agent=user_agent, ip_address=ip_address
            )
            session_token = db_session.session_token
        except Exception:
            # If database is temporarily unavailable, proceed with signed cookie
            pass

        create_session(response, norm_phone, session_token=session_token)
        response.headers["HX-Redirect"] = "/auth/account"
        return response

    return templates.TemplateResponse(
        request=request,
        name="auth/_otp_form.html",
        context={"phone": phone, "error": "کد اشتباه است یا منقضی شده؛ دوباره تلاش کن"},
    )


@router.post("/logout")
async def logout(
    request: Request,
    db: DbSessionDep,
) -> RedirectResponse:
    """Revoke database session and instruct browser to drop the session cookie."""
    token = read_session_token(request)
    if token:
        try:
            await revoke_session(db, token)
        except Exception:
            pass

    response = RedirectResponse("/", status_code=303)
    delete_session(response)
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
