"""Main FastAPI application entrypoint."""

from fastapi import FastAPI

from api.routes import auth_router, pages_router

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
