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
"""Template rendering configuration for Jinja2."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

# Path to the templates directory (app/e-commerce/templates)
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

templates = Jinja2Templates(directory=TEMPLATES_DIR)

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_persian_digits(value: object) -> str:
    """Convert ASCII digits to Persian digits."""
    if value is None:
        return ""
    return str(value).translate(_FA_DIGITS)


def to_persian_item_count(value: object) -> str:
    """Format an item count in Persian digits followed by 'کالا' (e.g. '۱ کالا')."""
    if value is None:
        return "۰ کالا"
    return f"{str(value).translate(_FA_DIGITS)} کالا"


def to_compact_price(value: int | str | None) -> dict[str, str]:
    """Compact price for product displays: 32,000,000 -> {'value': '۳۲', 'unit': 'میلیون'}.

    28,500,000 -> {'value': '۲۸٫۵', 'unit': 'میلیون'}; 500,000 -> '۵۰۰ هزار'.
    Display only: cart totals, checkout, orders and payment pages keep the
    exact full amount, because that is the money that moves.
    """
    if value is None:
        return {"value": "", "unit": ""}
    try:
        amount = int(value)
    except (TypeError, ValueError):
        return {"value": "", "unit": ""}
    if amount >= 1_000_000:
        text = f"{amount / 1_000_000:.1f}".rstrip("0").rstrip(".")
        return {"value": text.translate(_FA_DIGITS).replace(".", "٫"), "unit": "میلیون"}
    if amount >= 1_000:
        text = f"{amount / 1_000:.1f}".rstrip("0").rstrip(".")
        return {"value": text.translate(_FA_DIGITS).replace(".", "٫"), "unit": "هزار"}
    return {"value": str(amount).translate(_FA_DIGITS), "unit": ""}


templates.env.filters["persian_digits"] = to_persian_digits
templates.env.filters["item_count"] = to_persian_item_count
templates.env.filters["compact_price"] = to_compact_price

__all__ = [
    "TEMPLATES_DIR",
    "templates",
    "to_persian_digits",
    "to_persian_item_count",
    "to_compact_price",
]


