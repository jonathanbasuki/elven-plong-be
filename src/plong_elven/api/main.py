from fastapi import FastAPI

from plong_elven.api.detections import router as detections_router
from plong_elven.api.stream import router as stream_router

app = FastAPI(title="Plong Elven API", version="0.1.0")
app.include_router(detections_router)
app.include_router(stream_router)


@app.get("/healthz", tags=["system"])
async def health() -> dict[str, str]:
    """Return a process-level health check without requiring the database."""
    return {"status": "ok"}
