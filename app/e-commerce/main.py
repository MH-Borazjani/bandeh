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

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from api.routes import (
    admin_router,
    auth_router,
    orders_router,
    pages_router,
    payments_router,
    products_router,
)
from core.middleware import register_middleware
from core.security import read_session
from core.templates import templates
from integrations.http_client import create_http_client
from services.auth_service import mask_phone
from services.scheduler import ReservationCleanupTask

logger = logging.getLogger("bandeh.main")


class HealthCheckRead(BaseModel):
    """Health check response payload."""

    status: Literal["ok"] = "ok"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Create the shared httpx client and start the reservation cleanup timer."""
    app.state.http_client = create_http_client()
    cleanup_task = ReservationCleanupTask(interval_seconds=60.0)
    app.state.cleanup_task = cleanup_task
    cleanup_task.start()
    yield
    await cleanup_task.stop()
    await app.state.http_client.aclose()


app = FastAPI(
    title="Bandeh (easy-auto) API",
    version="0.1.0",
    description="Digital Worker & E-commerce automation platform",
    lifespan=lifespan,
)

# Middleware runs for every request: logging first, then CORS, then
# security headers, then rate limiting (see core/middleware.py).
register_middleware(app)


# Custom error handlers for HTML error pages (404 and 500) and JSON fallback
@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
    """Handle HTTP exceptions with HTML template responses for browsers."""
    if exc.status_code in (301, 302, 303, 307, 308):
        location = exc.headers.get("Location", "/") if exc.headers else "/"
        return RedirectResponse(url=location, status_code=exc.status_code)

    accept = request.headers.get("accept", "")
    is_html = "text/html" in accept and not request.url.path.startswith("/api/")

    if exc.status_code == 404 and is_html:
        phone = read_session(request)
        return templates.TemplateResponse(
            request=request,
            name="404.html",
            context={
                "phone": phone,
                "masked": mask_phone(phone) if phone is not None else None,
            },
            status_code=404,
        )

    if exc.status_code >= 500 and is_html:
        phone = read_session(request)
        return templates.TemplateResponse(
            request=request,
            name="500.html",
            context={
                "phone": phone,
                "masked": mask_phone(phone) if phone is not None else None,
            },
            status_code=500,
        )

    headers = dict(exc.headers) if exc.headers else None
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> Response:
    """Handle unhandled 500 exceptions with 500.html for browsers."""
    logger.error("Unhandled server exception: %s", exc, exc_info=True)
    accept = request.headers.get("accept", "")
    is_html = "text/html" in accept and not request.url.path.startswith("/api/")

    if is_html:
        phone = read_session(request)
        return templates.TemplateResponse(
            request=request,
            name="500.html",
            context={
                "phone": phone,
                "masked": mask_phone(phone) if phone is not None else None,
            },
            status_code=500,
        )

    return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


# Static files (shop.js, compiled tailwind.css, etc.)
app.mount("/static", StaticFiles(directory=Path(__file__).parent / "static"), name="static")

# Register modular routers
app.include_router(auth_router)
app.include_router(pages_router)
app.include_router(admin_router)
app.include_router(products_router)
app.include_router(orders_router)
app.include_router(payments_router)


@app.get("/health", tags=["system"])
def health_check() -> HealthCheckRead:
    """Health check endpoint for Docker and monitoring."""
    return HealthCheckRead()
