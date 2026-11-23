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
"""Standalone self-check for the ACP + UCP agent commerce endpoints.

Run with:

    python tests/check_agent_commerce.py

Uses an in-memory SQLite database and a fake Zibal (httpx
MockTransport), like tests/check_payments.py. Verifies:
- UCP discovery manifest (public) + services / capabilities / handler.
- UCP create/get/update/cancel, totals in Rial, catalog search.
- UCP complete: guest order + Zibal start URL + stock reserved;
  a foreign payment handler is rejected (422).
- ACP create (201), Idempotency-Key required (400), replay (same
  key + body -> Idempotent-Replayed), conflict (422), cancel on a
  completed session (405), unknown session (404).
- ACP complete with a Stripe SPT: the token string appears in NO
  Zibal request and NO stored state — only the Zibal link is returned.
- Order webhooks: signed delivery when a URL is configured, recorded
  as undelivered when it is not.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "app" / "e-commerce"))

os.environ["AGENT_API_KEY"] = "test-agent-key"

import httpx  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from db.base import Base  # noqa: E402
from db.models.agent_session import AgentCheckoutSession  # noqa: E402
from db.models.order import Order  # noqa: E402
from db.models.product_stock import ProductStock  # noqa: E402
from db.models.user import User  # noqa: E402
from db.session import get_db  # noqa: E402
from integrations.directus_client import get_directus_client  # noqa: E402
from integrations.zibal_client import ZibalClient, get_zibal_client  # noqa: E402
from main import app  # noqa: E402
from services.agent_commerce import emit_order_event  # noqa: E402

KEY = {"Authorization": "Bearer test-agent-key"}
FRIDGE_TOMAN = 100_000
TV_TOMAN = 50_000


class _FakeDirectusClient:
    """CMS stand-in: the two products this test buys."""

    def __init__(self, fridge_id: uuid.UUID, tv_id: uuid.UUID) -> None:
        self._rows = {
            str(fridge_id): {
                "id": str(fridge_id),
                "name": "یخچال",
                "description": None,
                "price_toman": FRIDGE_TOMAN,
                "image_path": None,
                "is_active": True,
            },
            str(tv_id): {
                "id": str(tv_id),
                "name": "تلویزیون",
                "description": None,
                "price_toman": TV_TOMAN,
                "image_path": None,
                "is_active": True,
            },
        }

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        return self._rows.get(str(product_id))

    async def list_products(self, limit: int = 20) -> list[dict]:
        return [dict(row) for row in self._rows.values()][:limit]

    async def get_products_by_ids(
        self, product_ids: list[uuid.UUID | str]
    ) -> list[dict]:
        return [
            dict(self._rows[str(pid)])
            for pid in product_ids
            if str(pid) in self._rows
        ]


def make_transport(state: dict):
    """Fake Zibal: hands out trackIds, verify answers from test state."""

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content or b"{}")
        if request.url.path.endswith("/v1/request"):
            state["requests"].append(body)
            state["last_request_body"] = body
            state["next_track_id"] += 1
            return httpx.Response(
                200,
                json={"result": 100, "message": "success", "trackId": state["next_track_id"]},
            )
        if request.url.path.endswith("/v1/verify"):
            return httpx.Response(
                200,
                json={
                    "result": 201,
                    "message": "verified",
                    "paidAt": "2026-11-20T17:20:00",
                    "cardNumber": "603799******1234",
                    "status": 2,
                    "orderId": body.get("orderId"),
                    "refNumber": "987654321",
                    "amount": state["verify_amount"],
                },
            )
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


async def main_async() -> None:
    state: dict = {
        "next_track_id": 111000,
        "last_request_body": {},
        "requests": [],
        "verify_amount": 0,
    }

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async def test_get_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_db] = test_get_db
    app.dependency_overrides[get_zibal_client] = lambda: ZibalClient(
        httpx.AsyncClient(transport=make_transport(state))
    )
    fridge_id = uuid.uuid4()
    tv_id = uuid.uuid4()
    app.dependency_overrides[get_directus_client] = (
        lambda: _FakeDirectusClient(fridge_id, tv_id)
    )
    client = TestClient(app)

    async with Session() as s:
        s.add_all(
            [
                ProductStock(directus_product_id=str(fridge_id), stock=5),
                ProductStock(directus_product_id=str(tv_id), stock=3),
            ]
        )
        await s.commit()

    async def session_row(session_id: str) -> AgentCheckoutSession:
        async with Session() as s:
            result = await s.execute(
                select(AgentCheckoutSession).where(
                    AgentCheckoutSession.session_id == session_id
                )
            )
            row = result.scalar_one()
            return row

    async def product_state(product_id: uuid.UUID) -> tuple[int, int]:
        async with Session() as s:
            p = await s.get(ProductStock, str(product_id))
            assert p is not None
            return p.stock, p.reserved

    async def order_by_id(order_id: str) -> Order:
        async with Session() as s:
            result = await s.execute(select(Order).where(Order.id == uuid.UUID(order_id)))
            return result.scalar_one()

    # 1. UCP discovery manifest is public -------------------------------
    resp = client.get("/.well-known/ucp")
    assert resp.status_code == 200, resp.status_code
    manifest = resp.json()
    assert manifest["business"]["name"] == "گنابِی", manifest
    shopping = manifest["services"]["dev.ucp.shopping"]
    assert shopping[0]["endpoint"] == "http://testserver/ucp", shopping
    assert shopping[0]["version"] == "2026-08-25"
    cap_names = set(manifest["capabilities"])
    assert "dev.ucp.shopping.checkout" in cap_names
    assert "dev.ucp.shopping.catalog" in cap_names
    assert manifest["payment_handlers"][0]["name"] == "ir.genabay.payments.zibal"
    assert manifest["payment_handlers"][0]["schema"].endswith(
        "/ucp/schemas/zibal-payment.json"
    )
    print("   [OK] UCP manifest: services, capabilities, Zibal handler")

    # 2. UCP auth: no key -> 401 -----------------------------------------
    resp = client.post(
        "/ucp/checkout-sessions",
        json={"line_items": [{"id": str(fridge_id), "quantity": 1}]},
    )
    assert resp.status_code == 401, resp.status_code
    print("   [OK] UCP without key: 401")

    # 3. UCP create: unknown product -> 422; valid -> incomplete ---------
    resp = client.post(
        "/ucp/checkout-sessions",
        json={"line_items": [{"id": str(uuid.uuid4()), "quantity": 1}]},
        headers=KEY,
    )
    assert resp.status_code == 422, resp.status_code
    resp = client.post(
        "/ucp/checkout-sessions",
        json={"line_items": [{"id": str(fridge_id), "quantity": 2}]},
        headers=KEY,
    )
    assert resp.status_code == 200, resp.status_code
    ucp = resp.json()
    assert ucp["id"].startswith("chk_"), ucp
    assert ucp["status"] == "incomplete", ucp
    assert ucp["currency"] == "irr", ucp
    assert ucp["line_items"][0]["item"]["price"] == FRIDGE_TOMAN * 10, ucp
    assert ucp["line_items"][0]["totals"][0]["amount"] == 2_000_000, ucp
    totals = {t["type"]: t["amount"] for t in ucp["totals"]}
    assert totals == {"subtotal": 2_000_000, "total": 2_000_000}, totals
    print("   [OK] UCP create: totals in Rial, currency irr, incomplete")

    # 4. UCP catalog: search / lookup / product --------------------------
    resp = client.post("/ucp/catalog/search", json={"query": "یخچال"}, headers=KEY)
    assert resp.status_code == 200, resp.status_code
    found = resp.json()["products"]
    assert len(found) == 1 and found[0]["price"] == FRIDGE_TOMAN * 10, found
    resp = client.post(
        "/ucp/catalog/lookup", json={"ids": [str(tv_id)]}, headers=KEY
    )
    assert resp.status_code == 200 and len(resp.json()["products"]) == 1
    resp = client.post("/ucp/catalog/product", json={"id": str(tv_id)}, headers=KEY)
    assert resp.status_code == 200 and resp.json()["id"] == str(tv_id)
    print("   [OK] UCP catalog: search, lookup, product")

    # 5. UCP update: add buyer -> ready_for_complete, reprice ------------
    ucp_id = ucp["id"]
    resp = client.put(
        f"/ucp/checkout-sessions/{ucp_id}",
        json={
            "buyer": {
                "first_name": "Mamad",
                "last_name": "Borazjani",
                "email": "mamad@example.com",
                "phone_number": "09123456789",
            },
            "fulfillment": {
                "address": {
                    "city": "Bandar Ganaveh",
                    "state": "Bushehr",
                    "country": "IR",
                    "postal_code": "75317",
                }
            },
        },
        headers=KEY,
    )
    assert resp.status_code == 200, resp.status_code
    assert resp.json()["status"] == "ready_for_complete", resp.json()
    print("   [OK] UCP update with buyer: ready_for_complete")

    # 6. UCP complete with a foreign handler -> 422, session stays open --
    resp = client.post(
        f"/ucp/checkout-sessions/{ucp_id}/complete",
        json={"payment": {"instruments": [{"handler_id": "google_pay"}]}},
        headers=KEY,
    )
    assert resp.status_code == 422, resp.status_code
    resp = client.get(f"/ucp/checkout-sessions/{ucp_id}", headers=KEY)
    assert resp.json()["status"] == "ready_for_complete"
    print("   [OK] UCP complete with Google Pay handler: 422, not charged")

    # 7. UCP complete with Zibal: order + start URL + stock reserved -----
    resp = client.post(
        f"/ucp/checkout-sessions/{ucp_id}/complete",
        json={"payment": {"instruments": [{"handler_id": "ir.genabay.payments.zibal"}]}},
        headers=KEY,
    )
    assert resp.status_code == 200, resp.status_code
    done = resp.json()
    assert done["status"] == "completed", done
    assert done["order"]["payment_url"].startswith("https://gateway.zibal.ir/start/"), done
    assert state["last_request_body"]["amount"] == 2_000_000, "Zibal must get Rial"
    assert done["order"]["totals"][0]["amount"] == 2_000_000
    order = await order_by_id(done["order"]["id"][4:])
    assert order.status == "waiting_payment"
    assert order.total_rial == 2_000_000
    async with Session() as s:
        user = await s.get(User, order.user_id)
        assert user is not None and user.phone == "09123456789", user
    stock, reserved = await product_state(fridge_id)
    assert (stock, reserved) == (5, 2), (stock, reserved)
    print("   [OK] UCP complete: guest order, Zibal link, stock reserved")

    # 8. UCP order read + cancel rules ------------------------------------
    resp = client.get(f"/ucp/orders/{done['order']['id']}", headers=KEY)
    assert resp.status_code == 200, resp.status_code
    assert resp.json()["totals"][0]["amount"] == 2_000_000
    resp = client.post(f"/ucp/checkout-sessions/{ucp_id}/cancel", headers=KEY)
    assert resp.status_code == 409, resp.status_code
    resp = client.post(
        "/ucp/checkout-sessions",
        json={"line_items": [{"id": str(tv_id), "quantity": 1}]},
        headers=KEY,
    )
    open_id = resp.json()["id"]
    resp = client.post(f"/ucp/checkout-sessions/{open_id}/cancel", headers=KEY)
    assert resp.status_code == 200 and resp.json()["status"] == "canceled"
    print("   [OK] UCP orders read; cancel completed=409, open=canceled")

    # 9. ACP create: key required, Idempotency-Key required, 201 ---------
    acp_body = {
        "line_items": [{"id": str(tv_id), "quantity": 1}],
        "buyer": {"first_name": "Mamad", "phone_number": "09123456789"},
    }
    resp = client.post("/acp/checkout_sessions", json=acp_body)
    assert resp.status_code == 401, resp.status_code
    resp = client.post("/acp/checkout_sessions", json=acp_body, headers=KEY)
    assert resp.status_code == 400, resp.status_code
    resp = client.post(
        "/acp/checkout_sessions",
        json=acp_body,
        headers={**KEY, "Idempotency-Key": "acp-key-1"},
    )
    assert resp.status_code == 201, resp.status_code
    acp = resp.json()
    assert acp["id"].startswith("cs_"), acp
    assert acp["status"] == "ready_for_payment", acp
    assert acp["line_items"][0]["unit_amount"] == TV_TOMAN * 10
    assert resp.headers["api-version"] == "2026-04-17"
    assert resp.headers["idempotency-key"] == "acp-key-1"
    print("   [OK] ACP create: 201, ready_for_payment, version + key echoed")

    # 10. ACP idempotency: replay -> same id + flag; conflict -> 422 ------
    resp = client.post(
        "/acp/checkout_sessions",
        json=acp_body,
        headers={**KEY, "Idempotency-Key": "acp-key-1"},
    )
    assert resp.status_code == 200, resp.status_code
    assert resp.headers.get("idempotent-replayed") == "true"
    assert resp.json()["id"] == acp["id"]
    other_body = {
        "line_items": [{"id": str(tv_id), "quantity": 2}],
        "buyer": {"first_name": "Mamad", "phone_number": "09123456789"},
    }
    resp = client.post(
        "/acp/checkout_sessions",
        json=other_body,
        headers={**KEY, "Idempotency-Key": "acp-key-1"},
    )
    assert resp.status_code == 422, resp.status_code
    resp = client.get(f"/acp/checkout_sessions/{acp['id']}", headers=KEY)
    assert resp.status_code == 200 and resp.json()["id"] == acp["id"]
    resp = client.get("/acp/checkout_sessions/cs_doesnotexist", headers=KEY)
    assert resp.status_code == 404, resp.status_code
    print("   [OK] ACP idempotency: replay flagged, conflict 422, unknown 404")

    # 11. ACP complete with an SPT: token never leaves the shop ----------
    resp = client.post(
        f"/acp/checkout_sessions/{acp['id']}/complete",
        json={"payment_data": {"provider": "stripe", "token": "spt_test_123"}},
        headers={**KEY, "Idempotency-Key": "acp-key-2"},
    )
    assert resp.status_code == 200, resp.status_code
    done = resp.json()
    assert done["status"] == "completed", done
    assert done["order"]["payment_url"].startswith("https://gateway.zibal.ir/start/")
    assert state["last_request_body"]["amount"] == TV_TOMAN * 10
    assert "spt_test_123" not in json.dumps(state["requests"]), "token must never reach Zibal"
    row = await session_row(acp["id"])
    assert "spt_test_123" not in json.dumps(row.state_json), "token must never be stored"
    assert row.state_json["token_offered"]["credential_type"] == "spt"
    assert any(
        "gateway.zibal.ir/start/" in m.get("content", "")
        for m in done.get("messages", [])
    ), done
    order = await order_by_id(done["order"]["id"][4:])
    assert order.status == "waiting_payment"
    stock, reserved = await product_state(tv_id)
    assert (stock, reserved) == (3, 1), (stock, reserved)
    print("   [OK] ACP complete with SPT: Zibal link, token never charged/stored")

    # 12. ACP cancel on completed -> 405 ----------------------------------
    resp = client.post(
        f"/acp/checkout_sessions/{acp['id']}/cancel",
        headers={**KEY, "Idempotency-Key": "acp-key-3"},
    )
    assert resp.status_code == 405, resp.status_code
    print("   [OK] ACP cancel on completed: 405")

    # 13. Webhooks: signed delivery with a URL, recorded without one ------
    captured: dict = {}

    def webhook_handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content or b"{}")
        return httpx.Response(200, json={"received": True, "request_id": "req-1"})

    os.environ["ACP_WEBHOOK_URL"] = "https://agent.example/hooks/order-events"
    # The signing secret is set ONLY for this step: with it set, the
    # routes would require a Signature on every POST.
    os.environ["ACP_SIGNING_SECRET"] = "test-secret"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(webhook_handler),
        base_url="https://agent.example",
    ) as http_client:
        record = await emit_order_event(
            "acp", "order_create", order, row, "http://testserver", http_client
        )
    os.environ.pop("ACP_SIGNING_SECRET", None)
    assert record["delivered"] is True
    assert captured["headers"].get("signature"), "webhook must be signed"
    assert captured["body"]["type"] == "order_create"
    assert captured["body"]["order"]["id"] == f"ord_{order.id.hex}"
    os.environ.pop("ACP_WEBHOOK_URL", None)
    record = await emit_order_event(
        "acp", "order_update", order, row, "http://testserver", None
    )
    assert record["delivered"] is False
    print("   [OK] webhooks: signed delivery with URL, recorded without one")

    print("ACP + UCP checks passed")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
