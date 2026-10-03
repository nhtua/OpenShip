# OpenShip

An agentic DevOps co-pilot for developers and platform engineers.

## Quick Start — Docker Compose

The fastest way to run OpenShip locally is with Docker Compose. This starts a PostgreSQL database, the FastAPI backend, and the Vue.js frontend.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/) installed

### Run the Stack

```bash
# Build and start all services
docker compose up --build

# Or run in the background
docker compose up --build -d
```

This starts three services:

| Service | Port | Description |
|---------|------|-------------|
| **PostgreSQL** | 5432 | Database (data persisted in a named volume) |
| **API** | 8000 | FastAPI backend with REST and SSE endpoints |
| **Web** | 8080 | Vue 3 frontend served by Nginx |

Open your browser to **[http://localhost:8080](http://localhost:8080)** to get started.

### Services

- **Register** a new account
- **Send a chat message** and see streaming AI responses
- **Manage conversations** from the sidebar

### API Documentation

When the API is running, interactive docs are available at **[http://localhost:8000/docs](http://localhost:8000/docs)** (Swagger UI) and **[http://localhost:8000/redoc](http://localhost:8000/redoc)** (ReDoc).

### Stopping the Stack

```bash
docker compose down          # Stop and remove containers
docker compose down -v       # Stop, remove containers, and delete the database volume
```

### Environment Configuration

The `docker-compose.yml` uses these defaults:

- **Database:** `openship` / user: `openship` / password: `openship`
- **DATABASE_URL:** `postgresql://openship:openship@postgres:5432/openship`

Override by editing `docker-compose.yml` or setting environment variables per service.

---

## Development (Local)

### Backend

```bash
cd apps/api
uv sync
uv run uvicorn src.openship.main:app --reload --host 0.0.0.0 --port 8000
```

Run migrations before first use:

```bash
uv run alembic upgrade head
```

Run tests:

```bash
uv run pytest
```

### Frontend

```bash
cd apps/web
pnpm install
pnpm dev
```

---

## Project Structure

```
├── apps/
│   ├── api/                 # FastAPI backend
│   │   ├── src/openship/    # Application source
│   │   ├── tests/           # Test suite
│   │   ├── alembic/         # Database migrations
│   │   ├── Dockerfile       # Container build
│   │   └── pyproject.toml   # Python dependencies (uv)
│   └── web/                 # Vue 3 frontend
│       ├── src/             # Application source
│       ├── tests/           # Test suite
│       ├── Dockerfile       # Container build
│       └── package.json     # Node dependencies (pnpm)
├── docker-compose.yml       # Full stack orchestration
└── README.md                # This file
```
