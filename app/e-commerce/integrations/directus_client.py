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
"""Directus client for shop content (categories, products).

Directus is the headless CMS that holds the shop's content, so editors
can change categories and products from a dashboard instead of code.
Directus OWNS this content: the app reads product and category content
through this client, never through SQLAlchemy models. The one exception
is stock: order creation and payment reserve and release stock inside
real database transactions, so stock/reserved stay in SQLAlchemy — the
same ``products`` table Directus manages, so the dashboard and the app
always see the same rows.
This client reads that content — and if Directus is down, slow, or
returns something broken, it falls back to the hardcoded content below,
so the landing page NEVER goes blank because the CMS is unreachable.

The fallback content is the same data the landing page used before
Directus existed. It lives here (not in ``pages.py``) so the client owns
both the live path and the fallback path.

Caching: successful reads are cached for 60 seconds (``LANDING_CONTENT_TTL``).
When content changes in Directus, call ``invalidate_landing_content``
(delete-on-change) instead of waiting for the TTL — a stale price is
worse than a slow page. Failures are never cached: the next request
retries Directus right away.
"""

from __future__ import annotations

import copy
import datetime
import json
import logging
import os
import uuid
from typing import Annotated, TypedDict, cast

import httpx
from fastapi import Depends

from core.cache import Cache, CacheDep
from integrations.http_client import HttpClientDep

logger = logging.getLogger("bandeh.directus")

# Cache key and lifetime for the landing content (seconds).
LANDING_CONTENT_KEY = "bandeh:landing:content"
LANDING_CONTENT_TTL = 60
CATEGORIES_MAP_KEY = "bandeh:categories:map"
CATEGORIES_MAP_TTL = 300
# Cache key and lifetime for the site content (settings, badges, pages).
SITE_CONTENT_KEY = "bandeh:site:content"
SITE_CONTENT_TTL = 60


class CategoryContent(TypedDict):
    """Rendered category card data on the landing page."""

    name: str
    desc: str
    # CMS icon (emoji or short symbol) shown in the card circle.
    # None means the template picks an SVG by the category name.
    icon: str | None
    # Display order set in Directus (lower first). 0 = no order set.
    sort: int


class ProductContent(TypedDict):
    """Rendered product card data on the landing page."""

    name: str
    price: str
    # Raw Toman integer, for the compact price display (۳۲ میلیون).
    # None when Directus did not send a price.
    price_toman: int | None
    # Directus product id (UUID string). None for rows without one, so the
    # landing page falls back to a plain name (no product link).
    id: str | None
    tag: str | None
    # Category name, for the per-category item count on the landing page.
    # Photo file name, for the product thumbnail. Both None when Directus
    # did not send them (for example in the hardcoded fallback).
    category: str | None
    image_path: str | None
    # Condition: 'new' or 'used'. Stars 1–5 only when used.
    condition: str
    condition_stars: int | None


class SiteSettings(TypedDict):
    """One row in Directus ``site_settings``: the site's identity.

    One codebase serves many sites: each site has its own Directus,
    so each one sets its own title, logo and switches here. Nothing
    about the shop's identity is hardcoded in the templates anymore.
    """

    site_title: str
    site_tagline: str
    hero_title: str
    hero_subtitle: str
    # Short suffix after the site title in <title> and the footer line,
    # e.g. "فروشگاه لوازم خانگی" for Genabay, "بازار سهام گناوه" for
    # Genaveh Stocks. Empty means no suffix at all.
    title_suffix: str
    # Enamad trust seal: the id and Code from the Enamad panel. Both
    # must be set for the seal to render; both empty means no seal.
    # Per-site, so one site never shows another site's seal.
    enamad_seal_id: str | None
    enamad_seal_code: str | None
    # Directus file id of the logo, or None (header falls back to text).
    logo: str | None
    # True when this site also sells second-hand goods. False hides the
    # whole used flow: condition labels, stars and the search filter.
    sells_used: bool
    # The two promo boxes above the hero on the landing page, and the
    # intro of the MCP guide page. All editable in Directus; the links
    # are TEST values until the MCP server is deployed on the server.
    mcp_title: str
    mcp_text: str
    mcp_url: str
    mcp_chatgpt_url: str
    mcp_gemini_url: str
    mcp_guide_intro: str
    instagram_title: str
    instagram_text: str
    instagram_url: str


class TrustBadge(TypedDict):
    """One service box on the landing page (from ``trust_badges``)."""

    title: str
    text: str
    icon: str


class LegalPage(TypedDict):
    """One footer page (from ``legal_pages``)."""

    slug: str
    title: str
    body: str
    is_active: bool


class CertBadge(TypedDict):
    """One trust seal in the footer (from ``certification_badges``)."""

    name: str
    status_text: str
    # Directus file id of the seal image, or None (shield icon instead).
    image: str | None
    link: str | None


class GuideSection(TypedDict):
    """One app section of the MCP guide (from ``mcp_guide_sections``).

    Steps stay in Persian; only the button/menu names the visitor must
    tap are in English, exactly as they appear in the app.
    """

    slug: str
    ai_name: str
    title: str
    support_note: str
    body: str
    supported: bool
    sort: int


class SiteContent(TypedDict):
    """Everything the shared header/footer/landing needs from the CMS."""

    settings: SiteSettings
    trust_badges: list[TrustBadge]
    legal_pages: list[LegalPage]
    cert_badges: list[CertBadge]
    # Categories live here too, so the header search panel (on every page)
    # can offer the category filter before the visitor types anything.
    categories: list[CategoryContent]


class LandingContent(TypedDict):
    """Combined landing page categories and products payload."""

    categories: list[CategoryContent]
    products: list[ProductContent]


# Hardcoded landing content, used ONLY when Directus cannot answer.
# Same data the shop showed before the Directus integration.
FALLBACK_CATEGORIES: list[CategoryContent] = [
    {"name": "یخچال و فریزر", "desc": "ساید، دوقلو، معمولی", "icon": None, "sort": 1},
    {"name": "ماشین لباسشویی", "desc": "۷ تا ۱۰ کیلویی", "icon": None, "sort": 2},
    {"name": "ماشین ظرفشویی", "desc": "رومیزی و ایستاده", "icon": None, "sort": 3},
    {"name": "اجاق گاز و فر", "desc": "صفحه‌ای و مبله", "icon": None, "sort": 4},
    {"name": "جاروبرقی", "desc": "کیسه‌ای و مخزن‌دار", "icon": None, "sort": 5},
    {"name": "لوازم کوچک", "desc": "آشپزخانه و خانه", "icon": None, "sort": 6},
]

FALLBACK_PRODUCTS: list[ProductContent] = [
    {
        "name": "یخچال فریزر دوقلو",
        "price_toman": 45000000,
        "price": "۴۵٬۰۰۰٬۰۰۰",
        "tag": "پرفروش",
        "category": None,
        "image_path": None,
        "id": None,
        "condition": "new",
        "condition_stars": None,
    },
    {
        "name": "ماشین لباسشویی ۸ کیلویی",
        "price_toman": 28500000,
        "price": "۲۸٬۵۰۰٬۰۰۰",
        "tag": None,
        "category": None,
        "image_path": None,
        "id": None,
        "condition": "new",
        "condition_stars": None,
    },
    {
        "name": "جاروبرقی ۲۰۰۰ وات",
        "price_toman": 9800000,
        "price": "۹٬۸۰۰٬۰۰۰",
        "tag": "تخفیف‌دار",
        "category": None,
        "image_path": None,
        "id": None,
        "condition": "new",
        "condition_stars": None,
    },
    {
        "name": "اجاق گاز ۵ شعله",
        "price_toman": 19000000,
        "price": "۱۹٬۰۰۰٬۰۰۰",
        "tag": None,
        "category": None,
        "image_path": None,
        "id": None,
        "condition": "new",
        "condition_stars": None,
    },
]

# Hardcoded site content, used ONLY when Directus cannot answer.
# Same identity the shop showed before the CMS took it over: the
# header, footer, trust boxes and seal cards render exactly as today.
FALLBACK_SITE_CONTENT: SiteContent = {
    "settings": {
        "site_title": "گنابِی",
        "site_tagline": "ضمانت اصالت و ارسال مستقیم با بهترین قیمت",
        "hero_title": "خانه‌ی مدرن، زندگی بهتر",
        "hero_subtitle": "وسایل ضروری مدرن برای هر لحظه از روز",
        "title_suffix": "فروشگاه لوازم خانگی",
        "enamad_seal_id": "8856743",
        "enamad_seal_code": "S6nwLjmqnP9H84VIqFaj0olAcSxyVyo3",
        "logo": None,
        "sells_used": False,
        "mcp_title": "هوش مصنوعی‌ات را به فروشگاهمون وصل کن؛ خرید با یک سوال",
        "mcp_text": (
            "دیگر لازم نیست دنبال کالا بگردی. به"
            "هوش مصنوعی‌ات بگو چی می‌خواهی؛ خودش در فروشگاه پیدا"
            "می‌کند، قیمت و موجودی را می‌گوید و مقایسه می‌کند."
        ),
        "mcp_url": "https://genabay.ir/mcp",
        "mcp_chatgpt_url": "https://chatgpt.com/plugins",
        "mcp_gemini_url": "https://gemini.google.com/customize/connectors",
        "mcp_guide_intro": (
            "لازم نیست بین صدها کالا بگردی. هوش مصنوعی‌ات را یک بار به فروشگاه وصل کن؛"
            " از آن به بعد فقط سؤال بپرس: خودش کالا پیدا می‌کند، قیمت و موجودی را می‌گوید،"
            " به سبد خرید اضافه می‌کند و پیش‌نویس سفارش می‌سازد. و خیالت راحت: پرداخت"
            " همیشه دستی است — هیچ پولی بدون دست خودت جابه‌جا نمی‌شود."
        ),
        "instagram_title": "ما را در اینستاگرام دنبال کنید",
        "instagram_text": "تخفیف‌ها، کالاهای جدید و ویدیوهای فروشگاه را اول در اینستاگرام می‌بینید.",
        "instagram_url": "https://instagram.com/genabay",
    },
    "trust_badges": [
        {"title": "ضمانت اصالت", "text": "کالا با ضمانت‌نامه‌ی معتبر", "icon": "✦"},
        {"title": "ارسال سریع", "text": "تحویل درب منزل", "icon": "🚚"},
        {"title": "پرداخت امن", "text": "درگاه بانکی معتبر", "icon": "🔒"},
        {"title": "مهلت بازگشت", "text": "۷ روز ضمانت بازگشت", "icon": "↺"},
    ],
    "legal_pages": [
        {"slug": "terms", "title": "قوانین و مقررات", "body": "", "is_active": True},
        {"slug": "privacy", "title": "حریم خصوصی", "body": "", "is_active": True},
        {"slug": "returns", "title": "شرایط بازگشت", "body": "", "is_active": True},
        {"slug": "contact", "title": "تماس با ما", "body": "", "is_active": True},
    ],
    "cert_badges": [
        {"name": "اینماد", "status_text": "در حال دریافت", "image": None, "link": None},
        {"name": "ساماندهی", "status_text": "در حال دریافت", "image": None, "link": None},
    ],
    "categories": list(FALLBACK_CATEGORIES),
}

# Fallback MCP guide sections, used ONLY when Directus cannot answer.
# Clean end-user text, synced with the rewritten guide artifact
# («وصل کردن هوش مصنوعی به فروشگاه گنابِی», Tracker id 110).
# No internal notes, no screenshot mentions, no research or status
# text — only the steps a normal visitor needs. Order: Claude,
# ChatGPT, Gemini, Codex, Gemini CLI, Antigravity, Claude Code
# (Gemini above Codex). The CMS seed holds the same text.
FALLBACK_GUIDE_SECTIONS: list[GuideSection] = [
    {
        "slug": "claude",
        "ai_name": "Claude",
        "title": "Claude — ساده‌ترین راه ⭐",
        "support_note": "رایگان و فقط با لینک — ساده‌ترین راه",
        "body": (
            "<p>در Claude وصل کردن فروشگاه رایگان است و فقط یک لینک می‌خواهد — نه"
            " فایل، نه کدنویسی. حساب رایگان اجازه یک کانکتور را می‌دهد و همین برای"
            " فروشگاه کافی است.</p><ol><li>لینک فروشگاه را از جعبه بالای همین صفحه کپی"
            " کن.</li><li>در Claude برو به <b>Customize &gt; Connectors</b>.</li><li>روی"
            " <b>+ Add</b> بزن و <b>Add custom connector</b> را انتخاب کن.</li><li>یک اسم"
            " بنویس (مثلاً «گنابِی»)، لینک را بچسبان و <b>Continue</b> را بزن.</li><li>در"
            " پایان <b>Add</b> را بزن. تمام شد!</li><li>در هر گفتگو، با دکمه <b>+</b>"
            " پایین صفحه، از بخش <b>Connectors</b> کانکتور فروشگاه را روشن کن و سؤالت"
            " را بپرس.</li></ol><p>چرا Claude راحت است؟ حساب رایگان کافی است، فقط با"
            " لینک وصل می‌شود و هیچ تنظیم اضافه‌ای نمی‌خواهد.</p>"
        ),
        "supported": True,
        "sort": 1,
    },
    {
        "slug": "chatgpt",
        "ai_name": "ChatGPT",
        "title": "ChatGPT — فقط در وب 💻",
        "support_note": "فقط در وب (نه موبایل) و با چسباندن لینک",
        "body": (
            "<p>قبل از شروع بدان: ChatGPT فقط در وب کار می‌کند، نه در موبایل. در فضای"
            " کاری، فقط مدیر می‌تواند آن را بسازد و منتشر کند.</p><ol><li>لینک فروشگاه"
            " را کپی کن و در مرورگر، <b>chatgpt.com</b> را باز کن.</li><li>برو به"
            " <b>Plugins</b> و دکمه <b>Add</b> را بزن؛ بعد <b>Add custom MCP server</b>"
            " را انتخاب کن.</li><li>در فیلد <b>Name</b> بنویس «گنابِی».</li><li>در بخش"
            " <b>Connection</b> گزینه <b>Server URL</b> را نگه دار و لینک فروشگاه را"
            " بچسبان.</li><li>هشدار روی فرم را بخوان (یک هشدار استاندارد است — پایین"
            " صفحه در بخش «آیا امن است؟» توضیحش داده‌ایم)، تیک <b>I understand and"
            " want to continue</b> را بزن.</li><li><b>Create as a plugin</b> را"
            " بزن.</li><li>برای استفاده: یک گفتگوی تازه باز کن، در کادر پیام <b>@</b>"
            " بزن و پلاگین فروشگاه را انتخاب کن.</li></ol><p>یک نکته صادقانه درباره"
            " پلن‌ها: فرم افزودن روی حساب رایگان هم باز می‌شود و می‌توانی لینک را وارد"
            " کنی. اما راهنمای رسمی OpenAI می‌گوید اتصال کامل برای حساب‌های Business /"
            " Enterprise / Edu است و پلن Pro فقط می‌تواند بخواند (جستجو، قیمت و موجودی)."
            " پس اگر روی حسابت جایی متوقف شد، از محدودیت پلن است، نه از خرابی لینک"
            " فروشگاه.</p>"
        ),
        "supported": True,
        "sort": 2,
    },
    {
        "slug": "gemini",
        "ai_name": "Gemini",
        "title": "Gemini — در اپ وب گوگل ✨",
        "support_note": "در اپ وب گوگل با چند کلیک؛ بعد از وصل شدن، روی موبایل هم کار می‌کند",
        "body": (
            "<p>Gemini هم فقط با لینک وصل می‌شود. وصل کردن در اپ وب انجام می‌شود، ولی"
            " خبر خوب این است: بعد از وصل شدن، در اپ موبایل Gemini هم در دسترس"
            " است.</p><ol><li>لینک فروشگاه را کپی کن و در مرورگر برو به"
            " <b>gemini.google.com</b>.</li><li>از نوار کناری <b>Customize</b> را بزن"
            " (اگر این گزینه را ندیدی، اول <b>More</b> را بزن).</li><li>بالای صفحه"
            " <b>Connectors</b> را بزن.</li><li><b>+ Custom</b> را بزن تا پنجره"
            " <b>Connect to an MCP server</b> باز شود.</li><li>لینک فروشگاه را در فیلد"
            " <b>MCP Server URL</b> بچسبان — لینک باید با <b>https://</b> شروع شود —"
            " و <b>Next</b> را بزن.</li><li>بعد از وصل شدن، در گفتگو <b>@</b> بزن و"
            " اپ فروشگاه را انتخاب کن تا Gemini از همان استفاده کند.</li></ol><p>شرط‌های"
            " Gemini: برای این قابلیت باید با حساب شخصی گوگل وارد شده باشی (حساب کاری"
            " یا مدرسه‌ای نمی‌شود)، بالای ۱۸ سال باشی، History حسابت روشن باشد، و فعلاً"
            " این بخش فقط به زبان انگلیسی کار می‌کند.</p>"
        ),
        "supported": True,
        "sort": 3,
    },
    {
        "slug": "codex",
        "ai_name": "Codex",
        "title": "Codex — ابزار کدنویسی 🛠️",
        "support_note": "ابزار برنامه‌نویسی OpenAI است، نه ابزار خرید",
        "body": (
            "<p>Codex ابزار برنامه‌نویسی OpenAI است، نه ابزار خرید؛ برای خرید روزمره"
            " سراغ سه برنامه اول برو. این بخش فقط برای کامل بودن راهنماست، برای کسانی"
            " که با Codex کار می‌کنند.</p><ol><li>در اپ دسکتاپ Codex برو به <b>Settings"
            " &gt; MCP servers</b>.</li><li><b>Add server</b> را بزن، یک اسم بنویس و"
            " <b>Streamable HTTP</b> را انتخاب کن.</li><li>لینک فروشگاه را بچسبان،"
            " ذخیره کن و <b>Restart</b> را بزن.</li></ol><p>یا همین متن را در فایل"
            " <b>config.toml</b> بگذار:</p><pre>[mcp_servers.genabay]\nurl ="
            " \"https://genabay.ir/mcp\"</pre>"
        ),
        "supported": True,
        "sort": 4,
    },
    {
        "slug": "gemini-cli",
        "ai_name": "Gemini CLI",
        "title": "Gemini CLI — فقط توسعه‌دهنده‌ها ⌨️",
        "support_note": "ابزار ترمینال گوگل؛ برای برنامه‌نویس‌هاست",
        "body": (
            "<p>این ابزار ترمینال گوگل است و فقط به درد برنامه‌نویس‌ها می‌خورد. کاربر"
            " عادی از خود اپ Gemini وصل می‌شود (بخش Gemini در بالا). سریع‌ترین راه،"
            " این دستور است:</p><pre>gemini mcp add --transport http genabay"
            " https://genabay.ir/mcp</pre><p>یا این متن را در فایل"
            " <b>~/.gemini/settings.json</b> بگذار. دقت کن: فیلد درست <b>httpUrl</b>"
            " است.</p><pre>{\n  \"mcpServers\": {\n    \"genabay\": {\n      \"httpUrl\":"
            " \"https://genabay.ir/mcp\",\n      \"timeout\": 5000\n    }\n  }\n}</pre>"
        ),
        "supported": True,
        "sort": 5,
    },
    {
        "slug": "antigravity",
        "ai_name": "Antigravity",
        "title": "Antigravity — فقط توسعه‌دهنده‌ها 🚀",
        "support_note": "محیط کدنویسی گوگل؛ فقط برای توسعه‌دهنده‌ها",
        "body": (
            "<p>Antigravity محیط کدنویسی گوگل است و این بخش هم فقط برای توسعه‌دهنده‌هاست."
            " اینجا سرور فروشگاه با چسباندن لینک در یک فرم اضافه نمی‌شود؛ باید متن زیر"
            " را در فایل <b>mcp_config.json</b> بگذاری. مسیر دقیق فایل را داخل برنامه،"
            " از دکمه <b>Copy Config File Path</b> در بخش سرورهای MCP بردار. دقت کن:"
            " اینجا فیلد درست <b>serverUrl</b> است.</p><pre>{\n  \"mcpServers\":"
            " {\n    \"genabay\": {\n      \"serverUrl\": \"https://genabay.ir/mcp/\"\n"
            "    }\n  }\n}</pre><p>ابزارها در Antigravity به‌صورت پیش‌فرض در حالت"
            " Ask اجرا می‌شوند؛ یعنی قبل از هر کاری از تو اجازه می‌گیرد.</p>"
        ),
        "supported": True,
        "sort": 6,
    },
    {
        "slug": "claude-code",
        "ai_name": "Claude Code",
        "title": "Claude Code — برای کامل بودن 👨‍💻",
        "support_note": "اگر کانکتور را در Claude اضافه کرده باشی، خودکار در دسترس است",
        "body": (
            "<p>اگر کانکتور فروشگاه را در Claude اضافه کرده باشی، در Claude Code هم"
            " خودکار در دسترس است. دستور مستقیمش هم این است:</p><pre>claude mcp add"
            " --transport http genabay https://genabay.ir/mcp</pre>"
        ),
        "supported": True,
        "sort": 7,
    },
]

# Persian digits and the Persian thousands separator, for prices.
_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def format_toman_price(value: int | str | None) -> str:
    """Format a price for the landing page (Persian digits, e.g. ۴۵٬۰۰۰٬۰۰۰).

    Directus stores a price as a Toman integer; the template prints the
    string as-is, so integers are formatted here. Strings pass through
    unchanged (they are already formatted).
    """
    if isinstance(value, int) and not isinstance(value, bool):
        grouped = f"{value:,}".replace(",", "٬")
        return grouped.translate(_FA_DIGITS)
    if isinstance(value, str):
        return value
    return ""


class DirectusClient:
    """Read-only client for the Directus collections the shop needs."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        base_url: str | None = None,
        token: str | None = None,
        cache: Cache | None = None,
    ) -> None:
        self._http = http
        # Default is the Directus service from compose.yaml.
        self._base_url = (base_url or os.environ.get("DIRECTUS_URL", "http://localhost:8055")).rstrip("/")
        self._token = token or os.environ.get("DIRECTUS_TOKEN")
        # None means "no caching" (callers that do not want it).
        self._cache = cache
        self._category_map: dict[int | str, str] | None = None

    def _headers(self) -> dict[str, str]:
        """Authorization header when a token is configured (public reads need none)."""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    async def _get_items(
        self, collection: str, fields: list[str], active_only: bool = True
    ) -> list[dict[str, object]]:
        """Fetch one published collection from Directus, or raise.

        Raises ``httpx.HTTPError`` on network/HTTP failure and ``ValueError``
        when the response body is not the expected shape. The caller decides
        what a failure means (for landing content: fall back).
        ``active_only=False`` skips the is_active filter — for collections
        that have no is_active column (site_settings) or when the caller
        needs to see inactive rows too (one legal page by slug).
        """
        url = f"{self._base_url}/items/{collection}"
        params: dict[str, str | int] = {
            "fields": ",".join(fields),
            "limit": 50,
        }
        if active_only:
            params["filter[is_active][_eq]"] = "true"
        response = await self._http.get(url, headers=self._headers(), params=params)
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError(f"Directus {collection} returned non-object JSON")
        rows = data.get("data")
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise ValueError(f"Directus {collection} returned no 'data' list")
        return rows

    async def _fetch_landing_content(self) -> LandingContent:
        """Fetch and map the landing content from Directus (no cache, no fallback)."""
        categories = await self._get_items(
            "categories", ["id", "name", "description", "icon", "sort"]
        )
        products = await self._get_items(
            "products",
            [
                "id",
                "name",
                "price_toman",
                "tag",
                "category",
                "image_path",
                "condition",
                "condition_stars",
            ],
        )

        # Category id -> name, so each product can carry its category name
        # for the per-category item count. Tolerates rows without an id.
        category_names: dict[int | str, str] = {}
        mapped_categories: list[CategoryContent] = []
        for row in categories:
            name = row["name"]
            if not isinstance(name, str):
                raise ValueError("Directus category name must be a string")
            raw_desc = row.get("description") or ""
            if not isinstance(raw_desc, str):
                raise ValueError("Directus category description must be a string")
            raw_icon = row.get("icon")
            icon_val: str | None = raw_icon if isinstance(raw_icon, str) else None
            raw_sort = row.get("sort")
            sort_val: int = (
                raw_sort
                if isinstance(raw_sort, int) and not isinstance(raw_sort, bool)
                else 0
            )
            cat_id = row.get("id")
            if isinstance(cat_id, int | str) and not isinstance(cat_id, bool):
                category_names[cat_id] = name
                category_names[str(cat_id)] = name
            mapped_categories.append(
                {"name": name, "desc": raw_desc, "icon": icon_val, "sort": sort_val}
            )
        # Display order comes from Directus (sort); ties keep CMS order.
        mapped_categories.sort(key=lambda c: c["sort"])

        mapped_products: list[ProductContent] = []
        for row in products:
            name = row["name"]
            if not isinstance(name, str):
                raise ValueError("Directus product name must be a string")
            raw_price = row.get("price_toman")
            price_val: int | str | None = (
                raw_price
                if isinstance(raw_price, int | str) and not isinstance(raw_price, bool)
                else None
            )
            raw_tag = row.get("tag")
            tag_val: str | None = raw_tag if isinstance(raw_tag, str) else None
            raw_category = row.get("category")
            category_val: str | None = None
            if isinstance(raw_category, dict):
                cname = raw_category.get("name")
                category_val = str(cname) if isinstance(cname, str) else None
            elif isinstance(raw_category, int | str) and not isinstance(
                raw_category, bool
            ):
                category_val = category_names.get(raw_category) or category_names.get(
                    str(raw_category)
                )
            raw_image = row.get("image_path")
            image_val: str | None = raw_image if isinstance(raw_image, str) else None
            raw_id = row.get("id")
            id_val: str | None = raw_id if isinstance(raw_id, str) else None
            price_toman_val: int | None = (
                price_val
                if isinstance(price_val, int) and not isinstance(price_val, bool)
                else None
            )
            mapped_products.append(
                {
                    "name": name,
                    "price": format_toman_price(price_val),
                    "price_toman": price_toman_val,
                    "id": id_val,
                    "tag": tag_val,
                    "category": category_val,
                    "image_path": image_val,
                    "condition": _clean_condition_value(row.get("condition")),
                    "condition_stars": _clean_stars_value(row.get("condition_stars")),
                }
            )

        return {"categories": mapped_categories, "products": mapped_products}

    async def get_landing_content(self) -> LandingContent:
        """Return landing content: cache first, then Directus, then fallback.

        Any failure — Directus down, timeout, HTTP error, unexpected body —
        means the same thing: serve the fallback and log a warning. The
        shop page must never fail because the CMS is unreachable. Failures
        are not cached, so the next request retries Directus right away.
        """
        if self._cache is not None:
            try:
                cached = await self._cache.get(LANDING_CONTENT_KEY)
                if cached is not None:
                    parsed = json.loads(cached)
                    if (
                        isinstance(parsed, dict)
                        and isinstance(parsed.get("categories"), list)
                        and isinstance(parsed.get("products"), list)
                    ):
                        return cast(LandingContent, parsed)
            except Exception as exc:  # noqa: BLE001 - cache failure never breaks landing
                logger.warning("Landing cache read failed, bypassing cache: %s", exc)
        try:
            content = await self._fetch_landing_content()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Directus unavailable, using hardcoded landing content: %s", exc
            )
            return {
                "categories": list(FALLBACK_CATEGORIES),
                "products": list(FALLBACK_PRODUCTS),
            }
        if self._cache is not None:
            try:
                await self._cache.set(
                    LANDING_CONTENT_KEY, json.dumps(content), LANDING_CONTENT_TTL
                )
            except Exception as exc:  # noqa: BLE001 - cache failure never breaks landing
                logger.warning("Landing cache write failed: %s", exc)
        return content

    async def get_category_map(self) -> dict[int | str, str]:
        """Fetch categories collection from Directus and return {id: name} mapping.

        Cached in memory and in Redis to avoid redundant network calls.
        """
        if self._category_map is not None:
            return self._category_map

        if self._cache is not None:
            try:
                cached = await self._cache.get(CATEGORIES_MAP_KEY)
                if cached is not None:
                    parsed = json.loads(cached)
                    if isinstance(parsed, dict):
                        mapping: dict[int | str, str] = {}
                        for k, v in parsed.items():
                            mapping[k] = str(v)
                            if k.isdigit():
                                mapping[int(k)] = str(v)
                        self._category_map = mapping
                        return self._category_map
            except Exception as exc:  # noqa: BLE001
                logger.warning("Category map cache read failed: %s", exc)

        mapping: dict[int | str, str] = {}
        try:
            items = await self._get_items("categories", ["id", "name"])
            for item in items:
                cid = item.get("id")
                cname = item.get("name")
                if isinstance(cname, str):
                    if isinstance(cid, int) and not isinstance(cid, bool):
                        mapping[cid] = cname
                        mapping[str(cid)] = cname
                    elif isinstance(cid, str):
                        mapping[cid] = cname
                        if cid.isdigit():
                            mapping[int(cid)] = cname
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch categories map from Directus: %s", exc)

        if mapping:
            self._category_map = mapping
            if self._cache is not None:
                try:
                    to_cache = {str(k): v for k, v in mapping.items()}
                    await self._cache.set(
                        CATEGORIES_MAP_KEY, json.dumps(to_cache), CATEGORIES_MAP_TTL
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Category map cache write failed: %s", exc)

        return mapping

    async def list_products(self, limit: int = 20) -> list[dict]:
        """Active products, newest first, from Directus.

        This is the product list the shop serves (``GET /products``).
        Raises ``httpx.HTTPError`` or ``ValueError`` on any failure, so
        the caller can fall back to the same table through SQLAlchemy.
        """
        params: dict[str, str | int] = {
            "fields": ",".join(PRODUCT_READ_FIELDS),
            "filter[is_active][_eq]": "true",
            "sort": "-created_at",
            "limit": max(1, limit),
        }
        response = await self._http.get(
            f"{self._base_url}/items/products",
            headers=self._headers(),
            params=params,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Directus products returned non-object JSON")
        rows = data.get("data")
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise ValueError("Directus products returned no 'data' list")
        category_map = await self.get_category_map()
        return [_product_row(row, category_map=category_map) for row in rows]

    async def list_categories(self) -> list[dict[str, object]]:
        """Fetch categories collection from Directus, or return fallback.

        Sorted by the ``sort`` value set in Directus (lower first); rows
        without one keep their CMS order. Every new category an editor
        adds in Directus shows up here — and in the search filters and
        the categories page — with no code change.
        """
        try:
            rows = await self._get_items(
                "categories",
                ["id", "name", "description", "icon", "sort", "filter_attributes"],
            )
            rows.sort(key=_sort_value)
            return rows
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch categories list from Directus: %s", exc)
            return [
                {
                    "id": idx + 1,
                    "name": c["name"],
                    "description": c["desc"],
                    "icon": c["icon"],
                    "sort": c["sort"],
                    "filter_attributes": [],
                }
                for idx, c in enumerate(FALLBACK_CATEGORIES)
            ]

    async def get_category_filter_attributes(
        self,
    ) -> dict[int | str, list[dict]]:
        """Attribute definitions per category, from Directus.

        Returns ``{category id: [definitions]}`` where each definition
        is a dict like {"key": "screen_size_inch", "label": "اندازه صفحه",
        "unit": "اینچ", "type": "number"}. The filter panel (Phase 4)
        reads the definitions for the visitor's category from here;
        categories without definitions map to []. Empty when Directus
        is unreachable (the fallback categories have no attributes).
        """
        definitions: dict[int | str, list[dict]] = {}
        for row in await self.list_categories():
            cid = row.get("id")
            if not isinstance(cid, int | str) or isinstance(cid, bool):
                continue
            attrs = _clean_filter_attributes_value(row.get("filter_attributes"))
            definitions[cid] = attrs
            if isinstance(cid, int):
                definitions[str(cid)] = attrs
            elif isinstance(cid, str) and cid.isdigit():
                definitions[int(cid)] = attrs
        return definitions

    async def get_site_content(self) -> SiteContent:
        """Return the site content: cache first, then Directus, then fallback.

        One cached payload for the shared header/footer/landing: settings,
        trust badges, legal pages and certification badges. Any failure —
        Directus down, a collection missing, an unexpected body — means
        the same thing: serve the hardcoded fallback and log a warning.
        The shop page must never fail because the CMS is unreachable.
        Failures are not cached, so the next request retries right away.
        """
        if self._cache is not None:
            try:
                cached = await self._cache.get(SITE_CONTENT_KEY)
                if cached is not None:
                    parsed = json.loads(cached)
                    if (
                        isinstance(parsed, dict)
                        and isinstance(parsed.get("settings"), dict)
                        and isinstance(parsed.get("trust_badges"), list)
                        and isinstance(parsed.get("legal_pages"), list)
                        and isinstance(parsed.get("cert_badges"), list)
                        and isinstance(parsed.get("categories"), list)
                    ):
                        return cast(SiteContent, parsed)
            except Exception as exc:  # noqa: BLE001 - cache failure never breaks a page
                logger.warning("Site content cache read failed, bypassing: %s", exc)
        try:
            content = await self._fetch_site_content()
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning(
                "Directus site content unavailable, using fallback: %s", exc
            )
            return copy.deepcopy(FALLBACK_SITE_CONTENT)
        if self._cache is not None:
            try:
                await self._cache.set(
                    SITE_CONTENT_KEY, json.dumps(content), SITE_CONTENT_TTL
                )
            except Exception as exc:  # noqa: BLE001 - cache failure never breaks a page
                logger.warning("Site content cache write failed: %s", exc)
        return content

    async def _fetch_site_content(self) -> SiteContent:
        """Fetch and map the site content from Directus (no cache, no fallback)."""
        settings_rows = await self._get_items(
            "site_settings",
            [
                "site_title",
                "site_tagline",
                "hero_title",
                "hero_subtitle",
                "title_suffix",
                "enamad_seal_id",
                "enamad_seal_code",
                "logo",
                "sells_used",
                "mcp_title",
                "mcp_text",
                "mcp_url",
                "mcp_chatgpt_url",
                "mcp_gemini_url",
                "mcp_guide_intro",
                "instagram_title",
                "instagram_text",
                "instagram_url",
            ],
            active_only=False,
        )
        fallback_settings = FALLBACK_SITE_CONTENT["settings"]
        settings: SiteSettings = copy.deepcopy(fallback_settings)
        if settings_rows:
            row = settings_rows[0]
            title = row.get("site_title")
            if isinstance(title, str) and title.strip():
                settings["site_title"] = title.strip()
            tagline = row.get("site_tagline")
            if isinstance(tagline, str):
                settings["site_tagline"] = tagline
            hero_title = row.get("hero_title")
            if isinstance(hero_title, str) and hero_title.strip():
                settings["hero_title"] = hero_title.strip()
            hero_subtitle = row.get("hero_subtitle")
            if isinstance(hero_subtitle, str):
                settings["hero_subtitle"] = hero_subtitle
            # Title suffix: a CMS string wins (an explicitly empty
            # string means no suffix); NULL keeps the fallback.
            title_suffix = row.get("title_suffix")
            if isinstance(title_suffix, str):
                settings["title_suffix"] = title_suffix.strip()
            # Enamad seal: a non-empty CMS value wins; an explicitly
            # empty string means "no seal on this site" (overrides the
            # fallback); NULL keeps the fallback (Genabay's seal).
            for key in ("enamad_seal_id", "enamad_seal_code"):
                raw = row.get(key)
                if isinstance(raw, str):
                    settings[key] = raw.strip() or None
            logo = row.get("logo")
            settings["logo"] = logo if isinstance(logo, str) else None
            sells_used = row.get("sells_used")
            settings["sells_used"] = sells_used is True
            # Promo boxes + guide intro: a non-empty CMS value wins,
            # otherwise the hardcoded fallback stays.
            for key in (
                "mcp_title",
                "mcp_text",
                "mcp_url",
                "mcp_chatgpt_url",
                "mcp_gemini_url",
                "mcp_guide_intro",
                "instagram_title",
                "instagram_text",
                "instagram_url",
            ):
                value = row.get(key)
                if isinstance(value, str) and value.strip():
                    settings[key] = value.strip()

        badge_rows = await self._get_items(
            "trust_badges", ["title", "text", "icon", "sort"]
        )
        trust_badges: list[TrustBadge] = []
        for row in sorted(badge_rows, key=_sort_value):
            title = row.get("title")
            text = row.get("text")
            if not isinstance(title, str) or not isinstance(text, str):
                continue
            icon = row.get("icon")
            trust_badges.append(
                {
                    "title": title,
                    "text": text,
                    "icon": icon if isinstance(icon, str) and icon else "✦",
                }
            )

        page_rows = await self._get_items(
            "legal_pages", ["slug", "title", "body", "sort", "is_active"]
        )
        legal_pages: list[LegalPage] = []
        for row in sorted(page_rows, key=_sort_value):
            slug = row.get("slug")
            title = row.get("title")
            if not isinstance(slug, str) or not isinstance(title, str):
                continue
            body = row.get("body")
            legal_pages.append(
                {
                    "slug": slug,
                    "title": title,
                    "body": body if isinstance(body, str) else "",
                    "is_active": row.get("is_active") is True,
                }
            )

        cert_rows = await self._get_items(
            "certification_badges", ["name", "status_text", "image", "link", "sort"]
        )
        cert_badges: list[CertBadge] = []
        for row in sorted(cert_rows, key=_sort_value):
            name = row.get("name")
            if not isinstance(name, str):
                continue
            status_text = row.get("status_text")
            image = row.get("image")
            link = row.get("link")
            cert_badges.append(
                {
                    "name": name,
                    "status_text": status_text if isinstance(status_text, str) else "",
                    "image": image if isinstance(image, str) else None,
                    "link": link if isinstance(link, str) and link else None,
                }
            )

        # Categories for the header search panel. Same mapping as the
        # landing content: name is required, the rest tolerates bad rows.
        category_rows = await self._get_items(
            "categories", ["id", "name", "description", "icon", "sort"]
        )
        site_categories: list[CategoryContent] = []
        for row in category_rows:
            cat_name = row.get("name")
            if not isinstance(cat_name, str):
                continue
            raw_desc = row.get("description") or ""
            raw_icon = row.get("icon")
            raw_sort = row.get("sort")
            site_categories.append(
                {
                    "name": cat_name,
                    "desc": raw_desc if isinstance(raw_desc, str) else "",
                    "icon": raw_icon if isinstance(raw_icon, str) else None,
                    "sort": (
                        raw_sort
                        if isinstance(raw_sort, int)
                        and not isinstance(raw_sort, bool)
                        else 0
                    ),
                }
            )
        site_categories.sort(key=lambda c: c["sort"])

        return {
            "settings": settings,
            "trust_badges": trust_badges,
            "legal_pages": legal_pages,
            "cert_badges": cert_badges,
            "categories": site_categories,
        }

    async def get_legal_page(self, slug: str) -> dict | None:
        """One legal page by slug, or None when it does not exist.

        No is_active filter here: the caller must tell "deleted by the
        editor" (inactive → 404) apart from "never created / CMS down"
        (None → fall back to the static template). Any failure returns
        None, so the route falls back instead of breaking the page.
        """
        try:
            response = await self._http.get(
                f"{self._base_url}/items/legal_pages",
                headers=self._headers(),
                params={
                    "fields": "slug,title,body,is_active,sort",
                    "filter[slug][_eq]": slug,
                    "limit": 1,
                },
            )
            response.raise_for_status()
            data = response.json()
            rows = data.get("data") if isinstance(data, dict) else None
            if isinstance(rows, list) and rows and isinstance(rows[0], dict):
                row = rows[0]
                body = row.get("body")
                return {
                    "slug": slug,
                    "title": row.get("title") if isinstance(row.get("title"), str) else slug,
                    "body": body if isinstance(body, str) else "",
                    "is_active": row.get("is_active") is True,
                }
            return None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch legal page %s: %s", slug, exc)
            return None

    async def get_mcp_guide_sections(self) -> list[GuideSection]:
        """All active MCP guide sections, in CMS sort order.

        Any failure — Directus down, collection missing — returns the
        hardcoded fallback sections, so the guide page never goes blank.
        """
        try:
            rows = await self._get_items(
                "mcp_guide_sections",
                ["slug", "ai_name", "title", "support_note", "body", "supported", "sort"],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "MCP guide sections unavailable, using fallback: %s", exc
            )
            return copy.deepcopy(FALLBACK_GUIDE_SECTIONS)
        sections: list[GuideSection] = []
        for row in sorted(rows, key=_sort_value):
            slug = row.get("slug")
            title = row.get("title")
            if not isinstance(slug, str) or not slug.strip():
                continue
            if not isinstance(title, str) or not title.strip():
                continue
            ai_name = row.get("ai_name")
            body = row.get("body")
            note = row.get("support_note")
            raw_sort = row.get("sort")
            sections.append(
                {
                    "slug": slug.strip(),
                    "ai_name": ai_name if isinstance(ai_name, str) else slug.strip(),
                    "title": title.strip(),
                    "support_note": note if isinstance(note, str) else "",
                    "body": body if isinstance(body, str) else "",
                    "supported": row.get("supported") is True,
                    "sort": (
                        raw_sort
                        if isinstance(raw_sort, int)
                        and not isinstance(raw_sort, bool)
                        else 0
                    ),
                }
            )
        sections.sort(key=lambda s: s["sort"])
        return sections or copy.deepcopy(FALLBACK_GUIDE_SECTIONS)

    async def get_asset(self, file_id: str) -> tuple[bytes, str] | None:
        """Fetch one Directus file's bytes + content type, or None.

        Serves the CMS logo and seal images through the shop itself
        (``/site/logo``, ``/site/badge/<id>``), so the browser never
        needs the Directus host. ``file_id`` must be a plain id — no
        slashes, no ``..`` — anything else is refused outright.
        """
        if not file_id or "/" in file_id or "\\" in file_id or ".." in file_id:
            return None
        try:
            response = await self._http.get(
                f"{self._base_url}/assets/{file_id}", headers=self._headers()
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            content_type = response.headers.get("content-type", "application/octet-stream")
            return response.content, content_type
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to fetch Directus asset %s: %s", file_id, exc)
            return None

    async def list_products_by_category(
        self, category_identifier: str | int, limit: int = 50
    ) -> list[dict]:
        """Fetch active products belonging to a category from Directus."""
        category_map = await self.get_category_map()
        target_id: int | str | None = None
        target_name: str | None = None

        if isinstance(category_identifier, int) or (
            isinstance(category_identifier, str) and category_identifier.isdigit()
        ):
            target_id = int(category_identifier)
            target_name = category_map.get(target_id) or category_map.get(str(target_id))
        else:
            target_name = str(category_identifier).strip()
            for cid, cname in category_map.items():
                if cname.lower() == target_name.lower():
                    target_id = cid
                    break

        params: dict[str, str | int] = {
            "fields": ",".join(PRODUCT_READ_FIELDS),
            "filter[is_active][_eq]": "true",
            "sort": "-created_at",
            "limit": max(1, limit),
        }
        if target_id is not None:
            params["filter[category][_eq]"] = str(target_id)

        try:
            response = await self._http.get(
                f"{self._base_url}/items/products",
                headers=self._headers(),
                params=params,
            )
            response.raise_for_status()
            data = response.json()
            rows = data.get("data")
            if isinstance(rows, list):
                result = [
                    _product_row(row, category_map=category_map)
                    for row in rows
                    if isinstance(row, dict)
                ]
                if target_name and target_id is None:
                    result = [p for p in result if p.get("category") == target_name]
                return result
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to list products by category from Directus: %s", exc)

        # Fallback: filter from list_products
        try:
            all_products = await self.list_products(limit)
            matched = []
            for p in all_products:
                p_cat = p.get("category")
                if target_name and p_cat and target_name.lower() in p_cat.lower():
                    matched.append(p)
                elif target_id and p_cat and (
                    str(target_id) == str(p_cat) or category_map.get(target_id) == p_cat
                ):
                    matched.append(p)
            return matched
        except Exception:  # noqa: BLE001
            return []

    async def get_product(self, product_id: uuid.UUID | str) -> dict | None:
        """One product by id from Directus. None when it does not exist.

        No ``is_active`` filter here: a single product read matches the
        old database read, which never filtered on active either.
        Directus answers 403 (not 404) for a missing single item under
        the public role, so both 403 and 404 mean "no such product".
        Any other failure raises, so the caller can fall back.
        """
        response = await self._http.get(
            f"{self._base_url}/items/products/{product_id}",
            headers=self._headers(),
            params={"fields": ",".join(PRODUCT_READ_FIELDS)},
        )
        if response.status_code in (404, 403):
            return None
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Directus product returned non-object JSON")
        row = data.get("data")
        if not isinstance(row, dict):
            raise ValueError("Directus product returned no 'data' object")
        category_map = await self.get_category_map()
        return _product_row(row, category_map=category_map)

    async def get_products_by_ids(
        self, product_ids: list[uuid.UUID | str]
    ) -> list[dict]:
        """Several products by id, in one request (cart + order views).

        Uses Directus ``filter[id][_in]``. Unknown ids are simply
        absent from the result — the caller decides what that means.
        Empty input means an empty list, never a request.
        """
        ids = [str(pid) for pid in product_ids]
        if not ids:
            return []
        response = await self._http.get(
            f"{self._base_url}/items/products",
            headers=self._headers(),
            params={
                "fields": ",".join(PRODUCT_READ_FIELDS),
                "filter[id][_in]": ",".join(ids),
                "limit": max(1, len(ids)),
            },
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ValueError("Directus products returned non-object JSON")
        rows = data.get("data")
        if not isinstance(rows, list) or not all(
            isinstance(row, dict) for row in rows
        ):
            raise ValueError("Directus products returned no 'data' list")
        category_map = await self.get_category_map()
        by_id = {
            str(row["id"]): _product_row(row, category_map=category_map)
            for row in rows
            if row.get("id") is not None
        }
        return [by_id[pid] for pid in ids if pid in by_id]


def _clean_condition_value(value: object) -> str:
    """Coerce a CMS condition value to 'new' or 'used'; anything else is 'new'."""
    if isinstance(value, str) and value.strip().lower() in ("new", "used"):
        return value.strip().lower()
    return "new"


def _clean_stars_value(value: object) -> int | None:
    """Coerce a CMS stars value to 1–5; anything else is None (no stars)."""
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
        return value
    return None


def _clean_specs_value(value: object) -> dict:
    """Coerce a CMS specs value to a dict; anything else is {} (no attributes)."""
    if isinstance(value, dict):
        return dict(value)
    return {}


def _clean_filter_attributes_value(value: object) -> list:
    """Coerce a CMS filter_attributes value to a list; anything else is [].

    Each definition is a dict like {"key": "screen_size_inch",
    "label": "اندازه صفحه", "unit": "اینچ", "type": "number"}.
    Rows that are not dicts, or dicts without a string "key", are dropped.
    """
    if not isinstance(value, list):
        return []
    cleaned: list[dict] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("key"), str):
            cleaned.append(dict(item))
    return cleaned


def _sort_value(row: dict[str, object]) -> int:
    """The CMS ``sort`` value of a row (lower first); missing → 0."""
    sort = row.get("sort")
    return sort if isinstance(sort, int) and not isinstance(sort, bool) else 0


def _product_row(
    row: dict,
    category_map: dict[int | str, str] | None = None,
) -> dict:
    """Validate one Directus product row into the ProductRead shape.

    Only the fields ``ProductRead`` needs are kept, so the route can
    build the response with ``ProductRead.model_validate``. Defaults
    gracefully for fields that Directus editors may omit (stock, reserved, is_active).
    """
    pid = row.get("id")
    if pid is None:
        raise ValueError("Directus product id is missing")
    pid_str = str(pid)

    name = row.get("name")
    if not isinstance(name, str):
        raise ValueError("Directus product name must be a string")

    price = row.get("price_toman")
    if not isinstance(price, int) or isinstance(price, bool):
        raise ValueError("Directus product price_toman must be an integer")

    # Stock lives in the shop's product_stock table, NOT in Directus
    # (Directus has no row lock, so it cannot own stock). The
    # stock/reserved columns still exist on the shared products table,
    # but they are dead: callers overwrite these two numbers with the
    # stock table's values via services.stock_service.attach_stock
    # before anything is shown or sold. Zero here means "no stock
    # attached yet", never a real count.
    stock = 0
    reserved = 0

    is_active = row.get("is_active")
    if is_active is None:
        is_active = True
    elif not isinstance(is_active, bool):
        raise ValueError("Directus product is_active must be a boolean")

    description = row.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("Directus product description must be a string or null")

    image_path = row.get("image_path")
    if image_path is not None and not isinstance(image_path, str):
        raise ValueError("Directus product image_path must be a string or null")

    created_at = row.get("created_at")
    if created_at is None:
        created_at = datetime.datetime.now(datetime.UTC).isoformat()
    elif not isinstance(created_at, str):
        raise ValueError("Directus product created_at must be a string")

    category_val = row.get("category")
    category: str | None = None
    if isinstance(category_val, dict):
        cname = category_val.get("name")
        category = str(cname) if cname is not None else None
    elif category_val is not None:
        if category_map and category_val in category_map:
            category = category_map[category_val]
        elif category_map and str(category_val) in category_map:
            category = category_map[str(category_val)]
        elif isinstance(category_val, str) and not category_val.isdigit():
            category = category_val
        elif category_map and isinstance(category_val, str) and category_val.isdigit():
            cid = int(category_val)
            category = category_map.get(cid, category_val)
        else:
            category = str(category_val)

    tag = row.get("tag")
    if tag is not None and not isinstance(tag, str):
        tag = str(tag)

    condition = _clean_condition_value(row.get("condition"))
    stars = _clean_stars_value(row.get("condition_stars"))
    if condition != "used":
        stars = None

    # Attribute values, e.g. {"screen_size_inch": 55}. Directus JSON
    # field; anything that is not an object becomes {} (no attributes).
    specs = _clean_specs_value(row.get("specs"))

    return {
        "id": pid_str,
        "name": name,
        "description": description,
        "price_toman": price,
        "stock": stock,
        "reserved": reserved,
        "image_path": image_path,
        "is_active": is_active,
        "created_at": created_at,
        "category": category,
        "tag": tag,
        "condition": condition,
        "condition_stars": stars,
        "specs": specs,
    }


# Fields the product list and single-product reads ask Directus for.
# Same columns the ``products`` table has, so no extra field is needed.
PRODUCT_READ_FIELDS = [
    "id",
    "name",
    "description",
    "price_toman",
    "stock",
    "reserved",
    "image_path",
    "is_active",
    "category",
    "tag",
    "created_at",
    "condition",
    "condition_stars",
    "specs",
]


async def invalidate_landing_content(cache: Cache) -> None:
    """Delete-on-change: drop the cached landing content right now.

    Call this when content changes in Directus (webhook receiver or admin
    action) instead of waiting up to 60 seconds for the TTL to expire.
    """
    await cache.delete(LANDING_CONTENT_KEY)
    await cache.delete(CATEGORIES_MAP_KEY)


async def invalidate_site_content(cache: Cache) -> None:
    """Delete-on-change: drop the cached site content right now.

    Call this when site settings, badges or legal pages change in
    Directus instead of waiting up to 60 seconds for the TTL.
    """
    await cache.delete(SITE_CONTENT_KEY)


def get_directus_client(client: HttpClientDep, cache: CacheDep) -> DirectusClient:
    """Dependency: build a DirectusClient on the shared HTTP client and cache."""
    return DirectusClient(client, cache=cache)


# Reusable FastAPI dependency alias for the Directus client
DirectusClientDep = Annotated[DirectusClient, Depends(get_directus_client)]


__all__ = [
    "CategoryContent",
    "CertBadge",
    "DirectusClient",
    "DirectusClientDep",
    "FALLBACK_CATEGORIES",
    "FALLBACK_PRODUCTS",
    "FALLBACK_SITE_CONTENT",
    "LANDING_CONTENT_KEY",
    "LANDING_CONTENT_TTL",
    "LandingContent",
    "LegalPage",
    "ProductContent",
    "SITE_CONTENT_KEY",
    "SITE_CONTENT_TTL",
    "SiteContent",
    "SiteSettings",
    "TrustBadge",
    "format_toman_price",
    "get_directus_client",
    "invalidate_landing_content",
    "invalidate_site_content",
]
