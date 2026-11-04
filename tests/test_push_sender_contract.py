"""Contract tests for PushSender implementations.

These tests verify that both ApnsPushSender and FakePushSender satisfy the
PushSender protocol contract with identical behavior.
"""

from types import SimpleNamespace

import httpx
import pytest

from plong_elven.services import apns_sender
from plong_elven.services.apns_sender import ApnsPushSender
from plong_elven.services.ports import PushSender

APNS_SETTINGS = SimpleNamespace(
    apns_key_path="k.p8",
    apns_team_id="T",
    apns_key_id="K",
    apns_topic="io.plong.app",
    apns_use_sandbox=True,
)


@pytest.fixture
def apns_requests(monkeypatch):
    seen: list[httpx.Request] = []
    real_client = httpx.AsyncClient
    transport = httpx.MockTransport(lambda req: seen.append(req) or httpx.Response(200))
    monkeypatch.setattr(apns_sender, "get_settings", lambda: APNS_SETTINGS)
    monkeypatch.setattr(apns_sender, "_signing_token", lambda: "jwt")
    monkeypatch.setattr(
        apns_sender.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=transport),
    )
    return seen


@pytest.fixture(params=["apns", "fake"])
def sender(request, apns_requests, fake_push_sender):
    return ApnsPushSender() if request.param == "apns" else fake_push_sender


def test_sender_follows_protocol(sender) -> None:
    assert isinstance(sender, PushSender)


@pytest.mark.asyncio
@pytest.mark.parametrize("tokens", [[], ["tok-a", "tok-b"]])
async def test_send_returns_none_and_never_raises(sender, tokens) -> None:
    assert await sender.send(tokens, "Title", "Body") is None


@pytest.mark.asyncio
async def test_apns_posts_one_request_per_token(apns_requests) -> None:
    await ApnsPushSender().send(["tok-a", "tok-b"], "Title", "Body")
    assert [r.url.path for r in apns_requests] == [
        "/3/device/tok-a",
        "/3/device/tok-b",
    ]
    assert apns_requests[0].headers["apns-topic"] == "io.plong.app"


@pytest.mark.asyncio
async def test_apns_failure_is_swallowed(monkeypatch, apns_requests) -> None:
    real_client = httpx.AsyncClient

    def boom(req):
        raise httpx.ConnectError("down")

    monkeypatch.setattr(
        apns_sender.httpx,
        "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(boom)),
    )
    assert await ApnsPushSender().send(["tok-a"], "T", "B") is None


@pytest.mark.asyncio
async def test_fake_records_calls(fake_push_sender) -> None:
    await fake_push_sender.send(["t"], "T", "B")
    assert fake_push_sender.calls == [(["t"], "T", "B")]
