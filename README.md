# OpenShip

An agentic DevOps co-pilot for developers and platform engineers.

## Features

- **Authentication** — User registration and login with JWT tokens (24-hour expiry)
- **Real-time Chat** — AI-powered conversations via SSE (Server-Sent Events) streaming
- **Conversation Management** — Multiple conversations with sidebar navigation
- **Docker Deployment** — Full-stack orchestration with PostgreSQL, FastAPI, and Vue.js

## Quick Start — Docker Compose

The fastest way to run OpenShip locally is with Docker Compose. This starts a PostgreSQL database, the FastAPI backend, and the Vue.js frontend.

### Prerequisites

- [Docker](https://docs.docker.com/get-docker/) and [Docker Compose](https://docs.docker.com/compose/) installed
- [OpenAI API Key](https://platform.openai.com/api-keys) (optional — app runs without it but shows a configuration message)

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

| Setting | Value |
|---------|-------|
| Database name | `openship` |
| Database user | `openship` |
| Database password | `openship` |
| DATABASE_URL | `postgresql://openship:openship@postgres:5432/openship` |
| JWT secret | `dev-secret-change-in-production` (change in production) |
| JWT expiry | 24 hours |
| OpenAI model | `gpt-4o` |

#### Setting the OpenAI API Key

To enable AI chat responses, set the `OPENAI_API_KEY` environment variable:

```bash
# Via docker-compose.yml
docker compose up --build -d
# Or override on the command line:
OPENAI_API_KEY=sk-xxx docker compose up --build
```

You can also pass additional environment variables via a `.env` file in the project root:

```env
OPENAI_API_KEY=sk-your-key-here
DATABASE_URL=postgresql://openship:openship@postgres:5432/openship
```

---

## Development (Local)

### 1. Database

Create the OpenShip database:

```bash
createdb -U postgres openship
```

Or with psql:

```bash
psql -U postgres -c "CREATE DATABASE openship;"
```

### 2. Backend

Set up environment variables:

```bash
cd apps/api
cp .env.example .env
# Edit .env to add your OPENAI_API_KEY
```

Install dependencies and run migrations:

```bash
uv sync
uv run alembic upgrade head
```

Start the API server:

```bash
uv run uvicorn src.openship.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at **http://localhost:8000**. Interactive docs at **[http://localhost:8000/docs](http://localhost:8000/docs)**.

Run tests:

```bash
uv run pytest tests/ -v
```

### 3. Frontend

Install dependencies and start the dev server:

```bash
cd apps/web
pnpm install
pnpm dev
```

The frontend will be available at **http://localhost:5173** (Vite dev server). It proxies `/api` requests to `http://localhost:8000`.

---

## API Reference

### Authentication Endpoints

#### `POST /api/auth/register`

Register a new user account.

**Request body:**

```json
{
  "username": "string (3-50 chars, required)",
  "email": "string, valid email (required)",
  "password": "string (min 8 chars, required)"
}
```

**Success response (201):**

```json
{
  "access_token": "eyJ...",
  "user": {
    "id": "uuid",
    "username": "string"
  }
}
```

**Error responses:**

| Status | Code | Description |
|--------|------|-------------|
| 400 | `username_taken` | Username already exists |
| 400 | `email_taken` | Email already registered |
| 422 | validation error | Invalid input data |

#### `POST /api/auth/login`

Authenticate and receive a JWT token.

**Request body:**

```json
{
  "username": "string (required)",
  "password": "string (required)"
}
```

**Success response (200):**

```json
{
  "access_token": "eyJ...",
  "user": {
    "id": "uuid",
    "username": "string"
  }
}
```

**Error responses:**

| Status | Code | Description |
|--------|------|-------------|
| 401 | `invalid_credentials` | Wrong username or password |

### Chat Endpoints

> All chat endpoints require a `Authorization: Bearer <token>` header.

#### `POST /api/chat/{conversation_id}/messages`

Send a message and receive a streamed AI response via SSE.

- If `conversation_id` doesn't exist, it is auto-created.
- Response uses `text/event-stream` media type.
- Each chunk is sent as a JSON event: `{"type": "chunk", "content": "..."}`.
- The stream ends with `{"type": "complete", "message_id": "..."}` and `data: [DONE]`.

**Request body:**

```json
{
  "content": "string (1-10000 chars, required)"
}
```

**Success response (200):** Stream of SSE events.

**Error responses:**

| Status | Code | Description |
|--------|------|-------------|
| 401 | `unauthorized` | Missing or invalid token |
| 503 | `api_key_not_configured` | OPENAI_API_KEY not set |

#### `GET /api/conversations`

List all conversations for the authenticated user.

**Success response (200):**

```json
[
  {
    "id": "uuid",
    "title": "Conversation title",
    "created_at": "2026-01-01T00:00:00Z",
    "updated_at": "2026-01-01T00:00:00Z"
  }
]
```

#### `GET /api/health`

Health check endpoint. Returns `{"status": "ok"}`.

---

## Testing

### Backend Tests

```bash
cd apps/api
uv run pytest tests/ -v
```

Runs unit tests for authentication, chat, and user model functionality.

### Frontend Tests

```bash
cd apps/web
pnpm exec vitest --run
```

Runs component and view tests for auth pages, chat components, and stores.

### Integration Tests

```bash
cd apps/api
uv run pytest tests/test_integration_auth_chat.py -v
```

End-to-end flow tests: register → login → send messages → list conversations.

---

## Project Structure

```
├── apps/
│   ├── api/                 # FastAPI backend
│   │   ├── src/openship/    # Application source
│   │   │   ├── auth/        # Authentication (routes, models, service)
│   │   │   ├── chat/        # Chat (routes, models, service, LLM)
│   │   │   ├── database/    # Database session management
│   │   │   └── config.py    # Application settings
│   │   ├── tests/           # Test suite (unit + integration)
│   │   ├── alembic/         # Database migrations
│   │   ├── Dockerfile       # Container build
│   │   └── pyproject.toml   # Python dependencies (uv)
│   └── web/                 # Vue 3 frontend
│       ├── src/             # Application source
│       │   ├── components/  # UI components
│       │   ├── views/       # Page views
│       │   ├── stores/      # Pinia state stores
│       │   └── router/      # Vue Router configuration
│       ├── tests/           # Test suite
│       ├── Dockerfile       # Container build
│       └── package.json     # Node dependencies (pnpm)
├── docker-compose.yml       # Full stack orchestration
└── README.md                # This file
```

---

## Troubleshooting

### "AI service is not configured" message

The app requires `OPENAI_API_KEY` to generate AI responses. Without it, the chat endpoint returns a 503 status with a clear message.

**Fix:** Set the environment variable before starting the API:

```bash
# Docker Compose
OPENAI_API_KEY=sk-xxx docker compose up --build

# Or via .env file
echo "OPENAI_API_KEY=sk-your-key" > .env
docker compose up --build
```

### Database connection errors

Ensure PostgreSQL is running and accessible:

```bash
# Check the database container is healthy
docker compose ps

# View database logs
docker compose logs postgres
```

### Port conflicts

If port 8000, 5432, or 8080 is already in use, modify `docker-compose.yml` to change the host port mapping:

```yaml
ports:
  - "8001:8000"   # API on 8001 instead of 8000
```

### JWT token expired

Tokens expire after 24 hours by default. Simply log in again to get a new token.

To change the expiry, set the `JWT_EXPIRY_HOURS` environment variable:

```bash
JWT_EXPIRY_HOURS=48 docker compose up --build
```

### Frontend cannot reach the API

The Vite dev server proxies `/api` requests to the backend. Ensure the proxy target in `apps/web/vite.config.ts` matches your backend URL:

```typescript
server: {
  proxy: {
    '/api': 'http://localhost:8000',
  },
}
```

---

## Security Notes

- **JWT secret:** The default `dev-secret-change-in-production` is used for development. **Always set a strong, random `JWT_SECRET` in production.**
- **API key:** Never commit your `OPENAI_API_KEY` to version control. Use environment variables or a secrets manager.
- **HTTPS:** Docker Compose runs over HTTP locally. Use a reverse proxy (e.g., nginx, Traefik) with TLS for production.

---

## License

MIT — See [LICENSE](LICENSE) for details.
