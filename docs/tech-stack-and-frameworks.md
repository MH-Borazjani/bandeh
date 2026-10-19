# Tech Stack and Frameworks Guide

If you have been away from modern Python, FastAPI, or Docker for a while, this guide gives you a quick and practical refresher on the main tools used in this project.

---

## 1. Astral `uv` (Package and Environment Manager)

This project uses **`uv`** (built with Rust by Astral) instead of `pip` or `Poetry`.

### Why `uv`?
- **Speed:** It is 10 to 100 times faster than `pip` when downloading and installing packages.
- **Standards:** Fully compatible with standard Python packaging (`pyproject.toml`).
- **Reliability:** Uses a lockfile (`uv.lock`) to make sure everyone on the team installs the exact same package versions.

### Useful Commands to Remember:
```bash
# Sync your virtual environment with uv.lock
uv sync

# Add a new package and update pyproject.toml and uv.lock
uv add sqlalchemy
uv add pytest --dev    # Development-only dependency

# Remove a package
uv remove <package-name>

# Run commands inside the virtual environment without activating it manually
uv run python app/e-commerce/main.py
uv run fastapi dev app/e-commerce/main.py
```

---

## 2. FastAPI (Modern Async Web Framework)

**FastAPI** is built on modern Python standards, including type hints, **Starlette** (for web routing and performance), and **Pydantic** (for data validation).

### Key Concepts:

#### A) Automatic API Documentation
When you start the server, two documentation pages are automatically available:
- `http://localhost:8000/docs`: Interactive Swagger UI where you can test API endpoints directly in your browser.
- `http://localhost:8000/redoc`: Clean, readable reference documentation.

#### B) `async def` vs Standard `def`
- Use `async def` when performing non-blocking I/O operations (like async database queries, HTTP requests with `httpx`, or reading files asynchronously).
- Use standard `def` for synchronous tasks or libraries that do not support async. FastAPI automatically runs regular `def` functions in a separate thread pool so the server is not blocked.

#### C) Modular Routing with `APIRouter`
To keep code organized, group routes into separate files instead of putting everything inside `main.py`:

```python
# In app/e-commerce/api/routes/orders.py
from fastapi import APIRouter

router = APIRouter(prefix="/orders", tags=["Orders"])

@router.get("/")
async def list_orders():
    return [{"id": 1, "status": "pending"}]

# In app/e-commerce/main.py
from fastapi import FastAPI
from app.e-commerce.api.routes.orders import router as orders_router

app = FastAPI()
app.include_router(orders_router)
```

#### D) Dependency Injection (`Depends`)
FastAPI has a built-in Dependency Injection system. It is commonly used for database sessions, user authentication, or loading configuration settings:

```python
from fastapi import Depends
from env_settings import Settings, get_settings

@router.get("/config")
def read_config(settings: Settings = Depends(get_settings)):
    return {"env": settings.environment}
```

---

## 3. Configuration Management with Pydantic-Settings

The file [env_settings.py](file:///home/mhb/app/coding/easy-auto/env_settings.py) uses `pydantic-settings` to load and validate environment variables from `.env`.

### Highlights from `env_settings.py`:

```python
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",           # Path to environment file
        extra="ignore",            # Ignore extra unknown variables
        case_sensitive=False,      # Case-insensitive variable names
    )
```

### 1) Security with `SecretStr`
```python
secret_key: SecretStr = Field(..., min_length=32)
postgres_password: SecretStr
```
- **Why use `SecretStr`?** If you print this object or log it, the value is masked as `**********`. This prevents passwords from leaking into application logs.
- To read the plain value when connecting to a service: `self.postgres_password.get_secret_value()`

### 2) Dynamic Fields with `@computed_field`
```python
@computed_field
@property
def database_url(self) -> PostgresDsn:
    return PostgresDsn.build(
        scheme="postgresql",
        username=self.postgres_user,
        password=self.postgres_password.get_secret_value(),
        host=self.postgres_host,
        port=self.postgres_port,
        path=self.postgres_db,
    )
```
- Instead of typing connection strings by hand, Pydantic builds and validates a full PostgreSQL URL using the individual parameters.

### 3) Singleton Pattern with `@lru_cache`
```python
@lru_cache
def get_settings() -> Settings:
    return Settings()
```
- Reading `.env` from the disk takes time. Caching this function ensures the settings file is read only once, and the same settings instance is reused everywhere.

---

## 4. Social Media SDK (`zernio-sdk`)

The project includes `zernio-sdk>=1.4.475` in `pyproject.toml`. This is the official Python client for the **Zernio API** (also known as **Late API**).

### What it does:
- **Platforms:** Publish and schedule posts to Instagram, Facebook, TikTok, Google Business, and more.
- **Media Types:** Supports images, videos, stories, and reels.
- **Data Models:** Provides pre-built types like `Platform`, `PostStatus`, `MediaType`, and `Visibility`.

### Example Usage:
```python
from zernio import Zernio, Platform

# Initialize client using your API key from .env
client = Zernio(api_key="your_api_key_from_env")

# Example: Create and schedule a social media post
# client.create_post(...)
```

---

## 5. PostgreSQL 16 and Redis 7

### PostgreSQL
- **Version:** `postgres:16-alpine` (lightweight and efficient).
- **Data Persistence:** Data is stored on your host machine inside `./data/database`.
- **Healthcheck:** Uses `pg_isready` to make sure the database is healthy before dependent services start.

### Redis
- **Version:** `redis:7`.
- **Purpose:** In-memory caching and message queue:
  - Caches database queries for Directus.
  - Manages background tasks and queue workers.

---

## 6. Directus 12 (Headless CMS & Data Platform)

**Directus** is an open-source data platform running on top of PostgreSQL:
- Gives you an instant admin panel without having to write custom admin UI code.
- Connects directly to the existing database without changing your table schemas.
- Local access URL: `http://localhost:8055`
- Handles file uploads, permissions (RBAC), and image resizing with uploads saved to `./volumes/uploads`.
