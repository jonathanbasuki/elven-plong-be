# API Contract Rework, Stage 1 (Layering, no contract change) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split today's fat routers into router → service → session layers, with two SOLID seams (`PushSender`, `AlertRule`), without changing any URL, status code or JSON field.

**Architecture:** Routers move to `api/routers/` and only parse input, call one service, and return the existing dict/model. Business rules move to `services/` (no `fastapi` import). Module globals move into one `LiveState` instance injected with `Depends`. Background work opens its own session from an injected session factory.

**Tech Stack:** Python 3.12, FastAPI 0.159.1, Pydantic 2.14.1, SQLAlchemy async + asyncpg, Alembic, pytest 9 + pytest-asyncio 1.4 (strict mode, no config in `pyproject.toml`), ruff, uv.

**Spec:** `docs/superpowers/specs/2026-11-03-api-contract-rework-design.md` §5 (Stage 1). Index: `docs/superpowers/plans/2026-11-03-api-contract-rework-plan.md`.

All commands run from the repo root:
`cd "/Users/diuta/Uta's Works/project 7/plong/elven-plong-be"`.
Run tests with `uv run pytest` (as `Makefile` does), **never** `uv run --env-file .env pytest`. The local `.env` sets `INTERNAL_API_KEY`, and with it 10 tests fail (verified 2026-11-03).

## Global Constraints

- No URL, status code or JSON field changes in Stage 1. Imports in existing tests may change; their assertions may not. (design §5)
- Services must not import `fastapi`. (design §2)
- No repository layer. Use `AsyncSession` directly. (design §2)
- No interface per service. Only `PushSender` and `AlertRule`. (design §2, §5 item 3)
- Broadcast rules stay identical: slot limit, one open broadcast per zone, expiry after 20 s. (design §2 anti-goals)
- Reuse `classify()`, `get_current_user`, `LatestUploader` and the cctv code. Do not rewrite them. (design §3)
- **No `git commit` anywhere.** Every task ends with: *leave in working tree; user commits.*

## Review Focus

1. **Two staff accept the last slot at the same moment.** Exactly one wins, and the other gets 409 `slots_full`. The only real proof is Task 1.9 against Postgres. Without the lock the race is timing-dependent, so a green run does not prove the lock exists. Task 1.8 also has a static check that the statement carries `FOR UPDATE`.
2. **A background alert after the request session closed.** The background job must never touch the request session. Task 1.7 test `test_background_job_uses_its_own_session` makes the request session raise if it is used.
3. **Non-ASCII `X-Internal-Api-Key` header.** `hmac.compare_digest` on two non-ASCII `str` values raises `TypeError` (a 500). It must stay a 401. Task 1.2 test `test_non_ascii_key_is_401_not_500`.
4. **Hostile `camera_id` on `/ui/{camera_id}`.** `<script>` and over-long ids get a 422, and the page escapes the id in both HTML and JS contexts. Task 1.6 tests.
5. **Role check now runs before body validation.** A `floor_staff` user who POSTs an *invalid* body to `/api/v1/dispatch/broadcasts` got 422 before and now gets 403. FastAPI resolves dependencies before body validation (verified with a probe on 0.159.1). No existing test covers this, and it is the only status change. The PR description must state it. Task 1.8 test `test_role_check_runs_before_body_validation` pins the new behaviour.

---

## Files owned per task (Stage 1)

A file listed under one task must not be edited by a task running at the same time. Tasks are serial unless marked parallel.

| Task | Creates | Modifies / deletes | Can run in parallel with |
|---|---|---|---|
| 1.1 Skeleton + moves | `api/routers/__init__.py`, `api/routers/{auth,dispatch,dispatch_zones,detections,stream,zones,ui}.py` (moved), `api/deps.py`, `services/__init__.py`, `services/errors.py`, `services/apns_sender.py` (moved), `state/__init__.py`, `state/live_state.py`, `schemas/__init__.py`, `schemas/{auth,dispatch,detections,layout}.py`, `tests/conftest.py`, `tests/test_route_table.py`, `tests/test_live_state.py` | deletes `api/{auth,dispatch,detections,stream,zones,ui,apns}.py`; modifies `api/main.py`, `tests/test_dispatch.py` (imports only) | none (first) |
| 1.2 Constant-time key | `tests/test_security.py` | `api/security.py` | 1.3, 1.5, 1.6 |
| 1.3 PushSender seam | `services/ports.py`, `services/notification_service.py`, `tests/test_push_sender_contract.py`, `tests/test_notification_service.py` | `services/apns_sender.py`, `tests/conftest.py` | 1.2, 1.5, 1.6 |
| 1.4 AlertRule seam | `services/alert_service.py`, `tests/test_alert_service.py` | none | 1.5, 1.6 (needs 1.3 done) |
| 1.5 AuthService | `services/auth_service.py`, `tests/test_auth_service.py` | `api/routers/auth.py`, `api/deps.py` | 1.2, 1.3, 1.4, 1.6 |
| 1.6 CameraService + XSS | `services/camera_service.py`, `schemas/common.py`, `tests/test_camera_service.py` | `api/routers/{stream,zones,ui}.py`, `tests/test_ui.py` | 1.2, 1.3, 1.4, 1.5 |
| 1.7 CrowdService + session fix | `services/crowd_service.py`, `tests/test_crowd_service.py` | `api/routers/{detections,dispatch_zones}.py`, `api/deps.py`, `tests/test_detections.py`, `tests/test_dispatch.py` | none (needs 1.4, 1.5) |
| 1.8 BroadcastService + lock | `services/broadcast_service.py`, `tests/test_broadcast_service.py` | `api/routers/dispatch.py`, `api/deps.py` | none (needs 1.7) |
| 1.9 Postgres race test + CI | `tests/test_broadcast_race_pg.py` | `.github/workflows/ci.yml` | none (needs 1.8) |
| 1.10 Stage 1 gate | none | none | none (last) |

All `src` paths are under `src/plong_elven/`.

Waves for delegation:
- Wave A: 1.1.
- Wave B (parallel): 1.2, 1.3, 1.5, 1.6.
- Wave C: 1.4 (it may start as soon as 1.3 is done, alongside any of 1.5 and 1.6 still running).
- Then serial: 1.7, 1.8, 1.9, 1.10.

---

### Task 1.1: Package skeleton and pure moves

Behaviour-identical file moves only. No logic change.

**Files:**
- Create: `tests/test_route_table.py` (step 1, **before** any move), `tests/conftest.py`, `tests/test_live_state.py`
- Move (`mv`, not `git mv`): `api/auth.py`→`api/routers/auth.py`, `api/detections.py`→`api/routers/detections.py`, `api/stream.py`→`api/routers/stream.py`, `api/zones.py`→`api/routers/zones.py`, `api/ui.py`→`api/routers/ui.py`, `api/apns.py`→`services/apns_sender.py`
- Split: `api/dispatch.py` → `api/routers/dispatch.py` (broadcasts + staff, lines 162–353) and `api/routers/dispatch_zones.py` (zones + history, lines 103–159, plus `_zone_payload`). Both use `APIRouter(prefix="/api/v1/dispatch", tags=["dispatch"])`.
- Create: `services/errors.py`, `state/live_state.py`, `api/deps.py`, `schemas/{auth,dispatch,detections,layout}.py`, empty `__init__.py` in `api/routers/`, `services/`, `state/`, `schemas/`
- Modify: `api/main.py:7-29`, `tests/test_dispatch.py:6-7,57,68,77`

**Interfaces (produces):**
- `services/errors.py`: `class AppError(Exception): def __init__(self, status_code: int, code: str, message: str)`, with the same attributes as today's `DispatchError` (`api/dispatch.py:33-37`). `DispatchError` is deleted. `main.py` handler renamed `app_error_handler`, same body.
- `state/live_state.py`: `@dataclass class Bucket` (moved verbatim from `_Bucket`, `api/detections.py:58-71`); `class LiveState` with plain attributes `counts: dict[str, int]`, `observed_at: dict[str, datetime]`, `buckets: dict[str, Bucket]`, `last_status: dict[str, str]`, `last_alert_sent_at: dict[tuple[str, str], datetime]`, `frames: dict[str, bytes]`, `raw_frames: dict[str, bytes]`; module singleton `live_state = LiveState()`; `def get_live_state() -> LiveState: return live_state`. No `fastapi` import.
- `api/deps.py`: `from plong_elven.db.session import get_db` (re-export, same object); `from plong_elven.api.routers.auth import get_current_user` (re-export; Task 1.5 moves the body here); `def require_internal_key(x_internal_api_key: str | None = Header(default=None)) -> None` calling `check_internal_key`; `def legacy_http_error(exc: AppError) -> HTTPException` returning `HTTPException(status_code=exc.status_code, detail=exc.message)` with the one-line comment `# shortcut: keeps Stage 1 {"detail"} shape for auth/camera routes; deleted in Task 2.2`.
- Schemas moved verbatim: `schemas/auth.py` (`LoginRequest`, `RefreshRequest`, `PushTokenRequest`, `UserResponse`, `TokenResponse`, `RefreshResponse`), `schemas/dispatch.py` (`CreateBroadcastRequest`), `schemas/detections.py` (`Detection`), `schemas/layout.py` (`LayoutZone`, which is today's `zones.Zone` renamed so it does not clash with `db.models.Zone`, and `ZonesIn`).
- `tests/conftest.py`: `class FakeSessionFactory` (callable; `__call__` returns an async context manager yielding `self.session`; counts `self.opened`).
- In the moved routers, module globals become `live_state.<attr>` (e.g. `latest_counts.update(...)` → `live_state.counts.update(...)`, `_frames[...]` → `live_state.frames[...]`). Header-key routes (`post_detection`, `post_frame`, `post_raw_frame`, `put_zones`) take `_: None = Depends(require_internal_key)` instead of the inline `Header` + `check_internal_key` call. The `?key=` query routes keep calling `check_internal_key(key)`.

- [x] **Step 1: Write the route-table pin test against today's code**

`app.routes` in FastAPI 0.159.1 holds lazy `_IncludedRouter` objects, not `APIRoute`s (verified). The test therefore reads `app.openapi()["paths"]`.

```python
# tests/test_route_table.py
from plong_elven.api.main import app

STAGE1_ROUTES = {
    ("GET", "/api/v1/auth/profile"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/logout"),
    ("POST", "/api/v1/auth/push-token"),
    ("POST", "/api/v1/auth/refresh"),
    ("GET", "/api/v1/dispatch/broadcasts/current"),
    ("GET", "/api/v1/dispatch/staff/{staffId}"),
    ("GET", "/api/v1/dispatch/zones"),
    ("GET", "/api/v1/dispatch/zones/{zoneId}"),
    ("GET", "/api/v1/dispatch/zones/{zoneId}/history"),
    ("POST", "/api/v1/dispatch/broadcasts"),
    ("POST", "/api/v1/dispatch/broadcasts/{broadcastId}/complete"),
    ("POST", "/api/v1/dispatch/broadcasts/{broadcastId}/finish"),
    ("POST", "/api/v1/dispatch/broadcasts/{broadcastId}/respond"),
    ("POST", "/detections"),
    ("POST", "/frames/{camera_id}"),
    ("POST", "/frames/{camera_id}/raw"),
    ("GET", "/frames/{camera_id}/latest"),
    ("GET", "/stream/{camera_id}"),
    ("GET", "/ui/{camera_id}"),
    ("GET", "/zones/{camera_id}"),
    ("PUT", "/zones/{camera_id}"),
    ("GET", "/healthz"),
    ("GET", "/readyz"),
}


def test_http_route_table_is_unchanged() -> None:
    paths = app.openapi()["paths"]
    actual = {(m.upper(), p) for p, ops in paths.items() for m in ops}
    assert actual == STAGE1_ROUTES
```

(`WS /ws/counts` is not in OpenAPI. `tests/test_detections.py::test_ws_counts_receives_broadcast_detection` already pins it.)

- [x] **Step 2: Run it on the unmodified code**

Run: `uv run pytest tests/test_route_table.py -v`
Expected: `1 passed`. This is a characterization test: it must pass before the move and stay green afterwards.

- [x] **Step 3: Write the LiveState isolation test**

```python
# tests/test_live_state.py
from plong_elven.state.live_state import Bucket, LiveState, get_live_state, live_state


def test_fresh_instances_do_not_share_state() -> None:
    a, b = LiveState(), LiveState()
    a.counts["z1"] = 3
    a.frames["cam1"] = b"x"
    assert b.counts == {} and b.frames == {}


def test_provider_returns_the_singleton() -> None:
    assert get_live_state() is live_state


def test_bucket_average_and_peak() -> None:
    from datetime import UTC, datetime

    bucket = Bucket(start=datetime(2026, 1, 1, tzinfo=UTC))
    for n in (2, 6, 4):
        bucket.add(n)
    assert (bucket.avg, bucket.peak, bucket.n) == (4.0, 6, 3)
```

- [x] **Step 4: Run it to verify it fails**

Run: `uv run pytest tests/test_live_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plong_elven.state'`.

- [x] **Step 5: Do the moves and the split listed under Files and Interfaces**

Update every import (`grep -rn "plong_elven.api\.\(auth\|dispatch\|detections\|stream\|zones\|ui\|apns\)" src tests scripts` must return only `api.routers.*` / `services.apns_sender` hits afterwards). Make these test edits and no others:
- `tests/test_dispatch.py`: `from plong_elven.api.deps import get_current_user`; replace `from plong_elven.api.detections import latest_counts` with `from plong_elven.state.live_state import live_state`; replace each `latest_counts.clear()` with `live_state.counts.clear()`.

- [x] **Step 6: Run the whole suite**

Run: `uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: `27 passed` (23 baseline + 1 route table + 3 live state); `All checks passed!`; ruff format reports no changes needed.

- [x] **Step 7: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.2: Constant-time internal key compare

**Files:**
- Modify: `src/plong_elven/api/security.py:10-13`
- Test: `tests/test_security.py`

**Interfaces:** Produces the same `check_internal_key(key: str | None) -> None`, unchanged for callers. Stage 1 keeps "blank expected key = check off" (design: that flips in Stage 2).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_security.py
import pytest
from fastapi import HTTPException

from plong_elven.api import security


def test_matching_key_passes(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    security.check_internal_key("secret")


@pytest.mark.parametrize("sent", ["wrong", None, "sécret"])
def test_bad_key_is_401(monkeypatch, sent) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "secret")
    with pytest.raises(HTTPException) as exc:
        security.check_internal_key(sent)
    assert exc.value.status_code == 401
    assert exc.value.detail == "invalid internal api key"


def test_non_ascii_key_is_401_not_500(monkeypatch) -> None:
    monkeypatch.setenv("INTERNAL_API_KEY", "sëcret")
    with pytest.raises(HTTPException) as exc:
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


def test_blank_expected_key_still_disables_check_in_stage1(monkeypatch) -> None:
    monkeypatch.delenv("INTERNAL_API_KEY", raising=False)
    security.check_internal_key(None)  # Task 2.1 flips this test
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_security.py -v`
Expected: FAIL in `test_compare_is_constant_time` with `AttributeError: module 'plong_elven.api.security' has no attribute 'hmac'`.

- [ ] **Step 3: Implement**

`import hmac`. Compare `(key or "").encode()` with `expected.encode()` via `hmac.compare_digest`; keep the `if expected and ...` guard. Encoding to bytes is what makes non-ASCII safe. Update the module docstring to say the compare is constant-time.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_security.py tests/test_detections.py tests/test_stream.py tests/test_zones_api.py -q`
Expected: all pass.

- [ ] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.3: `PushSender` seam and `NotificationService`

**Files:**
- Create: `src/plong_elven/services/ports.py`, `src/plong_elven/services/notification_service.py`
- Modify: `src/plong_elven/services/apns_sender.py` (append class), `tests/conftest.py` (append `FakePushSender` + fixtures)
- Test: `tests/test_push_sender_contract.py`, `tests/test_notification_service.py`

**Interfaces (produces):**
- `services/ports.py`:
  - `@runtime_checkable class PushSender(Protocol): async def send(self, tokens: list[str], title: str, body: str) -> None`
  - `@dataclass(frozen=True) class Alert: key: str; cooldown: timedelta | None; audience_role: str; title: str; body: str`
  - `class AlertRule(Protocol): def evaluate(self, zone: Zone, count: int, previous_status: str | None) -> Alert | None` (`Zone` = `plong_elven.db.models.Zone`)
  - Task 1.3 owns this whole file so that Task 1.4 does not edit it.
- `services/apns_sender.py`: `class ApnsPushSender: async def send(self, tokens, title, body) -> None` that awaits the existing module function `send_push(tokens, title, body)` (unchanged).
- `services/notification_service.py`: `class NotificationService: __init__(self, session_factory: async_sessionmaker[AsyncSession], sender: PushSender)`; `async def notify_role(self, role_name: str, title: str, body: str) -> None`. It opens its **own** session from the factory, runs today's token query (`select(User.push_token).join(Role, Role.id == User.role_id).where(Role.name == role_name, User.push_token.is_not(None))`, from `api/dispatch.py:240-244` / `api/detections.py:146-150`), then awaits `sender.send(list(tokens), title, body)`. A notify-by-user method is skipped because nothing calls it (YAGNI).
- `tests/conftest.py`: `class FakePushSender` with `calls: list[tuple[list[str], str, str]]`; fixture `fake_push_sender`.

- [ ] **Step 1: Write the contract test (same cases, both implementations)**

```python
# tests/test_push_sender_contract.py
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
        apns_sender.httpx, "AsyncClient", lambda **kw: real_client(transport=transport)
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
    assert [r.url.path for r in apns_requests] == ["/3/device/tok-a", "/3/device/tok-b"]
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
```

```python
# tests/test_notification_service.py
import pytest

from conftest import FakeSessionFactory
from plong_elven.services.notification_service import NotificationService


class _TokenSession:
    def __init__(self, tokens):
        self.tokens, self.statements = tokens, []

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
```

(`from conftest import ...` works because pytest's default `prepend` import mode puts `tests/` on `sys.path`.)

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_push_sender_contract.py tests/test_notification_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'ApnsPushSender'`.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_push_sender_contract.py tests/test_notification_service.py -v && uv run pytest -q`
Expected: new tests pass (10 cases); full suite green.

- [ ] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.4: `AlertRule` seam and `AlertService`

**Files:**
- Create: `src/plong_elven/services/alert_service.py`
- Test: `tests/test_alert_service.py`

**Interfaces:**
- Consumes: `Alert`, `AlertRule` (Task 1.3 `ports.py`); `NotificationService.notify_role` (Task 1.3); `LiveState.last_status`, `LiveState.last_alert_sent_at` (Task 1.1); `classify` (`common/zone_status.py`).
- Produces:
  - `class StatusChangeRule: __init__(self, status: str, cooldown: timedelta | None, audience_role: str, title: str, body_template: str)`; `evaluate(zone, count, previous_status) -> Alert | None`. It returns `Alert(key=status, ...)` only when `classify(count, zone.warning_threshold, zone.crowded_threshold) == self.status != previous_status`, with `body_template.format(zone=zone.name, count=count)`. (`title`/`body_template` are added to the design's three params because the copy must live in the rule. Design §7 leaves names to the builder.)
  - `def default_alert_rules() -> list[AlertRule]` returning two instances that copy `api/detections.py:35-52` verbatim: `("warning", 5 min, "ops_manager", "Crowd warning", "{zone} is getting busy ({count} people detected).")` and `("crowded", 5 min, "ops_manager", "Zone crowded — act now", "{zone} is crowded ({count} people). Dispatch staff immediately.")`. Move the cooldown comment block (`api/detections.py:45-50`) above it.
  - `class AlertService: __init__(self, notifier: NotificationService, rules: list[AlertRule], live: LiveState)`; `async def on_count(self, zone: Zone, count: int, now: datetime) -> None`. Algorithm, preserving `api/detections.py:131-144`: read `previous = live.last_status.get(zone_id)`, always store the new classified status, then for each rule's non-`None` alert skip it if `alert.cooldown` is set and `now - live.last_alert_sent_at[(zone_id, alert.key)] < alert.cooldown`, otherwise store `now` and `await notifier.notify_role(alert.audience_role, alert.title, alert.body)`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_alert_service.py
from datetime import UTC, datetime, timedelta

import pytest

from conftest import FakeSessionFactory
from plong_elven.db.models import Zone
from plong_elven.services.alert_service import (
    AlertService,
    StatusChangeRule,
    default_alert_rules,
)
from plong_elven.services.notification_service import NotificationService
from plong_elven.services.ports import Alert
from plong_elven.state.live_state import LiveState

T0 = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
ZONE = Zone(
    zone_id="z1",
    name="Main Lobby",
    map_image_url="u",
    polygon=[],
    warning_threshold=5,
    crowded_threshold=10,
)


class _Tokens:
    async def scalars(self, stmt):
        return type("R", (), {"all": lambda self: ["tok-mgr"]})()


def _service(sender, rules=None, live=None):
    notifier = NotificationService(FakeSessionFactory(_Tokens()), sender)
    return AlertService(notifier, rules or default_alert_rules(), live or LiveState())


@pytest.mark.asyncio
async def test_entering_warning_pushes_managers(fake_push_sender) -> None:
    await _service(fake_push_sender).on_count(ZONE, 7, T0)
    assert fake_push_sender.calls == [
        (
            ["tok-mgr"],
            "Crowd warning",
            "Main Lobby is getting busy (7 people detected).",
        )
    ]


@pytest.mark.asyncio
async def test_same_status_does_not_repeat(fake_push_sender) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 7, T0)
    await svc.on_count(ZONE, 8, T0 + timedelta(seconds=1))
    assert len(fake_push_sender.calls) == 1


@pytest.mark.asyncio
async def test_flapping_back_into_warning_within_cooldown_is_suppressed(
    fake_push_sender,
) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 7, T0)
    await svc.on_count(ZONE, 2, T0 + timedelta(seconds=1))
    await svc.on_count(ZONE, 7, T0 + timedelta(minutes=4))
    assert len(fake_push_sender.calls) == 1
    await svc.on_count(ZONE, 2, T0 + timedelta(minutes=5))
    await svc.on_count(ZONE, 7, T0 + timedelta(minutes=5, seconds=1))
    assert len(fake_push_sender.calls) == 2


@pytest.mark.asyncio
async def test_escalation_to_crowded_is_never_throttled(fake_push_sender) -> None:
    svc = _service(fake_push_sender)
    await svc.on_count(ZONE, 7, T0)
    await svc.on_count(ZONE, 12, T0 + timedelta(seconds=1))
    assert fake_push_sender.calls[1][1] == "Zone crowded — act now"


@pytest.mark.asyncio
async def test_extra_rule_fires_without_editing_status_change_rule(
    fake_push_sender,
) -> None:
    class EveryCountRule:
        def evaluate(self, zone, count, previous_status):
            return Alert(
                key="every",
                cooldown=None,
                audience_role="floor_staff",
                title="Count",
                body=f"{zone.name}: {count}",
            )

    svc = _service(fake_push_sender, rules=[*default_alert_rules(), EveryCountRule()])
    await svc.on_count(ZONE, 1, T0)
    assert fake_push_sender.calls == [(["tok-mgr"], "Count", "Main Lobby: 1")]
    assert isinstance(default_alert_rules()[0], StatusChangeRule)
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_alert_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plong_elven.services.alert_service'`.

- [x] **Step 3: Implement the interfaces above.** Nothing calls it yet: `api/routers/detections.py` keeps its own alert code until Task 1.7.

- [x] **Step 4: Run tests**

Run: `uv run pytest tests/test_alert_service.py -v && uv run pytest -q`
Expected: 5 passed; suite green.

- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.5: `AuthService`

**Files:**
- Create: `src/plong_elven/services/auth_service.py`
- Modify: `src/plong_elven/api/routers/auth.py`, `src/plong_elven/api/deps.py` (`get_current_user` body moves here from `routers/auth.py`; `bearer = HTTPBearer(auto_error=False)` moves with it)
- Test: `tests/test_auth_service.py`

**Interfaces (produces):**
- Module functions moved from `api/auth.py:63-131`: `jwt_secret() -> str` (raises `AppError(503, "auth_not_configured", "JWT authentication is not configured with a strong secret")`), `hash_refresh_token(token: str) -> str`, `new_refresh_token() -> tuple[str, str]`, `create_access_token(user: User) -> str`, `user_response(user: User) -> UserResponse`, `verify_password(password: str, password_hash: str) -> bool`, `password_hasher`.
- `class AuthService: __init__(self, session: AsyncSession)`:
  - `async def login(self, email: str, password: str) -> TokenResponse`, which raises `AppError(401, "unauthenticated", "Invalid email or password")`
  - `async def refresh(self, raw_refresh_token: str) -> RefreshResponse`, which raises `AppError(401, "unauthenticated", "Invalid refresh token")`
  - `async def logout(self, raw_refresh_token: str) -> None` (keeps the push-token clearing and its comment, `api/auth.py:247-251`)
  - `async def user_from_access_token(self, token: str) -> User`, which raises `AppError(401, "unauthenticated", "Invalid access token")` for every failure in `api/auth.py:146-161`
  - `async def set_push_token(self, user: User, token: str) -> None`
- `deps.get_current_user(credentials, session) -> User` keeps the same dependency signature (`Depends(bearer)`, `Depends(get_db)`). It raises `HTTPException(401, "Not authenticated")` when the credentials are missing; otherwise it calls `AuthService(session).user_from_access_token` and converts `AppError` via `legacy_http_error`.
- `routers/auth.py`: `def get_auth_service(session: AsyncSession = Depends(get_db)) -> AuthService`. Each route calls one method and catches `AppError` → `raise legacy_http_error(e) from None`. Response models are unchanged.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_auth_service.py
from uuid import uuid4

import jwt
import pytest

from plong_elven.common.settings import get_settings
from plong_elven.db.models import Role, User
from plong_elven.services import auth_service
from plong_elven.services.auth_service import AuthService, create_access_token
from plong_elven.services.errors import AppError

SECRET = "s" * 40


@pytest.fixture
def strong_secret(monkeypatch):
    monkeypatch.setenv("JWT_SECRET", SECRET)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _user() -> User:
    return User(
        id=uuid4(),
        role_id=1,
        role=Role(id=1, name="floor_staff"),
        email="a@b.io",
        password_hash="x",
        full_name="A",
        is_active=True,
        status="idle",
    )


class _UserSession:
    def __init__(self, user):
        self.user = user

    async def execute(self, stmt):
        user = self.user
        return type("R", (), {"scalar_one_or_none": lambda self: user})()


@pytest.mark.asyncio
async def test_access_token_round_trip(strong_secret) -> None:
    user = _user()
    token = create_access_token(user)
    assert await AuthService(_UserSession(user)).user_from_access_token(token) is user


@pytest.mark.asyncio
async def test_non_access_token_type_is_rejected(strong_secret) -> None:
    token = jwt.encode(
        {"sub": str(uuid4()), "type": "refresh"}, SECRET, algorithm="HS256"
    )
    with pytest.raises(AppError) as exc:
        await AuthService(_UserSession(_user())).user_from_access_token(token)
    assert (exc.value.status_code, exc.value.message) == (401, "Invalid access token")


def test_weak_secret_is_503(monkeypatch) -> None:
    monkeypatch.setenv("JWT_SECRET", "short")
    get_settings.cache_clear()
    try:
        with pytest.raises(AppError) as exc:
            auth_service.jwt_secret()
        assert exc.value.status_code == 503
    finally:
        get_settings.cache_clear()
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_auth_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plong_elven.services.auth_service'`.

- [ ] **Step 3: Implement the interfaces above.** `services/auth_service.py` must not import `fastapi`.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_auth_service.py tests/test_dispatch.py -v && uv run pytest -q && grep -n "fastapi" src/plong_elven/services/*.py`
Expected: tests pass; suite green; grep prints nothing.

- [ ] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.6: `CameraService` and the XSS fix

**Files:**
- Create: `src/plong_elven/services/camera_service.py`, `src/plong_elven/schemas/common.py`
- Modify: `src/plong_elven/api/routers/stream.py`, `src/plong_elven/api/routers/zones.py`, `src/plong_elven/api/routers/ui.py`
- Test: `tests/test_camera_service.py`, `tests/test_ui.py` (add tests; keep the existing one)

**Interfaces (produces):**
- `schemas/common.py`: `CAMERA_ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"` (design §6.7).
- `class CameraService: __init__(self, live: LiveState, zones_path: str)`:
  - `put_frame(self, camera_id: str, jpeg: bytes, raw: bool) -> None`
  - `latest_frame(self, camera_id: str, raw: bool) -> bytes`, which raises `AppError(404, "not_found", f"no frames received yet for {camera_id!r}")`
  - `get_layout(self) -> dict` (missing file → `{"anchor": "bottom_center", "zones": []}`)
  - `save_layout(self, anchor: str, zones: list[dict]) -> int`, which raises `AppError(422, "validation_error", <same message as today>)` for a bad anchor and for `ValueError` from `save_config`
- `routers/stream.py`: `def get_camera_service(live: LiveState = Depends(get_live_state)) -> CameraService`, reading `os.environ.get("ZONES_PATH", "zones.json")` per call (tests monkeypatch it). `routers/zones.py` imports this provider. Routers convert `AppError` with `legacy_http_error`, so the JSON stays `{"detail": ...}`. The MJPEG generator stays in the router and reads `live.frames`.
- `routers/ui.py`: `camera_id: str = Path(pattern=CAMERA_ID_PATTERN)`. Render by replacing `"__CAMERA_ID__"` (the quoted JS literal at `api/ui.py:93`) with `json.dumps(camera_id)` first, then the remaining `__CAMERA_ID__` with `html.escape(camera_id)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_ui.py  (append)
import pytest


@pytest.mark.parametrize("bad", ["%3Cscript%3E", "a%22b", "a" * 65])
def test_ui_rejects_unsafe_camera_id(bad) -> None:
    assert TestClient(app).get(f"/ui/{bad}").status_code == 422


def test_ui_accepts_dash_underscore_ids() -> None:
    response = TestClient(app).get("/ui/cam_1-A")
    assert response.status_code == 200
    assert 'const CAMERA_ID = "cam_1-A";' in response.text
```

```python
# tests/test_camera_service.py
import pytest

from plong_elven.services.camera_service import CameraService
from plong_elven.services.errors import AppError
from plong_elven.state.live_state import LiveState


def test_raw_and_annotated_frames_are_separate(tmp_path) -> None:
    svc = CameraService(LiveState(), str(tmp_path / "z.json"))
    svc.put_frame("cam1", b"annotated", raw=False)
    svc.put_frame("cam1", b"raw", raw=True)
    assert svc.latest_frame("cam1", raw=True) == b"raw"
    assert svc.latest_frame("cam1", raw=False) == b"annotated"


def test_missing_frame_is_not_found(tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        CameraService(LiveState(), str(tmp_path / "z.json")).latest_frame(
            "nope", raw=True
        )
    assert exc.value.status_code == 404


def test_bad_anchor_is_validation_error(tmp_path) -> None:
    with pytest.raises(AppError) as exc:
        CameraService(LiveState(), str(tmp_path / "z.json")).save_layout("feet", [])
    assert exc.value.status_code == 422
```

- [ ] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_ui.py tests/test_camera_service.py -v`
Expected: `test_ui_rejects_unsafe_camera_id` FAILs (`assert 200 == 422`); camera service tests fail with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the interfaces above.**

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/test_ui.py tests/test_camera_service.py tests/test_stream.py tests/test_zones_api.py -v && uv run pytest -q`
Expected: all pass, including the unchanged `test_stream.py` and `test_zones_api.py`.

- [ ] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.7: `CrowdService`, injected `LiveState`, background session fix

**Files:**
- Create: `src/plong_elven/services/crowd_service.py`
- Modify: `src/plong_elven/api/routers/detections.py`, `src/plong_elven/api/routers/dispatch_zones.py`, `src/plong_elven/api/deps.py`, `tests/test_detections.py` (setup only), `tests/test_dispatch.py` (setup only)
- Test: `tests/test_crowd_service.py`

**Interfaces:**
- Consumes: `AlertService`, `default_alert_rules` (1.4); `NotificationService` (1.3); `ApnsPushSender` (1.3); `LiveState`, `get_live_state` (1.1).
- Produces:
  - `deps.py`: `def get_session_factory() -> async_sessionmaker[AsyncSession]` (returns `db.session.SessionFactory`); `def get_push_sender() -> PushSender` (returns `ApnsPushSender()`); `def get_alert_rules() -> list[AlertRule]` (returns `default_alert_rules()`); `def get_notification_service(factory = Depends(get_session_factory), sender = Depends(get_push_sender)) -> NotificationService`; `def get_alert_service(notifier = Depends(get_notification_service), rules = Depends(get_alert_rules), live = Depends(get_live_state)) -> AlertService`.
  - `class CrowdService: __init__(self, session: AsyncSession, live: LiveState)`:
    - `record_live(self, counts: dict[str, int], now: datetime) -> None` (updates `live.counts`, `live.observed_at`)
    - `async def record_sample(self, zone_id: str, count: int, now: datetime) -> None` (moved verbatim from `_record_count_sample`, `api/detections.py:84-112`, plus `BUCKET_SECONDS` and `_bucket_start`)
    - `async def process_counts(self, counts: dict[str, int], alerts: AlertService) -> None`: for each zone found by `session.get(Zone, id)`, call `record_sample` and then `alerts.on_count(zone, count, now)`
    - `async def list_zones(self) -> list[dict]`, `async def get_zone(self, zone_id: str) -> dict` (raises `AppError(404, "not_found", f"zone {zone_id} not found")`), `async def history(self, zone_id: str, limit: int) -> list[dict]`. These return today's exact dicts (`_zone_payload` with `currentCount`/`status`, history dicts). They stay dicts: Pydantic would serialize UTC datetimes as `...Z` instead of today's `...+00:00` (verified), which is a JSON change.
  - Module function `async def process_counts_in_new_session(session_factory, live: LiveState, alerts: AlertService, counts: dict[str, int]) -> None`, which opens `async with session_factory() as session` and calls `CrowdService(session, live).process_counts(counts, alerts)`. This is the background task. The request session is never handed to it (design §5 fix).
  - `routers/detections.py::post_detection` takes `live`, `factory`, and `alerts` via `Depends`. It calls `record_live`, schedules `process_counts_in_new_session`, and keeps the WebSocket fan-out (`_clients` stays in the router: it is transport, not business state). It no longer depends on `get_db`.

- [x] **Step 1: Write the failing tests**
    async def get(self, *a, **k):
        raise AssertionError("background job used the request session")


async def _exploding_db():
    yield _ExplodingSession()


def test_background_job_uses_its_own_session(fake_push_sender) -> None:
    factory = FakeSessionFactory(_NoZones())
    live = LiveState()
    app.dependency_overrides.update({
        get_db: _exploding_db, get_session_factory: lambda: factory,
        get_live_state: lambda: live, get_push_sender: lambda: fake_push_sender,
    })
    try:
        response = TestClient(app).post("/detections", json={"camera_id": "cam1", "counts": {"z1": 4}})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 200
    assert factory.opened == 1
    assert live.counts == {"z1": 4} and "z1" in live.observed_at
```

Setup-only edits to existing tests (assertions untouched):
- `tests/test_detections.py`: in both tests that override `get_db`, also set `app.dependency_overrides[get_session_factory] = lambda: FakeSessionFactory(_FakeSession())`.
- `tests/test_dispatch.py`: replace the `live_state.counts.clear()` lines with a fresh `LiveState()` override (`app.dependency_overrides[get_live_state] = lambda: state`), and add `app.dependency_overrides[get_session_factory] = lambda: FakeSessionFactory(FakeSession())` in `test_dispatch_zone_uses_latest_detection_count`.

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_crowd_service.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_push_sender'`.

- [x] **Step 3: Implement the interfaces above.** Delete the moved globals and helpers from `routers/detections.py` (`_ALERT_MESSAGES`, `_ALERT_COOLDOWNS`, `_Bucket`, `_process_zone_update`, `_record_count_sample`). `routers/dispatch_zones.py` routes each call one `CrowdService` method via `def get_crowd_service(session = Depends(get_db), live = Depends(get_live_state)) -> CrowdService`.

- [x] **Step 4: Run tests**

Run: `uv run pytest -q && grep -rn "latest_counts\|_last_status\|_buckets" src/`
Expected: suite green; grep prints nothing.

- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.8: `BroadcastService`, `require_role`, row lock on accept

**Files:**
- Create: `src/plong_elven/services/broadcast_service.py`
- Modify: `src/plong_elven/api/routers/dispatch.py`, `src/plong_elven/api/deps.py`
- Test: `tests/test_broadcast_service.py`

**Interfaces:**
- Consumes: `NotificationService.notify_role` (1.3), `get_notification_service` (1.7), `AppError` (1.1).
- Produces:
  - `deps.require_role(*names: str) -> Callable[..., Awaitable[User]]`: a dependency factory depending on `get_current_user`. It raises `AppError(403, "wrong_role", f"This action requires the {' or '.join(names)} role.")`; for one name the text equals today's message (`api/dispatch.py:29`).
  - `class BroadcastService: __init__(self, session: AsyncSession, notifier: NotificationService)`, with every rule copied from `api/dispatch.py`:
    - `async def current_for_zone(self, zone_id: str) -> dict`
    - `async def create(self, zone_id: str, staff_needed: int, defer: Callable[..., None]) -> dict`. It calls `defer(self._notifier.notify_role, "floor_staff", "New broadcast", f"Crowd control needed in {zone.name} — tap to accept")`. The router passes `background_tasks.add_task` as `defer`, so the service never imports FastAPI and the push still runs after the response with its own session.
    - `async def accept(self, broadcast_id: UUID, user: User) -> None` (route `respond`). Its **first query** is `select(Broadcast).where(Broadcast.broadcast_id == broadcast_id).with_for_update()` through `session.scalar`, replacing `session.get` (design §5 race fix). The rest of `api/dispatch.py:268-297` follows unchanged.
    - `async def complete(self, broadcast_id: UUID, user: User) -> None`
    - `async def finish(self, broadcast_id: UUID) -> dict`
    - `async def get_staff(self, staff_id: UUID) -> dict`
    - These return today's dicts (same datetime-format reason as Task 1.7).
  - Router: `create_broadcast` and `finish_broadcast` use `Depends(require_role("ops_manager"))`; `respond_to_broadcast` uses `Depends(require_role("floor_staff"))`; `complete` keeps `get_current_user` (no role check today).

- [x] **Step 1: Write the failing tests**

```python
# tests/test_broadcast_service.py
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db
from plong_elven.services.broadcast_service import BroadcastService
from plong_elven.services.errors import AppError
from test_dispatch import fake_db, fake_user


class _CapturingSession:
    def __init__(self): self.statements = []
    async def scalar(self, stmt):
        self.statements.append(stmt)
        return None


@pytest.mark.asyncio
async def test_accept_locks_the_broadcast_row_first() -> None:
    session = _CapturingSession()
    with pytest.raises(AppError) as exc:
        await BroadcastService(session, notifier=None).accept(uuid4(), fake_user())
    assert exc.value.status_code == 404
    assert session.statements[0]._for_update_arg is not None


def test_role_check_runs_before_body_validation() -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("floor_staff")
    try:
        response = TestClient(app).post("/api/v1/dispatch/broadcasts", json={"zoneId": "z1", "staffNeeded": 0})
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403  # was 422 before Stage 1; see Review Focus 5
```

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_broadcast_service.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'plong_elven.services.broadcast_service'`.

- [x] **Step 3: Implement the interfaces above.** Delete `_require_role`, `_find_broadcast`, `_close_if_expired` and `_broadcast_response` from the router; they become private methods of the service.

- [x] **Step 4: Run tests**

Run: `uv run pytest -q && grep -n "select\|session\.\(get\|scalar\|execute\)" src/plong_elven/api/routers/*.py`
Expected: suite green; grep prints nothing (no SQL left in any router).

- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 1.9: Postgres concurrency test and CI Postgres service

**Files:**
- Create: `tests/test_broadcast_race_pg.py`
- Modify: `.github/workflows/ci.yml:7-24`

**Interfaces:** Consumes `BroadcastService.accept` (1.8), `NotificationService` (1.3).

- [x] **Step 1: Write the test**

```python
# tests/test_broadcast_race_pg.py
"""Real-Postgres proof of the accept row lock (design §5). Skips without a DB unless
REQUIRE_PG=1 (set in CI), so a broken CI database fails instead of silently skipping."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import delete, func, select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from conftest import FakePushSender
from plong_elven.db.models import Broadcast, BroadcastResponse, User, Zone
from plong_elven.services.broadcast_service import BroadcastService
from plong_elven.services.errors import AppError
from plong_elven.services.notification_service import NotificationService


@pytest.mark.asyncio
async def test_two_staff_racing_for_last_slot_only_one_wins() -> None:
    engine = create_async_engine(os.environ.get("DATABASE_URL", ""), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except (OSError, SQLAlchemyError) as e:
        await engine.dispose()
        if os.environ.get("REQUIRE_PG") == "1":
            raise
        pytest.skip(f"no Postgres reachable: {e}")

    factory = async_sessionmaker(engine, expire_on_commit=False)
    zone_id, tag = f"race-{uuid4().hex[:8]}", uuid4().hex[:8]
    async with factory() as s:
        s.add(
            Zone(
                zone_id=zone_id,
                name="Race",
                map_image_url="u",
                polygon=[],
                warning_threshold=5,
                crowded_threshold=10,
            )
        )
        users = [
            User(
                role_id=1,
                email=f"{tag}-{i}@race.test",
                password_hash="x",
                full_name=f"R{i}",
                is_active=True,
            )
            for i in range(2)
        ]
        s.add_all(users)
        await s.flush()
        b = Broadcast(
            zone_id=zone_id,
            staff_needed=1,
            expires_at=datetime.now(UTC) + timedelta(minutes=1),
        )
        s.add(b)
        await s.commit()
        broadcast_id, user_ids = b.broadcast_id, [u.id for u in users]

    async def accept(user_id):
        async with factory() as s:
            user = await s.get(User, user_id)
            svc = BroadcastService(s, NotificationService(factory, FakePushSender()))
            try:
                await svc.accept(broadcast_id, user)
                return "ok"
            except AppError as e:
                return e.code

    try:
        results = sorted(await asyncio.gather(*(accept(u) for u in user_ids)))
        async with factory() as s:
            rows = await s.scalar(
                select(func.count())
                .select_from(BroadcastResponse)
                .where(BroadcastResponse.broadcast_id == broadcast_id)
            )
        assert results == ["ok", "slots_full"]
        assert rows == 1
    finally:
        async with factory() as s:
            await s.execute(
                delete(BroadcastResponse).where(
                    BroadcastResponse.broadcast_id == broadcast_id
                )
            )
            await s.execute(
                delete(Broadcast).where(Broadcast.broadcast_id == broadcast_id)
            )
            await s.execute(delete(User).where(User.id.in_(user_ids)))
            await s.execute(delete(Zone).where(Zone.zone_id == zone_id))
            await s.commit()
        await engine.dispose()
```

- [x] **Step 2: Run locally**

Run: `uv run pytest tests/test_broadcast_race_pg.py -v -rs`
Expected on this machine: `1 skipped` with `no Postgres reachable` (no Docker or Postgres here, verified 2026-11-03). If a local DB is up (`make db-up && make db-migrate`), expected `1 passed`.

- [x] **Step 3: Add Postgres to CI**

In `.github/workflows/ci.yml` under `jobs.checks`, add the `services.postgres` block below and `REQUIRE_PG: "1"` to `env`. Insert `- run: uv run alembic upgrade head` before `- run: uv run pytest`.

```yaml
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: plong_elven
          POSTGRES_PASSWORD: ci-password
          POSTGRES_DB: plong_elven
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U plong_elven"
          --health-interval 5s --health-timeout 5s --health-retries 10
```

- [x] **Step 4: Validate YAML syntax locally**

Run: `uv run python -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml')); print('ok')"`
Expected: `ok`. If PyYAML is not importable: **[TBD-6]**. In that case, check the file by eye and rely on the first CI run.

- [x] **Step 5: Checkpoint.** Leave in working tree; user commits. The PR must say whether CI ran this test or skipped it (design §5).

---

### Task 1.10: Stage 1 gate

- [ ] **Step 1:** `uv run ruff check . && uv run ruff format --check .` → `All checks passed!`, no files to reformat.
- [ ] **Step 2:** `uv run pytest -q -rs` → all pass; the only skip is `test_broadcast_race_pg` (locally).
- [ ] **Step 3:** `uv run pytest tests/test_route_table.py -v` → passed (no URL changed).
- [ ] **Step 4:** `grep -rn "^from fastapi\|^import fastapi" src/plong_elven/services/ src/plong_elven/state/` → no output.
- [ ] **Step 5:** `git diff --stat -- tests/test_detections.py tests/test_dispatch.py tests/test_stream.py tests/test_zones_api.py tests/test_ui.py tests/test_health.py`. Then review the diff: every changed line must be an import, a dependency-override setup line, or an **added** test. Any edited `assert` fails the gate.
- [ ] **Step 6:** `git status --short`. Report the file list to the user. Leave in working tree; user commits. Stage 2 starts only after the user approves Stage 1.
