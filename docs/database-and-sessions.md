# راهنمای احراز هویت دیتابیسی، مدیریت سشن‌ها (Session Management) و SQLAlchemy + Alembic

این سند ساختار نهایی احراز هویت، مدل‌های پایگاه داده، سیستم مدیریت سشن و کنترل دسترسی بر اساس نقش (RBAC) در پروژه `easy-auto` (بنده) را توضیح می‌دهد.

---

## 🎯 اهداف معماری

1. **ارتباط مستقیم FastAPI با PostgreSQL:**
   - حذف تاخیرهای اضافی (Directus در مسیر ترافیک کاربران عادی نیست و صرفاً به عنوان پنل ادمین به دیتابیس مشترک وصل است).
   - استفاده از **SQLAlchemy 2.0 (Async Engine + asyncpg)** برای کار با دیتابیس به صورت کاملاً غیرهمگام (Non-blocking).
2. **مدیریت سشن سنتی (Traditional Session Management):**
   - عدم استفاده از پیچیدگی‌های JWT در مرورگر.
   - ذخیره سشن‌ها در جدول `user_sessions` در PostgreSQL.
   - حمل توکن سشن در کوکی امن `HttpOnly`، `SameSite=Lax` و با امضای کریپتوگرافیک (`itsdangerous`).
   - امکان باطل کردن فوری سشن (Revoke)، خروج از همه دستگاه‌ها و نظارت بر نشست‌های فعال.
3. **احراز هویت پیامکی (Phone OTP):**
   - بدون نیاز به رمز عبور.
   - نرمال‌سازی شماره‌های ایران و فرم‌های بهینه شده با HTMX.
4. **مدیریت مایگریشن‌ها با Alembic:**
   - مایگریشن‌های خودکار و کنترل‌شده برای تمام جداول پایگاه داده.

---

## 📂 ساختار فایل‌های ایجاد و بروزرسانی‌شده

```text
easy-auto/
├── alembic.ini                                # پیکربندی رسمی Alembic
├── alembic/
│   ├── env.py                                 # لود اتوماتیک مدل‌ها و اتصال به دیتابیس
│   ├── script.py.mako                         # قالب مایگریشن‌ها
│   └── versions/
│       └── 001_initial_users_and_sessions.py  # مایگریشن ایجاد جداول users و user_sessions
├── env_settings.py                            # اضافه شدن async_database_url و sync_database_url
├── pyproject.toml                             # افزودن وابستگی‌های sqlalchemy, alembic, asyncpg, psycopg2
├── app/
│   └── e-commerce/
│       ├── db/
│       │   ├── __init__.py
│       │   ├── base.py                        # کلاس پایه‌ای Base و TimestampMixin
│       │   ├── session.py                     # موتور ناهمگام (AsyncEngine) و وابستگی get_db
│       │   └── models/
│       │       ├── __init__.py
│       │       ├── user.py                    # مدل User و Enum نقش‌های کاربر (UserRole)
│       │       └── session.py                 # مدل UserSession (توکن، تاریخ انقضا و باطل‌سازی)
│       ├── schema/
│       │   └── user.py                        # اسکیماهای Pydantic برای اعتبارسنجی کاربر و نشست
│       ├── services/
│       │   ├── auth_service.py                # لاجیک OTP (هش، ریت لیمیت، کد یکبارمصرف)
│       │   ├── user_service.py                # مدیریت کاربر در دیتابیس (ایجاد خودکار پس از تایید OTP)
│       │   └── session_service.py             # ایجاد سشن، جستجو، تایید اعتبار و ابطال سشن
│       ├── core/
│       │   └── security.py                    # کوکی امضاشده، وابستگی‌های get_current_db_user و require_roles
│       └── api/
│           └── routes/
│               └── auth.py                    # روت‌های OTP، خروج، و ایجاد سشن در دیتابیس
```

---

## 🗄️ مدل‌های دیتابیس

### ۱. جدول کاربران (`users`)
| ستون | نوع | توضیحات |
|---|---|---|
| `id` | UUID (PK) | شناسه یکتای کاربر |
| `phone` | VARCHAR(20) | شماره یکتای نرمال‌شده (`09xxxxxxxxx`) با ایندکس |
| `role` | VARCHAR(20) | نقش کاربر: `customer`، `staff`، `admin` |
| `is_active` | BOOLEAN | فعال/غیرفعال بودن حساب کاربری |
| `first_name` | VARCHAR(100) | نام (اختیاری) |
| `last_name` | VARCHAR(100) | نام خانوادگی (اختیاری) |
| `last_login_at` | TIMESTAMPTZ | آخرین زمان لاگین |
| `created_at` | TIMESTAMPTZ | تاریخ ثبت‌نام خودکار |
| `updated_at` | TIMESTAMPTZ | آخرین بروزرسانی |

### ۲. جدول سشن‌ها (`user_sessions`)
| ستون | نوع | توضیحات |
|---|---|---|
| `id` | UUID (PK) | شناسه نشست |
| `session_token` | VARCHAR(128) | توکن امن تصادفی (یکتا و ایندکس‌شده) |
| `user_id` | UUID (FK) | کلید خارجی به `users.id` با CASCADE Delete |
| `expires_at` | TIMESTAMPTZ | تاریخ انقضای نشست (پیش‌فرض: ۳۰ روز) |
| `is_revoked` | BOOLEAN | وضعیت ابطال نشست (True هنگام خروج) |
| `user_agent` | VARCHAR(512) | مشخصات مرورگر/دستگاه |
| `ip_address` | VARCHAR(64) | آدرس IP کاربر |
| `created_at` | TIMESTAMPTZ | زمان ورود |

---

## 🔐 سطوح دسترسی (RBAC) و نحوه استفاده در روت‌ها

در [core/security.py](file:///home/mhb/app/coding/easy-auto/app/e-commerce/core/security.py) وابستگی‌های استاندارد FastAPI برای احراز هویت و دسترسی تعریف شده‌اند:

```python
from fastapi import APIRouter, Depends
from core.security import (
    CurrentDbUserDep,     # کاربر لاگین کرده (اختیاری - در صورت عدم لاگین None است)
    RequireDbUserDep,     # اجبار به لاگین (در صورت عدم لاگین ریدایرکت یا 401)
    RequireAdminDep,      # فقط ادمین (در غیر این صورت 403 Forbidden)
    RequireStaffDep,      # فقط پرسنل یا ادمین
)
from db.models import User

router = APIRouter()

# ۱. روت محافظت‌شده برای هر مشتری وارد شده:
@router.get("/profile")
async def get_profile(user: RequireDbUserDep):
    return {"phone": user.phone, "role": user.role}

# ۲. روت اختصاصی فقط برای مدیران (Admin):
@router.get("/admin/dashboard")
async def admin_dashboard(admin: RequireAdminDep):
    return {"message": "خوش آمدید مدیر محترم"}
```

---

## 🚀 نحوه اجرای مایگریشن‌ها در محیط داکر و لوکال

وقتی داکر را اجرا می‌کنید، مایگریشن‌ها به سادگی قابل اجرا هستند:

```bash
# اعمال مایگریشن‌ها به دیتابیس پست‌گرس:
docker compose exec web alembic upgrade head

# ایجاد مایگریشن جدید پس از تغییر مدل‌ها:
docker compose exec web alembic revision --autogenerate -m "add new table"
```
