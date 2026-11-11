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
"""Runnable self-check for integrations/http_client.py and
integrations/directus_client.py. Run from app/e-commerce:

    python check_httpx.py

No server, no live Directus, no database: httpx.MockTransport stands in
for the network, and TestClient drives the real app in-process.
"""

import asyncio

import httpx
from core.cache import reset_cache_for_tests
from integrations.directus_client import (
    FALLBACK_CATEGORIES,
    FALLBACK_PRODUCTS,
    DirectusClient,
    format_toman_price,
)
from integrations.http_client import DEFAULT_TIMEOUT, create_http_client, get_http_client
from main import app


def _mock_client(handler) -> httpx.AsyncClient:
    """Build a client whose network is the given handler function."""
    return create_http_client(transport=httpx.MockTransport(handler))


def _directus_ok(request: httpx.Request) -> httpx.Response:
    """A healthy Directus: categories and products collections."""
    if request.url.path.endswith("/items/categories"):
        return httpx.Response(
            200,
            json={"data": [{"name": "یخچال", "description": "ساید و دوقلو"}]},
        )
    if request.url.path.endswith("/items/products"):
        return httpx.Response(
            200,
            json={"data": [{"name": "یخچال تست", "price": 45000000, "tag": None}]},
        )
    return httpx.Response(404, json={"errors": [{"message": "not found"}]})


def _directus_down(request: httpx.Request) -> httpx.Response:
    """A dead Directus: the connection itself fails."""
    raise httpx.ConnectError("connection refused", request=request)


def _directus_broken(request: httpx.Request) -> httpx.Response:
    """A sick Directus: HTTP 500 on every call."""
    return httpx.Response(500, json={"errors": [{"message": "boom"}]})


def _directus_garbage(request: httpx.Request) -> httpx.Response:
    """A confused Directus: 200 OK with a body that is not JSON."""
    return httpx.Response(200, content=b"this is not json")


def main() -> None:
    # 1. The factory sets the project timeout on every client it builds.
    client = create_http_client()
    assert client.timeout.connect == DEFAULT_TIMEOUT, client.timeout
    assert client.timeout.read == DEFAULT_TIMEOUT, client.timeout
    asyncio.run(client.aclose())

    # 2. Price formatting: Toman integers become Persian-digit strings.
    assert format_toman_price(45000000) == "۴۵٬۰۰۰٬۰۰۰", format_toman_price(45000000)
    assert format_toman_price(9800000) == "۹٬۸۰۰٬۰۰۰", format_toman_price(9800000)
    assert format_toman_price("۴۵٬۰۰۰٬۰۰۰") == "۴۵٬۰۰۰٬۰۰۰"

    # 3. Healthy Directus: rows are mapped to the landing shape.
    async def _healthy() -> None:
        async with _mock_client(_directus_ok) as http:
            content = await DirectusClient(http).get_landing_content()
        assert content["categories"] == [{"name": "یخچال", "desc": "ساید و دوقلو"}], content
        assert content["products"] == [
            {"name": "یخچال تست", "price": "۴۵٬۰۰۰٬۰۰۰", "tag": None}
        ], content

    asyncio.run(_healthy())

    # 4. Dead, sick, or confused Directus: the hardcoded fallback, every time.
    async def _fallback(handler, label: str) -> None:
        async with _mock_client(handler) as http:
            content = await DirectusClient(http).get_landing_content()
        assert content["categories"] == FALLBACK_CATEGORIES, label
        assert content["products"] == FALLBACK_PRODUCTS, label

    asyncio.run(_fallback(_directus_down, "down"))
    asyncio.run(_fallback(_directus_broken, "broken"))
    asyncio.run(_fallback(_directus_garbage, "garbage"))

    # 5. Landing page with a healthy Directus shows the Directus rows.
    from fastapi.testclient import TestClient

    async def _mock_http_client() -> httpx.AsyncClient:
        return _mock_client(_directus_ok)

    app.dependency_overrides[get_http_client] = _mock_http_client
    try:
        with TestClient(app) as test_client:
            r = test_client.get("/")
            assert r.status_code == 200, r.status_code
            assert "یخچال تست" in r.text, r.text
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    # 6. Landing page with a dead Directus still renders the fallback.
    # The cache singleton is reset first: step 5 cached the mock rows for
    # 60s, and without the reset this request would serve that cache.
    reset_cache_for_tests()

    async def _mock_dead_client() -> httpx.AsyncClient:
        return _mock_client(_directus_down)

    app.dependency_overrides[get_http_client] = _mock_dead_client
    try:
        with TestClient(app) as test_client:
            r = test_client.get("/")
            assert r.status_code == 200, r.status_code
            assert "یخچال و فریزر" in r.text, r.text
            assert "یخچال فریزر دوقلو" in r.text, r.text
    finally:
        app.dependency_overrides.pop(get_http_client, None)

    print("All httpx checks passed.")


if __name__ == "__main__":
    main()
