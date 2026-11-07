"""Authentication routes."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.app.api.deps import get_db
from plong_elven.app.schemas.auth import (
    LoginRequest,
    RefreshRequest,
    RefreshResponse,
    TokenResponse,
)
from plong_elven.app.services.auth_service import AuthService

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def get_auth_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> AuthService:
    """Provide an AuthService instance for route handlers."""
    return AuthService(session)


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
):
    return await auth.login(str(payload.email), payload.password)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    payload: RefreshRequest,
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
):
    return await auth.refresh(payload.refreshToken)


@router.post("/logout")
async def logout(
    payload: RefreshRequest,
    auth: AuthService = Depends(get_auth_service),  # noqa: B008
) -> dict[str, str]:
    await auth.logout(payload.refreshToken)
    return {"status": "ok"}
