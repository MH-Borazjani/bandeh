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
"""Unit tests for the health check endpoint."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

# Ensure app/e-commerce is importable regardless of working directory
E_COMMERCE_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(E_COMMERCE_DIR) not in sys.path:
    sys.path.insert(0, str(E_COMMERCE_DIR))

from main import app  # noqa: E402


class HealthCheckTests(unittest.TestCase):
    """Tests for the GET /health endpoint."""

    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health_check_returns_ok(self) -> None:
        """GET /health must return 200 and a JSON object with status equal to 'ok'."""
        response = self.client.get("/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response.json().get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
