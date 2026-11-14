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
"""Public web routes for shop storefront."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

try:
    from core.security import CurrentUserDep
    from core.templates import templates
    from integrations.directus_client import (
        FALLBACK_CATEGORIES,
        FALLBACK_PRODUCTS,
        DirectusClientDep,
    )
    from services.auth_service import mask_phone
except ImportError:
    from auth import CurrentUserDep, mask_phone

    from core.templates import templates
    from integrations.directus_client import (
        FALLBACK_CATEGORIES,
        FALLBACK_PRODUCTS,
        DirectusClientDep,
    )

router = APIRouter(tags=["pages"])

# Hardcoded landing content (used when Directus is down). The single source
# of truth lives in integrations/directus_client.py; these names stay here
# so older imports keep working.
CATEGORIES = FALLBACK_CATEGORIES
SAMPLE_PRODUCTS = FALLBACK_PRODUCTS


@router.get("/", response_class=HTMLResponse)
async def landing(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """Render shop landing page with dynamic login/account header.

    Content comes from Directus (cached 60s); when Directus is down, the
    client returns the hardcoded fallback, so the page renders exactly the
    same. ``private`` Cache-Control: the page header depends on login
    state, so only the visitor's own browser may cache it, never a shared
    proxy.
    """
    content = await directus.get_landing_content()
    response = templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
            "categories": content["categories"],
            "products": content["products"],
        },
    )
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@router.get("/cart", response_class=HTMLResponse)
async def cart_page(
    request: Request,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Render the shopping cart page with draft items from localStorage."""
    response = templates.TemplateResponse(
        request=request,
        name="cart.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "private, no-cache"
    return response


@router.get("/checkout", response_class=HTMLResponse)
async def checkout_page(
    request: Request,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Render the checkout page with recipient address form."""
    response = templates.TemplateResponse(
        request=request,
        name="checkout.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "private, no-cache"
    return response


__all__ = ["CATEGORIES", "SAMPLE_PRODUCTS", "cart_page", "checkout_page", "landing", "router"]

