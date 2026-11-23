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
"""Genabay MCP server — the shop as a tool server for AI apps.

Served as Streamable HTTP at ``/mcp`` (mounted in main.py), using the
official ``mcp`` Python SDK (FastMCP, pinned ``<2`` because v2 renamed
FastMCP to MCPServer). ChatGPT, Gemini and Claude connect with the shop
URL only — no password, no app install.

Tools in two halves, by design:

- PUBLIC (no login): search_products, get_product, list_categories.
  Anyone's AI can read the live catalogue, prices and stock.
- PRIVATE (needs the customer's personal access token, sent as
  ``Authorization: Bearer <token>``): get_cart, add_to_cart,
  remove_from_cart, create_order_draft, cancel_order, get_payment_link.
  The token proves *who* the AI acts for; it is created in
  /auth/account and verified by :mod:`services.pat_service`.

The standing safety rules, built into the tools:

- The AI never pays alone. ``get_payment_link`` only returns the Zibal
  bank link; the person pays on the bank page.
- An order draft is confirm-only: it freezes prices and holds nothing.
  Stock is reserved only when the payment link is created (the normal
  15-minute hold), and cancelling frees it again.
- Private tools see only the token owner's own cart and orders.

Auth plumbing: :class:`MCPAuthMiddleware` (pure ASGI, wrapped around
the SDK's Starlette app) verifies the Bearer token once per HTTP
request and stores the user id in the request scope. Tools read it from
``ctx.request_context.request.scope["mcp_user_id"]`` — the SDK passes
the Starlette request through as the request context for streamable
HTTP. The middleware also stores the FastAPI app in the scope so tools
can reach the shared httpx client (``scope["genabay_app"]``).
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.types import ASGIApp, Receive, Scope, Send

from core.cache import get_cache
from db.models.user import User
from db.session import get_session_factory
from integrations.directus_client import DirectusClient
from integrations.zibal_client import ZibalClient
from services import cart_service, order_service, pat_service, payment_service
from services import stock_service
from services.auth_service import normalize_phone
from services.cart_service import CartError
from services.order_service import OrderError, OrderItemInput

INSTRUCTIONS = (
    "You are the Genabay shop assistant (home appliances, Bandar Ganaveh, Iran). "
    "Prices are in Toman. Read tools need no login. Cart, order and payment tools "
    "need the customer's personal access token, sent as Authorization: Bearer. "
    "NEVER pay anything yourself: get_payment_link only returns the bank payment "
    "link — always hand that link to the person and let them pay on the bank page. "
    "An order draft only freezes prices; it does not pay and does not reserve stock."
)

# streamable_http_path="/": the SDK app serves its endpoint at its own
# root, and main.py mounts it at /mcp — so clients connect to /mcp.
# DNS rebinding protection is OFF on purpose: the SDK's default only
# allows localhost hosts, but this server is called by AI apps on the
# shop's public domains (genabay.ir, and later the second site) — a
# Host allowlist would break the next site. The endpoint's real
# protection is the PAT on private tools; the public tools are public.
#
# A fresh server per lifespan entry: the SDK's session manager can only
# run() once per instance, and tests enter the app lifespan several
# times in one process. So the tools are plain functions, registered by
# create_mcp_server(), and main.py's lifespan builds a fresh server +
# app on every entry (the module-level ``mcp`` is only the first mount).
def create_mcp_server() -> FastMCP:
    """A fresh FastMCP server with all nine tools registered."""
    server = FastMCP(
        "genabay",
        instructions=INSTRUCTIONS,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    for tool_fn in (
        search_products,
        get_product,
        list_categories,
        get_cart,
        add_to_cart,
        remove_from_cart,
        create_order_draft,
        cancel_order,
        get_payment_link,
    ):
        server.tool()(tool_fn)
    return server

# Test hook only: tests point the tools at their in-memory SQLite
# sessionmaker. Production never sets this.
_session_factory_override: async_sessionmaker[AsyncSession] | None = None


def set_session_factory_for_tests(
    factory: async_sessionmaker[AsyncSession] | None,
) -> None:
    """Point MCP tools at a test database (tests only)."""
    global _session_factory_override
    _session_factory_override = factory


@asynccontextmanager
async def _db_session() -> AsyncGenerator[AsyncSession, None]:
    """One database session per tool call (commits on success)."""
    factory = _session_factory_override or get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _request(ctx: Context) -> Request | None:
    """The Starlette request behind this tool call, if any."""
    request_context = ctx.request_context
    if request_context is None:
        return None
    request = request_context.request
    return request if isinstance(request, Request) else None


def _mcp_user_id(ctx: Context) -> str | None:
    """The authenticated customer's id, set by MCPAuthMiddleware."""
    request = _request(ctx)
    if request is None:
        return None
    user_id = request.scope.get("mcp_user_id")
    return user_id if isinstance(user_id, str) else None


def _require_user(ctx: Context) -> str:
    """The token owner's id, or a plain error the AI can relay."""
    user_id = _mcp_user_id(ctx)
    if user_id is None:
        raise RuntimeError(
            "This action needs the customer's shop login token. "
            "Ask the person to create a token in their account page "
            "(/auth/account) and add it to the MCP server connection "
            "as Authorization: Bearer <token>."
        )
    return user_id


async def _directus(ctx: Context) -> DirectusClient:
    """A Directus client on the app's shared httpx client."""
    request = _request(ctx)
    http_client = None
    if request is not None:
        app = request.scope.get("genabay_app")
        if app is not None:
            http_client = getattr(app.state, "http_client", None)
    if http_client is None:
        raise RuntimeError("the shop's HTTP client is not ready yet")
    return DirectusClient(http_client, cache=await get_cache())


def _product_view(row: dict) -> dict:
    """One product as the AI sees it: id, name, price, condition, stock.

    ``row`` is a Directus product dict with its real stock attached
    (see services.stock_service.attach_stock). A product with no
    stock row has zero available, so it shows as out of stock.
    """
    available = row.get("available")
    if available is None:
        available = max(
            0,
            (row.get("stock") or 0) - (row.get("reserved") or 0),
        )
    return {
        "id": str(row.get("id")),
        "name": row.get("name"),
        "price_toman": row.get("price_toman"),
        "condition": row.get("condition", "new"),
        "condition_stars": row.get("condition_stars"),
        "in_stock": available > 0,
    }


async def _load_user(db: AsyncSession, user_id: str) -> User:
    user = await db.get(User, uuid.UUID(user_id))
    if user is None or not user.is_active:
        raise RuntimeError("the token owner no longer exists")
    return user


async def _cart_view(db: AsyncSession, user: User, directus: DirectusClient) -> dict:
    """The customer's cart with names, prices and totals (all Toman)."""
    lines = await cart_service.get_cart_items(db, user)
    rows = await directus.get_products_by_ids(
        [line.product_id for line in lines]
    )
    await stock_service.attach_stock(db, rows)
    by_id = {str(row.get("id")): row for row in rows}
    items: list[dict] = []
    total_toman = 0
    for line in lines:
        row = by_id.get(str(line.product_id))
        if row is None:
            continue
        line_total = (row.get("price_toman") or 0) * line.quantity
        total_toman += line_total
        items.append(
            {
                "product_id": str(line.product_id),
                "name": row.get("name"),
                "price_toman": row.get("price_toman"),
                "quantity": line.quantity,
                "line_total_toman": line_total,
            }
        )
    return {"items": items, "total_toman": total_toman}


# ---------------------------------------------------------------- public


async def search_products(
    ctx: Context,
    query: str,
    category: str | None = None,
    condition: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search the shop catalogue by name. Prices are in Toman.

    ``category`` is a category name (e.g. the name shown on the site);
    ``condition`` is "new" or "used" (second-hand). No login needed.
    """
    from services import search_service

    directus = await _directus(ctx)
    limit = max(1, min(limit, 50))
    if category:
        # Category search goes through Directus, like the website does.
        rows = await directus.list_products_by_category(category, limit=50)
        if not rows:
            # The category name did not match a real category. AI apps
            # often use a nicer name than the shop's own (e.g. "ماشین
            # لباسشویی" for the shop's "شستشو" category), and an exact
            # match then returns nothing — so the AI tells the person
            # the shop has no products, even though it does. Fall back
            # to a text search with the category name as the query, so
            # the products still come back. Only fall back when the
            # name is not a real category at all; a real but empty
            # category correctly returns nothing.
            categories = await directus.list_categories()
            real_names = {
                str(c.get("name", "")).strip().lower()
                for c in categories
                if c.get("name")
            }
            if category.strip().lower() not in real_names:
                # Try the full category name as a search term first,
                # then each word of it on its own. A multi-word name
                # like "یخچال و فریزر" never matches a product name as
                # one substring, but its word "یخچال" does match the
                # fridge. Skip the word "و" (and) and one-letter words.
                terms: list[str] = []
                full = category.strip()
                if query.strip():
                    full = f"{query.strip()} {full}"
                terms.append(full)
                for word in category.strip().split():
                    if len(word) > 1 and word != "و" and word not in terms:
                        terms.append(word)
                seen: set[str] = set()
                out: list[dict] = []
                found_rows: list[dict] = []
                for term in terms:
                    found = await search_service.search_products(
                        directus,
                        term,
                        limit=limit,
                        condition=condition,
                    )
                    for row in found:
                        key = str(row.get("id"))
                        if key in seen:
                            continue
                        seen.add(key)
                        found_rows.append(row)
                        if len(found_rows) >= limit:
                            break
                    if len(found_rows) >= limit:
                        break
                async with _db_session() as db:
                    await stock_service.attach_stock(db, found_rows)
                return [_product_view(row) for row in found_rows]
        needle = query.strip().lower()
        out: list[dict] = []
        for row in rows:
            if needle and needle not in str(row.get("name", "")).lower():
                continue
            if condition and row.get("condition", "new") != condition:
                continue
            out.append(row)
            if len(out) >= limit:
                break
        async with _db_session() as db:
            await stock_service.attach_stock(db, out)
        return [_product_view(row) for row in out]
    found = await search_service.search_products(
        directus, query, limit=limit, condition=condition
    )
    async with _db_session() as db:
        await stock_service.attach_stock(db, found)
    return [_product_view(row) for row in found]


async def get_product(ctx: Context, product_id: str) -> dict | None:
    """One product by id: name, description, price (Toman), condition and stock.

    Returns null when the product does not exist. No login needed.
    """
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        return None
    directus = await _directus(ctx)
    row = await directus.get_product(pid)
    if row is None:
        return None
    async with _db_session() as db:
        stock_row = await stock_service.get_stock(db, pid)
        await stock_service.attach_stock_one(db, row)
    view = _product_view(row)
    view["description"] = row.get("description")
    if stock_row is not None:
        view["stock_available"] = stock_row.available
    return view


async def list_categories(ctx: Context) -> list[dict]:
    """All product categories, in shop order. No login needed."""
    directus = await _directus(ctx)
    rows = await directus.list_categories()
    return [
        {"name": row.get("name"), "icon": row.get("icon")}
        for row in rows
        if row.get("name")
    ]


# --------------------------------------------------------------- private


async def get_cart(ctx: Context) -> dict:
    """The customer's own server-side cart, with totals. Needs the login token."""
    user_id = _require_user(ctx)
    directus = await _directus(ctx)
    async with _db_session() as db:
        user = await _load_user(db, user_id)
        return await _cart_view(db, user, directus)


async def add_to_cart(
    ctx: Context, product_id: str, quantity: int = 1
) -> dict:
    """Add a product to the customer's cart. Needs the login token."""
    user_id = _require_user(ctx)
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise RuntimeError("that is not a product id") from None
    directus = await _directus(ctx)
    async with _db_session() as db:
        user = await _load_user(db, user_id)
        try:
            await cart_service.add_to_cart(db, user, pid, directus, quantity)
        except CartError as exc:
            raise RuntimeError(str(exc)) from exc
        return await _cart_view(db, user, directus)


async def remove_from_cart(
    ctx: Context, product_id: str, quantity: int | None = None
) -> dict:
    """Remove a product from the cart (or lower its quantity). Needs the login token.

    ``quantity`` empty means the whole line goes away.
    """
    user_id = _require_user(ctx)
    try:
        pid = uuid.UUID(product_id)
    except ValueError:
        raise RuntimeError("that is not a product id") from None
    directus = await _directus(ctx)
    async with _db_session() as db:
        user = await _load_user(db, user_id)
        try:
            removed = await cart_service.remove_from_cart(db, user, pid, quantity)
        except CartError as exc:
            raise RuntimeError(str(exc)) from exc
        if not removed:
            raise RuntimeError("that product is not in the cart")
        return await _cart_view(db, user, directus)


async def create_order_draft(
    ctx: Context,
    recipient_name: str,
    recipient_phone: str,
    delivery_address: str,
) -> dict:
    """Turn the cart into an order draft. Needs the login token.

    The draft freezes prices; it does NOT pay and does NOT reserve stock.
    The cart is emptied. The person still has to pay via get_payment_link.
    """
    user_id = _require_user(ctx)
    try:
        phone = normalize_phone(recipient_phone)
    except ValueError:
        raise RuntimeError("that phone number is not valid") from None
    directus = await _directus(ctx)
    async with _db_session() as db:
        user = await _load_user(db, user_id)
        lines = await cart_service.get_cart_items(db, user)
        if not lines:
            raise RuntimeError("the cart is empty")
        items = [
            OrderItemInput(product_id=line.product_id, quantity=line.quantity)
            for line in lines
        ]
        try:
            order = await order_service.create_order(
                db,
                user,
                items,
                directus,
                recipient_name=recipient_name.strip(),
                recipient_phone=phone,
                delivery_address=delivery_address.strip(),
            )
        except OrderError as exc:
            raise RuntimeError(str(exc)) from exc
        await cart_service.clear_cart(db, user)
        return {
            "order_id": str(order.id),
            "status": order.status,
            "total_toman": order.total_toman,
            "total_rial": order.total_rial,
            "items": [
                {
                    "product_name": item.product_name,
                    "quantity": item.quantity,
                    "unit_price_toman": item.unit_price_toman,
                }
                for item in order.items
            ],
        }


async def cancel_order(ctx: Context, order_id: str) -> dict:
    """Cancel one of the customer's own unpaid orders. Needs the login token.

    Only an order that is not paid yet can be cancelled; a paid order
    cannot be cancelled this way.
    """
    user_id = _require_user(ctx)
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise RuntimeError("that is not an order id") from None
    async with _db_session() as db:
        user = await _load_user(db, user_id)
        try:
            cancelled = await payment_service.cancel_payment(db, oid, user.id)
        except OrderError as exc:
            raise RuntimeError(str(exc)) from exc
        if not cancelled:
            raise RuntimeError("that order can no longer be cancelled")
        return {"order_id": str(oid), "cancelled": True}


async def get_payment_link(ctx: Context, order_id: str) -> dict:
    """Get the bank payment link for an order draft. Needs the login token.

    This only CREATES the payment link — it never pays. Hand the link to
    the person; they pay on the bank page themselves.
    """
    user_id = _require_user(ctx)
    try:
        oid = uuid.UUID(order_id)
    except ValueError:
        raise RuntimeError("that is not an order id") from None
    request = _request(ctx)
    http_client = None
    if request is not None:
        app = request.scope.get("genabay_app")
        if app is not None:
            http_client = getattr(app.state, "http_client", None)
    if http_client is None:
        raise RuntimeError("the shop's HTTP client is not ready yet")
    callback_url = os.environ.get(
        "ZIBAL_CALLBACK_URL", "https://genabay.ir/payments/callback"
    )
    zibal = ZibalClient(http_client, merchant=os.environ.get("ZIBAL_MERCHANT", "zibal"))
    async with _db_session() as db:
        user = await _load_user(db, user_id)
        try:
            attempt = await payment_service.start_payment(
                db, oid, user.id, zibal, callback_url
            )
        except OrderError as exc:
            raise RuntimeError(str(exc)) from exc
        if attempt.track_id is None:
            raise RuntimeError("the payment gateway did not return a payment link")
        return {
            "order_id": str(oid),
            "payment_url": zibal.start_url(attempt.track_id),
            "amount_rial": attempt.amount_rial,
        }


# ------------------------------------------------------------ auth (HTTP)

# The module-level server (the first mount). main.py's lifespan builds a
# fresh one per entry, because the session manager runs only once.
mcp: FastMCP = create_mcp_server()


class MCPAuthMiddleware:
    """Verify the Bearer PAT once per HTTP request; never block.

    Public tools work without a token. When a valid token is present,
    the owner's id lands in ``scope["mcp_user_id"]`` for the tools to
    read; a bad or revoked token behaves like no token at all. The
    FastAPI app is also stored in the scope (``genabay_app``) so tools
    can reach the shared httpx client.
    """

    def __init__(self, app: ASGIApp, fastapi_app: FastAPI) -> None:
        self.app = app
        self.fastapi_app = fastapi_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") == "http":
            scope["genabay_app"] = self.fastapi_app
            headers = dict(scope.get("headers", []))
            auth = headers.get(b"authorization", b"").decode("utf-8", "ignore")
            if auth.startswith("Bearer "):
                token = auth[len("Bearer ") :].strip()
                if token:
                    try:
                        async with _db_session() as db:
                            user = await pat_service.verify_token(db, token)
                        if user is not None:
                            scope["mcp_user_id"] = str(user.id)
                    except Exception:  # noqa: BLE001 - a DB failure must not break public tools
                        pass
        await self.app(scope, receive, send)


def build_mcp_endpoint(
    fastapi_app: FastAPI, server: FastMCP | None = None
) -> tuple[MCPAuthMiddleware, Starlette]:
    """The ``/mcp`` endpoint (PAT auth) plus its SDK app.

    Returns the middleware-wrapped endpoint and the SDK Starlette app
    that owns the session manager — main.py registers the endpoint as
    a direct route at ``/mcp`` (a Mount would 307-redirect ``/mcp`` to
    ``/mcp/``) and starts the SDK app's lifespan itself.
    """
    server = server or mcp
    sdk_app = server.streamable_http_app()
    endpoint = None
    for route in sdk_app.routes:
        if getattr(route, "path", None) == server.settings.streamable_http_path:
            endpoint = getattr(route, "endpoint", None)
    if endpoint is None:
        raise RuntimeError("the MCP SDK app has no streamable HTTP route")
    return MCPAuthMiddleware(endpoint, fastapi_app), sdk_app


__all__ = [
    "build_mcp_endpoint",
    "create_mcp_server",
    "mcp",
    "set_session_factory_for_tests",
]
