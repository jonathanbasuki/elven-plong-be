"""FastAPI dependencies shared across routers."""

from collections.abc import Awaitable, Callable

from fastapi import Depends, Header
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.app.api.security import check_internal_key
from plong_elven.app.db.models import User
from plong_elven.app.db.session import SessionFactory, get_db
from plong_elven.app.services.auth_service import AuthService
from plong_elven.app.services.camera_service import CameraService
from plong_elven.app.services.crowd_service import CrowdService
from plong_elven.app.services.errors import AppError
from plong_elven.app.services.internal.alert_service import (
    AlertService,
    default_alert_rules,
)
from plong_elven.app.services.internal.notification_service import NotificationService
from plong_elven.app.services.ports import AlertRule, PushSender
from plong_elven.app.services.task_service import TaskService
from plong_elven.app.services.zone_service import ZoneService
from plong_elven.core.adapters.apns_sender import ApnsPushSender
from plong_elven.core.state.live_state import LiveState, get_live_state

bearer = HTTPBearer(auto_error=False)


def require_internal_key(
    x_internal_api_key: str | None = Header(default=None),
) -> None:
    """Dependency that checks the X-Internal-Api-Key header (camera routes)."""
    check_internal_key(x_internal_api_key)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),  # noqa: B008
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> User:
    """Extract and validate the current user from the Authorization header.

    Raises:
        AppError(401): If credentials are missing or token is invalid.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError(401, "unauthenticated", "Not authenticated")

    return await AuthService(session).user_from_access_token(credentials.credentials)


# --- Task 1.7: Dependencies for services ---


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the database session factory for background tasks."""
    return SessionFactory


def get_push_sender() -> PushSender:
    """Return the real APNs push sender."""
    return ApnsPushSender()


def get_alert_rules() -> list[AlertRule]:
    """Return the default alert rules."""
    return default_alert_rules()


def get_notification_service(
    factory: async_sessionmaker[AsyncSession] = Depends(get_session_factory),  # noqa: B008
    sender: PushSender = Depends(get_push_sender),  # noqa: B008
) -> NotificationService:
    """Return a NotificationService that opens its own session."""
    return NotificationService(factory, sender)


def get_alert_service(
    notifier: NotificationService = Depends(get_notification_service),  # noqa: B008
    rules: list[AlertRule] = Depends(get_alert_rules),  # noqa: B008
    live: LiveState = Depends(get_live_state),  # noqa: B008
) -> AlertService:
    """Return an AlertService with its dependencies."""
    return AlertService(notifier, rules, live)


def get_camera_service(
    live: LiveState = Depends(get_live_state),  # noqa: B008
) -> CameraService:
    """CameraService over the live state (latest frames)."""
    return CameraService(live)


def get_crowd_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    live: LiveState = Depends(get_live_state),  # noqa: B008
) -> CrowdService:
    """Return a CrowdService bound to the request session."""
    return CrowdService(session, live)


def get_zone_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> ZoneService:
    """Return a ZoneService bound to the request session."""
    return ZoneService(session)


def require_role(*names: str) -> Callable[..., Awaitable[User]]:
    """Dependency factory that checks the user has one of the required roles.

    Usage:
        user: User = Depends(require_role("ops_manager"))
        user: User = Depends(require_role("floor_staff", "ops_manager"))
    """

    async def _check_role(
        user: User = Depends(get_current_user),  # noqa: B008
    ) -> User:
        if user.role.name not in names:
            if len(names) == 1:
                msg = f"This action requires the {names[0]} role."
            else:
                msg = f"This action requires the {' or '.join(names)} role."
            raise AppError(403, "wrong_role", msg)
        return user

    return _check_role


def get_task_service(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    notifier: NotificationService = Depends(get_notification_service),  # noqa: B008
) -> TaskService:
    """Return a TaskService bound to the request session."""
    return TaskService(session, notifier)
