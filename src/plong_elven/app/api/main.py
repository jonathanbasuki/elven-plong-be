import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.exceptions import HTTPException

from plong_elven.app.api.routers.auth import router as auth_router
from plong_elven.app.api.routers.cameras import router as cameras_router
from plong_elven.app.api.routers.crowd import router as crowd_router
from plong_elven.app.api.routers.dev import router as dev_router
from plong_elven.app.api.routers.profile import router as profile_router
from plong_elven.app.api.routers.staff import router as staff_router
from plong_elven.app.api.routers.tasks import router as tasks_router
from plong_elven.app.api.routers.zones import router as zones_router
from plong_elven.app.db.session import get_db
from plong_elven.app.services.errors import AppError
from plong_elven.app.services.footage_service import fail_interrupted_footage
from plong_elven.core.common.settings import check_security_settings, get_settings

logger = logging.getLogger(__name__)

# Map HTTP status codes to error codes (Task 2.2)
_STATUS_TO_CODE = {
    401: "unauthenticated",
    403: "wrong_role",
    404: "not_found",
    413: "payload_too_large",
    422: "validation_error",
    503: "auth_not_configured",
}


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan: validate security settings before accepting requests."""
    check_security_settings(get_settings())
    try:
        if failed := await fail_interrupted_footage():
            logger.warning("marked %d interrupted footage job(s) failed", failed)
    except Exception:  # a down DB must not stop the API starting
        logger.exception("could not check for interrupted footage jobs")
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    application = FastAPI(title="Plong Elven API", version="0.1.0", lifespan=lifespan)
    application.include_router(auth_router)
    application.include_router(profile_router)
    application.include_router(cameras_router)
    application.include_router(crowd_router)
    application.include_router(tasks_router)
    application.include_router(staff_router)
    application.include_router(zones_router)
    if get_settings().app_env == "dev":
        application.include_router(dev_router)  # dev page, APP_ENV=dev only (§6.1)

    @application.exception_handler(AppError)
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        headers = dict(exc.headers) if hasattr(exc, "headers") and exc.headers else {}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
            headers=headers if headers else None,
        )

    @application.exception_handler(HTTPException)
    async def http_exception_handler(
        _request: Request, exc: HTTPException
    ) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, "http_error")
        headers = dict(exc.headers) if exc.headers else {}
        if exc.status_code == 401:
            headers["WWW-Authenticate"] = "Bearer"
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": code, "message": str(exc.detail)}},
            headers=headers if headers else None,
        )

    @application.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        message = "; ".join(
            f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()
        )
        return JSONResponse(
            status_code=422,
            content={"error": {"code": "validation_error", "message": message}},
        )

    @application.get("/healthz", tags=["system"])
    async def health() -> dict[str, str]:
        """Return a process-level health check without requiring the database."""
        return {"status": "ok"}

    @application.get("/readyz", tags=["system"])
    async def readiness(
        session: AsyncSession = Depends(get_db),  # noqa: B008
    ) -> dict[str, str]:
        try:
            await session.execute(text("SELECT 1"))
        except SQLAlchemyError:
            return {"status": "not_ready", "database": "unavailable"}
        return {"status": "ready", "database": "connected"}

    return application


app = create_app()
