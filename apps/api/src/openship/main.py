# ruff: noqa: BLE001, S110
# A status route answers "degraded" rather than failing: an endpoint whose job
# is to report whether the service can serve is a worse answer when it raises
# than when it says it cannot.
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth.routes import router as auth_router
from .chat.routes import router as chat_router
from .events.routes import router as events_router
from .providers.routes import router as providers_router
from .runs.routes import router as runs_router
from .workspace.routes import router as workspace_router

app = FastAPI(title="OpenShip API")


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    if isinstance(exc.detail, dict) and "error" in exc.detail:
        return JSONResponse(
            status_code=exc.status_code, content=exc.detail
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": "error",
                "message": str(exc.detail),
            }
        },
    )


app.include_router(auth_router)
app.include_router(chat_router)
app.include_router(runs_router)
app.include_router(workspace_router)
app.include_router(events_router)
app.include_router(providers_router)


@app.get("/health")
async def health_check_root():
    """Basic liveness check."""
    return {"status": "ok"}


@app.get("/api/health")
async def health_check():
    """API health check including database connectivity."""
    from sqlalchemy import text

    from .database.session import SessionLocal

    db_ok = False
    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass
    finally:
        if db is not None:
            db.close()

    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


@app.get("/ready")
async def readiness_check():
    """Readiness check: database + worker backlog/lease age.

    Returns 200 with status ok when ready to accept traffic.
    Returns 503 when not ready.
    """
    from fastapi import Response as FastAPIResponse
    from sqlalchemy import func, text

    from .database.session import SessionLocal
    from .runs.models import Run, RunJob

    # Check database
    db_ok = False
    db = None
    try:
        db = SessionLocal()
        db.execute(text("SELECT 1"))
        db_ok = True
    except Exception:
        pass

    if not db_ok:
        return FastAPIResponse(
            status_code=503,
            content=json.dumps({"status": "not_ready", "database": False})
        )

    # Check for stuck jobs (lease expired but still running)
    stuck_jobs = 0
    try:
        stuck_jobs = (
            db.query(RunJob)
            .join(Run)
            .filter(
                RunJob.lease_until.isnot(None),
                RunJob.lease_until < func.now(),
                Run.status == "running",
            )
            .count()
        )
    except Exception:
        pass

    # Check queued job count (backlog)
    queued = 0
    try:
        queued = (
            db.query(RunJob)
            .join(Run)
            .filter(Run.status == "queued")
            .count()
        )
    except Exception:
        pass

    ready = stuck_jobs < 5
    body = json.dumps({
        "status": "ok" if ready else "not_ready",
        "database": db_ok,
        "stuck_jobs": stuck_jobs,
        "queued": queued,
    })
    status = 200 if ready else 503
    return FastAPIResponse(status_code=status, content=body)
