"""Sends real push notifications to staff iPhones via Apple's APNs HTTP/2 provider
API, authenticated with a token-based .p8 key (no certificates to renew).
Fire-and-forget: a failed/unreachable push must not break broadcast creation.
"""

import logging
import time
from pathlib import Path

import httpx
import jwt

from plong_elven.common.settings import get_settings

logger = logging.getLogger(__name__)

_TOKEN_MAX_AGE = 50 * 60  # Apple allows up to 60 minutes; refresh before that
_cached_jwt: str | None = None
_cached_jwt_issued_at: float = 0.0


def _signing_token() -> str:
    """The ES256 JWT APNs wants in the Authorization header. Cached because Apple
    rate-limits how often you're allowed to mint a new one."""
    global _cached_jwt, _cached_jwt_issued_at
    now = time.monotonic()
    if _cached_jwt is not None and now - _cached_jwt_issued_at < _TOKEN_MAX_AGE:
        return _cached_jwt

    settings = get_settings()
    private_key = Path(settings.apns_key_path).read_text()
    _cached_jwt = jwt.encode(
        {"iss": settings.apns_team_id, "iat": int(time.time())},
        private_key,
        algorithm="ES256",
        headers={"kid": settings.apns_key_id},
    )
    _cached_jwt_issued_at = now
    return _cached_jwt


async def send_push(device_tokens: list[str], title: str, body: str) -> None:
    """Sends the same alert to every device token, concurrently. Configuration or
    per-device failures are logged, never raised — nothing here should block the
    caller (e.g. broadcast creation)."""
    settings = get_settings()
    if (
        not settings.apns_key_path
        or not settings.apns_team_id
        or not settings.apns_key_id
    ):
        logger.warning(
            "APNs not configured, skipping push to %d device(s)", len(device_tokens)
        )
        return
    if not device_tokens:
        return

    try:
        signing_token = _signing_token()
    except OSError as e:
        logger.warning("could not load APNs signing key, skipping push: %s", e)
        return

    host = (
        "api.sandbox.push.apple.com"
        if settings.apns_use_sandbox
        else "api.push.apple.com"
    )
    headers = {
        "authorization": f"bearer {signing_token}",
        "apns-topic": settings.apns_topic,
        "apns-push-type": "alert",
    }
    payload = {"aps": {"alert": {"title": title, "body": body}, "sound": "default"}}

    async with httpx.AsyncClient(http2=True, timeout=5.0) as client:
        for token in device_tokens:
            try:
                response = await client.post(
                    f"https://{host}/3/device/{token}", json=payload, headers=headers
                )
                if response.status_code != 200:
                    logger.warning(
                        "push to %s...%s failed: %s %s",
                        token[:8],
                        token[-4:],
                        response.status_code,
                        response.text,
                    )
            except httpx.HTTPError as e:
                logger.warning("push to %s...%s failed: %s", token[:8], token[-4:], e)
