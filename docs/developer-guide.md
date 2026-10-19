# Developer Guide

This guide helps you set up the project locally, configure environment variables, and build new features using the project's layered architecture.

---

## 🚀 Getting Started

### 1. Install Astral `uv`
If you do not have `uv` installed on your machine yet, install it with:

```bash
# Linux and macOS
curl -LsSf https://astral.sh/uv/install.sh | sh

# Windows (PowerShell)
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Set Up Environment Variables (`.env`)
Create a `.env` file in the project root. Here is an example for local development:

```env
# Application Settings
ENVIRONMENT=development
DEBUG=true

# Security (must be at least 32 characters long)
SECRET_KEY=supersecretkeythatisatleast32characterslong!

# PostgreSQL Database
POSTGRES_USER=easyauto_user
POSTGRES_PASSWORD=super_secret_db_password
POSTGRES_HOST=database
POSTGRES_PORT=5432
POSTGRES_DB=easyauto_db

# Redis Cache
REDIS_HOST=cache
REDIS_PORT=6379

# Directus Admin
ADMIN_EMAIL=admin@example.com
ADMIN_PASSWORD=admin_secure_password_123

# Social Media Integration
API_KEY_ZERNIO=sk_your_zernio_token_here
```

---

## 💻 Running the Project

### Method A: Run with Docker Compose (Recommended)
This method runs all services (FastAPI, PostgreSQL, Redis, and Directus) in isolated containers:

```bash
docker compose up --build
```
- API & Swagger documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- Directus Admin Panel: [http://localhost:8055](http://localhost:8055)

### Method B: Run FastAPI Locally with `uv`
If you want to run only the Python FastAPI server on your host machine:

```bash
# Install dependencies in the virtual environment
uv sync

# Start the FastAPI development server with auto-reload
uv run fastapi dev app/e-commerce/main.py --port 8000
```

> [!NOTE]
> If you run FastAPI directly on your host machine without Docker, remember to set `POSTGRES_HOST=localhost` and `REDIS_HOST=localhost` in your `.env` file.

---

## 🧩 Step-by-Step: Adding a New Feature

To keep the project clean, follow these steps when you add a new feature (for example, a `products` feature):

### Step 1: Create the Input/Output Schema
Create `app/e-commerce/schema/product.py`:
```python
from pydantic import BaseModel, Field

class ProductCreate(BaseModel):
    title: str = Field(..., min_length=3, max_length=100)
    price: float = Field(..., gt=0)
    inventory: int = Field(default=0, ge=0)

class ProductResponse(ProductCreate):
    id: int

    class Config:
        from_attributes = True
```

### Step 2: Write Business Logic in the Service Layer
Create `app/e-commerce/services/product_service.py`:
```python
from app.e-commerce.schema.product import ProductCreate

class ProductService:
    @staticmethod
    def create_product(data: ProductCreate):
        # Business logic goes here (database queries, checks, events)
        return {"id": 1, **data.model_dump()}
```

### Step 3: Create the Route in the API Layer
Create `app/e-commerce/api/routes/products.py`:
```python
from fastapi import APIRouter, status
from app.e-commerce.schema.product import ProductCreate, ProductResponse
from app.e-commerce.services.product_service import ProductService

router = APIRouter(prefix="/products", tags=["Products"])

@router.post("/", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate):
    return ProductService.create_product(payload)
```

### Step 4: Register the Router in the Main App
In `app/e-commerce/main.py`:
```python
from fastapi import FastAPI
from app.e-commerce.api.routes.products import router as products_router

app = FastAPI(title="Bandeh (easy-auto) API", version="0.1.0")

# Register routes
app.include_router(products_router)

@app.get("/")
def root():
    return {"message": "Hello from easy-auto!"}
```

---

## 🗄️ Database Migrations with Alembic

When you add or update database models in `app/e-commerce/db/models/`, use these commands:

```bash
# Create a new migration file based on model changes
uv run alembic revision --autogenerate -m "Add products table"

# Apply the migration to the database
uv run alembic upgrade head

# Roll back the last migration if needed
uv run alembic downgrade -1
```

---

## 🧪 Testing

To test your code, place tests in the `tests/` directory and run:

```bash
uv run pytest
```
