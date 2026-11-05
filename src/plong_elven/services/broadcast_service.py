"""Broadcast service: list, create, accept, complete, finish, staff lookup."""

import builtins
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.db.models import Broadcast, BroadcastResponse, User, Zone
from plong_elven.schemas.broadcasts import (
    AcceptedStaffOut,
    BroadcastOut,
    FinishOut,
    StaffOut,
)
from plong_elven.services.errors import AppError

if TYPE_CHECKING:
    from plong_elven.services.internal.notification_service import NotificationService


class BroadcastService:
    """Business logic for broadcast operations.

    Broadcast rules (unchanged from the old /dispatch routes):
    - Slot limit
    - One open broadcast per zone
    - Expiry after 20 seconds
    - accept/complete/finish flow
    """

    def __init__(
        self, session: AsyncSession, notifier: "NotificationService | None"
    ) -> None:
        self._session = session
        self._notifier = notifier

    def _not_found(self, message: str) -> AppError:
        return AppError(404, "not_found", message)

    async def _find_broadcast(self, broadcast_id: UUID) -> Broadcast:
        """Find broadcast by ID, using FOR UPDATE to lock the row for accept race safety."""
        broadcast = await self._session.scalar(
            select(Broadcast)
            .where(Broadcast.broadcast_id == broadcast_id)
            .with_for_update()
        )
        if broadcast is None:
            raise self._not_found(f"broadcast {broadcast_id} not found")
        return broadcast

    async def _close_if_expired(self, broadcast: Broadcast, now: datetime) -> bool:
        """Close the broadcast if it has expired. Returns True if closed."""
        if broadcast.status == "open" and now > broadcast.expires_at:
            broadcast.status = "closed"
            return True
        return False

    async def _broadcast_response(self, broadcast: Broadcast) -> BroadcastOut:
        """Build the BroadcastOut DTO with live accepted/completed counts."""
        accepted_count = await self._session.scalar(
            select(func.count())
            .select_from(BroadcastResponse)
            .where(BroadcastResponse.broadcast_id == broadcast.broadcast_id)
        )
        completed_count = await self._session.scalar(
            select(func.count())
            .select_from(BroadcastResponse)
            .where(
                BroadcastResponse.broadcast_id == broadcast.broadcast_id,
                BroadcastResponse.completed_at.is_not(None),
            )
        )
        accepted_staff_result = await self._session.execute(
            select(BroadcastResponse, User)
            .join(User, User.id == BroadcastResponse.staff_id)
            .where(BroadcastResponse.broadcast_id == broadcast.broadcast_id)
            .order_by(BroadcastResponse.responded_at)
        )
        accepted_staff = [
            AcceptedStaffOut(
                staffId=response.staff_id,
                name=user.full_name,
                completedAt=response.completed_at,
            )
            for response, user in accepted_staff_result.all()
        ]
        return BroadcastOut(
            broadcastId=broadcast.broadcast_id,
            zoneId=broadcast.zone_id,
            status=broadcast.status,
            staffNeeded=broadcast.staff_needed,
            expiresAt=broadcast.expires_at,
            acceptedCount=accepted_count or 0,
            completedCount=completed_count or 0,
            acceptedStaff=accepted_staff,
        )

    async def list(
        self,
        zone_id: str | None,
        status: Literal["open", "closed", "finished"] | None,
        limit: int,
    ) -> builtins.list[BroadcastOut]:
        """List broadcasts newest first. Empty result is [], never 404.

        Read-time side effect kept from the old "current" call (§6.2): open
        broadcasts past expires_at are closed and committed BEFORE the filtered
        query runs, so status="open" never returns an expired broadcast.
        """
        now = datetime.now(UTC)
        expired_stmt = select(Broadcast).where(
            Broadcast.status == "open", Broadcast.expires_at < now
        )
        if zone_id is not None:
            expired_stmt = expired_stmt.where(Broadcast.zone_id == zone_id)
        expired = (await self._session.scalars(expired_stmt)).all()
        changed = [await self._close_if_expired(b, now) for b in expired]
        if any(changed):
            await self._session.commit()

        stmt = select(Broadcast)
        if zone_id is not None:
            stmt = stmt.where(Broadcast.zone_id == zone_id)
        if status is not None:
            stmt = stmt.where(Broadcast.status == status)
        stmt = stmt.order_by(Broadcast.created_at.desc()).limit(limit)
        broadcasts = (await self._session.scalars(stmt)).all()
        # shortcut: N+1 count queries per broadcast, fine at limit<=50; join if lists grow
        return [await self._broadcast_response(b) for b in broadcasts]

    async def create(
        self, zone_id: str, staff_needed: int, defer: Callable[..., None]
    ) -> BroadcastOut:
        """Create a new broadcast for a zone.

        Args:
            zone_id: The zone to create the broadcast for
            staff_needed: Number of staff members needed
            defer: A callable to schedule background work (e.g., background_tasks.add_task)

        Returns:
            The new broadcast as BroadcastOut
        """
        zone = await self._session.get(Zone, zone_id)
        if zone is None:
            raise self._not_found(f"zone {zone_id} not found")

        now = datetime.now(UTC)
        open_broadcast = await self._session.scalar(
            select(Broadcast)
            .where(Broadcast.zone_id == zone_id, Broadcast.status == "open")
            .order_by(Broadcast.created_at.desc())
        )
        if open_broadcast is not None:
            if await self._close_if_expired(open_broadcast, now):
                await self._session.commit()
            else:
                raise AppError(
                    409,
                    "broadcast_open",
                    "This zone already has an open broadcast.",
                )

        broadcast = Broadcast(
            zone_id=zone_id,
            staff_needed=staff_needed,
            expires_at=now + timedelta(seconds=20),
        )
        self._session.add(broadcast)
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise AppError(
                409, "broadcast_open", "This zone already has an open broadcast."
            ) from None
        await self._session.refresh(broadcast)

        # Schedule push notification in background
        if self._notifier is not None:
            defer(
                self._notifier.notify_role,
                "floor_staff",
                "New broadcast",
                f"Crowd control needed in {zone.name} — tap to accept",
            )

        return await self._broadcast_response(broadcast)

    async def accept(self, broadcast_id: UUID, user: User) -> BroadcastOut:
        """Accept a broadcast and return its updated state.

        The first query uses SELECT ... FOR UPDATE to lock the broadcast row,
        preventing race conditions when multiple staff accept the last slot.
        """
        # This is the critical race-safe query: lock the row first
        broadcast = await self._find_broadcast(broadcast_id)

        if await self._close_if_expired(broadcast, datetime.now(UTC)):
            await self._session.commit()
        # Covers both "just expired" and already closed/finished broadcasts.
        if broadcast.status != "open":
            raise AppError(409, "expired", "This broadcast is no longer open.")

        existing = await self._session.get(
            BroadcastResponse, {"broadcast_id": broadcast_id, "staff_id": user.id}
        )
        if existing is not None:
            return await self._broadcast_response(broadcast)  # idempotent

        if user.status == "delegating":
            raise AppError(
                409, "already_delegating", "Staff member is already delegating."
            )

        accepted_count = await self._session.scalar(
            select(func.count())
            .select_from(BroadcastResponse)
            .where(BroadcastResponse.broadcast_id == broadcast_id)
        )
        if accepted_count >= broadcast.staff_needed:
            raise AppError(
                409,
                "slots_full",
                "This broadcast already has enough staff.",
            )

        self._session.add(
            BroadcastResponse(broadcast_id=broadcast_id, staff_id=user.id)
        )
        user.status = "delegating"
        await self._session.commit()
        return await self._broadcast_response(broadcast)

    async def complete(self, broadcast_id: UUID, user: User) -> BroadcastOut:
        """Mark a broadcast as complete for the current user; return its state."""
        # Use _find_broadcast for consistency (includes FOR UPDATE for safety)
        broadcast = await self._find_broadcast(broadcast_id)

        response = await self._session.get(
            BroadcastResponse, {"broadcast_id": broadcast_id, "staff_id": user.id}
        )
        if response is None:
            raise AppError(
                403,
                "not_your_response",
                "This staff member did not accept the broadcast.",
            )
        if response.completed_at is None:
            response.completed_at = datetime.now(UTC)
        user.status = "idle"
        await self._session.commit()
        return await self._broadcast_response(broadcast)

    async def finish(self, broadcast_id: UUID) -> FinishOut:
        """Finish a broadcast (manager action)."""
        broadcast = await self._find_broadcast(broadcast_id)

        if await self._close_if_expired(broadcast, datetime.now(UTC)):
            await self._session.commit()

        accepted_count = await self._session.scalar(
            select(func.count())
            .select_from(BroadcastResponse)
            .where(BroadcastResponse.broadcast_id == broadcast_id)
        )
        completed_count = await self._session.scalar(
            select(func.count())
            .select_from(BroadcastResponse)
            .where(
                BroadcastResponse.broadcast_id == broadcast_id,
                BroadcastResponse.completed_at.is_not(None),
            )
        )
        if accepted_count < 1 or completed_count != accepted_count:
            raise AppError(
                409,
                "not_all_done",
                "All accepted staff members must complete before finishing.",
            )
        if broadcast.status != "finished":
            broadcast.status = "finished"
            broadcast.finished_at = datetime.now(UTC)
            await self._session.commit()
        return FinishOut(
            broadcastId=broadcast.broadcast_id,
            status="finished",
            finishedAt=broadcast.finished_at,
        )

    async def get_staff(self, staff_id: UUID) -> StaffOut:
        """Get staff member information."""
        staff = await self._session.get(User, staff_id)
        if staff is None:
            raise self._not_found(f"staff {staff_id} not found")
        return StaffOut(staffId=staff.id, name=staff.full_name, status=staff.status)
