"""Notification service for sending push notifications to users by role."""

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.app.db.models import Role, User
from plong_elven.app.services.ports import PushSender


class NotificationService:
    """Service for sending push notifications to users.

    Opens its own session from the factory for each operation, ensuring it
    never depends on a request-scoped session that may be closed.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        sender: PushSender,
    ) -> None:
        self._session_factory = session_factory
        self._sender = sender

    async def notify_role(
        self,
        role_name: str,
        title: str,
        body: str,
        exclude_zone_id: str | None = None,
    ) -> None:
        """Send a push notification to all users with the given role.

        Args:
            role_name: The role whose users should receive the notification
            title: Push notification title
            body: Push notification body
            exclude_zone_id: Skip users whose home zone is this zone (a task is
                for staff from other zones). Users with no home zone still get it.
        """
        stmt = (
            select(User.push_token)
            .join(Role, Role.role_id == User.role_id)
            .where(Role.name == role_name, User.push_token.is_not(None))
        )
        if exclude_zone_id is not None:
            stmt = stmt.where(
                or_(User.zone_id.is_(None), User.zone_id != exclude_zone_id)
            )
        async with self._session_factory() as session:
            tokens = (await session.scalars(stmt)).all()

        await self._sender.send(list(tokens), title, body)
