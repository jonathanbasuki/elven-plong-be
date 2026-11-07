"""Shared internal-service auth: a single pre-shared key, checked against INTERNAL_API_KEY.

The compare is constant-time to prevent timing attacks. A blank expected key
rejects all calls (fail closed), even in dev mode (P2).
"""

import hmac

from plong_elven.app.services.errors import AppError
from plong_elven.core.common.settings import get_settings


def check_internal_key(key: str | None) -> None:
    """Check that the provided key matches the configured INTERNAL_API_KEY.

    Raises AppError 401 if the key is missing, wrong, or if the expected
    key is blank (P2: blank key means reject everything, fail closed).
    """
    expected = get_settings().internal_api_key
    # P2: blank expected key rejects every camera call, even in dev
    if not expected:
        raise AppError(401, "unauthenticated", "invalid internal api key")
    if not hmac.compare_digest((key or "").encode(), expected.encode()):
        raise AppError(401, "unauthenticated", "invalid internal api key")
