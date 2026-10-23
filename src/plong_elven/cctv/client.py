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
    client: httpx.Client, base_url: str, api_key: str, camera_id: str, jpeg: bytes
) -> None:
    try:
        client.post(
            f"{base_url}/frames/{camera_id}",
            content=jpeg,
            headers={"X-Internal-Api-Key": api_key, "Content-Type": "image/jpeg"},
            timeout=2.0,
        )
    except httpx.HTTPError as e:
        logger.warning("failed to post frame to %s: %s", base_url, e)
