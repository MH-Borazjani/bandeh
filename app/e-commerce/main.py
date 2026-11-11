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
"""Main FastAPI application entrypoint."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

from api.routes import (
    admin_router,
    auth_router,
    orders_router,
    pages_router,
    payments_router,
    products_router,
)
from core.middleware import register_middleware
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from integrations.http_client import create_http_client


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create the shared httpx client at startup, close it at shutdown."""
    app.state.http_client = create_http_client()
    yield
    await app.state.http_client.aclose()


app = FastAPI(
    title="Bandeh (easy-auto) API",
    version="0.1.0",
    description="Digital Worker & E-commerce automation platform",
    lifespan=lifespan,
)

# Middleware runs for every request: logging first, then CORS, then
# security headers (see core/middleware.py for the order rule).
register_middleware(app)

# Static files (shop.js and later assets). Uploads are NOT here:
# user photos live under uploads/ and are served by a route.
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# Register modular routers
app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(admin_router)
app.include_router(products_router)
app.include_router(orders_router)
app.include_router(payments_router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Health check endpoint for Docker and monitoring."""
    return {"status": "ok"}
