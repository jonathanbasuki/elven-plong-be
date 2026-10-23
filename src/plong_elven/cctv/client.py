"""Pushes per-zone counts to the API's ingestion endpoint. Fire-and-forget: a
down/slow API must not stall the detection loop, so request errors are logged, not raised.
"""

import logging

import httpx

logger = logging.getLogger(__name__)


def post_counts(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    camera_id: str,
    counts: dict[str, int],
) -> None:
    try:
        client.post(
            f"{base_url}/detections",
            json={"camera_id": camera_id, "counts": counts},
            headers={"X-Internal-Api-Key": api_key},
            timeout=2.0,
        )
    except httpx.HTTPError as e:
        logger.warning("failed to post counts to %s: %s", base_url, e)


def post_frame(
    client: httpx.Client,
    base_url: str,
    api_key: str,
    camera_id: str,
    jpeg: bytes,
    raw: bool = False,
) -> None:
    """raw=True posts the plain camera frame (no overlay) used for the zone editor's
    'capture frame' step; raw=False posts the annotated frame used for the live stream."""
    path = f"/frames/{camera_id}/raw" if raw else f"/frames/{camera_id}"
    try:
        client.post(
            f"{base_url}{path}",
            content=jpeg,
            headers={"X-Internal-Api-Key": api_key, "Content-Type": "image/jpeg"},
            timeout=2.0,
        )
    except httpx.HTTPError as e:
        logger.warning("failed to post frame to %s: %s", base_url, e)
