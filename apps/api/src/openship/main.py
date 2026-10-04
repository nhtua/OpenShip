from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth.routes import router as auth_router
from .chat.routes import router as chat_router
from .events.routes import router as events_router
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


@app.get("/health")
async def health_check_root():
    return {"status": "ok"}


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
