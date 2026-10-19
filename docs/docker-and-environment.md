# Docker and Infrastructure Guide

This document explains the container setup, Docker configuration, and how services connect in [dockerfile](file:///home/mhb/app/coding/easy-auto/dockerfile) and [compose.yaml](file:///home/mhb/app/coding/easy-auto/compose.yaml).

---

## 🏗️ Service Architecture in Docker Compose

Here are the services defined in [compose.yaml](file:///home/mhb/app/coding/easy-auto/compose.yaml):

```text
┌────────────────────────────────────────────────────────┐
│                      Docker Network                    │
│                                                        │
│   ┌───────────────┐               ┌────────────────┐   │
│   │  web (FastAPI)│               │  directus      │   │
│   │  Port 8000:80 │               │  Port 8055:8055│   │
│   └───┬───────┬───┘               └───┬────────┬───┘   │
│       │       │                       │        │       │
│       │       └───────────┐   ┌───────┘        │       │
│       ▼                   ▼   ▼                ▼       │
│   ┌───────────────┐   ┌───────────────┐   ┌────────┐   │
│   │ cache (Redis) │   │database (PG)  │   │Volumes │   │
│   │ Port 6379     │   │Port 5432      │   │data/   │   │
│   └───────────────┘   └───────────────┘   └────────┘   │
└────────────────────────────────────────────────────────┘
```

1. **`web`**: The FastAPI application container with live code reloading.
2. **`database`**: PostgreSQL 16 Alpine database with data saved locally in `./data/database`.
3. **`cache`**: Redis 7 container used for fast caching and task queues.
4. **`directus`**: Headless CMS and admin dashboard connected to PostgreSQL and Redis.

---

## 🔍 Understanding the `dockerfile`

The [dockerfile](file:///home/mhb/app/coding/easy-auto/dockerfile) uses modern best practices for speed and security:

### 1. Fast Installation with Official `uv` Binaries
```dockerfile
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/
```
Instead of installing `uv` with pip (which takes extra time), pre-compiled binaries are copied directly from the official image.

### 2. Docker Layer Caching (Faster Builds)
```dockerfile
# Step 1: Copy dependency files and install packages
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# Step 2: Copy application code
COPY . .
RUN uv sync --frozen --no-dev
```
- **Why do this?** Docker caches layers. As long as you do not change `pyproject.toml` or `uv.lock`, Docker reuses the installed packages. It only re-runs the second step when you edit code, making rebuilds very fast.

### 3. Security: Running as Non-Root User
```dockerfile
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser && \
    chown -R appuser:appuser /app
USER appuser
```
Running the app as `appuser` instead of `root` protects your host machine if someone finds a security bug in the container.

---

## 🛠️ Key Details in `compose.yaml`

### 1. Anonymous Volume for Python Virtual Environment
```yaml
volumes:
  - .:/app
  - /app/.venv
```
- The first line (`.:/app`) syncs your local code into the container for **Live Reloading**.
- The second line (`/app/.venv`) is an **Anonymous Volume**. It prevents your computer's local `.venv` folder from overwriting the Linux `.venv` created inside the container.

### 2. Healthchecks and Startup Order
Directus waits until both PostgreSQL and Redis are completely ready to accept connections before it starts:
```yaml
depends_on:
  database:
    condition: service_healthy
  cache:
    condition: service_healthy
```

---

## 🐛 The Directus Database Issue and Fix

In commit `f7547da`, this error was reported:
> *"we have some bug in docker compose file when docker-compose is up said 'database directus does not exist'"*

### What caused this problem?
When PostgreSQL starts for the first time, it only creates **one** database. The name of that database comes from the `POSTGRES_DB` environment variable (for example, `easyauto_db`).

In older settings, Directus had hardcoded values:
```yaml
# Old configuration (caused the error)
DB_DATABASE: "directus"
DB_USER: "directus"
DB_PASSWORD: "directus"
```
Because of this, Directus tried to connect to a database named `directus`, which did not exist.

### How it was fixed:
Directus now uses the same shared environment variables as PostgreSQL:
```yaml
# Fixed configuration
DB_DATABASE: ${POSTGRES_DB}
DB_USER: ${POSTGRES_USER}
DB_PASSWORD: ${POSTGRES_PASSWORD}
```

> [!TIP]
> **Important note about old database data:**
> If you already started the database before, your initial settings are stored inside `./data/database`. If you change your database username or password in `.env`, you must delete the existing database folder:
> ```bash
> # WARNING: This deletes your local database data!
> docker compose down -v
> rm -rf ./data/database/*
> ```

---

## ⌨️ Common Docker Commands

```bash
# Build and start all services in the background
docker compose up -d --build

# Check the status of containers and healthchecks
docker compose ps

# View live logs for a specific service
docker compose logs -f web
docker compose logs -f directus

# Stop and remove all running containers
docker compose down

# Open a bash shell inside the FastAPI container for debugging
docker compose exec web bash
```
