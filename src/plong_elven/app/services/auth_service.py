"""Authentication service layer. No fastapi imports."""

import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from plong_elven.app.db.models import RefreshToken, User
from plong_elven.app.schemas.auth import RefreshResponse, TokenResponse, UserResponse
from plong_elven.app.schemas.tickets import TicketOut
from plong_elven.app.services.errors import AppError
from plong_elven.core.common.settings import get_settings

password_hasher = PasswordHasher()

# Ticket expiry in seconds (§6.4)
TICKET_SECONDS = 60


def jwt_secret() -> str:
    """Return the JWT secret, raising AppError if too weak."""
    secret = get_settings().jwt_secret
    if len(secret) < 32:
        raise AppError(
            503,
            "auth_not_configured",
            "JWT authentication is not configured with a strong secret",
        )
    return secret


def hash_refresh_token(token: str) -> str:
    """Hash a refresh token for storage."""
    return hashlib.sha256(token.encode()).hexdigest()


def new_refresh_token() -> tuple[str, str]:
    """Generate a new refresh token and its hash."""
    raw = secrets.token_urlsafe(48)
    return raw, hash_refresh_token(raw)


def create_access_token(user: User) -> str:
    """Create a JWT access token for the user."""
    now = datetime.now(UTC)
    expires_in = timedelta(minutes=get_settings().access_token_minutes)
    return jwt.encode(
        {
            "sub": str(user.user_id),
            "type": "access",
            "iat": now,
            "exp": now + expires_in,
        },
        jwt_secret(),
        algorithm="HS256",
    )


def user_response(user: User) -> UserResponse:
    """Convert a User model to a UserResponse schema."""
    return UserResponse(
        id=user.user_id,
        email=user.email,
        fullName=user.full_name,
        role=user.role.name,
        isActive=user.is_active,
    )


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against its hash."""
    try:
        return password_hasher.verify(password_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False


class AuthService:
    """Authentication business logic. Operates on an AsyncSession."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def _find_user(self, email: str) -> User | None:
        """Find a user by email (case-insensitive)."""
        result = await self._session.execute(
            select(User)
            .options(selectinload(User.role))
            .where(func.lower(User.email) == email.lower())
        )
        return result.scalar_one_or_none()

    async def _create_refresh_record(self, user: User, token_hash: str) -> None:
        """Create a refresh token record in the database."""
        self._session.add(
            RefreshToken(
                user_id=user.user_id,
                token_hash=token_hash,
                expires_at=datetime.now(UTC)
                + timedelta(days=get_settings().refresh_token_days),
            )
        )

    async def login(self, email: str, password: str) -> TokenResponse:
        """Authenticate a user and return tokens.

        Raises:
            AppError(401): If email or password is invalid.
        """
        user = await self._find_user(email)
        if (
            user is None
            or not user.is_active
            or not verify_password(password, user.password_hash)
        ):
            raise AppError(401, "unauthenticated", "Invalid email or password")

        raw_refresh_token, refresh_hash = new_refresh_token()
        await self._create_refresh_record(user, refresh_hash)
        await self._session.commit()

        return TokenResponse(
            accessToken=create_access_token(user),
            refreshToken=raw_refresh_token,
            expiresIn=get_settings().access_token_minutes * 60,
            user=user_response(user),
        )

    async def refresh(self, raw_refresh_token: str) -> RefreshResponse:
        """Refresh access token using a valid refresh token.

        Raises:
            AppError(401): If refresh token is invalid, expired, or revoked.
        """
        result = await self._session.execute(
            select(RefreshToken)
            .options(selectinload(RefreshToken.user).selectinload(User.role))
            .where(RefreshToken.token_hash == hash_refresh_token(raw_refresh_token))
        )
        stored = result.scalar_one_or_none()
        now = datetime.now(UTC)
        if (
            stored is None
            or stored.revoked_at is not None
            or stored.expires_at <= now
            or not stored.user.is_active
        ):
            raise AppError(401, "unauthenticated", "Invalid refresh token")

        stored.revoked_at = now
        new_raw_refresh_token, refresh_hash = new_refresh_token()
        await self._create_refresh_record(stored.user, refresh_hash)
        await self._session.commit()

        return RefreshResponse(
            accessToken=create_access_token(stored.user),
            refreshToken=new_raw_refresh_token,
            expiresIn=get_settings().access_token_minutes * 60,
        )

    async def logout(self, raw_refresh_token: str) -> None:
        """Revoke a refresh token and clear push token.

        Silently succeeds if the token doesn't exist or is already revoked.
        """
        result = await self._session.execute(
            select(RefreshToken)
            .options(selectinload(RefreshToken.user))
            .where(RefreshToken.token_hash == hash_refresh_token(raw_refresh_token))
        )
        stored = result.scalar_one_or_none()
        if stored is not None and stored.revoked_at is None:
            stored.revoked_at = datetime.now(UTC)
            # Otherwise this device keeps receiving pushes for this account after the
            # user signs out — the token was never tied to "logged in", so nothing else
            # cleared it. Re-registered automatically on next login.
            stored.user.push_token = None
            await self._session.commit()

    async def user_from_access_token(self, token: str) -> User:
        """Decode an access token and return the user.

        Raises:
            AppError(401): If the token is invalid or the user is not found/inactive.
        """
        try:
            payload = jwt.decode(token, jwt_secret(), algorithms=["HS256"])
            if payload.get("type") != "access":
                raise ValueError
            user_id = UUID(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            raise AppError(401, "unauthenticated", "Invalid access token") from None

        result = await self._session.execute(
            select(User).options(selectinload(User.role)).where(User.user_id == user_id)
        )
        user = result.scalar_one_or_none()
        if user is None or not user.is_active:
            raise AppError(401, "unauthenticated", "Invalid access token")
        return user

    async def set_push_token(self, user: User, token: str) -> None:
        """Set a user's push notification token."""
        user.push_token = token
        await self._session.commit()

    def mint_ticket(self, user: User, scope: str) -> TicketOut:
        """Create a short-lived ticket JWT for browser endpoints.

        Args:
            user: The user minting the ticket (must be admin).
            scope: The scope claim, e.g. "stream:cam1" or "crowd:live".

        Returns:
            TicketOut with the signed JWT and expiresIn seconds.
        """
        now = datetime.now(UTC)
        token = jwt.encode(
            {
                "sub": str(user.user_id),
                "type": "ticket",
                "scope": scope,
                "iat": now,
                "exp": now + timedelta(seconds=TICKET_SECONDS),
            },
            jwt_secret(),
            algorithm="HS256",
        )
        return TicketOut(ticket=token, expiresIn=TICKET_SECONDS)

    async def verify_ticket(self, token: str, scope: str) -> User:
        """Verify a ticket JWT and return the user.

        Args:
            token: The ticket JWT.
            scope: The expected scope (must match the ticket's scope claim).

        Raises:
            AppError(401): If the token is invalid, expired, wrong scope,
                           user not found/inactive, or user is not admin (P4).

        Returns:
            The authenticated User.
        """
        try:
            payload = jwt.decode(
                token,
                jwt_secret(),
                algorithms=["HS256"],
                # PyJWT checks exp only when present; a ticket must always expire.
                options={"require": ["exp", "iat", "sub", "type", "scope"]},
            )
            if payload.get("type") != "ticket":
                raise ValueError("not a ticket")
            if payload.get("scope") != scope:
                raise ValueError("scope mismatch")
            user_id = UUID(payload["sub"])
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            raise AppError(401, "unauthenticated", "Invalid ticket") from None

        # Re-load the user and require is_active and admin role (P4)
        user = await self._session.get(User, user_id, options=[selectinload(User.role)])
        if user is None or not user.is_active:
            raise AppError(401, "unauthenticated", "Invalid ticket")
        if user.role.name != "admin":
            raise AppError(401, "unauthenticated", "Invalid ticket")
        return user
