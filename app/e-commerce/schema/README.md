# E-commerce schemas (Pydantic topic)

Data-validation models only. No routes, no database models, no auth.

| File | What it teaches |
| --- | --- |
| `product.py` | `Field` constraints, nested model (`Dimensions`), enum, `field_validator` (SKU normalisation), `model_validator` (discount < price), Create / Update / read split |
| `order.py` | Nested models, list minimum (`items`), `EmailStr`, `computed_field` (line total, order total), single-purpose update model |
| `check_models.py` | Runnable self-check: valid inputs, computed totals, and rejected invalid inputs |

Run the check from `app/e-commerce`:

```bash
python schema/check_models.py
```

Explicitly out of scope here (later topics): API routes, database models,
services, payments, and all auth — OAuth2, password hashing, JWT, `/token`
and protected routes.
