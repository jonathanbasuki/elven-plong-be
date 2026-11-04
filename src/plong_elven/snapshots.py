"""Best-effort broadcast snapshots from MediaMTX recordings.

MediaMTX writes segment files, not a random-access recording API. The processor
    therefore uses the segment nearest the requested wall-clock time.
"""

import asyncio
import logging
from datetime import datetime
from uuid import UUID

import httpx

from plong_elven.api.stream import latest_annotated_frame
from plong_elven.common.settings import get_settings
from plong_elven.db.models import Snapshot
from plong_elven.db.session import SessionFactory

log = logging.getLogger(__name__)


async def process_snapshot(
    broadcast_id: str, kind: str, captured_at: datetime, frame: bytes | None
) -> None:
    async with SessionFactory() as session:
        row = Snapshot(
            broadcast_id=UUID(broadcast_id), kind=kind, captured_at=captured_at
        )
        session.add(row)
        await session.commit()
        try:
            settings = get_settings()
            # The broadcast can be created just before the first worker frame arrives.
            # Give the in-memory stream a short window to receive it.
            for _ in range(25):
                if frame is not None:
                    break
                frame = latest_annotated_frame("cam1")
                if frame is None:
                    await asyncio.sleep(0.2)
            if frame is None:
                raise RuntimeError("no annotated frame available for cam1")
            if not settings.supabase_url or not settings.supabase_service_role_key:
                raise RuntimeError("Supabase snapshot credentials are not configured")
            key = f"{broadcast_id}/{kind}-{row.snapshot_id}.jpg"
            headers = {
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "apikey": settings.supabase_service_role_key,
                "Content-Type": "image/jpeg",
            }
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(
                    f"{settings.supabase_url}/storage/v1/object/{settings.supabase_snapshots_bucket}/{key}",
                    content=frame,
                    headers=headers,
                )
                response.raise_for_status()
            row.object_key = key
            row.public_url = f"{settings.supabase_url}/storage/v1/object/public/{settings.supabase_snapshots_bucket}/{key}"
            row.status = "uploaded"
        except Exception as exc:  # noqa: BLE001 - snapshot failures are non-fatal.
            row.status = "failed"
            row.error = str(exc)[:1000]
            log.warning("snapshot %s failed: %s", row.snapshot_id, exc)
        await session.commit()
