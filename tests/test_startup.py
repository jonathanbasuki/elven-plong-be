# tests/test_startup.py
"""Tests for fail-closed security startup settings."""

import pytest
from conftest import TEST_INTERNAL_KEY
from fastapi.testclient import TestClient

from plong_elven.app.api.main import create_app
from plong_elven.core.common.settings import (
    Settings,
    check_security_settings,
    get_settings,
)

GOOD = "k" * 32


def _s(**kw) -> Settings:
    return Settings(_env_file=None, database_url="postgresql+asyncpg://x@y/z", **kw)


@pytest.mark.parametrize(
    "key,secret,msg",
    [
        ("", GOOD, "INTERNAL_API_KEY"),
        ("k" * 31, GOOD, "INTERNAL_API_KEY"),
        (GOOD, "short", "JWT_SECRET"),
    ],
)
def test_refuses_weak_settings_outside_dev(key, secret, msg) -> None:
    with pytest.raises(RuntimeError, match=msg):
        check_security_settings(
            _s(app_env="prod", internal_api_key=key, jwt_secret=secret)
        )


def test_typo_env_is_not_dev() -> None:
    with pytest.raises(RuntimeError):
        check_security_settings(_s(app_env="Dev", internal_api_key="", jwt_secret=""))


def test_dev_allows_blank_settings() -> None:
    check_security_settings(_s(app_env="dev", internal_api_key="", jwt_secret=""))


def test_app_env_defaults_to_prod(monkeypatch) -> None:
    monkeypatch.delenv("APP_ENV")
    assert _s().app_env == "prod"


def test_server_refuses_to_start(monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    get_settings.cache_clear()
    with (
        pytest.raises(RuntimeError, match="INTERNAL_API_KEY"),
        TestClient(create_app()),
    ):
        pass


def test_conftest_env_beats_dotenv() -> None:
    assert get_settings().internal_api_key == TEST_INTERNAL_KEY
    assert get_settings().app_env == "dev"
