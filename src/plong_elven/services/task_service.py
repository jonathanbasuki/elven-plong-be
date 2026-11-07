"""Task service: list, broadcast (start a task), accept, complete, finish, staff lookup.

Task rules:
- A manager starts a task by broadcasting it to floor staff. A zone with an
  active task (zone.is_active_task) cannot be broadcast again.
- A task takes acceptances for 20 seconds, then becomes ongoing if at least one
  staff member accepted it, or closes if nobody accepted. Expiry is lazy: it is
  applied by whichever call reads the task next (close_expired_tasks).
- A zone stops being active when its task is finished, or when the task closes
  with no respondents at all (nobody came, so there is nothing to finish).
- Staff whose home zone is the task's zone are not notified and cannot accept.
"""

import builtins
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.db.models import (
    Role,
    Task,
    TaskFootage,
    TaskRespondent,
    TaskRespondentStatusId,
    TaskStatusId,
    User,
    UserStatusId,
    Zone,
)
from plong_elven.helpers.zone_status import classify
from plong_elven.schemas.tasks import (
    AcceptedStaffOut,
    FinishOut,
    LiveCrowdOut,
    StaffOut,
    TaskDetailOut,
    TaskOut,
    TaskStatus,
)
from plong_elven.services.crowd_service import CrowdService
from plong_elven.services.errors import AppError
from plong_elven.services.footage_service import process_task_footage
from plong_elven.state.live_state import LiveState

if TYPE_CHECKING:
    from plong_elven.services.internal.notification_service import NotificationService

TASK_OPEN_SECONDS = 20


def task_phase(task_status_id: int) -> Literal["ongoing", "completed"]:
    """ "ongoing" while a task is open or being worked on; closed (nobody came or
    cancelled), finished and complete are "completed"."""
    if task_status_id in (TaskStatusId.OPEN, TaskStatusId.ONGOING):
        return "ongoing"
    return "completed"


def crowd_log_end(
    task: Task, respondents: builtins.list[AcceptedStaffOut], now: datetime
) -> datetime:
    """When a completed task's crowd log stops: its finish time; for a closed,
    unfinished task, now while anyone is still working, else the last
    completion; for one nobody accepted, the end of its 20-second window. So an
    old task that was never finished does not return an ever-growing log."""
    if task.finished_at is not None:
        return task.finished_at
    if not respondents:
        return task.expires_at
    if any(r.completedAt is None for r in respondents):
        return now
    return max(r.completedAt for r in respondents)


async def _respondent_count(
    session: AsyncSession, task_id: UUID, completed_only: bool = False
) -> int:
    stmt = (
        select(func.count())
        .select_from(TaskRespondent)
        .where(TaskRespondent.task_id == task_id)
    )
    if completed_only:
        stmt = stmt.where(
            TaskRespondent.task_respondent_status_id == TaskRespondentStatusId.COMPLETED
        )
    return await session.scalar(stmt) or 0


async def _free_zone_if_latest(session: AsyncSession, task: Task) -> None:
    """Clear the zone's is_active_task, but only if `task` is the zone's newest
    task. An older leftover task (e.g. from before the migration) ending must
    not free a zone whose current task is still running."""
    latest = await session.scalar(
        select(Task)
        .where(Task.zone_id == task.zone_id)
        .order_by(Task.created_at.desc())
        .limit(1)
    )
    if latest is not None and latest.task_id != task.task_id:
        return
    zone = await session.get(Zone, task.zone_id)
    if zone is not None:
        zone.is_active_task = False


async def _close(session: AsyncSession, task: Task) -> None:
    """End the gathering window and start or close the task."""
    if await _respondent_count(session, task.task_id) == 0:
        task.task_status_id = TaskStatusId.CLOSED
        await _free_zone_if_latest(session, task)
    else:
        task.task_status_id = TaskStatusId.ONGOING


async def close_expired_tasks(
    session: AsyncSession, now: datetime, zone_id: str | None = None
) -> bool:
    """Close open tasks past expires_at. Returns True if anything changed; the
    caller commits."""
    stmt = select(Task).where(
        Task.task_status_id == TaskStatusId.OPEN, Task.expires_at < now
    )
    if zone_id is not None:
        stmt = stmt.where(Task.zone_id == zone_id)
    expired = (await session.scalars(stmt)).all()
    for task in expired:
        await _close(session, task)
    return bool(expired)


class TaskService:
    """Business logic for tasks. No FastAPI imports."""

    def __init__(
        self, session: AsyncSession, notifier: "NotificationService | None"
    ) -> None:
        self._session = session
        self._notifier = notifier

    async def _find_task(self, task_id: UUID) -> Task:
        """Find a task and lock its row (SELECT ... FOR UPDATE), so two staff
        racing for the last slot are serialized."""
        task = await self._session.scalar(
            select(Task).where(Task.task_id == task_id).with_for_update()
        )
        if task is None:
            raise AppError(404, "not_found", f"task {task_id} not found")
        return task

    async def _close_if_expired(self, task: Task, now: datetime) -> bool:
        if task.task_status_id == TaskStatusId.OPEN and now > task.expires_at:
            await _close(self._session, task)
            return True
        return False

    async def _task_out(self, task: Task) -> TaskOut:
        """Build the TaskOut DTO with live accepted/completed counts."""
        rows = await self._session.execute(
            select(TaskRespondent, User)
            .join(User, User.user_id == TaskRespondent.staff_id)
            .where(TaskRespondent.task_id == task.task_id)
            .order_by(TaskRespondent.responded_at)
        )
        accepted_staff = [
            AcceptedStaffOut(
                staffId=respondent.staff_id,
                name=user.full_name,
                status=TaskRespondentStatusId(
                    respondent.task_respondent_status_id
                ).name.lower(),
                completedAt=respondent.completed_at,
            )
            for respondent, user in rows.all()
        ]
        return TaskOut(
            taskId=task.task_id,
            zoneId=task.zone_id,
            status=TaskStatusId(task.task_status_id).name.lower(),
            totalStaff=task.total_staff,
            instruction=task.instruction,
            expiresAt=task.expires_at,
            acceptedCount=len(accepted_staff),
            completedCount=sum(s.status == "completed" for s in accepted_staff),
            acceptedStaff=accepted_staff,
        )

    async def list(
        self,
        zone_id: str | None,
        statuses: builtins.list[TaskStatus] | None,
        limit: int,
    ) -> builtins.list[TaskOut]:
        """List tasks newest first. Empty result is [], never 404.

        Expired open tasks are closed and committed BEFORE the filtered query
        runs, so status="open" never returns an expired task.
        """
        if await close_expired_tasks(self._session, datetime.now(UTC), zone_id):
            await self._session.commit()

        stmt = select(Task)
        if zone_id is not None:
            stmt = stmt.where(Task.zone_id == zone_id)
        if statuses:
            stmt = stmt.where(
                Task.task_status_id.in_({TaskStatusId[s.upper()] for s in statuses})
            )
        stmt = stmt.order_by(Task.created_at.desc()).limit(limit)
        tasks = (await self._session.scalars(stmt)).all()
        # shortcut: one respondent query per task, fine at limit<=50; join if lists grow
        return [await self._task_out(t) for t in tasks]

    async def broadcast(
        self,
        zone_id: str,
        total_staff: int,
        instruction: str,
        defer: Callable[..., None],
    ) -> TaskOut:
        """Start a task in a zone by broadcasting it to floor staff.

        Args:
            defer: schedules background work (e.g. background_tasks.add_task);
                used for the push so the response does not wait on APNs.

        Raises:
            AppError(404): unknown zone.
            AppError(409, "broadcast_open"): the zone already has an active task.
        """
        # Lock the zone row so two managers broadcasting at once are serialized
        # on the is_active_task check.
        zone = await self._session.get(Zone, zone_id, with_for_update=True)
        if zone is None:
            raise AppError(404, "not_found", f"zone {zone_id} not found")

        now = datetime.now(UTC)
        if await close_expired_tasks(self._session, now, zone_id):
            await self._session.commit()  # may have freed the zone
        if zone.is_active_task:
            raise AppError(
                409, "broadcast_open", "This zone already has an active task."
            )

        task = Task(
            zone_id=zone_id,
            total_staff=total_staff,
            instruction=instruction,
            task_status_id=TaskStatusId.OPEN,
            expires_at=now + timedelta(seconds=TASK_OPEN_SECONDS),
        )
        self._session.add(task)
        zone.is_active_task = True
        try:
            await self._session.commit()
        except IntegrityError:
            await self._session.rollback()
            raise AppError(
                409, "broadcast_open", "This zone already has an active task."
            ) from None
        await self._session.refresh(task)

        if self._notifier is not None:
            defer(
                self._notifier.notify_role,
                "floor_staff",
                "New task",
                f"{zone.name}: {instruction}",
                exclude_zone_id=zone_id,
            )

        return await self._task_out(task)

    async def accept(self, task_id: UUID, user: User) -> TaskOut:
        """Accept a task and return its updated state. Locks the task row first."""
        task = await self._find_task(task_id)

        existing = await self._session.get(
            TaskRespondent, {"task_id": task_id, "staff_id": user.user_id}
        )
        if existing is not None:
            return await self._task_out(task)  # idempotent

        if await self._close_if_expired(task, datetime.now(UTC)):
            await self._session.commit()
        # Covers both "just expired" and already closed/finished tasks.
        if task.task_status_id != TaskStatusId.OPEN:
            raise AppError(409, "expired", "This task is no longer open.")

        if user.zone_id is not None and user.zone_id == task.zone_id:
            raise AppError(
                409, "own_zone", "Staff cannot take a task in their home zone."
            )
        if user.user_status_id == UserStatusId.DELEGATING:
            raise AppError(
                409, "already_delegating", "Staff member is already delegating."
            )
        if await _respondent_count(self._session, task_id) >= task.total_staff:
            raise AppError(409, "slots_full", "This task already has enough staff.")

        self._session.add(
            TaskRespondent(
                task_id=task_id,
                staff_id=user.user_id,
                task_respondent_status_id=TaskRespondentStatusId.ONGOING,
            )
        )
        user.user_status_id = UserStatusId.DELEGATING
        await self._session.commit()
        return await self._task_out(task)

    async def complete(
        self,
        task_id: UUID,
        user: User,
        defer: Callable[..., None] | None = None,
    ) -> TaskOut:
        """Mark the current user's part of the task done; return its state.

        When this was the last open part, the task becomes complete and the
        footage job is handed to `defer` (FastAPI BackgroundTasks.add_task)."""
        task = await self._find_task(task_id)

        respondent = await self._session.get(
            TaskRespondent, {"task_id": task_id, "staff_id": user.user_id}
        )
        if respondent is None:
            raise AppError(
                403, "not_your_response", "This staff member did not accept the task."
            )
        if task.task_status_id == TaskStatusId.CLOSED:  # cancelled
            raise AppError(409, "expired", "This task was cancelled.")
        # Only the first completion frees the user; a repeat call must not reset a
        # user who has since accepted another task.
        if respondent.task_respondent_status_id != TaskRespondentStatusId.COMPLETED:
            respondent.task_respondent_status_id = TaskRespondentStatusId.COMPLETED
            respondent.completed_at = datetime.now(UTC)
            user.user_status_id = UserStatusId.IDLE
            accepted = await _respondent_count(self._session, task_id)
            completed = await _respondent_count(self._session, task_id, True)
            if accepted > 0 and completed == accepted:
                task.task_status_id = TaskStatusId.COMPLETE
                task.finished_at = respondent.completed_at
                await _free_zone_if_latest(self._session, task)
                self._session.add(TaskFootage(task_id=task_id))
            await self._session.commit()
            if task.task_status_id == TaskStatusId.COMPLETE and defer is not None:
                defer(process_task_footage, task_id)
        return await self._task_out(task)

    async def finish(self, task_id: UUID) -> FinishOut:
        """Finish a task (manager action). Frees the zone for a new broadcast."""
        task = await self._find_task(task_id)

        if await self._close_if_expired(task, datetime.now(UTC)):
            await self._session.commit()

        accepted = await _respondent_count(self._session, task_id)
        completed = await _respondent_count(self._session, task_id, True)
        if accepted < 1 or completed != accepted:
            raise AppError(
                409,
                "not_all_done",
                "All accepted staff members must complete before finishing.",
            )
        if task.task_status_id not in (TaskStatusId.FINISHED, TaskStatusId.COMPLETE):
            task.task_status_id = TaskStatusId.FINISHED
            task.finished_at = datetime.now(UTC)
            await _free_zone_if_latest(self._session, task)
            await self._session.commit()
        return FinishOut(
            taskId=task.task_id, status="finished", finishedAt=task.finished_at
        )

    async def detail(self, task_id: UUID, live: LiveState) -> TaskDetailOut:
        """One task with its zone, crowd (live while open, 5-minute log once
        completed), the zone's own staff and the delegated staff."""
        task = await self._find_task(task_id)
        if await self._close_if_expired(task, datetime.now(UTC)):
            await self._session.commit()
        zone = await self._session.get(Zone, task.zone_id)
        out = await self._task_out(task)
        phase = task_phase(task.task_status_id)
        live_crowd, log = None, []
        if phase == "ongoing":
            live_crowd = LiveCrowdOut(
                count=live.counts.get(task.zone_id, 0),
                observedAt=live.observed_at.get(task.zone_id),
            )
        else:
            end = crowd_log_end(task, out.acceptedStaff, datetime.now(UTC))
            log = await CrowdService(self._session, live).log_between(
                task.zone_id, task.created_at, end
            )
        return TaskDetailOut(
            taskId=task.task_id,
            zoneId=task.zone_id,
            zoneName=zone.name,
            zoneStatus=classify(live.counts.get(task.zone_id, 0), zone.threshold),
            instruction=task.instruction,
            status=out.status,
            phase=phase,
            createdAt=task.created_at,
            expiresAt=task.expires_at,
            finishedAt=task.finished_at,
            totalStaff=task.total_staff,
            acceptedCount=out.acceptedCount,
            completedCount=out.completedCount,
            liveCrowd=live_crowd,
            crowdLog=log,
            zoneStaff=await self.list_staff(task.zone_id),
            delegatedStaff=out.acceptedStaff,
        )

    async def cancel(self, task_id: UUID) -> TaskOut:
        """Cancel an open or ongoing task. Staff still working on it go back to
        idle (their respondent rows stay as the record), the task ends now, and
        the zone is free again."""
        task = await self._find_task(task_id)
        if task.task_status_id not in (TaskStatusId.OPEN, TaskStatusId.ONGOING):
            raise AppError(
                409, "not_open", "Only open or ongoing tasks can be cancelled."
            )
        rows = await self._session.execute(
            select(TaskRespondent, User)
            .join(User, User.user_id == TaskRespondent.staff_id)
            .where(TaskRespondent.task_id == task_id)
        )
        for respondent, user in rows.all():
            if respondent.task_respondent_status_id != TaskRespondentStatusId.COMPLETED:
                user.user_status_id = UserStatusId.IDLE
        task.task_status_id = TaskStatusId.CLOSED
        task.finished_at = datetime.now(UTC)
        await _free_zone_if_latest(self._session, task)
        await self._session.commit()
        return await self._task_out(task)

    async def get_staff(self, staff_id: UUID) -> StaffOut:
        """Get staff member information."""
        staff = await self._session.get(User, staff_id)
        if staff is None:
            raise AppError(404, "not_found", f"staff {staff_id} not found")
        return _staff_out(staff)

    async def list_staff(self, zone_id: str | None) -> builtins.list[StaffOut]:
        """Active floor staff ordered by name; with zone_id, only that home zone.
        An unknown zone gives []."""
        stmt = (
            select(User)
            .join(Role, Role.role_id == User.role_id)
            .where(Role.name == "floor_staff", User.is_active.is_(True))
        )
        if zone_id is not None:
            stmt = stmt.where(User.zone_id == zone_id)
        users = (await self._session.scalars(stmt.order_by(User.full_name))).all()
        return [_staff_out(u) for u in users]


def _staff_out(user: User) -> StaffOut:
    return StaffOut(
        staffId=user.user_id,
        name=user.full_name,
        status=UserStatusId(user.user_status_id).name.lower(),
        zoneId=user.zone_id,
    )
