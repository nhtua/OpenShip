# Phase 1: Authenticated Browser Chat

**Date:** 2026-10-03
**Status:** Proposed
**Milestone:** "I can install OpenShip, open my browser, sign in, and have a conversation."

## Overview

Phase 1 delivers a minimal but complete OpenShip installation with authenticated chat. Users can register, log in, and have conversations with the AI agent through a browser interface. This establishes the foundation for all future phases.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Browser (User)                       │
│  ┌──────────────────────────────────────────────────┐   │
│  │         Vue 3 Frontend (apps/web/)               │   │
│  │  - Chat Interface                                │   │
│  │  - Authentication (login page)                   │   │
│  │  - shadcn-vue Components                         │   │
│  └─────────────────────┬────────────────────────────┘   │
└────────────────────────┼────────────────────────────────┘
                         │ HTTP/HTTPS
                         │ (REST + SSE for streaming)
┌────────────────────────┼────────────────────────────────┐
│                    Server                               │
│  ┌─────────────────────┴────────────────────────────┐   │
│  │         FastAPI Backend (apps/api/)              │   │
│  │  - /api/auth/*    (login, logout, register)      │   │
│  │  - /api/chat/*    (send message, stream response)│   │
│  │  - /api/workspace (user's project context)       │   │
│  │  - OpenAI API Integration                        │   │
│  └─────────────────────┬────────────────────────────┘   │
│                        │ SQLAlchemy ORM                  │
│  ┌─────────────────────┴────────────────────────────┐   │
│  │         PostgreSQL Database                      │   │
│  │  - Users table                                   │   │
│  │  - Conversations table                           │   │
│  │  - Messages table                                │   │
│  └──────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────┘
```

## Backend Design (FastAPI)

### Directory Structure

```
apps/api/
├── src/
│   ├── openship/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI app creation, middleware
│   │   ├── config.py            # Settings (env vars)
│   │   ├── auth/
│   │   │   ├── routes.py        # /api/auth/* endpoints
│   │   │   ├── service.py       # Auth business logic
│   │   │   ├── models.py        # User model
│   │   │   └── schemas.py       # Pydantic schemas
│   │   ├── chat/
│   │   │   ├── routes.py        # /api/chat/* endpoints
│   │   │   ├── service.py       # Chat business logic
│   │   │   ├── models.py        # Conversation, Message models
│   │   │   ├── schemas.py       # Pydantic schemas
│   │   │   └── llm.py           # OpenAI API client wrapper
│   │   ├── workspace/
│   │   │   ├── routes.py        # /api/workspace endpoints
│   │   │   ├── service.py       # Workspace business logic
│   │   │   └── schemas.py       # Pydantic schemas
│   │   └── database/
│   │       ├── models.py        # Base SQLAlchemy models
│   │       ├── session.py       # Database session management
│   │       └── migrations/      # Alembic migrations
│   └── tests/                   # Pytest tests
├── pyproject.toml
├── alembic.ini
├── Dockerfile
└── README.md
```

### Key Dependencies

- `fastapi` - Web framework
- `uvicorn` - ASGI server
- `sqlalchemy` - ORM
- `psycopg2` - PostgreSQL driver
- `alembic` - Database migrations
- `pydantic` - Data validation
- `pyjwt` - JWT token management
- `bcrypt` - Password hashing
- `openai` - OpenAI API client
- `httpx` - Async HTTP client

### Authentication Flow

1. User registers → create user with hashed password → return JWT
2. User logs in → verify credentials → return JWT
3. Subsequent requests → validate JWT in `Authorization: Bearer` header
4. Token expiry → user logs in again (Phase 1: no refresh tokens)

### Chat Flow

1. User sends message → POST `/api/chat/{conversation_id}/messages`
2. Backend saves user message → calls OpenAI API → streams response
3. Response streamed back via SSE → saved to database on completion
4. Cancellation → client closes SSE stream → backend aborts API call

### Error Handling

- 400 Bad Request → validation errors
- 401 Unauthorized → invalid/expired JWT
- 404 Not Found → resource not found
- 500 Internal Server Error → unexpected errors
- OpenAI errors → translated to user-friendly messages (e.g., "API key not configured")

## Frontend Design (Vue 3)

### Directory Structure

```
apps/web/
├── src/
│   ├── main.ts
│   ├── App.vue
│   ├── router/
│   │   └── index.ts             # Vue Router
│   ├── stores/
│   │   ├── auth.ts              # Pinia auth store
│   │   ├── chat.ts              # Pinia chat store
│   │   └── workspace.ts         # Pinia workspace store
│   ├── views/
│   │   ├── LoginView.vue
│   │   ├── RegisterView.vue
│   │   └── ChatView.vue         # Main chat interface
│   ├── components/
│   │   ├── layout/
│   │   │   ├── AppHeader.vue
│   │   │   └── AppSidebar.vue
│   │   ├── chat/
│   │   │   ├── ChatStream.vue
│   │   │   ├── ChatInput.vue
│   │   │   ├── UserMessage.vue
│   │   │   └── AgentMessage.vue
│   │   └── ui/                  # shadcn-vue components
│   ├── composables/
│   │   ├── useChat.ts           # Chat logic composable
│   │   └── useAuth.ts           # Auth logic composable
│   ├── services/
│   │   └── api.ts               # API client (fetch wrapper)
│   └── types/
│       └── index.ts             # TypeScript interfaces
├── index.html
├── package.json
├── vite.config.js
├── tsconfig.json
├── Dockerfile
└── README.md
```

### Key Dependencies

- `vue` - Vue 3 framework
- `vue-router` - Routing
- `pinia` - State management
- `shadcn-vue` - UI component library
- `tailwindcss` - CSS framework
- `@vueuse/core` - Vue composables
- `axios` or `fetch` - HTTP client

### Chat Interface Design

- Chat-first layout with message stream
- User messages right-aligned, agent messages left-aligned
- Streaming text rendered progressively
- Loading indicators while agent is thinking
- Error states with retry options
- Clean, minimal design using shadcn-vue tokens

## Database Schema

### Users Table

```sql
CREATE TABLE users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    email VARCHAR(255) UNIQUE NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Conversations Table

```sql
CREATE TABLE conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE NOT NULL,
    title VARCHAR(255) DEFAULT 'New Conversation',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Messages Table

```sql
CREATE TABLE messages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    conversation_id UUID REFERENCES conversations(id) ON DELETE CASCADE NOT NULL,
    role VARCHAR(20) NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Indexes

```sql
CREATE INDEX idx_conversations_user_id ON conversations(user_id);
CREATE INDEX idx_messages_conversation_id ON messages(conversation_id);
```

## Authentication Design

### Registration Flow

1. User enters username, email, password (confirm)
2. Backend validates inputs (username 3-50 chars, valid email, password 8+ chars)
3. Check if username/email already taken
4. Hash password with bcrypt
5. Create user record
6. Return JWT token

### Login Flow

1. User enters username/email and password
2. Backend looks up user
3. Verify password hash with bcrypt
4. Generate JWT token (expiring in 24 hours)
5. Return token and user info

### JWT Token Structure

```json
{
  "sub": "<user_uuid>",
  "username": "<username>",
  "iat": <issued_at_timestamp>,
  "exp": <expiry_timestamp>
}
```

### Phase 1 Scope

- Username/password only
- No social login (GitHub, Google, etc.)
- No refresh tokens
- No password reset
- No email verification

## Chat/Conversation Flow

### Sending a Message

1. User types message → clicks Send or presses Enter
2. Frontend POSTs to `/api/chat/{conversation_id}/messages` with `{content: "..."}`
3. Backend saves user message to database
4. Backend constructs chat history from conversation messages
5. Backend calls OpenAI API with chat history
6. Backend streams response back to frontend via SSE
7. Frontend displays streamed text progressively
8. On completion, backend saves assistant message to database

### Streaming Implementation (SSE)

```
POST /api/chat/{conversation_id}/messages
Accept: text/event-stream

Response:
data: {"type": "chunk", "content": "He"}
data: {"type": "chunk", "content": "llo"}
data: {"type": "chunk", "content": " world!"}
data: {"type": "complete", "message_id": "<uuid>"}
```

### Conversation History

- Full message history sent to OpenAI API on each turn
- Phase 1 limit: 50 most recent messages
- Future phases: smarter context management, token counting

### System Prompt

```
You are OpenShip, an AI DevOps co-pilot. Help users with infrastructure,
deployment, and operations tasks. Be concise and practical.
```

## Configuration Management

### Environment Variables (Backend)

```bash
# OpenAI Configuration
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o
OPENAI_BASE_URL=https://api.openai.com/v1

# Database
DATABASE_URL=postgresql://user:pass@localhost:5432/openship

# JWT
JWT_SECRET=<random_secret>
JWT_EXPIRY_HOURS=24

# Server
HOST=0.0.0.0
PORT=8000
```

### Missing Configuration Handling

- If `OPENAI_API_KEY` is missing → user sees "API key not configured" on first message
- If `OPENAI_MODEL` is missing → default to `gpt-4o`
- If database connection fails → server logs error, health check fails

### Configuration Loading

- Use `pydantic-settings` for type-safe configuration
- Validate required fields at startup
- Provide clear error messages for missing configuration

## Testing Strategy

### Unit Tests (Pytest)

- Auth service (password hashing, token generation)
- Chat service (message handling, API response parsing)
- Database models (validation, relationships)

### Integration Tests

- API endpoints (login, register, send message)
- Database operations (CRUD, migrations)
- OpenAI API mocking (for chat flow)

### Frontend Tests (Vitest + Playwright)

- Component tests for chat UI
- E2E test: login → send message → receive response

### Exit Criteria

- [ ] User completes installation without undocumented steps
- [ ] 10 conversation turns complete with visible responses or actionable errors
- [ ] Unauthenticated requests cannot access the workspace
- [ ] Missing model credentials produce configuration message, not crash

## Not Included in Phase 1

- Tools and executable code
- Cloud access
- Reusable workflows
- Multiple model providers
- Social login
- Refresh tokens
- Password reset
- Email verification
- Team features
- Kubernetes deployment

## Future Phase Dependencies

Phase 1 establishes:
- Authentication infrastructure (JWT, user management)
- Database schema (users, conversations, messages)
- Chat streaming infrastructure (SSE, OpenAI integration)
- Frontend framework and component library
- Docker Compose packaging

These foundations are required for all subsequent phases in the roadmap.