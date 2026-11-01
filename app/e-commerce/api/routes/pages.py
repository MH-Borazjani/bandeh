"""Public web routes for shop storefront."""

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

try:
    from core.security import CurrentUserDep
    from core.templates import templates
    from services.auth_service import mask_phone
except ImportError:
    from auth import CurrentUserDep, mask_phone
    from core.templates import templates

router = APIRouter(tags=["pages"])

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
    """Render shop landing page with dynamic login/account header."""
    return templates.TemplateResponse(
        request=request,
        name="landing.html",
        context={
            "phone": phone,
            "masked": mask_phone(phone) if phone is not None else None,
            "categories": CATEGORIES,
            "products": SAMPLE_PRODUCTS,
        },
    )


__all__ = ["CATEGORIES", "SAMPLE_PRODUCTS", "router"]

