"""Ingests per-zone counts from the cctv worker, broadcasts them live over WebSocket,
rolls them up into 5-minute avg/peak history, and alerts managers on a status change.
latest_counts itself is in-memory only; history rows are persisted.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    Header,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from plong_elven.api.apns import send_push
from plong_elven.api.security import check_internal_key
from plong_elven.common.zone_status import classify
from plong_elven.db.models import Role, User, Zone, ZoneCountHistory
from plong_elven.db.session import get_db

router = APIRouter(tags=["detections"])
_clients: set[WebSocket] = set()
latest_counts: dict[str, int] = {}
latest_count_observed_at: dict[str, datetime] = {}
_last_status: dict[str, str] = {}

_ALERT_MESSAGES = {
    "warning": (
        "Crowd warning",
        "{zone} is getting busy ({count} people detected).",
    ),
    "crowded": (
        "Zone crowded — act now",
        "{zone} is crowded ({count} people). Dispatch staff immediately.",
    ),
}

BUCKET_SECONDS = 300  # 5 minutes


@dataclass
class _Bucket:
    start: datetime
    sum_count: int = 0
    n: int = 0
    peak: int = 0

    def add(self, count: int) -> None:
        self.sum_count += count
        self.n += 1
        self.peak = max(self.peak, count)

    @property
    def avg(self) -> float:
        return self.sum_count / self.n if self.n else 0.0


_buckets: dict[str, _Bucket] = {}


def _bucket_start(now: datetime) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % BUCKET_SECONDS, tz=UTC)


class Detection(BaseModel):
    camera_id: str
    counts: dict[str, int]


async def _record_count_sample(
    session: AsyncSession, zone_id: str, count: int, now: datetime
) -> None:
    """Accumulates one sample into the zone's current 5-minute window, and flushes
    the previous window to zone_count_history once a sample from the next window
    arrives (there's no timer — a window with no further detections after it closes
    just sits in memory until the next sample for that zone rolls it over)."""
    start = _bucket_start(now)
    bucket = _buckets.get(zone_id)
    if bucket is not None and bucket.start != start:
        if bucket.n > 0:
            session.add(
                ZoneCountHistory(
                    zone_id=zone_id,
                    bucket_start=bucket.start,
                    avg_count=round(bucket.avg, 2),
                    peak_count=bucket.peak,
                )
            )
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()  # already flushed; nothing lost
        bucket = None
    if bucket is None:
        bucket = _Bucket(start=start)
        _buckets[zone_id] = bucket
    bucket.add(count)


async def _process_zone_update(session: AsyncSession, counts: dict[str, int]) -> None:
    """Runs once per detection post: records this sample into the zone's 5-minute
    history bucket, then pushes ops_manager devices only when the zone newly crosses
    into warning or crowded — not on every post, which would spam a push every
    second while the zone stays busy."""
    now = datetime.now(UTC)
    for zone_id, count in counts.items():
        zone = await session.get(Zone, zone_id)
        if zone is None:
            continue

        await _record_count_sample(session, zone_id, count, now)

        status = classify(count, zone.warning_threshold, zone.crowded_threshold)
        previous = _last_status.get(zone_id)
        _last_status[zone_id] = status
        if status == previous or status not in _ALERT_MESSAGES:
            continue

        manager_tokens = await session.scalars(
            select(User.push_token)
            .join(Role, Role.id == User.role_id)
            .where(Role.name == "ops_manager", User.push_token.is_not(None))
        )
        title, body_template = _ALERT_MESSAGES[status]
        await send_push(
            list(manager_tokens.all()),
            title,
            body_template.format(zone=zone.name, count=count),
        )


@router.post("/detections")
async def post_detection(
    detection: Detection,
    background_tasks: BackgroundTasks,
    session: AsyncSession = Depends(get_db),  # noqa: B008
    x_internal_api_key: str | None = Header(default=None),
) -> dict:
    check_internal_key(x_internal_api_key)
    latest_counts.update(detection.counts)
    observed_at = datetime.now(UTC)
    for zone_id in detection.counts:
        latest_count_observed_at[zone_id] = observed_at
    background_tasks.add_task(_process_zone_update, session, detection.counts)
    dead = set()
    for ws in _clients:
        try:
            await ws.send_json(detection.model_dump())
        except (WebSocketDisconnect, RuntimeError):
            dead.add(ws)
    _clients.difference_update(dead)
    return {"status": "ok"}


@router.websocket("/ws/counts")
async def ws_counts(websocket: WebSocket) -> None:
    await websocket.accept()
    _clients.add(websocket)
    try:
        while True:
            await (
                websocket.receive_text()
            )  # keep the connection open; payload is ignored
    except WebSocketDisconnect:
        pass
    finally:
        _clients.discard(websocket)
