"""Build and upload task footage from MediaMTX's rolling MPEG-TS segments."""

import asyncio
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from plong_elven.common.settings import get_settings
from plong_elven.db.models import Task, TaskFootage, TaskStatusId
from plong_elven.db.session import SessionFactory


async def process_task_footage(task_id: UUID) -> None:
    async with SessionFactory() as session:
        task = await session.get(Task, task_id)
        footage = await session.get(TaskFootage, task_id)
        if task is None or footage is None or task.finished_at is None:
            return
        try:
            settings = get_settings()
            start, end = task.created_at, task.finished_at
            root = (
                Path(settings.mediamtx_recordings_dir).resolve()
                / settings.mediamtx_record_path
            )
            segments = [
                p
                for p in root.rglob("*.ts")
                if start.timestamp()
                <= p.stat().st_mtime
                <= end.timestamp() + settings.mediamtx_record_segment_seconds
            ]
            if not segments:
                raise RuntimeError("no MediaMTX segments in task interval")
            segments.sort(key=lambda p: p.stat().st_mtime)
            output = Path("/tmp") / f"{task_id}.mp4"
            concat = output.with_suffix(".txt")
            concat.write_text(
                "\n".join(f"file '{p.resolve()}'" for p in segments), encoding="utf-8"
            )
            await asyncio.to_thread(
                subprocess.run,
                [
                    "ffmpeg",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat),
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    "-movflags",
                    "+faststart",
                    str(output),
                ],
                check=True,
                capture_output=True,
            )
            path = f"tasks/{task_id}.mp4"
            url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{settings.supabase_footage_bucket}/{path}"
            headers = {
                "Authorization": f"Bearer {settings.supabase_service_role_key}",
                "apikey": settings.supabase_service_role_key,
                "Content-Type": "video/mp4",
                "x-upsert": "true",
            }
            async with httpx.AsyncClient() as client:
                for attempt in range(3):
                    try:
                        response = await client.post(
                            url,
                            headers=headers,
                            content=output.read_bytes(),
                            timeout=120,
                        )
                        response.raise_for_status()
                        break
                    except Exception:
                        if attempt == 2:
                            raise
                        await asyncio.sleep(2**attempt)
            footage.status, footage.object_path = "ready", path
            footage.public_url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/public/{settings.supabase_footage_bucket}/{path}"
            footage.updated_at = datetime.now(UTC)
            # Never remove a segment that falls inside another unfinished task.
            active = (
                await session.scalars(
                    select(Task).where(
                        Task.task_status_id.in_(
                            [TaskStatusId.OPEN, TaskStatusId.CLOSED]
                        ),
                        Task.task_id != task_id,
                    )
                )
            ).all()
            for segment in segments:
                mtime = datetime.fromtimestamp(segment.stat().st_mtime, UTC)
                if not any(
                    other.created_at <= mtime
                    and (other.finished_at is None or mtime <= other.finished_at)
                    for other in active
                ):
                    segment.unlink(missing_ok=True)
        except Exception as exc:  # noqa: BLE001 - mark footage failed for any pipeline error
            footage.status, footage.error = "failed", str(exc)[:1000]
            footage.updated_at = datetime.now(UTC)
        await session.commit()
