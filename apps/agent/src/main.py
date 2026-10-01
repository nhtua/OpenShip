"""OpenShip agent backend service.

FastAPI application that exposes the agent orchestration engine,
tool registry, workflow registry, and sandbox management through
a REST API.
"""

import logging
import os

from fastapi import FastAPI

from src.api.middleware import add_middleware
from src.api.routes import router
from src.database.schema import init_db
from src.registry.tools import init_registry as init_tool_registry

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("openship")

# Create FastAPI application
app = FastAPI(
    title="OpenShip Agent API",
    description="REST API for the OpenShip agent orchestration engine",
    version="0.1.0",
)

# Get database path from environment or use default
DB_PATH = os.environ.get("OPENSUP_DB_PATH", ":memory:")


def initialize_app() -> None:
    """Initialize application components.

    Sets up database, registries, and middleware.
    """
    logger.info("Initializing OpenShip agent backend...")

    # Initialize database
    logger.info("Initializing database at %s", DB_PATH)
    init_db(DB_PATH)

    # Initialize tool registry
    logger.info("Initializing tool registry")
    init_tool_registry(DB_PATH)

    # Initialize workflow registry (uses lazy init with DB_PATH env var)
    logger.info("Initializing workflow registry")
    # Force initialization by registering a dummy workflow
    from src.registry.workflows import register_workflow
    try:
        register_workflow({
            "name": "__init__",
            "version": "0.0.0",
            "origin": "builtin",
            "definition": "{}"
        })
    except RuntimeError:
        logger.info("Workflow registry already initialized")

    # Add middleware
    add_middleware(app)

    logger.info("Application initialized")


# Initialize on import
initialize_app()

# Include API routes
app.include_router(router)


@app.get("/")
async def root() -> dict:
    """Root endpoint.

    Returns:
        dict: API information.
    """
    return {
        "name": "OpenShip Agent API",
        "version": "0.1.0",
        "docs": "/docs",
    }


# Run with uvicorn when executed directly
if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        log_level="info",
    )
