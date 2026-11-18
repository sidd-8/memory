import psycopg
from fastapi import FastAPI, HTTPException

from apps.api.db import connect
from apps.api.memories import router as memory_router
from apps.api.subjects import router as subject_router

app = FastAPI(title="Agent Memory API")
app.include_router(subject_router)
app.include_router(memory_router)


@app.get("/healthz")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
def readiness() -> dict[str, str]:
    try:
        with connect() as connection:
            connection.execute("SELECT 1")
    except psycopg.Error as exc:
        raise HTTPException(status_code=503, detail="Database unavailable") from exc

    return {"status": "ready"}
