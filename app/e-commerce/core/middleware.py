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
"""Middleware for the Bandeh app: logging + timing, CORS, security headers, rate limiting.

Why middleware lives here (and not in the routes): a middleware runs for
EVERY request, before any route sees it. That is the right place for
cross-cutting work — logging, CORS, security headers, rate limiting — because
no route can forget to do it.

Starlette runs middleware in the REVERSE order they are added: the last
middleware added is the outermost one, so it runs first on the way in and
last on the way out. ``register_middleware`` adds them so the execution
order is: logging -> CORS -> security headers -> rate limiting -> route.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from fastapi.responses import Response
from starlette.middleware.cors import CORSMiddleware

# One logger for access lines, so tests and operators can find them in one place.
logger = logging.getLogger("bandeh.access")

# Default allowed origins for local development. The shop frontend is
# same-origin (Jinja pages served by this app), so CORS mainly matters for
# the future API door and local frontends on another port. Override with a
# comma-separated list in the BANDEH_CORS_ORIGINS environment variable.
_DEFAULT_ORIGINS = "http://localhost:8000,http://127.0.0.1:8000,http://localhost:3000"


def get_cors_origins() -> list[str]:
    """Return the allowed CORS origins from the environment (or the dev default)."""
    raw = os.environ.get("BANDEH_CORS_ORIGINS", _DEFAULT_ORIGINS)
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


# Content-Security-Policy: Tailwind is compiled locally to /static/css/tailwind.css.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self' https://cdn.jsdelivr.net https://unpkg.com 'unsafe-inline'; "
    "style-src 'self' https://cdn.jsdelivr.net 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self' data:; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

SECURITY_HEADERS: dict[str, str] = {
    # Stop the browser from guessing a file's type (blocks MIME-sniffing attacks).
    "X-Content-Type-Options": "nosniff",
    # Never let the shop load inside another site's <iframe> (blocks clickjacking).
    "X-Frame-Options": "DENY",
    # Tell the browser which origins may frame us (same as X-Frame-Options, modern form).
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    # Only send the origin (not the full URL) when leaving the site.
    "Referrer-Policy": "strict-origin-when-cross-origin",
    # Browsers only honour this over HTTPS; harmless on plain HTTP dev.
    "Strict-Transport-Security": "max-age=31536000; includeSubDomains",
}


class RateLimiter:
    """Sliding-window in-memory rate limiter per client IP for public API routes."""

    def __init__(
        self,
        default_limit: int = 120,
        default_window: float = 60.0,
        route_limits: dict[str, tuple[int, float]] | None = None,
    ) -> None:
        self.default_limit = default_limit
        self.default_window = default_window
        self.route_limits = route_limits or {
            "/auth/otp": (10, 60.0),
            "/auth/verify": (20, 60.0),
        }
        self._requests: dict[str, list[float]] = {}

    def get_client_ip(self, request: Request) -> str:
        """Extract client IP address respecting X-Forwarded-For."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
        if request.client:
            return request.client.host
        return "127.0.0.1"

    def is_rate_limited_route(self, path: str) -> bool:
        """Only public API routes are throttled; skip assets, health, and metadata."""
        if (
            path.startswith("/static")
            or path == "/health"
            or path == "/robots.txt"
            or path == "/sitemap.xml"
            or path == "/favicon.ico"
        ):
            return False
        return any(
            path.startswith(prefix)
            for prefix in (
                "/auth/otp",
                "/auth/verify",
                "/auth/refresh",
                "/orders",
                "/checkout",
                "/payments",
                "/products",
                "/categories",
                "/search",
            )
        )

    def check(self, request: Request) -> tuple[bool, int, float]:
        """Check if request is allowed. Returns (allowed, limit, retry_after)."""
        path = request.url.path
        if not self.is_rate_limited_route(path):
            return True, self.default_limit, 0.0

        limit = self.default_limit
        window = self.default_window
        matched_prefix = "general"
        for prefix, (r_limit, r_window) in self.route_limits.items():
            if path.startswith(prefix):
                limit = r_limit
                window = r_window
                matched_prefix = prefix
                break

        ip = self.get_client_ip(request)
        key = f"{ip}:{matched_prefix}"
        now = time.monotonic()
        cutoff = now - window

        history = self._requests.get(key, [])
        history = [ts for ts in history if ts > cutoff]

        if len(history) >= limit:
            oldest = history[0]
            retry_after = max(1.0, window - (now - oldest))
            self._requests[key] = history
            return False, limit, retry_after

        history.append(now)
        self._requests[key] = history
        return True, limit, 0.0

    def reset(self) -> None:
        """Clear all recorded request history (useful for tests)."""
        self._requests.clear()


default_rate_limiter = RateLimiter()


async def logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Log one line per request (method, path, status, milliseconds).

    Only the method, path, status and duration are logged — never headers,
    cookies or bodies, because those can carry secrets.
    """
    started = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - started) * 1000
    # The header lets clients (and tests) see the timing without reading logs.
    response.headers["X-Process-Time"] = f"{duration_ms:.3f}"
    logger.info(
        "%s %s -> %s in %.1fms",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


async def security_headers_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach the security headers to every response."""
    response = await call_next(request)
    for name, value in SECURITY_HEADERS.items():
        response.headers[name] = value
    return response


async def rate_limit_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Throttle requests to public API routes, returning 429 when limits are breached."""
    if os.environ.get("BANDEH_RATE_LIMIT_ENABLED", "1") != "0":
        allowed, limit, retry_after = default_rate_limiter.check(request)
        if not allowed:
            detail_msg = "تعداد درخواست‌ها بیش از حد مجاز است. لطفاً کمی بعد دوباره تلاش کنید."
            content = f'{{"detail":"{detail_msg}"}}'.encode()
            response = Response(
                content=content,
                status_code=429,
                media_type="application/json",
                headers={
                    "Retry-After": str(int(retry_after)),
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                },
            )
            for k, v in SECURITY_HEADERS.items():
                response.headers[k] = v
            return response

    return await call_next(request)


def register_middleware(app: FastAPI) -> None:
    """Register all middleware on the app, in the right order.

    Starlette executes middleware in reverse add order, so add them
    innermost-first: rate limiter, security headers, CORS, then logging.
    The execution order becomes logging -> CORS -> security headers -> rate limiter -> route.
    """
    app.middleware("http")(rate_limit_middleware)
    app.middleware("http")(security_headers_middleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=get_cors_origins(),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.middleware("http")(logging_middleware)


__all__ = [
    "CONTENT_SECURITY_POLICY",
    "RateLimiter",
    "SECURITY_HEADERS",
    "default_rate_limiter",
    "get_cors_origins",
    "logging_middleware",
    "rate_limit_middleware",
    "register_middleware",
    "security_headers_middleware",
]
