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
"""One shared httpx AsyncClient for all server-to-server calls.

Why one client, and why httpx: when Bandeh needs data from another
server (Directus, Zibal, an SMS provider), YOUR server is the client.
httpx is the tool for that job. One client is created at startup and
shared by every request, so connections are reused instead of opening a
new TCP connection for every call.

Timeouts are set here, once, so no call can hang forever: if the other
server is slow, the call fails fast and the caller can fall back.
"""

from __future__ import annotations

from typing import Annotated

import httpx
from fastapi import Depends, Request

# No server-to-server call may take longer than this (seconds). Ten seconds
# is generous for a local Directus and strict enough that a hung gateway
# never blocks a shop request forever.
DEFAULT_TIMEOUT = 10.0


def create_http_client(
    transport: httpx.AsyncHTTPTransport | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> httpx.AsyncClient:
    """Build an AsyncClient with the project's timeout and pool limits.

    ``transport`` exists for tests: pass an ``httpx.MockTransport`` and no
    real network call is ever made.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        transport=transport,
        # Do NOT inherit proxy settings from the process environment: the
        # values there vary by machine and can be malformed (httpx fails
        # to even build the client). If a proxy is ever needed, configure
        # it explicitly here instead of inheriting it.
        trust_env=False,
    )


async def get_http_client(request: Request) -> httpx.AsyncClient:
    """Dependency: return the shared client, creating it lazily if needed.

    The lifespan in ``main.py`` normally creates the client at startup.
    The lazy fallback covers runs without a lifespan (for example a plain
    ``TestClient(app)``): a missing or already-closed client is replaced
    with a fresh one instead of failing the request.
    """
    client = getattr(request.app.state, "http_client", None)
    if client is None or client.is_closed:
        client = create_http_client()
        request.app.state.http_client = client
    return client


# Reusable FastAPI dependency alias for the shared HTTP client
HttpClientDep = Annotated[httpx.AsyncClient, Depends(get_http_client)]


__all__ = ["DEFAULT_TIMEOUT", "HttpClientDep", "create_http_client", "get_http_client"]
