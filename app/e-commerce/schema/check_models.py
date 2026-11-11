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
"""Quick self-check for the Pydantic schemas (no server, no endpoints).

Run from ``app/e-commerce``::

    python schema/check_models.py

It prints one valid product, one valid order with its computed total,
and the validation errors for two invalid inputs. If anything is broken,
this script fails instead of silently passing.
"""

import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

# Allow `python schema/check_models.py` as well as `python -m schema.check_models`:
# the package lives in this file's parent directory, which is not on
# sys.path when the file is run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import BaseModel, ValidationError

from schema.order import Order, OrderCreate
from schema.product import ProductCreate, ProductUpdate


def main() -> None:
    product = ProductCreate.model_validate(
        {
            "name": "Ceramic mug",
            "price": Decimal("12.50"),
            "stock": 20,
            "sku": "mug-001",  # lower-case on purpose: the validator upper-cases it
            "status": "active",
        }
    )
    print("product SKU after validation:", product.sku)
    assert product.sku == "MUG-001"

    order = OrderCreate.model_validate(
        {
            "customer": {
                "full_name": "Sara Ahmadi",
                "email": "sara@example.com",
                "address": "12 Main Street",
                "city": "Tehran",
            },
            "items": [
                {
                    "product_id": 1,
                    "product_name": "Ceramic mug",
                    "unit_price": Decimal("12.50"),
                    "quantity": 2,
                }
            ],
        }
    )
    stored = Order(
        id=1,
        created_at=datetime(2026, 10, 27, 12, 0, 0),
        customer=order.customer,
        items=order.items,
        notes=order.notes,
    )
    print("order total (computed):", stored.total_amount)
    assert stored.total_amount == Decimal("25.00")

    invalid_cases: list[tuple[str, type[BaseModel], dict[str, object]]] = [
        ("negative price", ProductCreate, {"name": "Mug", "price": "-1", "sku": "X-1"}),
        ("empty order", OrderCreate, {"customer": order.customer, "items": []}),
        (
            "discount greater than price",
            ProductCreate,
            {
                "name": "Mug",
                "price": "10.00",
                "discount_price": "15.00",
                "sku": "MUG-002",
            },
        ),
        (
            "update discount greater than price",
            ProductUpdate,
            {"price": Decimal("10.00"), "discount_price": Decimal("15.00")},
        ),
    ]
    for label, model_cls, bad_input in invalid_cases:
        try:
            model_cls.model_validate(bad_input)
        except ValidationError as exc:
            print(f"correctly rejected ({label}):")
            print(exc)
        else:  # pragma: no cover - this branch means the check failed
            raise AssertionError(f"invalid input was accepted: {label}")

    print("All Pydantic checks passed.")


if __name__ == "__main__":
    main()
