"""Main FastAPI application entrypoint."""

from api.routes import auth_router, pages_router
from fastapi import FastAPI

app = FastAPI(
    title="Bandeh (easy-auto) API",
    version="0.1.0",
    description="Digital Worker & E-commerce automation platform",
)

# Register modular routers
app.include_router(auth_router)
app.include_router(pages_router)


@app.get("/health", tags=["system"])
def health_check() -> dict[str, str]:
    """Health check endpoint for Docker and monitoring."""
    return {"status": "ok"}
