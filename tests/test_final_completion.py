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
"""Unit and integration tests for final completion features:
- Admin order fulfillment (status change paid -> shipped -> delivered + tracking code)
- Scheduled expired reservations timer freeing stock holds
- Kavenegar SMS client and order notifications
- Rate limiting middleware for public API routes
- HTML error pages (404 and 500), robots.txt, and sitemap.xml
- Enamad and Samandehi trust seal placeholder slots in footer
- Staff user creation and promotion service
"""

from __future__ import annotations

import asyncio
import datetime
import sys
import unittest
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import httpx
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

# Ensure app/e-commerce is on sys.path
E_COMMERCE_DIR = Path(__file__).resolve().parent.parent / "app" / "e-commerce"
if str(E_COMMERCE_DIR) not in sys.path:
    sys.path.insert(0, str(E_COMMERCE_DIR))

from fastapi.testclient import TestClient  # noqa: E402

from core.datetime_utils import utcnow  # noqa: E402
from core.middleware import RateLimiter, default_rate_limiter  # noqa: E402
from core.security import SESSION_COOKIE_NAME, create_session  # noqa: E402
from db.base import Base  # noqa: E402
from db.models.order import Order, OrderItem, OrderStatus  # noqa: E402
from db.models.payment_attempt import PaymentAttempt, PaymentStatus  # noqa: E402
from db.models.product import Product  # noqa: E402
from db.models.user import User, UserRole  # noqa: E402
from db.session import get_db  # noqa: E402
from integrations.sms import (  # noqa: E402
    _DEV_SENT_MESSAGES,
    KavenegarClient,
    KavenegarError,
    send_order_paid,
    send_order_shipped,
    send_otp,
)
from main import app  # noqa: E402
from services.order_service import create_order  # noqa: E402
from services.scheduler import ReservationCleanupTask  # noqa: E402
from services.session_service import create_database_session  # noqa: E402
from services.user_service import create_or_promote_staff_user  # noqa: E402


class FinalCompletionTests(unittest.TestCase):
    """Test suite covering the final completion items."""

    engine: AsyncEngine
    Session: async_sessionmaker[AsyncSession]
    client: TestClient
    admin_user: User
    staff_user: User
    customer_user: User
    admin_cookie: str
    staff_cookie: str
    customer_cookie: str

    @classmethod
    def setUpClass(cls) -> None:
        cls.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )

        async def init_tables() -> None:
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(init_tables())
        cls.Session = async_sessionmaker(
            cls.engine, expire_on_commit=False, class_=AsyncSession
        )

        async def override_get_db() -> AsyncGenerator[AsyncSession, None]:
            async with cls.Session() as session:
                try:
                    yield session
                    await session.commit()
                except Exception:
                    await session.rollback()
                    raise

        app.dependency_overrides[get_db] = override_get_db

        async def seed_users() -> None:
            async with cls.Session() as s:
                cls.admin_user = User(
                    phone="09120000001",
                    role=UserRole.ADMIN,
                    first_name="Admin",
                    last_name="Boss",
                    is_active=True,
                )
                cls.staff_user = User(
                    phone="09120000002",
                    role=UserRole.STAFF,
                    first_name="Staff",
                    last_name="Worker",
                    is_active=True,
                )
                cls.customer_user = User(
                    phone="09120000003",
                    role=UserRole.CUSTOMER,
                    first_name="Customer",
                    last_name="Buyer",
                    is_active=True,
                )
                s.add_all([cls.admin_user, cls.staff_user, cls.customer_user])
                await s.commit()

                # Generate sessions
                admin_sess = await create_database_session(s, cls.admin_user)
                staff_sess = await create_database_session(s, cls.staff_user)
                cust_sess = await create_database_session(s, cls.customer_user)
                await s.commit()

                # Mint cookies
                from fastapi import Response

                r1 = Response()
                create_session(r1, cls.admin_user.phone, admin_sess.session_token)
                cls.admin_cookie = r1.headers["set-cookie"].split(";")[0].split("=", 1)[1]

                r2 = Response()
                create_session(r2, cls.staff_user.phone, staff_sess.session_token)
                cls.staff_cookie = r2.headers["set-cookie"].split(";")[0].split("=", 1)[1]

                r3 = Response()
                create_session(r3, cls.customer_user.phone, cust_sess.session_token)
                cls.customer_cookie = r3.headers["set-cookie"].split(";")[0].split("=", 1)[1]

        asyncio.run(seed_users())
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls) -> None:
        app.dependency_overrides.clear()
        asyncio.run(cls.engine.dispose())

    def setUp(self) -> None:
        default_rate_limiter.reset()

    # 1. Admin order fulfillment tests -----------------------------------------
    def test_admin_orders_access_control(self) -> None:
        """Guest gets 401/303, customer gets 403, staff and admin get 200."""
        # Guest
        self.client.cookies.clear()
        resp = self.client.get("/admin/orders", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 401)

        # Customer (forbidden)
        self.client.cookies.set(SESSION_COOKIE_NAME, self.customer_cookie)
        resp = self.client.get("/admin/orders", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 403)

        # Staff (allowed)
        self.client.cookies.set(SESSION_COOKIE_NAME, self.staff_cookie)
        resp = self.client.get("/admin/orders", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 200)

        # Admin (allowed)
        self.client.cookies.set(SESSION_COOKIE_NAME, self.admin_cookie)
        resp = self.client.get("/admin/orders", headers={"Accept": "application/json"})
        self.assertEqual(resp.status_code, 200)

    def test_admin_order_fulfillment_lifecycle(self) -> None:
        """Test fulfillment: paid -> shipped (with tracking code) -> delivered, plus SMS trigger."""
        # 1. Create a paid order in database
        async def create_paid_order() -> Order:
            async with self.Session() as s:
                prod = Product(
                    name="تست فر پیتزا",
                    price_toman=10_000_000,
                    stock=5,
                    reserved=0,
                    is_active=True,
                )
                s.add(prod)
                await s.flush()
                order = await create_order(
                    s,
                    user=self.customer_user,
                    items=[{"product_id": prod.id, "quantity": 1}],
                    recipient_name="سارا احمدی",
                    recipient_phone="09128888888",
                    delivery_address="تهران، خیابان ولیعصر، پلاک ۱۰",
                )
                order.status = OrderStatus.PAID.value
                await s.commit()
                await s.refresh(order)
                return order

        order = asyncio.run(create_paid_order())
        _DEV_SENT_MESSAGES.clear()

        # 2. Staff views order detail page (HTML and JSON)
        self.client.cookies.set(SESSION_COOKIE_NAME, self.staff_cookie)
        resp = self.client.get(
            f"/admin/orders/{order.id}", headers={"Accept": "text/html"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertIn("مدیریت سفارش", resp.text)
        self.assertIn("سارا احمدی", resp.text)

        json_resp = self.client.get(
            f"/admin/orders/{order.id}", headers={"Accept": "application/json"}
        )
        self.assertEqual(json_resp.status_code, 200)
        data = json_resp.json()
        self.assertEqual(data["status"], "paid")
        self.assertIsNone(data["tracking_code"])

        # 3. Fulfillment status change: paid -> shipped with tracking code
        ship_resp = self.client.post(
            f"/admin/orders/{order.id}/status",
            data={"status": "shipped", "tracking_code": "IRPOST1234567890"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(ship_resp.status_code, 200)
        updated = ship_resp.json()
        self.assertEqual(updated["status"], "shipped")
        self.assertEqual(updated["tracking_code"], "IRPOST1234567890")

        # Verify shipped SMS was dispatched via the unified SMS interface
        shipped_messages = [
            m for m in _DEV_SENT_MESSAGES if m.get("type") == "order_shipped"
        ]
        self.assertGreaterEqual(len(shipped_messages), 1)
        last_sms = shipped_messages[-1]
        self.assertEqual(last_sms["phone"], "09128888888")
        self.assertEqual(last_sms["tracking_code"], "IRPOST1234567890")
        self.assertIn("IRPOST1234567890", last_sms["message"])

        # 4. Fulfillment status change: shipped -> delivered
        deliv_resp = self.client.post(
            f"/admin/orders/{order.id}/status",
            data={"status": "delivered"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(deliv_resp.status_code, 200)
        self.assertEqual(deliv_resp.json()["status"], "delivered")
        self.assertEqual(deliv_resp.json()["tracking_code"], "IRPOST1234567890")

    def test_admin_order_invalid_transition_rejected(self) -> None:
        """Orders that are not paid cannot be transitioned directly to shipped."""
        async def create_unpaid_order() -> Order:
            async with self.Session() as s:
                prod = Product(
                    name="یخچال آزمایشگاهی",
                    price_toman=50_000_000,
                    stock=2,
                    reserved=0,
                    is_active=True,
                )
                s.add(prod)
                await s.flush()
                order = await create_order(
                    s,
                    user=self.customer_user,
                    items=[{"product_id": prod.id, "quantity": 1}],
                )
                # status is created / unpaid
                return order

        unpaid_order = asyncio.run(create_unpaid_order())
        self.client.cookies.set(SESSION_COOKIE_NAME, self.staff_cookie)
        resp = self.client.post(
            f"/admin/orders/{unpaid_order.id}/status",
            data={"status": "shipped", "tracking_code": "TRACK001"},
            headers={"Accept": "application/json"},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("تنها سفارش‌های پرداخت شده", resp.json()["detail"])

    # 2. Reservation Timer / Scheduler Tests -----------------------------------
    def test_reservation_cleanup_frees_expired_holds(self) -> None:
        """ReservationCleanupTask frees holds older than hold_minutes and restores stock."""
        async def scenario() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
            async with self.Session() as s:
                p = Product(
                    name="تلویزیون هوشمند تست زمان‌بندی",
                    price_toman=30_000_000,
                    stock=4,
                    reserved=2,
                    is_active=True,
                )
                s.add(p)
                await s.flush()

                order = Order(
                    user_id=self.customer_user.id,
                    status=OrderStatus.WAITING_PAYMENT.value,
                    total_toman=60_000_000,
                    total_rial=600_000_000,
                )
                s.add(order)
                await s.flush()

                item = OrderItem(
                    order_id=order.id,
                    product_id=p.id,
                    product_name=p.name,
                    quantity=2,
                    unit_price_toman=30_000_000,
                )
                s.add(item)

                # Expired payment attempt (20 minutes ago)
                expired_time = utcnow() - datetime.timedelta(minutes=20)
                attempt = PaymentAttempt(
                    order_id=order.id,
                    track_id=1234567,
                    amount_rial=600_000_000,
                    status=PaymentStatus.WAITING.value,
                    idempotency_key=uuid.uuid4(),
                    gateway="zibal",
                )
                attempt.created_at = expired_time
                s.add(attempt)
                await s.commit()
                return p.id, order.id, attempt.id

        prod_id, order_id, attempt_id = asyncio.run(scenario())

        # Run scheduler cleanup task with our session factory
        # Temporarily wire get_session_factory to return our test Session
        import db.session as dbsess
        orig_factory = dbsess._sessionmaker
        dbsess._sessionmaker = self.Session

        try:
            task = ReservationCleanupTask(interval_seconds=60.0, hold_minutes=15)
            freed = asyncio.run(task.run_once())
            self.assertEqual(freed, 1)

            async def verify_state() -> None:
                async with self.Session() as s:
                    p = await s.get(Product, prod_id)
                    assert p is not None
                    self.assertEqual(p.reserved, 0)
                    self.assertEqual(p.available, 4)

                    ord_rec = await s.get(Order, order_id)
                    assert ord_rec is not None
                    self.assertEqual(ord_rec.status, OrderStatus.EXPIRED.value)

                    att_rec = await s.get(PaymentAttempt, attempt_id)
                    assert att_rec is not None
                    self.assertEqual(att_rec.status, PaymentStatus.EXPIRED.value)

            asyncio.run(verify_state())
        finally:
            dbsess._sessionmaker = orig_factory

    # 3. Kavenegar SMS Client Tests --------------------------------------------
    def test_kavenegar_client_dev_fallback_and_notifications(self) -> None:
        """When no key is configured, uses dev fallback for OTP, order paid, and shipped."""
        client = KavenegarClient(api_key=None)
        self.assertFalse(client.is_configured)

        _DEV_SENT_MESSAGES.clear()
        send_otp("09121112233", "54321")
        send_order_paid("09121112233", "order-uuid-99", 500_000)
        send_order_shipped("09121112233", "order-uuid-99", "TRACK998877")

        types = [m["type"] for m in _DEV_SENT_MESSAGES]
        self.assertIn("otp", types)
        self.assertIn("order_paid", types)
        self.assertIn("order_shipped", types)

    def test_kavenegar_client_with_key_http_request(self) -> None:
        """With key configured, dispatches HTTP POST to Kavenegar API."""
        captured_requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured_requests.append(request)
            if "test-api-key" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "return": {"status": 200, "message": "تایید شد"},
                        "entries": [{"messageid": 990011, "status": 1}],
                    },
                )
            return httpx.Response(401, json={"return": {"status": 401, "message": "Invalid key"}})

        mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = KavenegarClient(api_key="test-api-key", sender="10008888")
        self.assertTrue(client.is_configured)

        async def run_sms() -> None:
            ok = await client.send_sms_async("09129999999", "تست ارسال", http_client=mock_http)
            self.assertTrue(ok)

        asyncio.run(run_sms())
        self.assertEqual(len(captured_requests), 1)
        req = captured_requests[0]
        self.assertIn("/test-api-key/sms/send.json", req.url.path)
        content_str = req.content.decode("utf-8")
        self.assertIn("receptor=09129999999", content_str)
        self.assertIn("sender=10008888", content_str)

    def test_kavenegar_client_error_handling(self) -> None:
        """Kavenegar API returning status != 200 raises KavenegarError."""
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"return": {"status": 411, "message": "شماره گیرنده نامعتبر است"}},
            )

        mock_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = KavenegarClient(api_key="valid-key")

        async def run_err() -> None:
            with self.assertRaises(KavenegarError) as ctx:
                await client.send_sms_async("09120000000", "پیام", http_client=mock_http)
            self.assertEqual(ctx.exception.status, 411)

        asyncio.run(run_err())

    # 4. Rate Limiting Tests ---------------------------------------------------
    def test_rate_limiting_middleware(self) -> None:
        """Rate limiter allows requests up to limit and rejects excess with 429."""
        limiter = RateLimiter(
            default_limit=3,
            default_window=60.0,
            route_limits={"/auth/otp": (2, 60.0)},
        )

        class DummyRequest:
            def __init__(self, path: str, ip: str = "192.168.1.10") -> None:
                self.url = type("URL", (), {"path": path})()
                self.client = type("Client", (), {"host": ip})()
                self.headers: dict[str, str] = {}

        # First 2 requests to /auth/otp allowed
        req = DummyRequest("/auth/otp/request")
        allowed1, _, _ = limiter.check(req)  # type: ignore
        allowed2, _, _ = limiter.check(req)  # type: ignore
        self.assertTrue(allowed1)
        self.assertTrue(allowed2)

        # Third request rejected (429)
        allowed3, limit, retry_after = limiter.check(req)  # type: ignore
        self.assertFalse(allowed3)
        self.assertEqual(limit, 2)
        self.assertGreater(retry_after, 0.0)

        # General routes have their own limit
        gen_req = DummyRequest("/products")
        self.assertTrue(limiter.check(gen_req)[0])  # type: ignore

    # 5. HTML Error Pages, Robots.txt, Sitemap, Trust Seals --------------------
    def test_error_pages_404_html_and_json(self) -> None:
        """HTML request gets 404.html, JSON request gets 404 JSON."""
        # Browser request with text/html
        resp = self.client.get("/non-existent-page-xyz", headers={"Accept": "text/html"})
        self.assertEqual(resp.status_code, 404)
        self.assertIn("صفحه مورد نظر یافت نشد", resp.text)
        self.assertIn("بازگشت به صفحه اصلی", resp.text)

        # API request with application/json
        json_resp = self.client.get(
            "/non-existent-page-xyz", headers={"Accept": "application/json"}
        )
        self.assertEqual(json_resp.status_code, 404)
        self.assertEqual(json_resp.json(), {"detail": "Not Found"})

    def test_robots_txt_and_sitemap_xml(self) -> None:
        """GET /robots.txt and /sitemap.xml return proper format and entries."""
        # robots.txt
        robots = self.client.get("/robots.txt")
        self.assertEqual(robots.status_code, 200)
        self.assertIn("User-agent: *", robots.text)
        self.assertIn("Disallow: /admin", robots.text)
        self.assertIn("Sitemap: /sitemap.xml", robots.text)

        # sitemap.xml
        sitemap = self.client.get("/sitemap.xml")
        self.assertEqual(sitemap.status_code, 200)
        self.assertIn("application/xml", sitemap.headers.get("content-type", ""))
        self.assertIn("<urlset", sitemap.text)
        self.assertIn("<loc>/</loc>", sitemap.text)
        self.assertIn("<loc>/products</loc>", sitemap.text)

    def test_footer_trust_seals_present(self) -> None:
        """Base footer contains Enamad and Samandehi placeholder slots."""
        resp = self.client.get("/terms", headers={"Accept": "text/html"})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="enamad-slot"', resp.text)
        self.assertIn('id="samandehi-slot"', resp.text)
        self.assertIn("اینماد", resp.text)
        self.assertIn("ساماندهی", resp.text)

    # 6. Staff User Creation & Promotion ---------------------------------------
    def test_create_or_promote_staff_user_service(self) -> None:
        """create_or_promote_staff_user creates a new user or elevates existing role."""
        async def run_staff_service() -> None:
            async with self.Session() as s:
                # 1. Create a brand new admin user
                new_admin = await create_or_promote_staff_user(
                    db=s,
                    phone="09191112233",
                    role=UserRole.ADMIN,
                    first_name="مدیر",
                    last_name="کل",
                )
                self.assertEqual(new_admin.phone, "09191112233")
                self.assertEqual(new_admin.role, UserRole.ADMIN)

                # 2. Promote an existing customer to staff
                promoted = await create_or_promote_staff_user(
                    db=s,
                    phone=self.customer_user.phone,
                    role=UserRole.STAFF,
                )
                self.assertEqual(promoted.role, UserRole.STAFF)

        asyncio.run(run_staff_service())


if __name__ == "__main__":
    unittest.main()
