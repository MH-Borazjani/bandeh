"""The login pages and endpoints. All auth URLs live under /auth.

URL table (every auth URL starts with /auth -- one place to remember):
- GET  /auth/login        the login page (phone-number form)
- POST /auth/otp/request  HTMX: make a code, swap in the code form
- POST /auth/otp/verify   HTMX: check the code, log in on success
- POST /auth/logout       log out, back to /
- GET  /auth/account      demo protected page: only logged-in users

How the HTMX flow works, step by step:
1. The login page shows a <div id="auth-box"> holding the phone form.
2. Submitting the phone form POSTs to /auth/otp/request WITHOUT a page
   reload (hx-post). The server answers with a small HTML fragment --
   either the code form or the phone form with an error -- and HTMX swaps
   it into #auth-box (hx-target + hx-swap="innerHTML").
3. Submitting the code form POSTs to /auth/otp/verify. On success the
   server sets the session cookie AND answers with an HX-Redirect header;
   HTMX then navigates the whole browser to /auth/account. On failure it
   re-renders the code form with a Persian error message.
No JavaScript is written by hand anywhere: HTMX attributes do it all.
"""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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

# Templates live next to this file, so the path works no matter which
# directory the server (or the tests) is started from.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# Prefix and tags live on the router itself (official FastAPI skill
# style) -- include_router() in main.py stays bare.
router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login", response_class=HTMLResponse, response_model=None)
def login_page(request: Request, phone: CurrentUserDep) -> HTMLResponse | RedirectResponse:
    """The login page. Already logged in? Skip it, go to the account page.

    ``response_model=None``: this route returns ready-made responses
    (a page OR a redirect), not data to validate -- so FastAPI is told
    not to build a response model from the ``|`` return annotation.
    """
    if phone is not None:
        return RedirectResponse("/auth/account", status_code=303)
    # Modern Starlette style: request comes FIRST in TemplateResponse.
    return templates.TemplateResponse(request, "auth/login.html", {})


@router.post("/otp/request", response_class=HTMLResponse)
def otp_request(
    request: Request, phone: Annotated[str, Form()]
) -> HTMLResponse:
    """HTMX endpoint: user submitted their number, send them a code.

    Answers with an HTML fragment for #auth-box: the code form on
    success, the phone form with a Persian error on failure. The page
    never reloads -- that is the whole point of HTMX here.
    """
    try:
        normalised = request_otp(phone)
    except (ValueError, OtpError) as exc:
        # str(exc) is the Persian message from auth.py -- show it as-is.
        return templates.TemplateResponse(
            request, "auth/_phone_form.html", {"error": str(exc), "phone": phone}
        )
    return templates.TemplateResponse(
        request, "auth/_otp_form.html", {"phone": normalised}
    )


@router.post("/otp/verify")
def otp_verify(
    request: Request,
    phone: Annotated[str, Form()],
    code: Annotated[str, Form()],
) -> Response:
    """HTMX endpoint: user submitted the code. Right code = logged in.

    Success: set the session cookie and tell HTMX to navigate to the
    account page via the HX-Redirect header. (A plain 303 redirect would
    NOT work here: HTMX would fetch the new page and swap it into the
    form box instead of navigating the browser.)
    Failure: re-render the code form with a Persian error message.
    """
    if verify_otp(phone, code):
        response = Response(status_code=200)
        create_session(response, phone)
        response.headers["HX-Redirect"] = "/auth/account"
        return response
    return templates.TemplateResponse(
        request,
        "auth/_otp_form.html",
        {"phone": phone, "error": "کد اشتباه است یا منقضی شده؛ دوباره تلاش کن"},
    )


@router.post("/logout")
def logout() -> RedirectResponse:
    """Log out: delete the session cookie and go back to the homepage."""
    response = RedirectResponse("/", status_code=303)
    delete_session(response)
    return response


@router.get("/account", response_class=HTMLResponse)
def account_page(request: Request, phone: RequireUserDep) -> HTMLResponse:
    """Demo protected page. RequireUserDep already bounced strangers to
    /auth/login, so `phone` is guaranteed to be a real logged-in number."""
    return templates.TemplateResponse(
        request, "account.html", {"phone": phone, "masked": mask_phone(phone)}
    )
