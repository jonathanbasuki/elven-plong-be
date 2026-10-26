from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.detections import latest_counts
from plong_elven.db.models import Broadcast, BroadcastResponse, Staff, Zone
from plong_elven.db.session import get_db

router = APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])


class DispatchError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message


class CreateBroadcastRequest(BaseModel):
    zoneId: str
    staffNeeded: int = Field(gt=0)


class StaffRequest(BaseModel):
    staffId: UUID


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
        select(BroadcastResponse, Staff)
        .join(Staff, Staff.staff_id == BroadcastResponse.staff_id)
        .where(BroadcastResponse.broadcast_id == broadcast.broadcast_id)
        .order_by(BroadcastResponse.responded_at)
    )
    accepted_staff = [
        {
            "staffId": str(response.staff_id),
            "name": staff.name,
            "completedAt": response.completed_at,
        }
        for response, staff in accepted_staff_result.all()
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


@router.get("/zones/{zoneId}")
async def get_dispatch_zone(
    zoneId: str,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
    zone = await session.get(Zone, zoneId)
    if zone is None:
        raise _not_found(f"zone {zoneId} not found")

    current_count = latest_counts.get(zoneId, 0)
    if current_count < zone.warning_threshold:
        status = "safe"
    elif current_count < zone.crowded_threshold:
        status = "warning"
    else:
        status = "crowded"
    return {
        "zoneId": zone.zone_id,
        "name": zone.name,
        "currentCount": current_count,
        "status": status,
        "mapImageUrl": zone.map_image_url,
        "polygon": zone.polygon,
    }


@router.get("/broadcasts/current")
async def get_current_broadcast(
    zoneId: str = Query(...),
    session: AsyncSession = Depends(get_db),  # noqa: B008
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
) -> dict:
    staff = await session.get(Staff, staffId)
    if staff is None:
        raise _not_found(f"staff {staffId} not found")
    return {"staffId": str(staff.staff_id), "name": staff.name, "status": staff.status}


@router.post("/broadcasts", status_code=201)
async def create_broadcast(
    payload: CreateBroadcastRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
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
    return await _broadcast_response(session, broadcast)


@router.post("/broadcasts/{broadcastId}/respond")
async def respond_to_broadcast(
    broadcastId: UUID,
    payload: StaffRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    broadcast = await _find_broadcast(session, broadcastId)
    if await _close_if_expired(broadcast, datetime.now(UTC)):
        await session.commit()
        raise DispatchError(409, "expired", "This broadcast has expired.")
    staff = await session.get(Staff, payload.staffId)
    if staff is None:
        raise _not_found(f"staff {payload.staffId} not found")

    existing = await session.get(
        BroadcastResponse, {"broadcast_id": broadcastId, "staff_id": payload.staffId}
    )
    if existing is not None:
        return {"status": "ok"}
    if staff.status == "delegating":
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

    session.add(BroadcastResponse(broadcast_id=broadcastId, staff_id=payload.staffId))
    staff.status = "delegating"
    await session.commit()
    return {"status": "ok"}


@router.post("/broadcasts/{broadcastId}/complete")
async def complete_broadcast(
    broadcastId: UUID,
    payload: StaffRequest,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict[str, str]:
    await _find_broadcast(session, broadcastId)
    staff = await session.get(Staff, payload.staffId)
    if staff is None:
        raise _not_found(f"staff {payload.staffId} not found")
    response = await session.get(
        BroadcastResponse, {"broadcast_id": broadcastId, "staff_id": payload.staffId}
    )
    if response is None:
        raise DispatchError(
            403,
            "not_your_response",
            "This staff member did not accept the broadcast.",
        )
    if response.completed_at is None:
        response.completed_at = datetime.now(UTC)
    staff.status = "idle"
    await session.commit()
    return {"status": "ok"}


@router.post("/broadcasts/{broadcastId}/finish")
async def finish_broadcast(
    broadcastId: UUID,
    session: AsyncSession = Depends(get_db),  # noqa: B008
) -> dict:
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
