from fastapi import FastAPI

app = FastAPI(title="Plong Elven API", version="0.1.0")


@app.get("/healthz", tags=["system"])
async def health() -> dict[str, str]:
    """Return a process-level health check without requiring the database."""
    return {"status": "ok"}
