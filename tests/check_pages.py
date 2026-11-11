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
"""Runnable self-check for pages.py + landing.html. Run from repo root:

    python tests/check_pages.py

No server, no database: TestClient drives the real app in-process.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

from auth import SESSION_COOKIE_NAME, create_session, reset_auth_state
from fastapi import Response
from fastapi.testclient import TestClient
from main import app


def _logged_in_client() -> TestClient:
    """A TestClient carrying a valid session cookie (no OTP round-trip:
    the cookie is minted directly, exactly like a verified login does)."""
    reset_auth_state()
    probe = Response()
    create_session(probe, "09123456789")
    pair = probe.headers["set-cookie"].split(";", 1)[0]
    value = pair.split("=", 1)[1].strip().strip('"')
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE_NAME, value)
    return client


def main() -> None:
    reset_auth_state()
    guest = TestClient(app)

    # 1. Guest landing: 200, shop name, login link, no account link.
    r = guest.get("/")
    assert r.status_code == 200, r.status_code
    assert "بنده" in r.text
    assert 'href="/auth/login"' in r.text, "guest must see the login link"
    assert 'href="/auth/account"' not in r.text, r.text

    # 2. Logged-in landing: account link instead of login link.
    user = _logged_in_client()
    r = user.get("/")
    assert r.status_code == 200, r.status_code
    assert 'href="/auth/account"' in r.text
    assert 'href="/auth/login"' not in r.text, r.text

    # 3. Sections render: categories, sample products, trust, footer.
    assert "دسته‌بندی‌ها" in r.text
    assert "یخچال و فریزر" in r.text
    assert "پیشنهادهای ویژه" in r.text
    assert "یخچال فریزر دوقلو" in r.text
    assert "تومان" in r.text
    assert "ضمانت اصالت" in r.text

    print("All page checks passed.")


if __name__ == "__main__":
    main()
