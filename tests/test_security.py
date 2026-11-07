# tests/test_security.py
import pytest

from plong_elven.app.api import security
from plong_elven.app.services.errors import AppError


def test_matching_key_passes(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    security.check_internal_key("secret")


@pytest.mark.parametrize("sent", ["wrong", None, "sécret"])
def test_bad_key_is_401(monkeypatch, sent) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    with pytest.raises(AppError) as exc:
        security.check_internal_key(sent)
    assert exc.value.status_code == 401
    assert exc.value.code == "unauthenticated"


def test_non_ascii_key_is_401_not_500(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "sëcret")
    with pytest.raises(AppError) as exc:
        security.check_internal_key("sécret")
    assert exc.value.status_code == 401


def test_compare_is_constant_time(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    calls = []
    real = security.hmac.compare_digest
    monkeypatch.setattr(
        security.hmac, "compare_digest", lambda a, b: calls.append(1) or real(a, b)
    )
    security.check_internal_key("secret")
    assert calls == [1]


def test_blank_expected_key_rejects_everything(monkeypatch) -> None:
    """P2: blank INTERNAL_API_KEY rejects every worker call, even in dev."""
    monkeypatch.setenv("INTERNAL_API_KEY", "")
    with pytest.raises(AppError) as exc:
        security.check_internal_key(None)
    assert exc.value.status_code == 401

    with pytest.raises(AppError) as exc:
        security.check_internal_key("")
    assert exc.value.status_code == 401
