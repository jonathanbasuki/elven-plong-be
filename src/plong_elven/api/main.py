from fastapi import FastAPI

from plong_elven.api.detections import router as detections_router
from plong_elven.api.stream import router as stream_router
from plong_elven.api.ui import router as ui_router
from plong_elven.api.zones import router as zones_router

app = FastAPI(title="Plong Elven API", version="0.1.0")
app.include_router(detections_router)
app.include_router(stream_router)
app.include_router(zones_router)
app.include_router(ui_router)


@app.get("/healthz", tags=["system"])
async def health() -> dict[str, str]:
    """Return a process-level health check without requiring the database."""
    return {"status": "ok"}
