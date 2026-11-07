"""Profile routes: the current user and their push token."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.app.api.deps import get_current_user, get_db
from plong_elven.app.db.models import User
from plong_elven.app.schemas.auth import PushTokenRequest, UserResponse
from plong_elven.app.services.auth_service import AuthService, user_response

router = APIRouter(prefix="/api/v1/profile", tags=["profile"])


@router.get("", response_model=UserResponse)
async def get_profile(user: User = Depends(get_current_user)) -> UserResponse:  # noqa: B008
    return user_response(user)


@router.put("/push-token")
async def put_push_token(
    payload: PushTokenRequest,
    user: User = Depends(get_current_user),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    await AuthService(session).set_push_token(user, payload.pushToken)
    return {"status": "ok"}
