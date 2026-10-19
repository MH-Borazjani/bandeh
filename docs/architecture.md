# Architecture Guide

This document explains the software architecture and folder structure of **Bandeh (easy-auto)**. It follows a clean and modular layered pattern.

---

## 🏛️ General Architecture: Modular Monolith

The project is built as a **Modular Monolith** with clean layers. Instead of building complex microservices at an early stage, different domains live in separate folders under `app/`. This approach makes the code easy to read, fast to debug, and simple to test.

### Two Main System Domains:
1. **`app/e-commerce/`**: Handles store operations, product inventory, customer orders, payments, and shopping workflows.
2. **`app/automaiton/`**: Handles background automation tasks, event processing, AI triggers, and automated workflows.

---

## 📂 Folder Structure and Responsibilities

Inside `app/e-commerce/`, the code is organized into clear layers:

```text
app/
├── e-commerce/
│   ├── main.py              # Application entrypoint and router registration
│   ├── api/
│   │   └── routes/          # API controllers and REST endpoints (APIRouter)
│   ├── core/                # Core settings, security, and helper utilities
│   ├── db/
│   │   └── models/          # Database models (ORM / Database Entities)
│   ├── schema/              # Data validation models (Pydantic DTOs)
│   ├── services/            # Business logic layer
│   ├── integrations/        # External service clients (Zernio, payment gateways, etc.)
│   └── templates/           # Optional HTML templates (such as Jinja2)
└── automaiton/              # Workers and automated pipelines
```

### Explanation of Each Layer:

### 1. Routes Layer (`api/routes/`)
- **Role:** Receives HTTP requests, validates headers and parameters with Pydantic, calls the service layer, and returns the response.
- **Key Rule:** **Thin Controllers**. Never put complex business logic inside route functions.

### 2. Schema Layer (`schema/`)
- **Role:** Defines data models using Pydantic `BaseModel`. These models define what request bodies, query parameters, and responses look like.
- Automatically validates types, checks requirements, and converts data safely.

### 3. Service Layer (`services/`)
- **Role:** The brain of the application where all business rules live (for example: "Is the product in stock?", "Calculate discounts", "Save order and trigger payment").
- This layer does not depend on HTTP. It can be called by both API routes and background automation tasks.

### 4. Database Models Layer (`db/models/`)
- **Role:** Defines database tables and relationships for PostgreSQL.
- Works together with **Alembic** to manage database schema migrations over time.

### 5. Integrations Layer (`integrations/`)
- **Role:** Contains adapters and API clients for third-party services. This keeps the rest of the app decoupled from external libraries.
- Examples: `zernio-sdk` for social media, payment gateways, SMS providers, and Directus connections.

---

## 🔄 Request Life Cycle

This diagram shows how data travels from a client request down to the database:

```
[ Client / User ]
       │
       ▼  (1. HTTP POST /orders)
┌──────────────────────────────────────────────┐
│  FastAPI Router (app/e-commerce/api/routes)  │
│  - Automatically validates input using schema│
└──────────────────────┬───────────────────────┘
                       │ (2. Calls business logic)
                       ▼
┌──────────────────────────────────────────────┐
│   Service Layer (app/e-commerce/services)    │
│   - Calculates discounts & checks stock      │
│   - Calls payment processor                  │
└──────────────┬───────────────────────────────┘
               │
       ┌───────┴───────────────────────┐
       ▼ (3. Saves data)               ▼ (4. Triggers events)
┌─────────────────────────────┐  ┌─────────────────────────────┐
│ Database Layer (db/models)  │  │ Integrations (Zernio/Redis) │
│ - Saves rows in PostgreSQL  │  │ - Sends Instagram update    │
└─────────────────────────────┘  └─────────────────────────────┘
```

---

## 🧩 How Directus Works with FastAPI

A key architectural choice in this project is using **Directus** alongside FastAPI:
- **FastAPI:** Handles custom business logic, high-speed API endpoints, background tasks, and complex workflows.
- **Directus:** Connects directly to the same PostgreSQL database. It provides an instant **Admin Dashboard**, user permissions (RBAC), file storage, and auto-generated REST/GraphQL endpoints for your data.
- This means you do not need to write a custom admin panel from scratch for simple CRUD operations.

---

## 🗄️ Database Migrations with Alembic

The `alembic/` folder lives at the root of the project:
- When your database models change, you need versioned and reversible migration scripts.
- Alembic inspects your SQLAlchemy models, compares them with PostgreSQL, and creates migration files that you can run or roll back safely.
