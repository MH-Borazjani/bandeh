"""Route controllers for e-commerce API and web views."""

from .auth import router as auth_router
from .pages import router as pages_router

__all__ = ["auth_router", "pages_router"]

