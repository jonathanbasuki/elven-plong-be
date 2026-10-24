from fastapi import Depends, FastAPI
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.auth import router as auth_router
from plong_elven.api.detections import router as detections_router
from plong_elven.api.stream import router as stream_router
from plong_elven.api.ui import router as ui_router
from plong_elven.api.zones import router as zones_router
from plong_elven.db.session import get_db

app = FastAPI(title="Plong Elven API", version="0.1.0")
app.include_router(auth_router)
app.include_router(detections_router)
app.include_router(stream_router)
app.include_router(zones_router)
app.include_router(ui_router)


@app.get("/healthz", tags=["system"])
async def health() -> dict[str, str]:
    """Return a process-level health check without requiring the database."""
    return {"status": "ok"}


@app.get("/readyz", tags=["system"])
async def readiness(
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return {"status": "not_ready", "database": "unavailable"}
    return {"status": "ready", "database": "connected"}
