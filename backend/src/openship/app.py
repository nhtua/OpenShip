"""FastAPI application setup."""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from openship.api import router

def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="OpenShip",
        version="0.1.0",
        description="Agentic DevOps workflow engine",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(router, prefix="/api")
    return app