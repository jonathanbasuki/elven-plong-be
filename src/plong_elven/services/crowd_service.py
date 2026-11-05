"""Crowd detection service: count ingestion, history buckets and crowd reads.

Doesn't import FastAPI. Reads return Pydantic DTOs from schemas/zones.py.
The zone catalogue (list, one zone) is in ZoneService.
Alert logic is delegated to AlertService.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from plong_elven.db.models import Zone, ZoneLog
from plong_elven.helpers.zone_status import classify
from plong_elven.schemas.zones import CrowdOut, HistoryBucketOut
from plong_elven.services.errors import AppError
from plong_elven.services.internal.alert_service import AlertService
from plong_elven.state.live_state import Bucket, LiveState

BUCKET_SECONDS = 300  # 5 minutes


def _bucket_start(now: datetime) -> datetime:
    """Return the start of the 5-minute bucket containing the given time."""
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % BUCKET_SECONDS, tz=UTC)


class CrowdService:
    """Service for crowd detection data: live counts, history buckets, crowd reads.

    Alert decisions are delegated to AlertService, which was added in Task 1.4.
    This service handles only the count management and persistence.
    """

    def __init__(self, session: AsyncSession, live: LiveState) -> None:
        self._session = session
        self._live = live

    def record_live(self, counts: dict[str, int], now: datetime) -> None:
        """Update live counts and observed_at timestamps.

        This is synchronous and touches only in-memory state — called from the
        request handler before scheduling background work.
        """
        self._live.counts.update(counts)
        for zone_id in counts:
            self._live.observed_at[zone_id] = now

    async def record_sample(self, zone_id: str, count: int, now: datetime) -> None:
        """Accumulate one sample into the zone's current 5-minute window.

        When a sample from a new window arrives, the previous window is flushed
        to zone_logs. There's no timer — a window with no further detections
        after it closes just sits in memory until the next sample for that zone
        rolls it over.
        """
        start = _bucket_start(now)
        bucket = self._live.buckets.get(zone_id)
        if bucket is not None and bucket.start != start:
            if bucket.n > 0:
                self._session.add(
                    ZoneLog(
                        zone_id=zone_id,
                        bucket_start=bucket.start,
                        avg_count=round(bucket.avg, 2),
                        peak_count=bucket.peak,
                    )
                )
                try:
                    await self._session.commit()
                except IntegrityError:
                    await self._session.rollback()  # already flushed; nothing lost
            bucket = None
        if bucket is None:
            bucket = Bucket(start=start)
            self._live.buckets[zone_id] = bucket
        bucket.add(count)

    async def process_counts(
        self, counts: dict[str, int], alerts: AlertService
    ) -> None:
        """Process a batch of zone counts: record samples and trigger alerts.

        For each zone found in the database, records the sample into history
        and calls alerts.on_count to evaluate alert rules.
        """
        now = datetime.now(UTC)
        for zone_id, count in counts.items():
            zone = await self._session.get(Zone, zone_id)
            if zone is None:
                continue
            await self.record_sample(zone_id, count, now)
            await alerts.on_count(zone, count, now)

    async def crowd(self, zone_id: str) -> CrowdOut:
        """Return the zone's live count and status.

        Before the first detection: count 0, status "safe", observedAt None (§6.2).

        Raises:
            AppError(404, "not_found"): If the zone does not exist.
        """
        zone = await self._load_zone(zone_id)
        count = self._live.counts.get(zone_id, 0)
        return CrowdOut(
            zoneId=zone.zone_id,
            count=count,
            status=classify(count, zone.threshold),
            observedAt=self._live.observed_at.get(zone_id),
        )

    async def history(self, zone_id: str, limit: int) -> list[HistoryBucketOut]:
        """Return the most recent 5-minute avg/peak buckets for a zone, newest first.

        An unknown zone has no rows, so it returns [] (unchanged from Stage 1).
        """
        rows = await self._session.scalars(
            select(ZoneLog)
            .where(ZoneLog.zone_id == zone_id)
            .order_by(ZoneLog.bucket_start.desc())
            .limit(limit)
        )
        return [
            HistoryBucketOut(
                bucketStart=row.bucket_start,
                avgCount=row.avg_count,
                peakCount=row.peak_count,
            )
            for row in rows.all()
        ]

    async def _load_zone(self, zone_id: str) -> Zone:
        zone = await self._session.get(Zone, zone_id)
        if zone is None:
            raise AppError(404, "not_found", f"zone {zone_id} not found")
        return zone


async def process_counts_in_new_session(
    session_factory: async_sessionmaker[AsyncSession],
    live: LiveState,
    alerts: AlertService,
    counts: dict[str, int],
) -> None:
    """Background task that opens its own session for count processing.

    This function is scheduled by the camera route's background_tasks.
    It opens a fresh session from the factory so it never depends on the
    request session, which may be closed by the time the background work runs.

    Review Focus #2: The request session is never handed to this function.
    """
    async with session_factory() as session:
        await CrowdService(session, live).process_counts(counts, alerts)
