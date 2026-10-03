from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .auth.routes import router as auth_router
from .chat.routes import conversations_router, router as chat_router

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
app.include_router(conversations_router)


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
