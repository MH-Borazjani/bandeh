# Bandeh (easy-auto) Documentation

Welcome! This documentation helps you understand the architecture, frameworks, and infrastructure of the **Bandeh (easy-auto)** project. If you are new to the project or need a quick refresher on modern Python tools, this guide is designed to be simple, clear, and practical.

---

## 📌 Project Overview

**Bandeh** (named `easy-auto` in the repository) is an open-source business automation system. It works as a **Digital Worker**:

> **"You give the order, Bandeh does the work."**

### Goals and Vision
The main goal is to connect different business platforms and remove repetitive manual tasks using automated workflows and AI:
- **Social Media and Messaging:** Instagram, Telegram, TikTok, and other communication channels (using `zernio-sdk` and webhooks).
- **Headless CMS and Data Management:** Directus for managing data models and media files easily.
- **E-Commerce Management:** A dedicated module for products, orders, and payment gateways.
- **Fast Database and Caching:** PostgreSQL for relational data storage and Redis for caching and background queues.
- **Fast and Scalable API:** Built with the high-performance FastAPI framework and managed with `uv`.

---

## 🗺️ Documentation Roadmap

Here are the main guides in this folder:

| Guide | Description |
| :--- | :--- |
| 🏗️ [Architecture Guide](architecture.md) | Folder structure, layers, data flow, and system modules |
| 🧰 [Tech Stack & Frameworks](tech-stack-and-frameworks.md) | Overview of FastAPI, Pydantic-Settings, `uv`, Directus, and Zernio |
| 🐳 [Docker & Infrastructure](docker-and-environment.md) | Deep dive into `dockerfile`, `compose.yaml`, database setup, and common fixes |
| 🚀 [Developer Guide](developer-guide.md) | Local setup steps, environment variables, and how to add new features |

---

## ⚡ Tech Stack at a Glance

```
┌────────────────────────────────────────────────────────┐
│                   Client / Frontend                    │
│          (Webhooks, Dashboard, Directus App)          │
└───────────▲───────────────────────────────▲────────────┘
            │                               │
            ▼ (HTTP / REST)                 ▼
┌───────────────────────────┐   ┌────────────────────────┐
│      FastAPI App          │   │      Directus CMS      │
│  (app/e-commerce & auto)  │   │      (Port 8055)       │
└───────┬───────────┬───────┘   └───────────┬────────────┘
        │           │                       │
        │           └───────────┐   ┌───────┘
        ▼                       ▼   ▼
┌───────────────┐       ┌───────────────────────┐
│     Redis     │       │      PostgreSQL       │
│  Cache & Queue│       │   Primary Database    │
└───────────────┘       └───────────────────────┘
```

- **Language:** Python 3.12+ / 3.14
- **Package & Environment Manager:** Astral `uv` (extremely fast Python package manager)
- **Web Framework:** FastAPI + Uvicorn
- **Configuration Management:** Pydantic v2 & Pydantic-Settings
- **Relational Database:** PostgreSQL 16
- **In-Memory Cache & Message Broker:** Redis 7
- **Data & Content Management:** Directus 12 (Headless CMS)
- **Social Media Integration:** Zernio SDK (Late API)
- **Container & Deployment:** Docker & Docker Compose
