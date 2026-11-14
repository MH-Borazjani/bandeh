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

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, PlainTextResponse

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


@router.get("/terms", response_class=HTMLResponse)
@router.get("/terms-of-service", response_class=HTMLResponse)
async def terms_page(
    request: Request,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Render Terms of Service trust page."""
    response = templates.TemplateResponse(
        request=request,
        name="terms.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/privacy", response_class=HTMLResponse)
@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_page(
    request: Request,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Render Privacy Policy trust page."""
    response = templates.TemplateResponse(
        request=request,
        name="privacy.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/returns", response_class=HTMLResponse)
@router.get("/return-policy", response_class=HTMLResponse)
async def returns_page(
    request: Request,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Render Return Policy trust page."""
    response = templates.TemplateResponse(
        request=request,
        name="returns.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/contact", response_class=HTMLResponse)
@router.get("/contact-us", response_class=HTMLResponse)
async def contact_page(
    request: Request,
    phone: CurrentUserDep,
) -> HTMLResponse:
    """Render Contact Us trust page."""
    response = templates.TemplateResponse(
        request=request,
        name="contact.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/robots.txt", response_class=PlainTextResponse)
def robots_txt() -> PlainTextResponse:
    """Direct web crawlers and indexers to allowed paths and sitemap."""
    content = (
        "User-agent: *\n"
        "Allow: /\n"
        "Disallow: /admin\n"
        "Disallow: /admin/\n"
        "Disallow: /auth/\n"
        "Disallow: /payments/\n"
        "\n"
        "Sitemap: /sitemap.xml\n"
    )
    response = PlainTextResponse(content, media_type="text/plain")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@router.get("/sitemap.xml", response_class=Response)
def sitemap_xml() -> Response:
    """XML sitemap covering public storefront pages."""
    urls = [
        ("/", "1.0", "daily"),
        ("/products", "0.9", "daily"),
        ("/cart", "0.5", "weekly"),
        ("/terms", "0.3", "monthly"),
        ("/privacy", "0.3", "monthly"),
        ("/returns", "0.3", "monthly"),
        ("/contact", "0.3", "monthly"),
    ]
    xml_lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for path, priority, changefreq in urls:
        xml_lines.append(
            f"  <url>\n"
            f"    <loc>{path}</loc>\n"
            f"    <changefreq>{changefreq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            f"  </url>"
        )
    xml_lines.append("</urlset>\n")
    response = Response(content="\n".join(xml_lines), media_type="application/xml")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


__all__ = [
    "CATEGORIES",
    "SAMPLE_PRODUCTS",
    "cart_page",
    "checkout_page",
    "contact_page",
    "landing",
    "privacy_page",
    "returns_page",
    "robots_txt",
    "router",
    "sitemap_xml",
    "terms_page",
]

