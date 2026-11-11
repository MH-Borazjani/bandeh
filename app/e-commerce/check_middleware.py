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
"""Runnable self-check for core/middleware.py. Run from app/e-commerce:

    python check_middleware.py

No server, no database: TestClient drives the real app in-process.
"""

import logging

from fastapi.testclient import TestClient
from main import app


class _ListHandler(logging.Handler):
    """A logging handler that stores formatted lines in a list."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def main() -> None:
    client = TestClient(app)

    # 1. Security headers are on every response, including the health check.
    r = client.get("/health")
    assert r.status_code == 200, r.status_code
    assert r.headers["X-Content-Type-Options"] == "nosniff", r.headers
    assert r.headers["X-Frame-Options"] == "DENY", r.headers
    assert "Content-Security-Policy" in r.headers, r.headers
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]

    # 2. Timing: the X-Process-Time header is present and parses as a number.
    process_time = float(r.headers["X-Process-Time"])
    assert process_time >= 0, r.headers["X-Process-Time"]

    # 3. The access logger wrote one line for the request above.
    logger = logging.getLogger("bandeh.access")
    handler = _ListHandler()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        client.get("/health")
    finally:
        logger.removeHandler(handler)
    assert len(handler.lines) == 1, handler.lines
    assert "GET /health -> 200" in handler.lines[0], handler.lines
    assert "ms" in handler.lines[0], handler.lines

    # 4. CORS preflight from an allowed origin is answered by the CORS layer.
    r = client.options(
        "/health",
        headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "GET",
        },
    )
    assert r.status_code == 200, r.status_code
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000", r.headers

    # 5. A normal cross-origin GET gets the allow-origin header back.
    r = client.get("/health", headers={"Origin": "http://localhost:3000"})
    assert r.status_code == 200, r.status_code
    assert r.headers["access-control-allow-origin"] == "http://localhost:3000", r.headers

    # 6. An origin that is NOT allowed gets no allow-origin header.
    r = client.get("/health", headers={"Origin": "https://evil.example.com"})
    assert r.status_code == 200, r.status_code
    assert "access-control-allow-origin" not in r.headers, r.headers

    print("All middleware checks passed.")


if __name__ == "__main__":
    main()
