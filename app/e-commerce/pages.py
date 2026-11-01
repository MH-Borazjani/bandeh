"""Public storefront pages re-export for backwards compatibility.

Implementation is located in ``api.routes.pages``.
"""

from api.routes.pages import CATEGORIES, SAMPLE_PRODUCTS, landing, router

__all__ = ["CATEGORIES", "SAMPLE_PRODUCTS", "landing", "router"]
