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
"""Runnable self-check for Zibal payments. Run from repo root:

    python tests/check_payments.py

In-memory SQLite + httpx MockTransport (no real Zibal, no live
Postgres). Covers: order totals (Toman x10 = Rial), stock reservation,
idempotent start, paid exactly once on double callback, the callback
hint ignored (success=0 still pays when verify says 100), verify-201
replay, amount mismatch blocks payment, failed callback releases
stock, ownership (404), insufficient stock at start, gateway refusal
(502 + compensation), expired-hold release, cancel, unknown trackId.
"""

from __future__ import annotations

import asyncio
import datetime
import json
from pathlib import Path
import sys
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))

import httpx
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.datetime_utils import utcnow
from core.security import SESSION_COOKIE_NAME, create_session
from db.base import Base
from db.models.order import Order
from db.models.payment_attempt import PaymentAttempt
from db.models.product import Product
from db.models.user import User, UserRole
from db.session import get_db
from integrations.zibal_client import ZibalClient, get_zibal_client
from main import app
from services.payment_service import release_expired_reservations
from services.session_service import create_database_session


def cookie_value(response: Response, name: str) -> str:
    """Pull one cookie's value out of a response's Set-Cookie headers."""
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list"
    )
    for header in getlist("set-cookie"):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


def make_transport(state: dict) -> httpx.MockTransport:
    """A fake Zibal: /v1/request hands out trackIds, /v1/verify answers
    whatever the test put in state['verify'] (a dict or a callable)."""

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if request.url.path == "/v1/request":
            if state.get("request_result", 100) != 100:
                return httpx.Response(
                    200,
                    json={
                        "result": state["request_result"],
                        "message": state.get("request_message", "gateway error"),
                        "trackId": 0,
                    },
                )
            track_id = state["next_track_id"]
            state["next_track_id"] += 1
            state["last_request_body"] = json.loads(request.content)
            return httpx.Response(
                200, json={"result": 100, "message": "success", "trackId": track_id}
            )
        if request.url.path == "/v1/verify":
            verify = state.get("verify")
            if callable(verify):
                verify = verify(json.loads(request.content))
            return httpx.Response(200, json=verify)
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def paid_verify(amount_rial: int, result: int = 100) -> dict:
    """A verify-100 body for a payment of ``amount_rial`` Rial."""
    return {
        "result": result,
        "paidAt": "2026-11-11T10:30:00+00:00",
        "cardNumber": "603799******1234",
        "status": 1,
        "orderId": "ignored-by-the-service",
        "refNumber": 987654321,
        "amount": amount_rial,
    }


async def main_async() -> None:
    state: dict = {
        "requests": [],
        "next_track_id": 111000,
        "request_result": 100,
        "verify": lambda body: paid_verify(state["verify_amount"]),
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
    client = TestClient(app)

    async with Session() as s:
        customer = User(phone="09121111111", role=UserRole.CUSTOMER, is_active=True)
        other = User(phone="09129999999", role=UserRole.CUSTOMER, is_active=True)
        fridge = Product(name="یخچال", price_toman=100_000, stock=5)
        tv = Product(name="تلویزیون", price_toman=50_000, stock=3)
        s.add_all([customer, other, fridge, tv])
        await s.commit()
        await s.refresh(customer)
        await s.refresh(other)
        await s.refresh(fridge)
        await s.refresh(tv)

    async def mint_session(user: User) -> str:
        async with Session() as s:
            record = await create_database_session(s, user)
            await s.commit()
            token = record.session_token
        resp = Response()
        create_session(resp, user.phone, session_token=token)
        return cookie_value(resp, SESSION_COOKIE_NAME)

    customer_cookie = await mint_session(customer)
    other_cookie = await mint_session(other)

    def set_session(cookie: str | None) -> None:
        client.cookies.clear()
        if cookie:
            client.cookies.set(SESSION_COOKIE_NAME, cookie)

    async def product_state(product_id):
        async with Session() as s:
            p = await s.get(Product, product_id)
            return p.stock, p.reserved

    async def order_state(order_id):
        async with Session() as s:
            oid = uuid.UUID(order_id) if isinstance(order_id, str) else order_id
            result = await s.execute(select(Order).where(Order.id == oid))
            order = result.scalar_one()
            attempts = (
                await s.execute(
                    select(PaymentAttempt).where(PaymentAttempt.order_id == oid)
                )
            ).scalars().all()
            return order.status, attempts

    def make_order(cookie: str, product_id, quantity: int = 1) -> str:
        set_session(cookie)
        resp = client.post(
            "/orders", json={"items": [{"product_id": str(product_id), "quantity": quantity}]}
        )
        assert resp.status_code == 201, resp.status_code
        return resp.json()["id"]

    def start_order(cookie: str, order_id: str):
        set_session(cookie)
        return client.post(f"/payments/{order_id}/start")

    def callback(track_id: int, order_id: str, success: str = "1", status: str = "1"):
        set_session(None)
        return client.get(
            "/payments/callback",
            params={"trackId": track_id, "success": success, "status": status, "orderId": order_id},
        )

    # 1. Order totals: Toman x10 = Rial, prices frozen ----------------------
    set_session(customer_cookie)
    resp = client.post(
        "/orders",
        json={"items": [{"product_id": str(fridge.id), "quantity": 2}]},
    )
    assert resp.status_code == 201, resp.status_code
    order1 = resp.json()["id"]
    assert resp.json()["total_toman"] == 200_000, resp.json()
    assert resp.json()["total_rial"] == 2_000_000, resp.json()
    assert resp.json()["items"][0]["unit_price_toman"] == 100_000
    print("   [OK] order totals: 200,000 Toman = 2,000,000 Rial, price frozen")

    # 2. Start: reservation, attempt, start URL, Zibal got Rial -------------
    state["verify_amount"] = 2_000_000
    resp = start_order(customer_cookie, order1)
    assert resp.status_code == 200, resp.status_code
    track1 = resp.json()["track_id"]
    assert resp.json()["payment_url"] == f"https://gateway.zibal.ir/start/{track1}"
    assert state["last_request_body"]["amount"] == 2_000_000, "Zibal must get Rial"
    assert state["last_request_body"]["merchant"] == "zibal"
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (5, 2), (stock, reserved)
    status, attempts = await order_state(order1)
    assert status == "waiting_payment"
    assert len(attempts) == 1 and attempts[0].idempotency_key is not None
    assert attempts[0].amount_rial == 2_000_000
    print("   [OK] start: stock reserved, attempt with idempotency key, Rial to Zibal")

    # 3. Idempotent start: same URL, one attempt, reserved once --------------
    resp = start_order(customer_cookie, order1)
    assert resp.status_code == 200
    assert resp.json()["track_id"] == track1
    _, attempts = await order_state(order1)
    assert len(attempts) == 1
    _, reserved = await product_state(fridge.id)
    assert reserved == 2, "second start must not reserve again"
    print("   [OK] start twice: same URL, one attempt, reserved once")

    # 4. Paid callback: stock moves exactly once ----------------------------
    resp = callback(track1, order1)
    assert resp.status_code == 200, resp.status_code
    assert resp.json()["status"] == "paid", resp.json()
    assert resp.json()["ref_number"] == "987654321"
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (3, 0), (stock, reserved)
    status, attempts = await order_state(order1)
    assert status == "paid" and attempts[0].status == "paid"
    assert attempts[0].card_number == "603799******1234"
    print("   [OK] callback paid: stock 5->3, reserved 2->0, refNumber saved")

    # 5. Double callback: already paid, nothing moves ------------------------
    resp = callback(track1, order1)
    assert resp.status_code == 200
    assert resp.json()["status"] == "already_paid", resp.json()
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (3, 0), "double callback must not move stock again"
    print("   [OK] double callback: already_paid, stock untouched")

    # 6. The hint is ignored: success=0 still pays when verify says 100 ----
    order2 = make_order(customer_cookie, tv.id, 1)
    state["verify_amount"] = 500_000
    resp = start_order(customer_cookie, order2)
    track2 = resp.json()["track_id"]
    resp = callback(track2, order2, success="0", status="3")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid", resp.json()
    stock, reserved = await product_state(tv.id)
    assert (stock, reserved) == (2, 0)
    print("   [OK] callback hint ignored: success=0 still paid on verify 100")

    # 7. Verify-201 replay: paid, stock moves once ---------------------------
    order3 = make_order(customer_cookie, tv.id, 1)
    state["verify_amount"] = 500_000
    state["verify"] = lambda body: paid_verify(500_000, result=201)
    resp = start_order(customer_cookie, order3)
    track3 = resp.json()["track_id"]
    resp = callback(track3, order3)
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid", resp.json()
    stock, reserved = await product_state(tv.id)
    assert (stock, reserved) == (1, 0), (stock, reserved)
    state["verify"] = lambda body: paid_verify(state["verify_amount"])
    print("   [OK] verify 201: paid, stock moved once")

    # 8. Amount mismatch: NOT paid, hold released ----------------------------
    order4 = make_order(customer_cookie, fridge.id, 1)  # 100,000 Toman = 1,000,000 Rial
    resp = start_order(customer_cookie, order4)
    track4 = resp.json()["track_id"]
    state["verify_amount"] = 999_999  # verify claims a different amount
    resp = callback(track4, order4)
    assert resp.status_code == 200
    assert resp.json()["status"] == "failed", resp.json()
    assert resp.json()["reason"] == "amount_mismatch", resp.json()
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (3, 0), (stock, reserved)
    status, _ = await order_state(order4)
    assert status == "failed"
    print("   [OK] amount mismatch: not paid, hold released")

    # 9. Failed callback (verify 202): hold released -------------------------
    order5 = make_order(customer_cookie, fridge.id, 1)
    state["verify_amount"] = 100_000
    resp = start_order(customer_cookie, order5)
    track5 = resp.json()["track_id"]
    state["verify"] = lambda body: paid_verify(100_000, result=202)
    resp = callback(track5, order5)
    assert resp.json()["status"] == "failed", resp.json()
    assert resp.json()["reason"] == "not_paid"
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (3, 0)
    state["verify"] = lambda body: paid_verify(state["verify_amount"])
    print("   [OK] verify 202: failed, hold released")

    # 10. Ownership: another user's order is invisible ------------------------
    assert start_order(other_cookie, order5).status_code == 404
    set_session(other_cookie)
    assert client.get(f"/payments/{order5}/status").status_code == 404
    assert client.post(f"/payments/{order5}/cancel").status_code == 404
    print("   [OK] ownership: other user's order is 404 everywhere")

    # 11. Insufficient stock at start: 409, nothing reserved -----------------
    order6 = make_order(customer_cookie, fridge.id, 2)  # stock 3, ok at creation
    async with Session() as s:
        p = await s.get(Product, fridge.id)
        p.stock = 1  # stock drops before payment starts
        await s.commit()
    resp = start_order(customer_cookie, order6)
    assert resp.status_code == 409, resp.status_code
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (1, 0)
    _, attempts = await order_state(order6)
    assert attempts == []
    async with Session() as s:  # restore stock for the tests below
        p = await s.get(Product, fridge.id)
        p.stock = 3
        await s.commit()
    print("   [OK] insufficient stock at start: 409, nothing reserved")

    # 12. Gateway refusal (result 105): 502, hold released, order failed -----
    order7 = make_order(customer_cookie, fridge.id, 1)
    state["request_result"] = 105
    resp = start_order(customer_cookie, order7)
    assert resp.status_code == 502, resp.status_code
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (3, 0), "refusal must release the hold"
    status, attempts = await order_state(order7)
    assert status == "failed" and attempts[0].status == "failed"
    state["request_result"] = 100
    print("   [OK] gateway refusal: 502, hold released, order failed")

    # 13. Expired holds: old one freed, fresh one kept ------------------------
    order8 = make_order(customer_cookie, fridge.id, 1)
    state["verify_amount"] = 100_000
    resp = start_order(customer_cookie, order8)
    track8 = resp.json()["track_id"]
    order9 = make_order(customer_cookie, tv.id, 1)
    state["verify_amount"] = 500_000
    start_order(customer_cookie, order9)
    async with Session() as s:
        result = await s.execute(
            select(PaymentAttempt).where(
                PaymentAttempt.order_id == uuid.UUID(order8)
            )
        )
        attempt8 = result.scalar_one()
        attempt8.created_at = utcnow() - datetime.timedelta(minutes=16)
        await s.commit()
    async with Session() as s:
        released = await release_expired_reservations(s)
    assert released == 1, released
    stock, reserved = await product_state(fridge.id)
    assert (stock, reserved) == (3, 0)
    status, attempts = await order_state(order8)
    assert status == "expired" and attempts[0].status == "expired"
    stock, reserved = await product_state(tv.id)
    assert reserved == 1, "the fresh hold must survive"
    print("   [OK] expired holds: old freed, fresh kept")

    # 14. Cancel: hold freed, order failed -------------------------------------
    set_session(customer_cookie)
    resp = client.post(f"/payments/{order9}/cancel")
    assert resp.status_code == 200, resp.status_code
    stock, reserved = await product_state(tv.id)
    assert (stock, reserved) == (1, 0)
    status, _ = await order_state(order9)
    assert status == "failed"
    print("   [OK] cancel: hold freed, order failed")

    # 15. Unknown trackId callback: 404 ---------------------------------------
    resp = callback(999999, order9)
    assert resp.status_code == 404, resp.status_code
    print("   [OK] unknown trackId: 404")

    # 16. Status endpoint shows the payment ------------------------------------
    set_session(customer_cookie)
    resp = client.get(f"/payments/{order1}/status")
    assert resp.status_code == 200
    assert resp.json()["order_status"] == "paid"
    assert resp.json()["payment"]["ref_number"] == "987654321"
    print("   [OK] status endpoint: paid + refNumber")

    print("All payment checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
