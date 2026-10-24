import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from plong_elven.common.settings import get_settings
from plong_elven.db.models import RefreshToken, User
from plong_elven.db.session import get_db

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
bearer = HTTPBearer(auto_error=False)
password_hasher = PasswordHasher()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    full_name: str
    role: str
    is_active: bool


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse


class RefreshResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


def _jwt_secret() -> str:
    secret = get_settings().jwt_secret
    if len(secret) < 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="JWT authentication is not configured with a strong secret",
        )
    return secret


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _new_refresh_token() -> tuple[str, str]:
    raw = secrets.token_urlsafe(48)
    return raw, _hash_refresh_token(raw)


def _create_access_token(user: User) -> str:
    now = datetime.now(UTC)
    expires_in = timedelta(minutes=get_settings().access_token_minutes)
    return jwt.encode(
        {
            "sub": str(user.id),
            "type": "access",
            "iat": now,
            "exp": now + expires_in,
        },
        _jwt_secret(),
        algorithm="HS256",
    )


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role.name,
        is_active=user.is_active,
    )


async def _find_user(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(
        select(User)
        .options(selectinload(User.role))
        .where(func.lower(User.email) == email.lower())
    )
    return result.scalar_one_or_none()


async def _create_refresh_record(
    session: AsyncSession, user: User, token_hash: str
) -> None:
    session.add(
        RefreshToken(
            user_id=user.id,
            token_hash=token_hash,
            expires_at=datetime.now(UTC)
            + timedelta(days=get_settings().refresh_token_days),
        )
    )


def _verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> User:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Not authenticated")

    try:
        payload = jwt.decode(
            credentials.credentials, _jwt_secret(), algorithms=["HS256"]
        )
        if payload.get("type") != "access":
            raise ValueError
        user_id = UUID(payload["sub"])
    except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="Invalid access token") from None

    result = await session.execute(
        select(User).options(selectinload(User.role)).where(User.id == user_id)
    )
    user = result.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(status_code=401, detail="Invalid access token")
    return user


@router.post("/login", response_model=TokenResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
):
    user = await _find_user(session, str(payload.email))
    if (
        user is None
        or not user.is_active
        or not _verify_password(payload.password, user.password_hash)
    ):
        raise HTTPException(status_code=401, detail="Invalid email or password")

    raw_refresh_token, refresh_hash = _new_refresh_token()
    user.last_login_at = datetime.now(UTC)
    await _create_refresh_record(session, user, refresh_hash)
    await session.commit()

    return TokenResponse(
        access_token=_create_access_token(user),
        refresh_token=raw_refresh_token,
        expires_in=get_settings().access_token_minutes * 60,
        user=_user_response(user),
    )


@router.get("/profile", response_model=UserResponse)
async def profile(user: User = Depends(get_current_user)):  # noqa: B008
    return _user_response(user)


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
):
    result = await session.execute(
        select(RefreshToken)
        .options(selectinload(RefreshToken.user).selectinload(User.role))
        .where(RefreshToken.token_hash == _hash_refresh_token(payload.refresh_token))
    )
    stored = result.scalar_one_or_none()
    now = datetime.now(UTC)
    if (
        stored is None
        or stored.revoked_at is not None
        or stored.expires_at <= now
        or not stored.user.is_active
    ):
        raise HTTPException(status_code=401, detail="Invalid refresh token")

    stored.revoked_at = now
    raw_refresh_token, refresh_hash = _new_refresh_token()
    await _create_refresh_record(session, stored.user, refresh_hash)
    await session.commit()

    return RefreshResponse(
        access_token=_create_access_token(stored.user),
        refresh_token=raw_refresh_token,
        expires_in=get_settings().access_token_minutes * 60,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    payload: RefreshRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
):
    result = await session.execute(
        select(RefreshToken).where(
            RefreshToken.token_hash == _hash_refresh_token(payload.refresh_token)
        )
    )
    stored = result.scalar_one_or_none()
    if stored is not None and stored.revoked_at is None:
        stored.revoked_at = datetime.now(UTC)
        await session.commit()
