from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.apns import send_push
from plong_elven.api.auth import get_current_user
from plong_elven.api.detections import latest_counts
from plong_elven.common.zone_status import classify
from plong_elven.db.models import Broadcast, BroadcastResponse, Role, User, Zone
from plong_elven.db.session import get_db

router = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])


def _require_role(user: User, role: str) -> None:
    if user.role.name != role:
        raise DispatchError(403, "wrong_role", f"This action requires the {role} role.")


class DispatchError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class CreateBroadcastRequest(BaseModel):
    zoneId: str
    staffNeeded: int = Field(gt=0)


def _not_found(message: str) -> DispatchError:
    return DispatchError(404, "not_found", message)


async def _find_broadcast(session: AsyncSession, broadcast_id: UUID) -> Broadcast:
    broadcast = await session.get(Broadcast, broadcast_id)
    if broadcast is None:
        raise _not_found(f"broadcast {broadcast_id} not found")
    return broadcast


async def _close_if_expired(broadcast: Broadcast, now: datetime) -> bool:
    if broadcast.status == "open" and now > broadcast.expires_at:
        broadcast.status = "closed"
        return True
    return False


async def _broadcast_response(session: AsyncSession, broadcast: Broadcast) -> dict:
    accepted_count = await session.scalar(
        select(func.count())
        .select_from(BroadcastResponse)
        .where(BroadcastResponse.broadcast_id == broadcast.broadcast_id)
    )
    completed_count = await session.scalar(
        select(func.count())
        .select_from(BroadcastResponse)
        .where(
            BroadcastResponse.broadcast_id == broadcast.broadcast_id,
            BroadcastResponse.completed_at.is_not(None),
        )
    )
    accepted_staff_result = await session.execute(
        select(BroadcastResponse, User)
        .join(User, User.id == BroadcastResponse.staff_id)
        .where(BroadcastResponse.broadcast_id == broadcast.broadcast_id)
        .order_by(BroadcastResponse.responded_at)
    )
    accepted_staff = [
        {
            "staffId": str(response.staff_id),
            "name": user.full_name,
            "completedAt": response.completed_at,
        }
        for response, user in accepted_staff_result.all()
    ]
    return {
        "broadcastId": str(broadcast.broadcast_id),
        "zoneId": broadcast.zone_id,
        "status": broadcast.status,
        "staffNeeded": broadcast.staff_needed,
        "expiresAt": broadcast.expires_at,
        "acceptedCount": accepted_count or 0,
        "completedCount": completed_count or 0,
        "acceptedStaff": accepted_staff,
    }


def _zone_payload(zone: Zone) -> dict:
    current_count = latest_counts.get(zone.zone_id, 0)
    status = classify(current_count, zone.warning_threshold, zone.crowded_threshold)
    return {
        "zoneId": zone.zone_id,
        "name": zone.name,
        "currentCount": current_count,
        "status": status,
        "mapImageUrl": zone.map_image_url,
        "polygon": zone.polygon,
    }


@router.get("/zones")
async def list_dispatch_zones(
    session: AsyncSession = Depends(get_db),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> list[dict]:
    """Lets the client discover real zone ids instead of hardcoding one — this is a
    single-zone POC, so the app just uses whichever zone comes back first."""
    zones = await session.scalars(select(Zone).order_by(Zone.zone_id))
    return [_zone_payload(zone) for zone in zones.all()]


@router.get("/zones/{zoneId}")
async def get_dispatch_zone(
    zoneId: str,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    zone = await session.get(Zone, zoneId)
    if zone is None:
        raise _not_found(f"zone {zoneId} not found")
    return _zone_payload(zone)


@router.get("/broadcasts/current")
async def get_current_broadcast(
    zoneId: str = Query(...),
    session: AsyncSession = Depends(get_db),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    broadcasts = await session.scalars(
        select(Broadcast)
        .where(Broadcast.zone_id == zoneId)
        .order_by(Broadcast.created_at.desc())
    )
    broadcast = broadcasts.first()
    if broadcast is None:
        raise _not_found(f"no broadcast for zone {zoneId}")

    changed = await _close_if_expired(broadcast, datetime.now(UTC))
    if changed:
        await session.commit()
    return await _broadcast_response(session, broadcast)


@router.get("/staff/{staffId}")
async def get_staff(
    staffId: UUID,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    _user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    staff = await session.get(User, staffId)
    if staff is None:
        raise _not_found(f"staff {staffId} not found")
    return {"staffId": str(staff.id), "name": staff.full_name, "status": staff.status}


@router.post("/broadcasts", status_code=201)
async def create_broadcast(
    payload: CreateBroadcastRequest,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    _require_role(user, "ops_manager")
    zone = await session.get(Zone, payload.zoneId)
    if zone is None:
        raise _not_found(f"zone {payload.zoneId} not found")

    now = datetime.now(UTC)
    open_broadcast = await session.scalar(
        select(Broadcast)
        .where(Broadcast.zone_id == payload.zoneId, Broadcast.status == "open")
        .order_by(Broadcast.created_at.desc())
    )
    if open_broadcast is not None:
        if await _close_if_expired(open_broadcast, now):
            await session.commit()
        else:
            raise DispatchError(
                409,
                "broadcast_open",
                "This zone already has an open broadcast.",
            )

    broadcast = Broadcast(
        zone_id=payload.zoneId,
        staff_needed=payload.staffNeeded,
        expires_at=now + timedelta(seconds=20),
    )
    session.add(broadcast)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise DispatchError(
            409, "broadcast_open", "This zone already has an open broadcast."
        ) from None
    await session.refresh(broadcast)

    staff_tokens = await session.scalars(
        select(User.push_token)
        .join(Role, Role.id == User.role_id)
        .where(Role.name == "floor_staff", User.push_token.is_not(None))
    )
    background_tasks.add_task(
        send_push,
        list(staff_tokens.all()),
        "New broadcast",
        f"Crowd control needed in {zone.name} — tap to accept",
    )

    return await _broadcast_response(session, broadcast)


@router.post("/broadcasts/{broadcastId}/respond")
async def respond_to_broadcast(
    broadcastId: UUID,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    _require_role(user, "floor_staff")
    broadcast = await _find_broadcast(session, broadcastId)
    if await _close_if_expired(broadcast, datetime.now(UTC)):
        await session.commit()
        raise DispatchError(409, "expired", "This broadcast has expired.")

    existing = await session.get(
        BroadcastResponse, {"broadcast_id": broadcastId, "staff_id": user.id}
    )
    if existing is not None:
        return {"status": "ok"}
    if user.status == "delegating":
        raise DispatchError(
            409, "already_delegating", "Staff member is already delegating."
        )
    accepted_count = await session.scalar(
        select(func.count())
        .select_from(BroadcastResponse)
        .where(BroadcastResponse.broadcast_id == broadcastId)
    )
    if accepted_count >= broadcast.staff_needed:
        raise DispatchError(
            409,
            "slots_full",
            "This broadcast already has enough staff.",
        )

    session.add(BroadcastResponse(broadcast_id=broadcastId, staff_id=user.id))
    user.status = "delegating"
    await session.commit()
    return {"status": "ok"}


@router.post("/broadcasts/{broadcastId}/complete")
async def complete_broadcast(
    broadcastId: UUID,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict[str, str]:
    await _find_broadcast(session, broadcastId)
    response = await session.get(
        BroadcastResponse, {"broadcast_id": broadcastId, "staff_id": user.id}
    )
    if response is None:
        raise DispatchError(
            403,
            "not_your_response",
            "This staff member did not accept the broadcast.",
        )
    if response.completed_at is None:
        response.completed_at = datetime.now(UTC)
    user.status = "idle"
    await session.commit()
    return {"status": "ok"}


@router.post("/broadcasts/{broadcastId}/finish")
async def finish_broadcast(
    broadcastId: UUID,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    user: User = Depends(get_current_user),  # noqa: B008
) -> dict:
    _require_role(user, "ops_manager")
    broadcast = await _find_broadcast(session, broadcastId)
    if await _close_if_expired(broadcast, datetime.now(UTC)):
        await session.commit()
    accepted_count = await session.scalar(
        select(func.count())
        .select_from(BroadcastResponse)
        .where(BroadcastResponse.broadcast_id == broadcastId)
    )
    completed_count = await session.scalar(
        select(func.count())
        .select_from(BroadcastResponse)
        .where(
            BroadcastResponse.broadcast_id == broadcastId,
            BroadcastResponse.completed_at.is_not(None),
        )
    )
    if accepted_count < 1 or completed_count != accepted_count:
        raise DispatchError(
            409,
            "not_all_done",
            "All accepted staff members must complete before finishing.",
        )
    if broadcast.status != "finished":
        broadcast.status = "finished"
        broadcast.finished_at = datetime.now(UTC)
        await session.commit()
    return {
        "broadcastId": str(broadcast.broadcast_id),
        "status": "finished",
        "finishedAt": broadcast.finished_at,
    }
