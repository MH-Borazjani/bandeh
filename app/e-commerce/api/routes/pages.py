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

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse

try:
    from core.security import CurrentUserDep
    from core.templates import templates
    from integrations.directus_client import (
        FALLBACK_CATEGORIES,
        FALLBACK_PRODUCTS,
        DirectusClient,
        DirectusClientDep,
    )
    from services.auth_service import mask_phone
except ImportError:
    from auth import CurrentUserDep, mask_phone

    from core.templates import templates
    from integrations.directus_client import (
        FALLBACK_CATEGORIES,
        FALLBACK_PRODUCTS,
        DirectusClient,
        DirectusClientDep,
    )

router = APIRouter(tags=["pages"])

# PWA files live under static/, but the service worker MUST be served
# from the site root: a worker's scope is the folder it is served from,
# and a worker under /static/ could only control /static/*.
_STATIC_DIR = Path(__file__).resolve().parent.parent.parent / "static"

# Hardcoded landing content (used when Directus is down). The single source
# of truth lives in integrations/directus_client.py; these names stay here
# so older imports keep working.
CATEGORIES = FALLBACK_CATEGORIES
SAMPLE_PRODUCTS = FALLBACK_PRODUCTS


def _base_template(request: Request) -> str:
    """Full base for a direct load, fragment base for an HTMX swap.

    HTMX navigation swaps the response into ``<main>``; returning the
    full page (header + footer) would nest a second header inside
    ``<main>``. Same ``HX-Request`` check as ``products._wants_html``.
    (``core/templates.py`` also sets this through a context processor;
    passing it here makes the intent explicit per route.)
    """
    if request.headers.get("hx-request") == "true":
        return "base_fragment.html"
    return "base.html"


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
            "base_template": _base_template(request),
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
            "categories": content["categories"],
            "products": content["products"],
        },
    )
    response.headers["Cache-Control"] = "private, max-age=60"
    return response


@router.get("/categories", response_class=HTMLResponse)
async def categories_page(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """Render the all-categories page (header nav + landing 'see all').

    Every category Directus knows, with its real product count — adding
    a category in Directus adds a card here, in the search filters and
    on the landing page, with no code change. Content comes from
    Directus (cached 60s); when Directus is down the client returns
    the hardcoded fallback, so the page renders exactly the same.
    """
    content = await directus.get_landing_content()
    products = content["products"]
    counts: dict[str, int] = {}
    for p in products:
        cat = p.get("category")
        if cat:
            counts[cat] = counts.get(cat, 0) + 1
    categories = [
        {
            "name": c["name"],
            "description": c["desc"],
            "icon": c.get("icon"),
            "count": counts.get(c["name"], 0),
        }
        for c in content["categories"]
    ]
    response = templates.TemplateResponse(
        request=request,
        name="categories.html",
        context={
            "base_template": _base_template(request),
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
            "categories": categories,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=60"
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
            "base_template": _base_template(request),
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
            "base_template": _base_template(request),
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "private, no-cache"
    return response


async def _render_legal_page(
    request: Request,
    phone: str | None,
    directus: DirectusClient,
    slug: str,
    fallback_template: str,
) -> HTMLResponse:
    """Render one footer page from the CMS, or its static template.

    The editor writes the page in Directus (``legal_pages``: title +
    body). Three cases:
    - The CMS has it and it is active → ``legal_page.html`` with CMS text.
    - The CMS has it and it is inactive → the editor removed it → 404.
    - The CMS has no row for it, or Directus is down → the static
      template (the text the shop showed before the CMS took over).
    Turning a page off also hides its link in the footer (see base.html).
    """
    page = await directus.get_legal_page(slug)
    if page is not None:
        if not page["is_active"]:
            raise HTTPException(status_code=404, detail="page not found")
        response = templates.TemplateResponse(
            request=request,
            name="legal_page.html",
            context={
                "base_template": _base_template(request),
                "phone": phone,
                "masked": mask_phone(phone) if phone is not None else None,
                "page": page,
            },
        )
        response.headers["Cache-Control"] = "public, max-age=3600"
        return response
    response = templates.TemplateResponse(
        request=request,
        name=fallback_template,
        context={
            "base_template": _base_template(request),
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/terms", response_class=HTMLResponse)
@router.get("/terms-of-service", response_class=HTMLResponse)
async def terms_page(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """Render Terms of Service trust page (CMS text, static fallback)."""
    return await _render_legal_page(request, phone, directus, "terms", "terms.html")


@router.get("/privacy", response_class=HTMLResponse)
@router.get("/privacy-policy", response_class=HTMLResponse)
async def privacy_page(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """Render Privacy Policy trust page (CMS text, static fallback)."""
    return await _render_legal_page(
        request, phone, directus, "privacy", "privacy.html"
    )


@router.get("/returns", response_class=HTMLResponse)
@router.get("/return-policy", response_class=HTMLResponse)
async def returns_page(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """Render Return Policy trust page (CMS text, static fallback)."""
    return await _render_legal_page(
        request, phone, directus, "returns", "returns.html"
    )


@router.get("/contact", response_class=HTMLResponse)
@router.get("/contact-us", response_class=HTMLResponse)
async def contact_page(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """Render Contact Us trust page (CMS text, static fallback)."""
    return await _render_legal_page(
        request, phone, directus, "contact", "contact.html"
    )


@router.get("/mcp-guide", response_class=HTMLResponse)
async def mcp_guide_page(
    request: Request,
    phone: CurrentUserDep,
    directus: DirectusClientDep,
) -> HTMLResponse:
    """MCP guide: how a normal person connects their AI to the shop.

    Sections come from Directus (``mcp_guide_sections``), with a
    hardcoded fallback when the CMS is down; the intro and the link
    come from ``site_settings`` through the shared site content.
    """
    sections = await directus.get_mcp_guide_sections()
    response = templates.TemplateResponse(
        request=request,
        name="mcp_guide.html",
        context={
            "base_template": _base_template(request),
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
            "sections": sections,
        },
    )
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/site/logo", response_class=Response)
async def site_logo(directus: DirectusClientDep) -> Response:
    """Serve the CMS logo (site_settings.logo) through the shop itself.

    The browser never needs the Directus host: the shop fetches the
    file server-side and serves its bytes. No logo configured (or the
    file is gone) → 404, and the header falls back to the text brand.
    """
    site = await directus.get_site_content()
    logo = site["settings"].get("logo")
    if not logo:
        raise HTTPException(status_code=404, detail="no logo configured")
    asset = await directus.get_asset(logo)
    if asset is None:
        raise HTTPException(status_code=404, detail="logo not found")
    content, content_type = asset
    response = Response(content=content, media_type=content_type)
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/site/badge/{file_id}", response_class=Response)
async def site_badge(file_id: str, directus: DirectusClientDep) -> Response:
    """Serve one CMS seal image (certification_badges.image) via the shop.

    Same proxy idea as /site/logo: the file id comes from the CMS data,
    and get_asset refuses anything that is not a plain id.
    """
    asset = await directus.get_asset(file_id)
    if asset is None:
        raise HTTPException(status_code=404, detail="badge image not found")
    content, content_type = asset
    response = Response(content=content, media_type=content_type)
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
        ("/categories", "0.8", "daily"),
        ("/mcp-guide", "0.6", "monthly"),
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


@router.get("/sw.js", response_class=Response)
def service_worker() -> Response:
    """Serve the PWA service worker from the site root (scope = /)."""
    content = (_STATIC_DIR / "sw.js").read_text(encoding="utf-8")
    response = Response(content=content, media_type="application/javascript")
    # Never cache the worker itself: the browser checks for a new
    # worker on every visit, and a cached copy would freeze the old
    # worker in place.
    response.headers["Cache-Control"] = "no-cache"
    return response


@router.get("/favicon.ico", response_class=Response)
def favicon() -> Response:
    """Serve the shop favicon (Ganabi logo) from the site root."""
    content = (_STATIC_DIR / "favicon.ico").read_bytes()
    response = Response(content=content, media_type="image/x-icon")
    response.headers["Cache-Control"] = "public, max-age=86400"
    return response


@router.get("/manifest.webmanifest", response_class=Response)
def web_manifest() -> Response:
    """Serve the PWA manifest from the site root."""
    content = (_STATIC_DIR / "manifest.webmanifest").read_text(encoding="utf-8")
    response = Response(content=content, media_type="application/manifest+json")
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


@router.get("/.well-known/ucp")
def ucp_well_known(request: Request) -> JSONResponse:
    """UCP discovery: the merchant capability profile (public).

    AI agents fetch this first to learn what the shop can do: the
    shopping service (REST, UCP 2026-08-25) at /ucp, the checkout /
    catalog / orders capabilities, and the Zibal payment handler.
    """
    from services.agent_commerce import ucp_profile

    base_url = str(request.base_url).rstrip("/")
    response = JSONResponse(content=ucp_profile(base_url))
    response.headers["Cache-Control"] = "public, max-age=3600"
    return response


__all__ = [
    "CATEGORIES",
    "SAMPLE_PRODUCTS",
    "cart_page",
    "categories_page",
    "checkout_page",
    "contact_page",
    "favicon",
    "landing",
    "privacy_page",
    "returns_page",
    "robots_txt",
    "router",
    "service_worker",
    "site_badge",
    "site_logo",
    "sitemap_xml",
    "terms_page",
    "ucp_well_known",
    "web_manifest",
]

