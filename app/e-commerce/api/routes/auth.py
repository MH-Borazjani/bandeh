"""Authentication web endpoints and HTMX interactions.

All auth URLs are prefixed with /auth:
- GET  /auth/login        The login page (phone-number form)
- POST /auth/otp/request  HTMX: generate and dispatch OTP, swap in code form
- POST /auth/otp/verify   HTMX: verify code, set session cookie on success
- POST /auth/logout       Log out user, redirect to /
- GET  /auth/account      Protected account page
"""

from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

try:
    from core.security import (
        CurrentUserDep,
        RequireUserDep,
        create_session,
        delete_session,
    )
    from core.templates import templates
    from services.auth_service import (
        OtpError,
        mask_phone,
        request_otp,
        verify_otp,
    )
except ImportError:
    from auth import (
        CurrentUserDep,
        OtpError,
        RequireUserDep,
        create_session,
        delete_session,
        mask_phone,
        request_otp,
        verify_otp,
    )
    from core.templates import templates

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
def otp_verify(
    request: Request,
    phone: Annotated[str, Form()],
    code: Annotated[str, Form()],
) -> Response:
    """HTMX endpoint: validates OTP code.

    On success: sets signed session cookie and returns HX-Redirect header.
    On failure: re-renders the OTP form with error message.
    """
    if verify_otp(phone, code):
        response = Response(status_code=200)
        create_session(response, phone)
        response.headers["HX-Redirect"] = "/auth/account"
        return response

    return templates.TemplateResponse(
        request=request,
        name="auth/_otp_form.html",
        context={"phone": phone, "error": "کد اشتباه است یا منقضی شده؛ دوباره تلاش کن"},
    )


@router.post("/logout")
def logout() -> RedirectResponse:
    """Clear session cookie and redirect visitor to landing page."""
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

