"""API middleware for the OpenShip backend service.

Provides request logging, error handling, and CORS configuration.
"""

import logging
import time

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)


def add_middleware(app: FastAPI) -> None:
    """Add all middleware to the FastAPI app.

    Args:
        app: The FastAPI application instance.
    """
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Request logging middleware
    app.middleware("http")(request_logging)

    # Error handling middleware
    app.middleware("http")(error_handling)


async def request_logging(request: Request, call_next) -> Response:
    """Log each HTTP request with timing information.

    Args:
        request: The incoming request.
        call_next: The next handler in the chain.

    Returns:
        The response from the downstream handler.
    """
    start_time = time.time()

    response = await call_next(request)

    duration_ms = (time.time() - start_time) * 1000
    logger.info(
        "%s %s -> %s (%.2fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )

    return response


async def error_handling(request: Request, call_next) -> Response:
    """Catch unhandled exceptions and return JSON error responses.

    Args:
        request: The incoming request.
        call_next: The next handler in the chain.

    Returns:
        The response from the downstream handler or a JSON error response.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content={"error": "Internal server error", "detail": str(exc)},
        )
