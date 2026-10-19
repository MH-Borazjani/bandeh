# -----------------------------------------------------------------------------
# Base Image Configuration
# -----------------------------------------------------------------------------
# Pin to a stable, lightweight Debian-based Python image.
# Note: For production stability, avoid bleeding-edge or pre-release Python versions 
# unless your dependency tree explicitly requires them.
FROM python:3.12-slim-bookworm

# -----------------------------------------------------------------------------
# Environment Variables & Python Runtime Flags
# -----------------------------------------------------------------------------
# - PYTHONUNBUFFERED=1: Flushes stdout/stderr immediately; essential for real-time Docker logging.
# - PYTHONDONTWRITEBYTECODE=1: Prevents Python from writing .pyc files to disk inside the container.
# - UV_COMPILE_BYTECODE=1: Compiles installed packages to bytecode during the build phase for faster cold starts.
# - UV_LINK_MODE=copy: Ensures uv copies files instead of hardlinking across distinct filesystem boundaries.
# - PATH: Prefixes the container's PATH with uv's virtual environment directory so binaries run transparently.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"

# -----------------------------------------------------------------------------
# Binary Extraction
# -----------------------------------------------------------------------------
# Pull official uv and uvx binaries from the Astral container registry.
COPY --from=ghcr.io/astral-sh/uv:0.11.29 /uv /uvx /bin/

# Set the canonical working directory inside the container
WORKDIR /app

# -----------------------------------------------------------------------------
# Dependency Resolution & Layer Caching Phase
# -----------------------------------------------------------------------------
# Copy only manifest and lock files first. This leverages Docker's layer cache,
# ensuring dependencies are NOT re-downloaded unless lockfiles change.
COPY pyproject.toml uv.lock ./

# Install project dependencies without installing the project itself (--no-install-project).
# - --frozen / --locked: Ensures strict lockfile adherence and prevents accidental mutations.
# - --no-dev: Excludes test runners, linters, and other developer tools from production images.
RUN uv sync --frozen --no-dev --no-install-project

# -----------------------------------------------------------------------------
# Application Source Code Phase
# -----------------------------------------------------------------------------
# Copy remaining project source code after dependencies are installed
COPY . .

# Finalize the installation to register the root package/editable links if configured
RUN uv sync --frozen --no-dev

# -----------------------------------------------------------------------------
# Security Hardening: Non-Root Execution
# -----------------------------------------------------------------------------
# Create a dedicated non-privileged user and group to run the process securely
RUN groupadd -r appuser && useradd -r -g appuser -d /app appuser && \
    chown -R appuser:appuser /app

USER appuser

# Document the internal listening port
EXPOSE 80

# -----------------------------------------------------------------------------
# Container Entrypoint
# -----------------------------------------------------------------------------
# Execute the production server directly via the virtualenv's executable.
# FastAPIs CLI `fastapi run` automatically selects a production-grade ASGI runner (Uvicorn).
CMD ["fastapi", "run", "app/e-commerce/main.py", "--host", "0.0.0.0", "--port", "80"]