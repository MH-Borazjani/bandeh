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
"""Checks for the real MCP server at /mcp (Streamable HTTP, JSON-RPC).

The whole stack is exercised over real HTTP: the MCPAuthMiddleware
(Bearer PAT -> user), the FastMCP tools, the server-side cart, the order
draft, the Zibal payment link, and the per-customer isolation. Directus
and Zibal are faked with an httpx MockTransport; the database is an
in-memory SQLite engine, like the other check_* files.
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core.cache import reset_cache_for_tests
from db.base import Base
from db.models.product_stock import ProductStock
from db.session import get_db
from integrations import mcp_server
from main import app
from services import pat_service, user_service

FRIDGE_ID = uuid.UUID("ad7d6a7f-3d92-412c-a523-55deb334fa8c")
TV_ID = uuid.UUID("4a9be4d9-5194-4b0d-b401-9594ebb8d030")
WASHER_ID = uuid.UUID("4b31ea7d-c2df-4068-877a-983cfa2734ba")

CATEGORIES = [
    {"id": 1, "name": "آشپزخانه", "icon": "🍳", "sort": 1},
    {"id": 2, "name": "تلویزیون و صدا", "icon": "📺", "sort": 2},
    {"id": 3, "name": "شستشو", "icon": "🧺", "sort": 3},
]

PRODUCTS = [
    {
        "id": str(FRIDGE_ID),
        "name": "یخچال دو درب",
        "description": "کم‌مصرف",
        "price_toman": 45_000_000,
        "category": 1,
        "is_active": True,
        "condition": "new",
        "condition_stars": None,
    },
    {
        "id": str(TV_ID),
        "name": "تلویزیون ۵۵ اینچ",
        "description": "هوشمند",
        "price_toman": 32_000_000,
        "category": 2,
        "is_active": True,
        "condition": "new",
        "condition_stars": None,
    },
    {
        "id": str(WASHER_ID),
        "name": "ماشین لباسشویی ۸ کیلویی",
        "description": "کم‌صدا",
        "price_toman": 28_500_000,
        "category": 3,
        "is_active": True,
        "condition": "used",
        "condition_stars": 4,
    },
]


def _mock_handler(request: httpx.Request) -> httpx.Response:
    """Fake Directus (categories + products) and fake Zibal (request)."""
    host = request.url.host
    path = request.url.path
    if host == "gateway.zibal.ir" and path == "/v1/request":
        return httpx.Response(200, json={"result": 100, "trackId": 1234567890})
    if path == "/items/categories":
        return httpx.Response(200, json={"data": CATEGORIES})
    if path == "/items/products":
        cat = request.url.params.get("filter[category][_eq]")
        ids = request.url.params.get("filter[id][_in]")
        rows = PRODUCTS
        if cat is not None:
            rows = [p for p in rows if str(p["category"]) == cat]
        if ids is not None:
            wanted = set(ids.split(","))
            rows = [p for p in rows if p["id"] in wanted]
        return httpx.Response(200, json={"data": rows})
    if path.startswith("/items/products/"):
        pid = path.rsplit("/", 1)[-1]
        for row in PRODUCTS:
            if row["id"] == pid:
                return httpx.Response(200, json={"data": row})
        return httpx.Response(404, json={"errors": [{"message": "not found"}]})
    if path == "/items/site_settings":
        return httpx.Response(200, json={"data": []})
    return httpx.Response(404, json={"errors": [{"message": "unknown"}]})


class McpClient:
    """A tiny JSON-RPC client for the /mcp Streamable HTTP endpoint."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.session_id: str | None = None
        self._next_id = 0

    def _call_raw(
        self, method: str, params: dict | None = None, token: str | None = None
    ) -> dict:
        self._next_id += 1
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        if self.session_id is not None:
            headers["mcp-session-id"] = self.session_id
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id,
            "method": method,
            "params": params or {},
        }
        resp = self.client.post("/mcp", headers=headers, json=body)
        assert resp.status_code == 200, (resp.status_code, resp.text[:300])
        if "mcp-session-id" in resp.headers:
            self.session_id = resp.headers["mcp-session-id"]
        payload = None
        for line in resp.text.splitlines():
            if line.startswith("data: "):
                payload = json.loads(line[len("data: ") :])
        assert payload is not None, f"no SSE data in {resp.text[:300]!r}"
        return payload

    def initialize(self) -> None:
        payload = self._call_raw(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "check", "version": "1"},
            },
        )
        assert "result" in payload, payload
        self._call_raw("notifications/initialized")

    def list_tools(self) -> list[str]:
        payload = self._call_raw("tools/list")
        return [t["name"] for t in payload["result"]["tools"]]

    def call_tool(self, name: str, args: dict, token: str | None = None) -> dict:
        payload = self._call_raw(
            "tools/call", {"name": name, "arguments": args}, token=token
        )
        result = payload.get("result", {})
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", [])]
            raise AssertionError(f"tool {name} errored: {' '.join(texts)}")
        # The structured output carries the real value (a list stays a
        # list); the text blocks are one JSON item each, so a list of
        # one item would look like a single dict there.
        structured = result.get("structuredContent")
        if isinstance(structured, dict) and "result" in structured:
            return structured["result"]
        texts = [c.get("text", "") for c in result.get("content", [])]
        parsed = [json.loads(text) for text in texts]
        return parsed if len(parsed) > 1 else parsed[0]

    def call_tool_expect_error(
        self, name: str, args: dict, token: str | None = None
    ) -> str:
        payload = self._call_raw(
            "tools/call", {"name": name, "arguments": args}, token=token
        )
        result = payload.get("result", {})
        assert result.get("isError"), f"tool {name} should have errored: {result}"
        return " ".join(c.get("text", "") for c in result.get("content", []))


async def main_async() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    mcp_server.set_session_factory_for_tests(Session)

    async def test_get_db():  # noqa: ANN202
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = test_get_db
    reset_cache_for_tests()
    client = TestClient(app, raise_server_exceptions=False)
    # Enter the client so the app lifespan (and the mounted MCP app's
    # session manager) actually runs; the real httpx client it creates
    # is replaced with the mock right after.
    client.__enter__()
    app.state.http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(_mock_handler)
    )
    mcp_client = McpClient(client)

    async with Session() as s:
        # Content lives in the mocked Directus above; only the stock
        # numbers live here (the stock table, keyed by Directus id).
        s.add_all(
            [
                ProductStock(directus_product_id=str(FRIDGE_ID), stock=5),
                ProductStock(directus_product_id=str(TV_ID), stock=5),
                ProductStock(directus_product_id=str(WASHER_ID), stock=5),
            ]
        )
        user, _ = await user_service.get_or_create_user_by_phone(s, "09123456789")
        _, token1 = await pat_service.create_token(s, user, "ChatGPT")
        user2, _ = await user_service.get_or_create_user_by_phone(s, "09987654321")
        _, token2 = await pat_service.create_token(s, user2, "Gemini")
        await s.commit()

    # --- 1. initialize + tools/list: all nine tools, no login needed ---
    mcp_client.initialize()
    tools = mcp_client.list_tools()
    for name in (
        "search_products",
        "get_product",
        "list_categories",
        "get_cart",
        "add_to_cart",
        "remove_from_cart",
        "create_order_draft",
        "cancel_order",
        "get_payment_link",
    ):
        assert name in tools, f"missing tool {name} in {tools}"
    print("1. MCP initialize + tools/list: 9 tools")

    # --- 2. public search: finds the TV, with price and condition ---
    found = mcp_client.call_tool("search_products", {"query": "تلویزیون"})
    assert len(found) == 1 and found[0]["name"] == "تلویزیون ۵۵ اینچ", found
    assert found[0]["price_toman"] == 32_000_000 and found[0]["condition"] == "new"
    print("2. public search_products finds the TV")

    # --- 3. public search by category: only the washer, in its category ---
    found = mcp_client.call_tool(
        "search_products", {"query": "", "category": "شستشو"}
    )
    assert [p["name"] for p in found] == ["ماشین لباسشویی ۸ کیلویی"], found
    print("3. public search_products by category")

    # --- 4. public get_product: the washer is used, 4 stars ---
    washer = mcp_client.call_tool("get_product", {"product_id": str(WASHER_ID)})
    assert washer["condition"] == "used" and washer["condition_stars"] == 4, washer
    assert washer["in_stock"] is True
    missing = mcp_client.call_tool("get_product", {"product_id": str(uuid.uuid4())})
    assert missing is None
    print("4. public get_product (used washer + missing -> null)")

    # --- 5. public list_categories: all three ---
    cats = mcp_client.call_tool("list_categories", {})
    assert [c["name"] for c in cats] == ["آشپزخانه", "تلویزیون و صدا", "شستشو"], cats
    print("5. public list_categories")

    # --- 6. private tools without a token must refuse ---
    err = mcp_client.call_tool_expect_error("get_cart", {})
    assert "token" in err.lower(), err
    err = mcp_client.call_tool_expect_error(
        "add_to_cart", {"product_id": str(WASHER_ID), "quantity": 1}
    )
    assert "token" in err.lower(), err
    print("6. private tools refuse without a token")

    # --- 7. add to cart with the token; totals are real Toman ---
    cart = mcp_client.call_tool(
        "add_to_cart", {"product_id": str(WASHER_ID), "quantity": 2}, token=token1
    )
    assert cart["total_toman"] == 57_000_000, cart
    assert cart["items"][0]["quantity"] == 2
    cart = mcp_client.call_tool("get_cart", {}, token=token1)
    assert cart["total_toman"] == 57_000_000
    print("7. add_to_cart + get_cart with the token")

    # --- 8. remove one, then the whole line ---
    cart = mcp_client.call_tool(
        "remove_from_cart", {"product_id": str(WASHER_ID), "quantity": 1}, token=token1
    )
    assert cart["total_toman"] == 28_500_000, cart
    cart = mcp_client.call_tool(
        "remove_from_cart", {"product_id": str(WASHER_ID)}, token=token1
    )
    assert cart["items"] == [] and cart["total_toman"] == 0
    print("8. remove_from_cart (one, then the line)")

    # --- 9. order draft: freezes prices, empties the cart ---
    mcp_client.call_tool(
        "add_to_cart", {"product_id": str(WASHER_ID), "quantity": 1}, token=token1
    )
    draft = mcp_client.call_tool(
        "create_order_draft",
        {
            "recipient_name": "Test Buyer",
            "recipient_phone": "09123456789",
            "delivery_address": "گناوه، خیابان اصلی، پلاک ۱",
        },
        token=token1,
    )
    assert draft["total_toman"] == 28_500_000 and draft["total_rial"] == 285_000_000
    assert draft["status"] == "created", draft
    cart = mcp_client.call_tool("get_cart", {}, token=token1)
    assert cart["items"] == [], "the draft must empty the cart"
    print("9. create_order_draft freezes prices and empties the cart")

    # --- 10. payment link: real Zibal start URL, no money moved ---
    link = mcp_client.call_tool(
        "get_payment_link", {"order_id": draft["order_id"]}, token=token1
    )
    assert link["payment_url"] == "https://gateway.zibal.ir/start/1234567890", link
    assert link["amount_rial"] == 285_000_000
    print("10. get_payment_link returns the Zibal start URL")

    # --- 11. cancel: works once, then refuses; isolation holds ---
    cancelled = mcp_client.call_tool(
        "cancel_order", {"order_id": draft["order_id"]}, token=token1
    )
    assert cancelled["cancelled"] is True
    err = mcp_client.call_tool_expect_error(
        "cancel_order", {"order_id": draft["order_id"]}, token=token1
    )
    assert "cancel" in err.lower(), err
    # The second customer cannot see the first customer's cart or order.
    cart2 = mcp_client.call_tool("get_cart", {}, token=token2)
    assert cart2["items"] == []
    err = mcp_client.call_tool_expect_error(
        "cancel_order", {"order_id": draft["order_id"]}, token=token2
    )
    assert "not found" in err.lower() or "order" in err.lower(), err
    print("11. cancel_order once + per-customer isolation")

    # --- 12. a revoked token stops working ---
    async with Session() as s:
        user, _ = await user_service.get_or_create_user_by_phone(s, "09123456789")
        tokens = await pat_service.list_tokens(s, user)
        assert await pat_service.revoke_token(s, user, tokens[0].id)
        await s.commit()
    err = mcp_client.call_tool_expect_error("get_cart", {}, token=token1)
    assert "token" in err.lower(), err
    print("12. revoked token is refused")

    # --- 13. the token plaintext is never stored ---
    async with Session() as s:
        user, _ = await user_service.get_or_create_user_by_phone(s, "09123456789")
        tokens = await pat_service.list_tokens(s, user)
        assert token1 not in (t.token_hash for t in tokens)
        assert all(t.token_hash != token1 for t in tokens)
    print("13. only the token hash is stored, never the plaintext")

    # --- 14. an unknown category name falls back to a text search ---
    # AI apps use nicer category names than the shop's own (ChatGPT
    # asked for "ماشین لباسشویی" while the shop's category is "شستشو"
    # and returned nothing, so it told the person the shop has no
    # products). The fallback searches the name as text instead.
    found = mcp_client.call_tool(
        "search_products", {"query": "", "category": "ماشین لباسشویی"}
    )
    assert [p["name"] for p in found] == ["ماشین لباسشویی ۸ کیلویی"], found
    found = mcp_client.call_tool(
        "search_products", {"query": "", "category": "یخچال و فریزر"}
    )
    assert [p["name"] for p in found] == ["یخچال دو درب"], found
    print("14. unknown category name falls back to text search")

    mcp_server.set_session_factory_for_tests(None)
    client.__exit__(None, None, None)
    print("ALL MCP CHECKS PASSED")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
