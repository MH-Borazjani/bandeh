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
"""Runnable self-check for the Meli Payamak SMS integration. Run from repo root:

    python tests/check_sms.py

In-memory SQLite + httpx MockTransport (no real SMS, no live panel,
no live Postgres). The primary path is the pattern send over the
shared service line (POST /api/SendSMS/BaseServiceNumber, form fields
username/password/to/text/bodyId, text = ';'-joined variables) —
mirroring the official library's ``send_by_base_number``. Covers:
pattern send success/failure, the plain-SendSMS fallback, GetDeliveries2
status codes, the error-code map, the bodyId coming from env (not
hardcoded) and the clear error on a missing bodyId, the log-then-send
ordering (row "queued" before the provider is called, "sent" + provider
id after, "failed" + re-raise on error), the once-only low-stock alert
and payment reminder, the failed-payment reminder worker (15 min /
24 h window), the tracking-SMS guard at the real admin route (only
when the tracking code is newly set), and the OTP dev fallback
(populates _DEV_LAST_CODE, writes no DB row).
"""

from __future__ import annotations

import asyncio
import datetime
import os
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app" / "e-commerce"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from fastapi import Response
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import services.scheduler as scheduler_module
import services.sms_service as sms_service
from core.datetime_utils import utcnow
from core.security import SESSION_COOKIE_NAME, create_session
from db.base import Base
from db.models.order import Order, OrderItem
from db.models.product import Product
from db.models.sms_log import SmsLog, SmsMessageType
from db.models.user import User, UserRole
from db.session import get_db
from env_settings import get_settings
from integrations.sms import (
    _DEV_LAST_CODE,
    _DEV_SENT_MESSAGES,
    MeliPayamakClient,
    MeliPayamakError,
    get_sms_client,
)
from main import app
from services.auth_service import request_otp
from services.order_service import load_order
from services.scheduler import FailedPaymentReminderTask
from services.session_service import create_database_session
from services.sms_service import (
    SmsConfigError,
    check_low_stock_after_payment,
    log_and_send,
    send_low_stock_sms,
    send_order_paid_sms,
    send_otp_sms,
    send_otp_sync,
)


def cookie_value(response: Response, name: str) -> str:
    """Pull one cookie's value out of a response's Set-Cookie headers."""
    getlist = getattr(response.headers, "getlist", None) or getattr(
        response.headers, "get_list", None
    )
    assert callable(getlist)
    for header in getlist("set-cookie"):
        if header.startswith(name + "="):
            return header.split(";", 1)[0].split("=", 1)[1]
    raise AssertionError(f"cookie {name} was not set")


def make_transport(state: dict) -> httpx.MockTransport:
    """A fake Meli Payamak panel: pattern sends, plain sends, deliveries, credit."""

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        path = request.url.path
        if path in ("/api/SendSMS/BaseServiceNumber", "/api/SendSMS/SendSMS"):
            if state.get("fail_code") is not None:
                return httpx.Response(
                    200,
                    json={
                        "Value": "",
                        "RetStatus": state["fail_code"],
                        "StrRetStatus": "Err",
                    },
                )
            provider_id = "777001" if "BaseServiceNumber" in path else "777002"
            return httpx.Response(
                200,
                json={"Value": provider_id, "RetStatus": 1, "StrRetStatus": "Ok"},
            )
        if path == "/api/SendSMS/GetDeliveries2":
            return httpx.Response(
                200,
                json={
                    "Value": state.get("delivery_value", "1"),
                    "RetStatus": 1,
                    "StrRetStatus": "Ok",
                },
            )
        if path == "/api/SendSMS/GetCredit":
            return httpx.Response(
                200,
                json={
                    "Value": state.get("credit_value", "1234"),
                    "RetStatus": 1,
                    "StrRetStatus": "Ok",
                },
            )
        return httpx.Response(404, json={})

    return httpx.MockTransport(handler)


def form_fields(request: httpx.Request) -> dict[str, str]:
    """Decode a form-encoded request body into single values."""
    parsed = parse_qs(request.content.decode("utf-8"))
    return {key: values[0] for key, values in parsed.items()}


async def main_async() -> None:
    # Pattern codes come from env, like in production. Dummy settings
    # values let Settings validate (no real Postgres is ever touched:
    # every test session is the in-memory SQLite engine below).
    os.environ["SECRET_KEY"] = "dummy-secret-key-for-tests-12345678"
    os.environ["POSTGRES_USER"] = "test"
    os.environ["POSTGRES_PASSWORD"] = "test"
    os.environ["POSTGRES_DB"] = "test"
    os.environ["MELI_SMS_OTP_BODY_ID"] = "101"
    os.environ["MELI_SMS_ORDER_BODY_ID"] = "102"
    os.environ["MELI_SMS_TRACKING_BODY_ID"] = "103"
    os.environ["MELI_SMS_OWNER_BODY_ID"] = "104"
    get_settings.cache_clear()

    state: dict = {"requests": [], "fail_code": None, "delivery_value": "1"}

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    def make_client() -> MeliPayamakClient:
        return MeliPayamakClient(
            username="test-user",
            api_key="test-apikey",
            sender="10008888",
            http_client=httpx.AsyncClient(transport=make_transport(state)),
        )

    client = make_client()
    assert client.is_configured
    assert not MeliPayamakClient(username=None, api_key=None).is_configured

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

    # Seed users + products -------------------------------------------------
    async with Session() as s:
        customer = User(phone="09121111111", role=UserRole.CUSTOMER, is_active=True)
        admin = User(phone="09120000001", role=UserRole.ADMIN, is_active=True)
        fridge = Product(name="یخچال", price_toman=100_000, stock=5)
        tv = Product(name="تلویزیون", price_toman=50_000, stock=3)
        s.add_all([customer, admin, fridge, tv])
        await s.commit()
        for obj in (customer, admin, fridge, tv):
            await s.refresh(obj)

    async def make_order(
        status: str,
        phone: str | None = None,
        tracking: str | None = None,
        items: list[tuple[uuid.UUID, int]] | None = None,
        updated_ago_minutes: int | None = None,
    ) -> uuid.UUID:
        async with Session() as s:
            order = Order(
                user_id=customer.id,
                status=status,
                total_toman=100_000,
                total_rial=1_000_000,
                recipient_phone=phone,
                tracking_code=tracking,
            )
            s.add(order)
            await s.flush()
            for product_id, quantity in items or []:
                s.add(
                    OrderItem(
                        order_id=order.id,
                        product_id=product_id,
                        product_name="یخچال",
                        quantity=quantity,
                        unit_price_toman=100_000,
                    )
                )
            if updated_ago_minutes is not None:
                order.updated_at = utcnow() - datetime.timedelta(
                    minutes=updated_ago_minutes
                )
            await s.commit()
            return order.id

    async def load_order_by_id(order_id: uuid.UUID) -> Order:
        async with Session() as s:
            order = await load_order(s, order_id)
            assert order is not None
            return order

    # 1. BodyIds come from env, not hardcoded ----------------------------------
    assert sms_service._get_body_id("otp") == 101  # noqa: SLF001
    assert sms_service._get_body_id("order") == 102  # noqa: SLF001
    assert sms_service._get_body_id("tracking") == 103  # noqa: SLF001
    assert sms_service._get_body_id("owner") == 104  # noqa: SLF001
    print("   [OK] bodyIds come from env (otp 101, order 102, tracking 103, owner 104)")

    # 2. Pattern send success: provider id, exact BaseServiceNumber form -------
    provider_id = await client.send_pattern("09121111111", 101, ["12345"])
    assert provider_id == "777001", provider_id
    req = state["requests"][-1]
    assert req.url.path == "/api/SendSMS/BaseServiceNumber", req.url.path
    fields = form_fields(req)
    assert fields["username"] == "test-user", fields
    assert fields["password"] == "test-apikey", fields
    assert fields["to"] == "09121111111", fields
    assert fields["text"] == "12345", fields
    assert fields["bodyId"] == "101", fields
    print("   [OK] pattern send: BaseServiceNumber + username/password/to/text/bodyId")

    # 3. Pattern send joins variables with ';' -----------------------------------
    provider_id = await client.send_pattern(
        "09121111111", 102, ["order-1", "100,000"]
    )
    assert provider_id == "777001", provider_id
    fields = form_fields(state["requests"][-1])
    assert fields["text"] == "order-1;100,000", fields
    assert fields["bodyId"] == "102", fields
    print("   [OK] pattern variables: joined with ';' into one text field")

    # 4. Pattern send failure: RetStatus 0 raises with the code ------------------
    state["fail_code"] = 0
    try:
        await client.send_pattern("09121111111", 101, ["12345"])
        raise AssertionError("RetStatus 0 did not raise")
    except MeliPayamakError as exc:
        assert exc.status == 0, exc.status
        assert "wrong username or password" in exc.message, exc.message
    state["fail_code"] = None
    print("   [OK] pattern send failure: RetStatus 0 -> MeliPayamakError(0)")

    # 5. Plain SendSMS fallback still works ---------------------------------------
    provider_id = await client.send_sms("09121111111", "متن تبلیغاتی")
    assert provider_id == "777002", provider_id
    req = state["requests"][-1]
    assert req.url.path == "/api/SendSMS/SendSMS", req.url.path
    assert form_fields(req)["isFlash"] == "false"
    print("   [OK] plain SendSMS fallback: RetStatus 1 -> provider id 777002")

    # 6. GetDeliveries2 returns the status code ------------------------------------
    assert await client.get_delivery_status("777001") == 1
    state["delivery_value"] = "8"
    assert await client.get_delivery_status("777001") == 8
    state["delivery_value"] = "1"
    print("   [OK] GetDeliveries2: 1 (delivered), then 8 (delivered)")

    # 7. Error-code map: RetStatus 2 and 53 -----------------------------------------
    state["fail_code"] = 2
    try:
        await client.send_pattern("09121111111", 101, ["12345"])
        raise AssertionError("RetStatus 2 did not raise")
    except MeliPayamakError as exc:
        assert exc.status == 2, exc.status
        assert "insufficient credit" in exc.message, exc.message
    state["fail_code"] = 53
    try:
        await client.send_pattern("09121111111", 101, ["12345"])
        raise AssertionError("RetStatus 53 did not raise")
    except MeliPayamakError as exc:
        assert exc.status == 53, exc.status
        assert "blacklist" in exc.message, exc.message
    state["fail_code"] = None
    print("   [OK] error map: 2 -> 'insufficient credit', 53 -> blacklist")

    # 8. GetCredit --------------------------------------------------------------------
    assert await client.get_credit() == 1234
    print("   [OK] GetCredit: 1234")

    # 9. log_and_send: row is "queued" BEFORE the provider is called ----------------
    seen: dict = {}

    async def fake_send() -> str:
        async with Session() as s2:
            row = (
                await s2.execute(
                    select(SmsLog).where(SmsLog.message_type == "otp")
                )
            ).scalar_one()
            seen["status_at_call"] = row.status
            seen["provider_at_call"] = row.provider_message_id
        return "999"

    async with Session() as s:
        row = await log_and_send(
            s,
            phone="09121111111",
            message_type="otp",
            text="00000",
            body_id=101,
            send=fake_send,
        )
        assert seen["status_at_call"] == "queued", seen
        assert seen["provider_at_call"] is None, seen
        assert row.status == "sent", row.status
        assert row.provider_message_id == "999", row.provider_message_id
        assert row.body_id == 101, row.body_id
    print("   [OK] log-then-send: row 'queued' at call time, 'sent' + id after")

    # 10. Send failure: row marked "failed", exception propagates ----------------------
    async def failing_send() -> str:
        raise MeliPayamakError(2, "insufficient credit")

    async with Session() as s:
        try:
            await log_and_send(
                s,
                phone="09121111111",
                message_type="otp",
                text="00000",
                body_id=101,
                send=failing_send,
            )
            raise AssertionError("failing send did not raise")
        except MeliPayamakError as exc:
            assert exc.status == 2
    failed = [
        r
        for r in await sms_rows("otp")
        if r.status == "failed" and r.provider_message_id is None
    ]
    assert len(failed) == 1, failed
    print("   [OK] send failure: row 'failed', exception propagates")

    # 11. OTP via the OTP pattern, logged ----------------------------------------------
    requests_before = len(state["requests"])
    async with Session() as s:
        row = await send_otp_sms(s, "09121111111", "12345", client=client)
        assert row.message_type == SmsMessageType.OTP
        assert row.status == "sent" and row.provider_message_id == "777001"
        assert row.text == "12345"
        assert row.body_id == 101
    fields = form_fields(state["requests"][-1])
    assert fields["bodyId"] == "101" and fields["text"] == "12345"
    assert len(state["requests"]) == requests_before + 1
    print("   [OK] send_otp_sms: OTP pattern 101, row sent + provider id stored")

    # 12. Missing bodyId with a configured client: clear error, no send, no row ---------
    orig_get_body_id = sms_service._get_body_id  # noqa: SLF001
    sms_service._get_body_id = lambda kind: None  # noqa: SLF001
    try:
        otp_rows_before = await sms_rows("otp")
        requests_before = len(state["requests"])
        async with Session() as s:
            try:
                await send_otp_sms(s, "09121111111", "12345", client=client)
                raise AssertionError("missing bodyId did not raise")
            except SmsConfigError as exc:
                assert "MELI_SMS_OTP_BODY_ID" in str(exc), str(exc)
        assert len(state["requests"]) == requests_before, "nothing must be sent"
        assert len(await sms_rows("otp")) == len(otp_rows_before), "no row logged"
    finally:
        sms_service._get_body_id = orig_get_body_id  # noqa: SLF001
    print("   [OK] missing bodyId: SmsConfigError names the env var, nothing sent")

    # 13. Order-paid SMS via the order pattern ------------------------------------------
    order_paid_id = await make_order("paid", phone="09121111111")
    order_paid = await load_order_by_id(order_paid_id)
    async with Session() as s:
        row = await send_order_paid_sms(s, order_paid, client=client)
        assert row is not None
        assert row.message_type == SmsMessageType.ORDER_PAID
        assert row.reference == str(order_paid_id)
        assert row.status == "sent"
        assert row.body_id == 102
        assert row.text == f"{order_paid_id};100,000", row.text
    fields = form_fields(state["requests"][-1])
    assert fields["bodyId"] == "102", fields
    print("   [OK] send_order_paid_sms: order pattern 102, variables id + total")

    # Owner phone: patched for the owner/low-stock checks below -----------------
    orig_owner_phone = sms_service._get_owner_phone
    sms_service._get_owner_phone = lambda: "09120000000"  # noqa: SLF001
    try:
        # 14. Low-stock alert sends once per product (owner pattern) ----------------
        requests_before = len(state["requests"])
        async with Session() as s:
            product = await s.get(Product, tv.id)
            assert product is not None
            product.stock = 2  # available 2 <= threshold 2
            await s.commit()
            row = await send_low_stock_sms(s, product, client=client)
            assert row is not None and row.status == "sent"
            assert row.reference == str(tv.id)
            assert row.body_id == 104, row.body_id
            row2 = await send_low_stock_sms(s, product, client=client)
            assert row2 is None, "second low-stock alert must be a no-op"
        assert len(await sms_rows(SmsMessageType.LOW_STOCK)) == 1
        assert len(state["requests"]) == requests_before + 1
        print("   [OK] low-stock: once per product, second call is a no-op")

        # 15. Low-stock hook after payment ----------------------------------------
        low_order_id = await make_order(
            "paid", phone="09121111111", items=[(fridge.id, 1)]
        )
        async with Session() as s:
            product = await s.get(Product, fridge.id)
            assert product is not None
            product.stock = 2  # payment just moved stock 3 -> 2
            await s.commit()
        low_order = await load_order_by_id(low_order_id)
        async with Session() as s:
            await check_low_stock_after_payment(s, low_order, client=client)
        assert len(await sms_rows(SmsMessageType.LOW_STOCK, str(fridge.id))) == 1
        print("   [OK] check_low_stock_after_payment: alert fired at threshold")
    finally:
        sms_service._get_owner_phone = orig_owner_phone  # noqa: SLF001

    # 16. Failed-payment reminder worker: 15 min / 24 h window, once per order ---
    old_order_id = await make_order("failed", phone="09121111111", updated_ago_minutes=30)
    fresh_order_id = await make_order("failed", phone="09121111111", updated_ago_minutes=5)
    ancient_order_id = await make_order(
        "failed", phone="09121111111", updated_ago_minutes=25 * 60
    )
    orig_factory = scheduler_module.get_session_factory
    scheduler_module.get_session_factory = lambda: Session  # noqa: SLF001
    try:
        task = FailedPaymentReminderTask(interval_seconds=60.0, client=client)
        assert await task.run_once() == 1, "only the 30-minute-old failure is due"
        assert await task.run_once() == 0, "second pass must be a no-op"
    finally:
        scheduler_module.get_session_factory = orig_factory  # noqa: SLF001
    reminded = await sms_rows(SmsMessageType.PAYMENT_REMINDER)
    assert len(reminded) == 1, reminded
    assert reminded[0].reference == str(old_order_id), reminded[0].reference
    assert reminded[0].body_id == 102, reminded[0].body_id
    assert not await sms_rows(SmsMessageType.PAYMENT_REMINDER, str(fresh_order_id))
    assert not await sms_rows(SmsMessageType.PAYMENT_REMINDER, str(ancient_order_id))
    print("   [OK] reminder worker: 30-min-old reminded once; 5-min and 25-h skipped")

    # 17. Tracking SMS guard at the real admin route ----------------------------
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

        async def mint_session(user: User) -> str:
            async with Session() as s:
                record = await create_database_session(s, user)
                await s.commit()
                token = record.session_token
            resp = Response()
            create_session(resp, user.phone, session_token=token)
            return cookie_value(resp, SESSION_COOKIE_NAME)

        admin_cookie = await mint_session(admin)
        http_client.cookies.set(SESSION_COOKIE_NAME, admin_cookie)

        order_a_id = await make_order("paid", phone="09121111111")
        order_b_id = await make_order(
            "paid", phone="09121111111", tracking="PRESET123"
        )

        # A: first time a tracking code is set -> SMS goes out.
        resp = http_client.patch(
            f"/admin/orders/{order_a_id}",
            json={"status": "shipped", "tracking_code": "TRK1"},
        )
        assert resp.status_code == 200, resp.status_code
        tracking_rows = await sms_rows(SmsMessageType.ORDER_TRACKING, str(order_a_id))
        assert len(tracking_rows) == 1, tracking_rows
        assert tracking_rows[0].body_id == 103, tracking_rows[0]
        assert tracking_rows[0].text == f"{order_a_id};TRK1", tracking_rows[0].text

        # A again: shipped -> shipped with a new code -> NO second SMS.
        resp = http_client.patch(
            f"/admin/orders/{order_a_id}",
            json={"status": "shipped", "tracking_code": "TRK2"},
        )
        assert resp.status_code == 200, resp.status_code
        assert len(await sms_rows(SmsMessageType.ORDER_TRACKING, str(order_a_id))) == 1

        # B: order already had a tracking code before this update -> NO SMS.
        resp = http_client.patch(
            f"/admin/orders/{order_b_id}",
            json={"status": "shipped", "tracking_code": "TRK9"},
        )
        assert resp.status_code == 200, resp.status_code
        assert len(await sms_rows(SmsMessageType.ORDER_TRACKING, str(order_b_id))) == 0
        print("   [OK] tracking guard: SMS only when the code is newly set")
    finally:
        sms_service.get_sms_client = orig_get_sms_client  # noqa: SLF001
        app.dependency_overrides.pop(get_db, None)

    # 18. OTP dev fallback: populates _DEV_LAST_CODE, writes no DB row ---------
    _DEV_LAST_CODE.clear()
    _DEV_SENT_MESSAGES.clear()
    send_otp_sync("09123334455", "54321")
    assert _DEV_LAST_CODE["09123334455"] == "54321"
    otp_rows = await sms_rows(SmsMessageType.OTP)
    assert all(r.phone != "09123334455" for r in otp_rows), otp_rows
    assert get_sms_client().is_configured is False, "tests run unconfigured"
    request_otp("09124445566")
    code = _DEV_LAST_CODE["09124445566"]
    assert len(code) == 5 and code.isdigit(), code
    otp_rows = await sms_rows(SmsMessageType.OTP)
    assert all(r.phone != "09124445566" for r in otp_rows), otp_rows
    print("   [OK] OTP dev fallback: _DEV_LAST_CODE set, no DB row written")

    print("All SMS checks passed.")


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
