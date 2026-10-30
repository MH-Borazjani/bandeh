"""Public pages. Right now: only the landing page.

URL table:
- GET /  the shop landing page (the header shows login state)
"""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from auth import CurrentUserDep, mask_phone

# Same pattern as auth_routes.py: templates live next to this file.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# No prefix: these are the shop's top-level public URLs.
router = APIRouter(tags=["pages"])

# -- Landing content -------------------------------------------------------
# SAMPLE DATA, clearly marked: placeholder categories and products so the
# page looks like a real shop. The real catalog (database + admin panel)
# comes in a later topic -- then this block gets replaced, not extended.
CATEGORIES = [
    {"name": "یخچال و فریزر", "desc": "ساید، دوقلو، معمولی"},
    {"name": "ماشین لباسشویی", "desc": "۷ تا ۱۰ کیلویی"},
    {"name": "ماشین ظرفشویی", "desc": "رومیزی و ایستاده"},
    {"name": "اجاق گاز و فر", "desc": "صفحه‌ای و مبله"},
    {"name": "جاروبرقی", "desc": "کیسه‌ای و مخزن‌دار"},
    {"name": "لوازم کوچک", "desc": "آشپزخانه و خانه"},
]

SAMPLE_PRODUCTS = [
    {"name": "یخچال فریزر دوقلو", "price": "۴۵٬۰۰۰٬۰۰۰", "tag": "پرفروش"},
    {"name": "ماشین لباسشویی ۸ کیلویی", "price": "۲۸٬۵۰۰٬۰۰۰", "tag": None},
    {"name": "جاروبرقی ۲۰۰۰ وات", "price": "۹٬۸۰۰٬۰۰۰", "tag": "تخفیف‌دار"},
    {"name": "اجاق گاز ۵ شعله", "price": "۱۹٬۰۰۰٬۰۰۰", "tag": None},
]


@router.get("/", response_class=HTMLResponse)
def landing(request: Request, phone: CurrentUserDep) -> HTMLResponse:
    """The shop homepage.

    The header is the only dynamic part: a logged-in visitor sees
    "حساب من", everyone else sees "ورود". Everything else is static
    content rendered from the lists above.
    """
    return templates.TemplateResponse(
        request,
        "landing.html",
        {
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
            "categories": CATEGORIES,
            "products": SAMPLE_PRODUCTS,
        },
    )
