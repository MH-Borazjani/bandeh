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
"""Runnable self-check for the Directus Flow internal endpoints. Run from repo root:

    python tests/check_internal_directus.py

Architecture: ALL logic lives in this code (FastAPI + SQLAlchemy);
Directus is only the shell that shows the data and triggers actions.
A Directus Flow calls these endpoints with the shared secret in the
``X-Directus-Flow-Secret`` header. Covers: 503 when no secret is
configured, 401 on a missing or wrong secret, 404 on an unknown
order, the mark-shipped happy path (status + tracking code updated,
tracking SMS sent once), the once-only SMS guard (second ship with a
new code sends nothing; an order that already had a code sends
nothing), and the reindex 503 when Meilisearch is unconfigured.

In-memory SQLite + httpx MockTransport (no real SMS, no live panel,
no live Postgres).
"""

from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import services.sms_service as sms_service
from db.base import Base
from db.models.order import Order, OrderItem
from db.models.sms_log import SmsLog, SmsMessageType
from db.models.user import User, UserRole
from db.session import get_db
from env_settings import get_settings
from integrations.sms import MeliPayamakClient
from main import app

FLOW_SECRET = "test-directus-flow-secret-0123456789"
FLOW_HEADER = {"X-Directus-Flow-Secret": FLOW_SECRET}


def make_transport(state: dict) -> httpx.MockTransport:
    """A fake Meli Payamak panel: pattern sends always succeed."""

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        path = request.url.path
        if path in ("/api/SendSMS/BaseServiceNumber", "/api/SendSMS/SendSMS"):
            return httpx.Response(
                200,
                json={"Value": "777001", "RetStatus": 1, "StrRetStatus": "Ok"},
            )
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


async def main_async() -> None:
    os.environ["SECRET_KEY"] = "dummy-secret-key-for-tests-12345678"
    os.environ["POSTGRES_USER"] = "test"
    os.environ["POSTGRES_PASSWORD"] = "test"
    os.environ["POSTGRES_DB"] = "test"
    os.environ["MELI_SMS_TRACKING_BODY_ID"] = "103"
    # No DIRECTUS_FLOW_SECRET yet: the endpoints must be off (503).
    os.environ.pop("DIRECTUS_FLOW_SECRET", None)
    get_settings.cache_clear()

    state: dict = {"requests": []}

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    client = MeliPayamakClient(
        username="test-user",
        api_key="test-apikey",
        sender="10008888",
        http_client=httpx.AsyncClient(transport=make_transport(state)),
    )

    async def sms_rows(
        message_type: str | None = None, reference: str | None = None
    ) -> list[SmsLog]:
        async with Session() as s:
            query = select(SmsLog)
            if message_type is not None:
                query = query.where(SmsLog.message_type == message_type)
            if reference is not None:
                query = query.where(SmsLog.reference == reference)
            return list((await s.execute(query)).scalars().all())

    async with Session() as s:
        customer = User(phone="09121111111", role=UserRole.CUSTOMER, is_active=True)
        s.add(customer)
        await s.commit()
        await s.refresh(customer)

    async def make_order(
        status: str,
        tracking: str | None = None,
    ) -> uuid.UUID:
        async with Session() as s:
            order = Order(
                user_id=customer.id,
                status=status,
                total_toman=100_000,
                total_rial=1_000_000,
                recipient_phone="09121111111",
                tracking_code=tracking,
            )
            s.add(order)
            await s.flush()
            s.add(
                OrderItem(
                    order_id=order.id,
                    product_id=uuid.uuid4(),
                    product_name="یخچال",
                    quantity=1,
                    unit_price_toman=100_000,
                )
            )
            await s.commit()
            return order.id

    async def test_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = test_get_db
    orig_get_sms_client = sms_service.get_sms_client
    sms_service.get_sms_client = lambda: client  # noqa: SLF001
    try:
        http_client = TestClient(app)
        order_a_id = await make_order("paid")
        order_b_id = await make_order("paid", tracking="PRESET123")

        # 1. No secret configured -> 503 on both endpoints ---------------
        resp = http_client.post(
            f"/internal/directus/orders/{order_a_id}/status",
            json={"status": "shipped", "tracking_code": "TRK1"},
            headers=FLOW_HEADER,
        )
        assert resp.status_code == 503, resp.status_code
        resp = http_client.post(
            "/internal/directus/search/reindex", headers=FLOW_HEADER
        )
        assert resp.status_code == 503, resp.status_code
        print("   [OK] no secret configured -> 503 on both endpoints")

        # Turn the endpoints on.
        os.environ["DIRECTUS_FLOW_SECRET"] = FLOW_SECRET
        get_settings.cache_clear()

        # 2. Missing secret header -> 401 --------------------------------
        resp = http_client.post(
            f"/internal/directus/orders/{order_a_id}/status",
            json={"status": "shipped", "tracking_code": "TRK1"},
        )
        assert resp.status_code == 401, resp.status_code
        resp = http_client.post("/internal/directus/search/reindex")
        assert resp.status_code == 401, resp.status_code
        print("   [OK] missing secret header -> 401")

        # 3. Wrong secret -> 401 -----------------------------------------
        wrong = {"X-Directus-Flow-Secret": "wrong-secret"}
        resp = http_client.post(
            f"/internal/directus/orders/{order_a_id}/status",
            json={"status": "shipped", "tracking_code": "TRK1"},
            headers=wrong,
        )
        assert resp.status_code == 401, resp.status_code
        print("   [OK] wrong secret -> 401")

        # 4. Unknown order -> 404 ----------------------------------------
        resp = http_client.post(
            f"/internal/directus/orders/{uuid.uuid4()}/status",
            json={"status": "shipped", "tracking_code": "TRK1"},
            headers=FLOW_HEADER,
        )
        assert resp.status_code == 404, resp.status_code
        print("   [OK] unknown order -> 404")

        # 5. Happy path: first ship with a new code -> 200 + one SMS ----
        resp = http_client.post(
            f"/internal/directus/orders/{order_a_id}/status",
            json={"status": "shipped", "tracking_code": "TRK1"},
            headers=FLOW_HEADER,
        )
        assert resp.status_code == 200, resp.status_code
        assert resp.json()["status"] == "shipped", resp.json()
        assert resp.json()["tracking_code"] == "TRK1", resp.json()
        tracking_rows = await sms_rows(SmsMessageType.ORDER_TRACKING, str(order_a_id))
        assert len(tracking_rows) == 1, tracking_rows
        assert tracking_rows[0].body_id == 103, tracking_rows[0]
        assert tracking_rows[0].text == f"{order_a_id};TRK1", tracking_rows[0].text
        print("   [OK] mark shipped via Flow: 200 + one tracking SMS")

        # 6. Ship again with a new code -> 200, NO second SMS ------------
        resp = http_client.post(
            f"/internal/directus/orders/{order_a_id}/status",
            json={"status": "shipped", "tracking_code": "TRK2"},
            headers=FLOW_HEADER,
        )
        assert resp.status_code == 200, resp.status_code
        assert len(await sms_rows(SmsMessageType.ORDER_TRACKING, str(order_a_id))) == 1
        print("   [OK] second ship -> no second SMS")

        # 7. Order that already had a code -> 200, NO SMS ----------------
        resp = http_client.post(
            f"/internal/directus/orders/{order_b_id}/status",
            json={"status": "shipped", "tracking_code": "TRK9"},
            headers=FLOW_HEADER,
        )
        assert resp.status_code == 200, resp.status_code
        assert len(await sms_rows(SmsMessageType.ORDER_TRACKING, str(order_b_id))) == 0
        print("   [OK] order with preset code -> no SMS")

        # 8. Reindex with Meilisearch unconfigured -> 503 ----------------
        resp = http_client.post(
            "/internal/directus/search/reindex", headers=FLOW_HEADER
        )
        assert resp.status_code == 503, resp.status_code
        print("   [OK] reindex with Meilisearch unconfigured -> 503")
    finally:
        sms_service.get_sms_client = orig_get_sms_client  # noqa: SLF001
        app.dependency_overrides.pop(get_db, None)

    print("ALL INTERNAL-DIRECTUS CHECKS PASSED")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
