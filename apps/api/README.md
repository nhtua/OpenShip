# OpenShip API

Backend API server for OpenShip, built with FastAPI.

## Quick Start

```bash
cd apps/api
uv sync
uv run uvicorn src.openship.main:app --host 0.0.0.0 --port 8000
```

Then visit `http://localhost:8000/api/health` to verify the server is running.

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```bash
cp .env.example .env
```

## Project Structure

```
apps/api/
├── pyproject.toml      # Project dependencies
├── alembic.ini         # Database migration config
├── README.md
└── src/
    └── openship/
        ├── __init__.py
        ├── main.py       # FastAPI application entry point
        └── config.py     # Settings from environment variables
```
