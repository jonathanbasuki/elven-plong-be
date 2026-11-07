"""Tests for NotificationService."""

import pytest
from conftest import FakeSessionFactory

from plong_elven.app.services.internal.notification_service import NotificationService


class _TokenSession:
    """A fake session that returns a fixed list of tokens for scalars()."""

    def __init__(self, tokens):
        self.tokens = tokens
        self.statements = []

    async def scalars(self, stmt):
        self.statements.append(stmt)
        tokens = self.tokens
        return type("R", (), {"all": lambda self: tokens})()


@pytest.mark.asyncio
async def test_notify_role_sends_role_tokens_through_sender(fake_push_sender) -> None:
    session = _TokenSession(["tok-1"])
    factory = FakeSessionFactory(session)
    await NotificationService(factory, fake_push_sender).notify_role(
        "ops_manager", "T", "B"
    )
    assert fake_push_sender.calls == [(["tok-1"], "T", "B")]
    assert factory.opened == 1
    assert "ops_manager" in str(
        session.statements[0].compile(compile_kwargs={"literal_binds": True})
    )
