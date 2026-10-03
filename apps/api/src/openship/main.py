from fastapi import FastAPI

from .config import settings

app = FastAPI(title="OpenShip API")


@app.get("/api/health")
async def health_check():
    return {"status": "ok"}
