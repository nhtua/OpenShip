"""OpenShip agent backend service.

FastAPI application that exposes the agent orchestration engine,
tool registry, workflow registry, and sandbox management through
a REST API.
"""

import logging
import os

from dotenv import load_dotenv
from fastapi import FastAPI

# Load environment variables from .env file
load_dotenv()

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

    # Register built-in tool implementations
    logger.info("Registering built-in tool implementations")
    from src.tools.builtin import register_builtin_tools
    register_builtin_tools()

    # Register built-in tools in registry
    logger.info("Registering built-in tools")
    from src.registry.tools import register_tool
    builtin_tools = [
        {
            "name": "file.read",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read contents of a file",
            "category": "file",
            "inputs": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file to read"}
                },
                "required": ["path"]
            },
            "outputs": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "content": {"type": "string"}
                }
            }
        },
        {
            "name": "file.write",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Write content to a file",
            "category": "file",
            "inputs": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path to file to write"},
                    "content": {"type": "string", "description": "Content to write"}
                },
                "required": ["path", "content"]
            },
            "outputs": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"}
                }
            }
        },
        {
            "name": "shell.exec",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute a shell command",
            "category": "shell",
            "inputs": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to execute"},
                    "timeout": {"type": "integer", "description": "Timeout in seconds"}
                },
                "required": ["command"]
            },
            "outputs": {
                "type": "object",
                "properties": {
                    "success": {"type": "boolean"},
                    "output": {"type": "string"}
                }
            }
        }
    ]
    for tool in builtin_tools:
        try:
            register_tool(tool)
            logger.info("Registered tool: %s", tool["name"])
        except Exception as e:
            logger.warning("Failed to register tool %s: %s", tool["name"], e)

    # Register built-in workflows
    logger.info("Registering built-in workflows")
    builtin_workflows = [
        {
            "name": "hello",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Say hello - a simple demonstration workflow",
            "tags": ["demo", "hello"],
            "definition": """{
                "steps": [
                    {"order": 1, "tool": "shell.exec", "args": {"command": "echo Hello from OpenShip!"}}
                ]
            }"""
        },
        {
            "name": "read-file",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Read the contents of a file",
            "tags": ["file", "read"],
            "definition": """{
                "steps": [
                    {"order": 1, "tool": "file.read", "args": "{{path}}"}
                ]
            }"""
        },
        {
            "name": "write-file",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Write content to a file",
            "tags": ["file", "write"],
            "definition": """{
                "steps": [
                    {"order": 1, "tool": "file.write", "args": {"path": "{{path}}", "content": "{{content}}"}}
                ]
            }"""
        },
        {
            "name": "run-command",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Execute a shell command",
            "tags": ["shell", "exec"],
            "definition": """{
                "steps": [
                    {"order": 1, "tool": "shell.exec", "args": {"command": "{{command}}"}}
                ]
            }"""
        },
        {
            "name": "build-workflow",
            "version": "1.0.0",
            "origin": "builtin",
            "description": "Build a new workflow from a natural language description",
            "tags": ["build", "create", "workflow"],
            "definition": """{
                "steps": [
                    {"order": 1, "tool": "shell.exec", "args": {"command": "echo Building workflow: {{description}}"}}
                ]
            }"""
        }
    ]
    for workflow in builtin_workflows:
        try:
            register_workflow(workflow)
            logger.info("Registered workflow: %s", workflow["name"])
        except Exception as e:
            logger.warning("Failed to register workflow %s: %s", workflow["name"], e)

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
    import argparse

    import uvicorn

    parser = argparse.ArgumentParser(description="OpenShip Agent Backend")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument("--port", type=int, default=8000, help="Port to bind to")
    parser.add_argument("--hot-reload", action="store_true", help="Enable hot reload")
    args = parser.parse_args()

    # When using reload, uvicorn needs an import string to spawn a new process
    if args.hot_reload:
        uvicorn.run(
            "src.main:app",
            host=args.host,
            port=args.port,
            log_level="info",
            reload=True,
        )
    else:
        uvicorn.run(
            app,
            host=args.host,
            port=args.port,
            log_level="info",
        )
