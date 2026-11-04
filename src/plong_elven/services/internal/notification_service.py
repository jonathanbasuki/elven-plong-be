"""Notification service for sending push notifications to users by role."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.db.models import Role, User
from plong_elven.services.ports import PushSender


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

    async def notify_role(self, role_name: str, title: str, body: str) -> None:
        """Send a push notification to all users with the given role.

        Args:
            role_name: The role whose users should receive the notification
            title: Push notification title
            body: Push notification body
        """
        async with self._session_factory() as session:
            result = await session.scalars(
                select(User.push_token)
                .join(Role, Role.id == User.role_id)
                .where(Role.name == role_name, User.push_token.is_not(None))
            )
            tokens = result.all()

        await self._sender.send(list(tokens), title, body)
