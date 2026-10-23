"""Shared internal-service auth: a single pre-shared key, checked against INTERNAL_API_KEY.
Blank (unset) key means auth is off, matching .env.example's default for local dev.
"""

import os

from fastapi import HTTPException


def check_internal_key(key: str | None) -> None:
    expected = os.environ.get("INTERNAL_API_KEY", "")
    if expected and key != expected:
        raise HTTPException(status_code=401, detail="invalid internal api key")
