# API Contract Rework, Stage 2 (Contract cutover, admin role, security) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace every old route with the design §6.1 contract (one error shape, camelCase, admin role, tickets, fail-closed startup), delete the old routes, and write `docs/API_V1_MIGRATION.md`.

**Architecture:** This builds on the Stage 1 layout (`plan-stage1.md`). Each domain gets its own router file under `api/routers/`. Services return Pydantic DTOs from `schemas/`. Startup refuses weak secrets outside `APP_ENV=dev`. The browser-only surfaces (MJPEG `<img>`, browser WebSocket) authenticate with short-lived JWT tickets.

**Tech Stack:** Same as Stage 1.

**Spec:** `docs/superpowers/specs/2026-11-03-api-contract-rework-design.md` §6. Stage 1 must be approved first.

All commands run from `cd "/Users/diuta/Uta's Works/project 7/plong/elven-plong-be"` with `uv run pytest` (no `--env-file`).

## Global Constraints

- App routes are prefixed `/api/v1`; worker routes `/internal/v1/cameras/{cameraId}` with header `X-Internal-Api-Key`; dev routes only when `APP_ENV=dev`. (§6.1)
- Every error is `{"error": {"code": "...", "message": "..."}}`. Codes: `unauthenticated` (401, keeps `WWW-Authenticate`), `wrong_role` (403), `not_found` (404), `validation_error` (422), existing 409 codes `broadcast_open`, `expired`, `slots_full`, `already_delegating`, `not_all_done`. (§6.2)
- camelCase for all JSON, including auth: `accessToken`, `refreshToken`, `tokenType`, `expiresIn`, `fullName`, `isActive`. (§6.2)
- Roles: `floor_staff`=1, `ops_manager`=2, `admin`=3. Admin has **no** dispatch powers; roles do not inherit. (§6.3)
- `app_env` defaults to `"prod"`. Outside dev, `INTERNAL_API_KEY` must be ≥ 32 chars and `JWT_SECRET` ≥ 32 chars, or startup fails. (§6.4)
- Frame body ≤ 5 MB; `counts` ≤ 50 entries, each int 0..10000. (§6.4)
- `cameraId` pattern `^[A-Za-z0-9_-]{1,64}$` on every camera route. (§6.7)
- Ticket: JWT, `type="ticket"`, `scope` claim, `expiresIn: 60`, accepted only as `?ticket=` on the stream and WebSocket endpoints and only for its own scope. (§6.4)
- No old route left behind and no old/new duplicate code. (§2 anti-goals)
- Skipped on purpose (name them in the PR): `GET /staff` list, `GET /broadcasts/{id}`, pagination beyond `limit`, threshold fields on zones, APNs 410 cleanup, `users.updated_at`. (§6.1)
- **No `git commit` anywhere.** Every task ends with: *leave in working tree; user commits.*

### Plan calls (decisions this plan makes where the design is silent)

- **P1:** camelCase is produced by naming Pydantic fields in camelCase, as `CreateBroadcastRequest.zoneId` and `PushTokenRequest.pushToken` already do. There is no alias generator. Old snake_case request bodies therefore get a 422 instead of being accepted silently.
- **P2:** A blank `INTERNAL_API_KEY` rejects every worker call, even in dev (fail closed). The dev exception applies only to the *startup* check.
- **P3:** Status codes outside the design list map to `payload_too_large` (413), `auth_not_configured` (503), and `http_error` (anything else, e.g. 405). The existing `not_your_response` (403 on `complete`) is kept.
- **P4:** `verify_ticket` also re-loads the user and requires `is_active` and role `admin`, because tickets can only be minted by an admin.
- **P5:** WebSocket auth opens a short session from the session factory and closes it before `accept()`, so an open socket does not hold a pool connection for its whole life.
- **P6:** Zone keys inside `counts` use the same pattern as `cameraId`, because they also become in-memory dict keys.
- **P7:** The role matrix asserts allowed roles get "not 401/403" rather than 200, because fakes legitimately return 404 for unknown ids.

## Review Focus

1. **`GET /broadcasts?status=open` while an open broadcast is past `expiresAt`.** It must be closed and committed first, then excluded from the `open` filter. Task 2.10 test `test_expired_open_broadcast_is_closed_and_filtered`.
2. **Worker sends 5 MB + 1 byte, 51 zones, a negative count, or a hostile zone key.** It gets 413 or 422, and nothing is stored. Task 2.6 tests `test_frame_over_5mb_is_413`, `test_counts_bounds`.
3. **A ticket reused for another scope or camera, an expired ticket, or an access token passed as a ticket.** Each gets 401. Task 2.4 tests. A stream that is already open keeps running after its ticket expires, because the ticket is checked only at connect. This is documented in Task 2.12, not changed.
4. **Settings cache vs. environment.** `get_settings()` is `lru_cache`d, and pydantic-settings also reads the local `.env`, which has a real `INTERNAL_API_KEY`. Tests must see only conftest values. Task 2.1 test `test_conftest_env_beats_dotenv`.
5. **Datetime format changes when services return Pydantic DTOs.** FastAPI 0.159.1 encodes UTC datetimes in plain dicts as `2026-11-03T19:00:00+00:00`, and Pydantic 2.14.1 encodes them as `2026-11-03T19:00:00Z` (verified). Task 2.10 test `test_datetimes_are_iso8601_utc` pins the `Z` form. Task 2.12 documents it for the iOS developer. **[TBD-7]**

---

## Files owned per task (Stage 2)

`src` paths are under `src/plong_elven/`. `api/main.py`, `tests/conftest.py` and `tests/test_route_table.py` are shared, so every task that touches them is serial.

| Task | Creates | Modifies / deletes | Parallel with |
|---|---|---|---|
| 2.1 Fail-closed startup | `tests/test_startup.py` | `common/settings.py`, `api/security.py`, `api/main.py`, `tests/conftest.py`, `tests/test_security.py`, `tests/test_dispatch.py`, `tests/test_broadcast_service.py`, `tests/test_stream.py`, `tests/test_zones_api.py`, `tests/test_detections.py`, `.github/workflows/ci.yml`, `.env.example` | none |
| 2.2 Error shape | `tests/test_error_shape.py` | `api/main.py`, `api/deps.py`, `api/security.py`, `api/routers/{auth,stream,zones}.py`, `tests/test_security.py` | 2.3 |
| 2.3 Admin role | `alembic/versions/<rev>_add_admin_role.py`, `tests/test_migrations.py` | `scripts/create_user.py`, `scripts/seed_dispatch.py`, `.github/workflows/ci.yml` | 2.2 |
| 2.4 Tickets | `schemas/tickets.py`, `api/routers/tickets.py`, `tests/test_tickets.py` | `services/auth_service.py`, `api/main.py`, `tests/test_route_table.py` | none |
| 2.5 Auth camelCase + `/me` | `api/routers/me.py`, `tests/test_auth_routes.py` | `schemas/auth.py`, `services/auth_service.py`, `api/routers/auth.py`, `api/main.py`, `tests/test_error_shape.py`, `tests/test_route_table.py` | none |
| 2.6 Worker routes + WS + cctv | `api/routers/internal.py`, `api/routers/crowd.py`, `tests/test_internal_routes.py`, `tests/test_crowd_live.py`, `tests/test_cctv_client.py` | `schemas/detections.py`, `cctv/client.py`, `api/main.py`; deletes `api/routers/detections.py`, `tests/test_detections.py`; edits `tests/test_stream.py`, `tests/test_crowd_service.py`, `tests/test_dispatch.py`, `tests/test_route_table.py` | none |
| 2.7 Camera admin routes | `api/routers/cameras.py`, `tests/test_cameras.py` | `api/main.py`, `tests/test_route_table.py`; deletes `api/routers/{stream,zones}.py`, `tests/test_stream.py`, `tests/test_zones_api.py` | none |
| 2.8 Dev page | `api/routers/dev.py`, `tests/test_dev.py` | `api/main.py`, `tests/test_route_table.py`; deletes `api/routers/ui.py`, `tests/test_ui.py` | none |
| 2.9 Zones + crowd reads | `api/routers/zones.py` (new content), `schemas/zones.py`, `tests/test_zones_crowd.py` | `services/crowd_service.py`, `api/routers/crowd.py`, `api/main.py`, `tests/test_route_table.py`; deletes `api/routers/dispatch_zones.py`, zone tests in `tests/test_dispatch.py` | none |
| 2.10 Broadcasts + staff | `api/routers/{broadcasts,staff}.py`, `schemas/broadcasts.py`, `tests/test_broadcasts.py` | `services/broadcast_service.py`, `api/main.py`, `tests/test_broadcast_service.py`, `tests/test_broadcast_race_pg.py`, `tests/test_error_shape.py`, `tests/test_route_table.py`; deletes `api/routers/dispatch.py`, `schemas/dispatch.py`, `tests/test_dispatch.py` | none |
| 2.11 Route auth walker + role matrix | `tests/test_route_auth.py` | `tests/conftest.py` (adds `NullSession`) | 2.12 |
| 2.12 Docs | `docs/API_V1_MIGRATION.md` | `docs/DISPATCH_API.md`, `README.md`, `docs/{SETUP_AUTH,SETUP_CCTV,SETUP_HLS,AUTH_SCHEMA,PUSH_NOTIFICATIONS}.md`, `docs/PROPOSAL_{ARCHITECTURE,API_CONTRACT}.md` | 2.11 |
| 2.13 Final verification | none | none | none |

Waves: 2.1 → {2.2 ‖ 2.3} → 2.4 → 2.5 → 2.6 → 2.7 → 2.8 → 2.9 → 2.10 → {2.11 ‖ 2.12} → 2.13. The 2.4–2.10 chain is serial on purpose: each task edits `api/main.py` and `tests/test_route_table.py`, and this is the risky half of the change.

---

### Task 2.1: Fail-closed settings and startup

**Files:** see table.

**Interfaces (produces):**
- `common/settings.py`: `Settings.app_env: str = "prod"`, `Settings.internal_api_key: str = ""`; `def check_security_settings(s: Settings) -> None` raising `RuntimeError` with `"INTERNAL_API_KEY must be at least 32 characters outside APP_ENV=dev"` or `"JWT_SECRET must be at least 32 characters outside APP_ENV=dev"`. Returns immediately when `s.app_env == "dev"`. Any other value, including typos, is treated as not dev.
- `api/main.py`: `def create_app() -> FastAPI` with a `lifespan` that calls `check_security_settings(get_settings())` before yielding; module-level `app = create_app()`.
- `api/security.py::check_internal_key` now reads `get_settings().internal_api_key` and rejects when the expected key is blank (P2). It still uses `hmac.compare_digest` on bytes.
- `tests/conftest.py`, at the very top before any `plong_elven` import: force `os.environ["APP_ENV"] = "dev"`, `os.environ["INTERNAL_API_KEY"] = TEST_INTERNAL_KEY` (a 40-char constant), `os.environ["JWT_SECRET"] = TEST_JWT_SECRET` (a 40-char constant). Use plain assignment, not `setdefault`, so CI's short `JWT_SECRET` and the local `.env` cannot leak in. Add an autouse fixture that calls `get_settings.cache_clear()` before and after each test, so `monkeypatch.setenv` inside a test still takes effect. Add a fixture `internal_headers -> {"X-Internal-Api-Key": TEST_INTERNAL_KEY}`. Move `FakeSession`, `fake_db` and `fake_user(role_name)` here from `tests/test_dispatch.py`, with `fake_user` using role ids `{"floor_staff": 1, "ops_manager": 2, "admin": 3}`.
- `.github/workflows/ci.yml`: add `APP_ENV: dev` to `env`. `.env.example`: comments above `APP_ENV` and `INTERNAL_API_KEY` say that outside dev the key and secret must be ≥ 32 chars or the API will not start.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_startup.py
import pytest
from fastapi.testclient import TestClient

from conftest import TEST_INTERNAL_KEY
from plong_elven.api.main import create_app
from plong_elven.common.settings import Settings, check_security_settings, get_settings

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
    with pytest.raises(RuntimeError, match="INTERNAL_API_KEY"):
        with TestClient(create_app()):
            pass


def test_conftest_env_beats_dotenv() -> None:
    assert get_settings().internal_api_key == TEST_INTERNAL_KEY
    assert get_settings().app_env == "dev"
```

In `tests/test_security.py`, replace `test_blank_expected_key_still_disables_check_in_stage1` with `test_blank_expected_key_rejects_everything`: `monkeypatch.setenv("INTERNAL_API_KEY", "")`, then `check_internal_key(None)` and `check_internal_key("")` both raise with status 401. Swap `monkeypatch.setenv`-only setups there for the autouse cache clear (no other change needed).

- [x] **Step 2: Run to verify failure**

Run: `uv run pytest tests/test_startup.py -v`
Expected: FAIL with `ImportError: cannot import name 'create_app'`.

- [x] **Step 3: Implement the interfaces above.** Update the tests that post to worker or camera routes without a key so they send `internal_headers`: the frame posts in `tests/test_stream.py`, `put` in `tests/test_zones_api.py`, and the posts in `tests/test_detections.py`. Old routes stay until 2.6/2.7. Update the imports in `tests/test_dispatch.py` and `tests/test_broadcast_service.py` to `from conftest import FakeSession, fake_db, fake_user`.

- [x] **Step 4: Run tests**

Run: `uv run pytest -q`
Expected: all pass.

- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.2: One error shape everywhere

**Interfaces (produces):**
- `api/main.py` registers three handlers inside `create_app`:
  - `AppError`: same body as today, plus a `WWW-Authenticate: Bearer` header when the status is 401.
  - `starlette.exceptions.HTTPException`: maps status to code (401 `unauthenticated`, 403 `wrong_role`, 404 `not_found`, 413 `payload_too_large`, 422 `validation_error`, 503 `auth_not_configured`, else `http_error`); message is `str(exc.detail)`; keeps `exc.headers`, adding `WWW-Authenticate: Bearer` on 401.
  - `RequestValidationError`: 422, code `validation_error`, message `"; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())`.
- `deps.legacy_http_error` and every call to it are deleted. `deps.get_current_user` raises `AppError(401, "unauthenticated", "Not authenticated")` when the credentials are missing and lets the `AppError`s from `AuthService` propagate. `check_internal_key` raises `AppError(401, "unauthenticated", "invalid internal api key")`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_error_shape.py
from fastapi.testclient import TestClient

from conftest import fake_db, fake_user
from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db

PROFILE = "/api/v1/auth/profile"  # Task 2.5 changes to /api/v1/me
CREATE_BROADCAST = (
    "/api/v1/dispatch/broadcasts"  # Task 2.10 changes to /api/v1/broadcasts
)


def _error(response, status, code):
    assert response.status_code == status
    assert set(response.json()) == {"error"}
    assert response.json()["error"]["code"] == code
    assert isinstance(response.json()["error"]["message"], str)


def test_401_shape_and_header() -> None:
    response = TestClient(app).get(PROFILE)
    _error(response, 401, "unauthenticated")
    assert response.headers["www-authenticate"] == "Bearer"


def test_403_shape() -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user("floor_staff")
    try:
        response = TestClient(app).post(
            CREATE_BROADCAST, json={"zoneId": "z1", "staffNeeded": 1}
        )
    finally:
        app.dependency_overrides.clear()
    _error(response, 403, "wrong_role")


def test_404_for_unknown_path() -> None:
    _error(TestClient(app).get("/no-such-path"), 404, "not_found")


def test_422_shape() -> None:
    _error(TestClient(app).post("/api/v1/auth/login", json={}), 422, "validation_error")
```

Update `tests/test_security.py` to expect `AppError` (`.status_code == 401`, `.code == "unauthenticated"`) instead of `HTTPException`.

- [ ] **Step 2: Run** `uv run pytest tests/test_error_shape.py -v`. Expected: FAIL (`KeyError: 'error'` or `assert {'detail'} == {'error'}`).
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** `uv run pytest -q && grep -rn "HTTPException\|legacy_http_error" src/`. Expected: suite green; grep shows only `api/main.py` (the handler import).
- [ ] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.3: Admin role migration and scripts

**Interfaces (produces):** role row `id=3, name='admin'`; `create_user.py --role` choices `("floor_staff", "ops_manager", "admin")`; `seed_dispatch.USERS` gains `("admin@plongelven.io", "Admin", "admin")` and shares the same `PASSWORD`.

- [ ] **Step 1: Generate the revision file**

Run: `uv run alembic revision -m "add admin role"`
Expected: `Generating .../alembic/versions/<rev>_add_admin_role.py ...  done`. `alembic.ini:37` leaves `revision_environment` off, so `env.py` (which needs `DATABASE_URL`) is not run. Set `down_revision = "f67711b4a0bb"`, the current head (`alembic/versions/f67711b4a0bb_add_zone_count_history.py:16`). `upgrade()` uses `op.bulk_insert` in the style of `ad950fffbf32_create_auth_tables.py:39-49` with `[{"id": 3, "name": "admin"}]`. `downgrade()` is `op.execute("DELETE FROM roles WHERE id = 3")`. It fails loudly if admin users exist (FK `RESTRICT`); that is intended.

- [ ] **Step 2: Write the tests**

```python
# tests/test_migrations.py
import importlib.util
import subprocess
import sys
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

ROOT = Path(__file__).resolve().parents[1]


def test_admin_role_migration_is_the_single_head() -> None:
    script = ScriptDirectory.from_config(Config(str(ROOT / "alembic.ini")))
    (head,) = script.get_heads()
    rev = script.get_revision(head)
    assert rev.down_revision == "f67711b4a0bb"
    source = Path(rev.path).read_text()
    assert '"id": 3' in source and '"name": "admin"' in source
    assert "DELETE FROM roles WHERE id = 3" in source


def test_create_user_accepts_admin_role() -> None:
    out = subprocess.run(
        [sys.executable, str(ROOT / "scripts/create_user.py"), "--help"],
        capture_output=True,
        text=True,
        check=True,
        cwd=ROOT,
    ).stdout
    assert "admin" in out


def test_seed_includes_admin() -> None:
    spec = importlib.util.spec_from_file_location(
        "seed", ROOT / "scripts/seed_dispatch.py"
    )
    seed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(seed)
    assert ("admin@plongelven.io", "Admin", "admin") in seed.USERS
```

- [ ] **Step 3: Run** `uv run pytest tests/test_migrations.py -v`. Expected before the script edits: the two script tests FAIL; after them: 3 passed.
- [ ] **Step 4: CI round trip.** In `.github/workflows/ci.yml`, after `uv run alembic upgrade head`, add `- run: uv run alembic downgrade -1 && uv run alembic upgrade head`. This can only be verified in CI (**[TBD-6]**).
- [ ] **Step 5: Checkpoint.** Leave in working tree; user commits. Deployment note for the PR: the dev server needs `alembic upgrade head` before the new build starts (**[TBD-4]**).

---

### Task 2.4: Tickets

**Interfaces:**
- Consumes: `require_role` (1.8), `AuthService` (1.5), `CAMERA_ID_PATTERN` (1.6).
- Produces:
  - `services/auth_service.py`: `TICKET_SECONDS = 60`; `AuthService.mint_ticket(self, user: User, scope: str) -> TicketOut`, a JWT with claims `sub`, `type="ticket"`, `scope`, `iat`, and `exp = now + TICKET_SECONDS`; `async def verify_ticket(self, token: str, scope: str) -> User`, which raises `AppError(401, "unauthenticated", "Invalid ticket")` unless the type is `ticket`, the scope equals `scope`, the user loaded by `session.get(User, id, options=[selectinload(User.role)])` is active, and the role is `admin` (P4).
  - `schemas/tickets.py`: `TicketRequest.scope: str = Field(pattern=r"^(stream:[A-Za-z0-9_-]{1,64}|crowd:live)$")`; `TicketOut(ticket: str, expiresIn: int)`.
  - `api/routers/tickets.py`: `POST /api/v1/tickets`, `Depends(require_role("admin"))`, returns `TicketOut`. Include it in `create_app`; add `("POST", "/api/v1/tickets")` to `tests/test_route_table.py`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_tickets.py
import pytest
from fastapi.testclient import TestClient

from conftest import fake_db, fake_user
from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db
from plong_elven.services import auth_service
from plong_elven.services.auth_service import AuthService, create_access_token
from plong_elven.services.errors import AppError


class _UserById:
    def __init__(self, user):
        self.user = user

    async def get(self, model, key, **kw):
        return self.user if key == self.user.id else None

    async def execute(self, stmt):
        user = self.user
        return type("R", (), {"scalar_one_or_none": lambda self: user})()


@pytest.fixture
def admin():
    return fake_user("admin")


@pytest.mark.asyncio
async def test_ticket_round_trip(admin) -> None:
    svc = AuthService(_UserById(admin))
    ticket = svc.mint_ticket(admin, "stream:cam1")
    assert ticket.expiresIn == 60
    assert await svc.verify_ticket(ticket.ticket, "stream:cam1") is admin


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "minted,expected",
    [
        ("stream:cam1", "crowd:live"),  # wrong scope
        ("stream:cam1", "stream:cam2"),  # wrong camera
    ],
)
async def test_ticket_scope_mismatch_is_401(admin, minted, expected) -> None:
    svc = AuthService(_UserById(admin))
    with pytest.raises(AppError) as exc:
        await svc.verify_ticket(svc.mint_ticket(admin, minted).ticket, expected)
    assert exc.value.status_code == 401


@pytest.mark.asyncio
async def test_expired_ticket_is_401(admin, monkeypatch) -> None:
    monkeypatch.setattr(auth_service, "TICKET_SECONDS", -1)
    svc = AuthService(_UserById(admin))
    with pytest.raises(AppError):
        await svc.verify_ticket(
            svc.mint_ticket(admin, "crowd:live").ticket, "crowd:live"
        )


@pytest.mark.asyncio
async def test_access_token_is_not_a_ticket(admin) -> None:
    with pytest.raises(AppError):
        await AuthService(_UserById(admin)).verify_ticket(
            create_access_token(admin), "crowd:live"
        )


@pytest.mark.asyncio
async def test_ticket_is_not_an_access_token(admin) -> None:
    svc = AuthService(_UserById(admin))
    with pytest.raises(AppError):
        await svc.user_from_access_token(svc.mint_ticket(admin, "crowd:live").ticket)


@pytest.mark.parametrize(
    "role,status", [("admin", 200), ("ops_manager", 403), ("floor_staff", 403)]
)
def test_only_admin_mints(role, status) -> None:
    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user] = lambda: fake_user(role)
    try:
        response = TestClient(app).post(
            "/api/v1/tickets", json={"scope": "stream:cam1"}
        )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == status


@pytest.mark.parametrize("scope", ["stream:", "stream:a/b", "crowd:other", "admin"])
def test_bad_scope_is_422(scope) -> None:
    app.dependency_overrides[get_current_user] = lambda: fake_user("admin")
    try:
        assert (
            TestClient(app).post("/api/v1/tickets", json={"scope": scope}).status_code
            == 422
        )
    finally:
        app.dependency_overrides.clear()
```

- [x] **Step 2: Run** `uv run pytest tests/test_tickets.py -v`. Expected: FAIL (`AttributeError: 'AuthService' object has no attribute 'mint_ticket'`).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q`. Expected: green.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.5: Auth camelCase and `/me`

**Interfaces (produces):**
- `schemas/auth.py` (P1): `RefreshRequest.refreshToken`; `UserResponse(id, email, fullName, role, isActive)`; `TokenResponse(accessToken, refreshToken, tokenType="bearer", expiresIn, user)`; `RefreshResponse(accessToken, refreshToken, tokenType="bearer", expiresIn)`; `PushTokenRequest.pushToken` (unchanged).
- `api/routers/auth.py` keeps only `POST /api/v1/auth/{login,refresh,logout}`. `api/routers/me.py` has `GET /api/v1/me` (any role, returns `UserResponse`) and `PUT /api/v1/me/push-token` (any role, body `PushTokenRequest`, returns `{"status": "ok"}`).
- `tests/test_route_table.py`: remove `GET /api/v1/auth/profile` and `POST /api/v1/auth/push-token`; add `GET /api/v1/me` and `PUT /api/v1/me/push-token`. `tests/test_error_shape.py`: set `PROFILE = "/api/v1/me"`.

- [x] **Step 1: Write the failing tests** (`tests/test_auth_routes.py`):
  - `test_me_returns_camel_case_user`: override `get_current_user` with `fake_user("ops_manager")`. Assert `set(GET /api/v1/me .json()) == {"id", "email", "fullName", "role", "isActive"}` and `role == "ops_manager"`.
  - `test_put_push_token_stores_token`: override `get_db` with `fake_db` and `get_current_user` with a captured user. `PUT /api/v1/me/push-token {"pushToken": "abc"}` → 200 `{"status": "ok"}`, and `user.push_token == "abc"`.
  - `test_old_auth_profile_route_is_gone`: `GET /api/v1/auth/profile` → 404 `not_found`.
  - `test_refresh_rejects_snake_case_body`: `POST /api/v1/auth/refresh {"refresh_token": "x"}` → 422 `validation_error`.
  - `test_token_response_fields_are_camel_case`: `set(TokenResponse.model_fields) == {"accessToken", "refreshToken", "tokenType", "expiresIn", "user"}`.
- [x] **Step 2: Run** `uv run pytest tests/test_auth_routes.py -v`. Expected: FAIL (404 on `/api/v1/me`).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q`. Expected: green.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.6: Worker routes, `WS /api/v1/crowd/live`, cctv client

**Interfaces (produces):**
- `schemas/detections.py`: `ZoneKey = Annotated[str, StringConstraints(pattern=CAMERA_ID_PATTERN)]` (P6); `DetectionIn.counts: dict[ZoneKey, Annotated[int, Field(ge=0, le=10000)]] = Field(max_length=50)`. The old `Detection` with `camera_id` is deleted.
- `api/routers/crowd.py`: module `_clients: set[WebSocket]`; `async def publish_counts(camera_id: str, counts: dict[str, int]) -> None` sends `{"cameraId": camera_id, "counts": counts}` and drops dead sockets (logic from the Stage 1 detections router); `async def ingest_counts(camera_id, counts, background_tasks, live, factory, alerts) -> None` runs `CrowdService.record_live` → `publish_counts` → `background_tasks.add_task(process_counts_in_new_session, ...)`; `@router.websocket("/api/v1/crowd/live")` authenticates **before** `accept()`. If the `authorization` header is `Bearer <t>`, it calls `user_from_access_token`; otherwise it uses `?ticket=` with `verify_ticket(t, "crowd:live")`. Either check runs inside `async with factory() as session` (P5). On failure it calls `websocket.close(code=1008)` and returns.
- `api/routers/internal.py`: `APIRouter(prefix="/internal/v1/cameras/{cameraId}", dependencies=[Depends(require_internal_key)])`. Routes: `POST /detections` (body `DetectionIn`, returns `{"status": "ok"}`) and `POST /frames` + `POST /frames/raw` (returns `{"status": "ok"}`). Each route takes `cameraId: str = Path(pattern=CAMERA_ID_PATTERN)`. Frames use the private `async def _read_capped_body(request: Request, max_bytes: int = 5 * 1024 * 1024) -> bytes`. It raises `AppError(413, "payload_too_large", ...)` when `content-length` is above the cap, and again while streaming `request.stream()` once the running total passes the cap.
- `cctv/client.py` (the only cctv file touched, §6.6): `post_counts` posts to `f"{base_url}/internal/v1/cameras/{camera_id}/detections"` with `json={"counts": counts}`; `post_frame` posts to `.../frames` or `.../frames/raw`. Signatures are unchanged, so `cctv/loop.py:68-80` is untouched.
- Delete `api/routers/detections.py` and `tests/test_detections.py`. In `tests/test_stream.py`, `tests/test_crowd_service.py` and `tests/test_dispatch.py`, change the frame and detection **posts** to the new internal URLs (with `internal_headers`, and with no `camera_id` in the body). `tests/test_route_table.py`: remove `/detections`, `/frames/{camera_id}`, `/frames/{camera_id}/raw`; add the three `/internal/v1/cameras/{cameraId}/...` POSTs.

- [x] **Step 1: Write the failing tests**
  - `tests/test_internal_routes.py`:
    - `test_detection_updates_live_counts_and_publishes`: override `get_live_state` and `get_session_factory` (`FakeSessionFactory` over a no-zone session) and `get_push_sender`. Open `websocket_connect("/api/v1/crowd/live", headers={"Authorization": "Bearer <access token for fake admin>"})` with the factory's session returning that user. POST `{"counts": {"z1": 3}}` to `/internal/v1/cameras/cam1/detections`. Then `ws.receive_json() == {"cameraId": "cam1", "counts": {"z1": 3}}` and `live.counts == {"z1": 3}`.
    - `test_missing_or_wrong_key_is_401` (parametrize: no header, `"wrong"`) → 401 `unauthenticated`.
    - `test_counts_bounds` (parametrize `body, status`): `{"counts": {f"z{i}": 1 for i in range(51)}}` → 422; `{"counts": {"z1": -1}}` → 422; `{"counts": {"z1": 10001}}` → 422; `{"counts": {"a/b": 1}}` → 422; `{"counts": {f"z{i}": 10000 for i in range(50)}}` → 200. For every 422 case, `live.counts` stays empty.
    - `test_frame_exactly_5mb_ok_and_5mb_plus_one_is_413`: post `b"x" * (5 * 1024 * 1024)` → 200; `+ 1` byte → 413 `payload_too_large`; `live.frames` holds only the first.
    - `test_bad_camera_id_is_422`: `/internal/v1/cameras/a%22b/frames` with key → 422.
  - `tests/test_crowd_live.py`:
    - `test_rejects_without_credentials` → `pytest.raises(WebSocketDisconnect)` with `.code == 1008`.
    - `test_rejects_stream_scoped_ticket` → 1008.
    - `test_accepts_crowd_ticket`.
    - `test_accepts_bearer_header`.
  - `tests/test_cctv_client.py`: `httpx.Client(transport=httpx.MockTransport(...))`. Assert `post_counts(client, "http://api", "k", "cam1", {"z1": 2}) is True`, request path `/internal/v1/cameras/cam1/detections`, JSON body `{"counts": {"z1": 2}}`, header `X-Internal-Api-Key: k`. For `post_frame`, assert paths `/internal/v1/cameras/cam1/frames` and `.../frames/raw`.
- [x] **Step 2: Run** `uv run pytest tests/test_internal_routes.py tests/test_crowd_live.py tests/test_cctv_client.py -v`. Expected: FAIL (404s, path mismatch).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q && grep -rn '"/detections"\|/ws/counts\|camera_id": ' src/ tests/`. Expected: green; grep empty.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.7: Camera admin routes

**Interfaces (produces):** `api/routers/cameras.py`, prefix `/api/v1/cameras/{cameraId}`, every route with `cameraId: str = Path(pattern=CAMERA_ID_PATTERN)`:
- `GET /layout` and `PUT /layout` (body `ZonesIn`, returns `{"status": "ok", "zones": n}`), both `Depends(require_role("admin"))`
- `GET /snapshot` (`require_role("admin")`, returns `image/jpeg` raw frame or 404)
- `GET /stream`, authenticated by the dependency `async def _stream_ticket_user(cameraId = Path(pattern=...), ticket: str | None = Query(default=None), session = Depends(get_db)) -> User`. It raises `AppError(401, "unauthenticated", "Missing ticket")` when there is no ticket, otherwise returns `verify_ticket(ticket, f"stream:{cameraId}")`. The MJPEG generator moves over unchanged.

`get_camera_service` moves here. Delete `api/routers/stream.py`, `api/routers/zones.py`, `tests/test_stream.py`, `tests/test_zones_api.py`. `?key=` is gone (§6.4). Route table: remove the five old camera routes and add these five.

- [x] **Step 1: Write the failing tests** (`tests/test_cameras.py`). Port every assertion from the deleted `test_stream.py` and `test_zones_api.py` to the new URLs: raw vs annotated independence, 404 before the first frame, multipart content type, layout round trip, 422 on a bad polygon. Add:
  - `test_layout_requires_admin` (parametrize `ops_manager`, `floor_staff` → 403)
  - `test_get_layout_requires_auth` → 401 (it was open before, §6.4)
  - `test_stream_with_matching_ticket_streams` → 200, multipart, JPEG in the first chunk
  - `test_stream_with_other_camera_ticket_is_401`
  - `test_stream_key_query_param_no_longer_works`: `?key=<TEST_INTERNAL_KEY>` → 401
- [x] **Step 2: Run** `uv run pytest tests/test_cameras.py -v`. Expected: FAIL (404).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q`. Expected: green.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.8: Dev page

**Interfaces (produces):** `api/routers/dev.py`, prefix `/dev/cameras/{cameraId}`, included by `create_app()` **only when `get_settings().app_env == "dev"`**:
- `GET ""` returns the page HTML. **[TBD-1]** The design table says role `admin`, but a browser navigation cannot send a bearer header, and §6.5 puts the login form *on* this page. Default in this plan: the page is served without auth (dev only; it holds no data; `cameraId` validated and escaped as in Task 1.6). **RESOLVED (user, 2026-11-03): confirmed. The page is open in dev only; every API call it makes still needs an admin login.**
- `POST /detections` (`require_role("admin")`, body `DetectionIn`) calls `crowd.ingest_counts(camera_id, ...)`.
- Page JS changes, from today's `api/ui.py:92-252`:
  - A login form calls `POST /api/v1/auth/login` and keeps `accessToken` in a JS variable (never `localStorage`).
  - The stream uses `POST /api/v1/tickets {"scope": "stream:"+CAMERA_ID}`, then `<img src="/api/v1/cameras/${CAMERA_ID}/stream?ticket=...">`.
  - The WebSocket uses a `crowd:live` ticket on `/api/v1/crowd/live?ticket=...` and reads `msg.cameraId`.
  - Snapshot and layout calls use `Authorization: Bearer`.
  - "Set count" posts `{counts}` to `/dev/cameras/${CAMERA_ID}/detections`.
  - Any 401 shows the login form again, and errors display `body.error.message`.
  - Remove `KEY`, `qs` and `authHeaders` entirely.
- Delete `api/routers/ui.py` and `tests/test_ui.py`. Route table: remove `GET /ui/{camera_id}`, add `GET /dev/cameras/{cameraId}` and `POST /dev/cameras/{cameraId}/detections`.

- [x] **Step 1: Write the failing tests** (`tests/test_dev.py`):
  - Port the three Task 1.6 page tests to `/dev/cameras/...`.
  - `test_page_has_no_internal_key_and_no_localstorage`: `"X-Internal-Api-Key" not in text`, `"localStorage" not in text`, `"?key=" not in text`.
  - `test_manual_count_requires_admin` (`ops_manager` → 403).
  - `test_manual_count_feeds_live_state` (admin → 200, and `live.counts`).
  - `test_dev_routes_absent_outside_dev`: monkeypatch `APP_ENV=prod`, `INTERNAL_API_KEY` and `JWT_SECRET` to 32-char values; `get_settings.cache_clear()`; assert no path in `create_app().openapi()["paths"]` starts with `/dev/`.
- [x] **Step 2: Run** `uv run pytest tests/test_dev.py -v`. Expected: FAIL (404).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q`. Expected: green.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.9: Zones and crowd reads

**Interfaces (produces):**
- `schemas/zones.py`: `ZoneOut(zoneId, name, mapImageUrl, polygon)`; `CrowdOut(zoneId, count, status, observedAt: datetime | None)`; `HistoryBucketOut(bucketStart, avgCount, peakCount)`.
- `CrowdService`:
  - `list_zones() -> list[ZoneOut]` (order by `zone_id`)
  - `get_zone(zone_id) -> ZoneOut` (404 `not_found`)
  - `crowd(zone_id) -> CrowdOut`. It returns 404 for an unknown zone. Before the first detection it returns `count=0`, `status="safe"`, `observedAt=None` (§6.2).
  - `history(zone_id, limit) -> list[HistoryBucketOut]`
  - The Stage 1 dict methods are deleted.
- `api/routers/zones.py`: `GET /api/v1/zones` and `GET /api/v1/zones/{zoneId}`, any role. `api/routers/crowd.py` adds `GET /api/v1/zones/{zoneId}/crowd` and `GET /api/v1/zones/{zoneId}/crowd/history?limit=` (`Query(12, gt=0, le=288)`, as today), any role. Delete `api/routers/dispatch_zones.py` and the zone tests in `tests/test_dispatch.py`. Route table updated accordingly.

- [x] **Step 1: Write the failing tests** (`tests/test_zones_crowd.py`). Port the four zone tests from `test_dispatch.py`:
  - count 7 → `{"count": 7, "status": "warning"}` on `/crowd`
  - zero before the first detection, plus `observedAt is None`
  - 404 shape for `z9`
  - 401 without auth

  Add:
  - `test_zone_payload_has_no_live_fields`: `set(GET /api/v1/zones/z1 .json()) == {"zoneId", "name", "mapImageUrl", "polygon"}`
  - `test_history_limit_bounds`: `limit=0` and `limit=289` → 422
- [x] **Step 2: Run** `uv run pytest tests/test_zones_crowd.py -v`. Expected: FAIL (404).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q`. Expected: green.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.10: Broadcasts and staff

**Interfaces (produces):**
- `schemas/broadcasts.py`: `CreateBroadcastRequest(zoneId: str, staffNeeded: int = Field(gt=0))` (moved); `AcceptedStaffOut(staffId, name, completedAt)`; `BroadcastOut(broadcastId, zoneId, status, staffNeeded, expiresAt, acceptedCount, completedCount, acceptedStaff)`; `FinishOut(broadcastId, status, finishedAt)`; `StaffOut(staffId, name, status)`.
- `BroadcastService`:
  - `async def list(self, zone_id: str | None, status: Literal["open", "closed", "finished"] | None, limit: int) -> list[BroadcastOut]`. It first selects `open` broadcasts with `expires_at < now` (filtered by zone when given), sets them to `closed`, and commits if any changed; this keeps today's read-time side effect (§6.2). Then it queries with the filters, ordered by `created_at desc`, limited. An empty result is `[]`, never 404. Per-item counts stay N+1 queries, with the code comment `# shortcut: N+1 count queries per broadcast, fine at limit<=50; join if lists grow`.
  - `current_for_zone` is deleted. `create`, `accept`, `complete`, `finish` and `get_staff` return the DTOs above; their rules are unchanged.
- Routers:
  - `api/routers/broadcasts.py`: `POST /api/v1/broadcasts` (201, `ops_manager`); `GET /api/v1/broadcasts?zoneId=&status=&limit=` (any role, `limit: int = Query(20, ge=1, le=50)`); `POST /{broadcastId}/accept` (`floor_staff`); `POST /{broadcastId}/complete` (`floor_staff`, and must have accepted, else 403 `not_your_response`); `POST /{broadcastId}/finish` (`ops_manager`).
  - `api/routers/staff.py`: `GET /api/v1/staff/{staffId}` (any role).
- Delete `api/routers/dispatch.py`, `schemas/dispatch.py`, `tests/test_dispatch.py` (move `test_create_broadcast_requires_manager_role` into the new file with the new URL). Update `tests/test_broadcast_service.py`, `tests/test_broadcast_race_pg.py` (the service API is unchanged, so only imports change if any), `tests/test_error_shape.py` (`CREATE_BROADCAST = "/api/v1/broadcasts"`), and the route table.

- [x] **Step 1: Write the failing tests** (`tests/test_broadcasts.py`, with a fake session that records `add` and `commit` and returns broadcasts from `scalars`):
  - `test_expired_open_broadcast_is_closed_and_filtered`: one `open` broadcast with `expires_at` in the past. `GET ?zoneId=z1&status=open` → `[]`; that broadcast's `status == "closed"`; `commit` was called once.
  - `test_list_without_status_returns_newest_first_including_closed`.
  - `test_list_empty_is_200_empty_list`.
  - `test_limit_bounds`: `0` and `51` → 422; the default is 20 (assert the compiled statement's limit).
  - `test_admin_cannot_create_broadcast` → 403 `wrong_role` (§6.3: no dispatch powers).
  - `test_floor_staff_cannot_finish` → 403.
  - `test_409_error_shape`: a session whose open-broadcast lookup returns a non-expired broadcast, then `POST /api/v1/broadcasts` → 409 with `{"error": {"code": "broadcast_open", ...}}`.
  - `test_datetimes_are_iso8601_utc`: `BroadcastOut(...).model_dump(mode="json")["expiresAt"].endswith("Z")` (Review Focus 5).
  - `test_old_dispatch_routes_are_gone`: `/api/v1/dispatch/broadcasts/current` → 404.
- [x] **Step 2: Run** `uv run pytest tests/test_broadcasts.py -v`. Expected: FAIL (404).
- [x] **Step 3: Implement.**
- [x] **Step 4: Run** `uv run pytest -q && grep -rn "/api/v1/dispatch\|DispatchError\|_require_role" src/ tests/`. Expected: green; grep empty.
- [x] **Step 5: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.11: Every route is protected (walker) and role matrix

**Files:** Create `tests/test_route_auth.py`; modify `tests/conftest.py` (add `NullSession`: `get`→`None`, `scalar`→`None`, `scalars`→empty, `execute`→empty result, `add`/`commit`/`rollback`/`refresh` no-ops).

The walker reads `app.openapi()["paths"]`, because `app.routes` holds unexpanded `_IncludedRouter` objects in FastAPI 0.159.1 (verified 2026-11-03). WebSockets are not in OpenAPI and are covered by `tests/test_crowd_live.py`.

- [ ] **Step 1: Write the tests**

```python
# tests/test_route_auth.py
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from conftest import NullSession, fake_user
from plong_elven.api.deps import get_current_user
from plong_elven.api.main import app
from plong_elven.db.session import get_db

PUBLIC = {
    ("GET", "/healthz"),
    ("GET", "/readyz"),
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/logout"),
    ("GET", "/dev/cameras/{cameraId}"),  # [TBD-1]
}
PARAMS = {
    "cameraId": "cam1",
    "zoneId": "z1",
    "broadcastId": str(uuid4()),
    "staffId": str(uuid4()),
}
ALL = sorted((m.upper(), p) for p, ops in app.openapi()["paths"].items() for m in ops)

ANY = {"floor_staff", "ops_manager", "admin"}
ROLE_MATRIX = {
    ("GET", "/api/v1/me"): ANY,
    ("PUT", "/api/v1/me/push-token"): ANY,
    ("GET", "/api/v1/zones"): ANY,
    ("GET", "/api/v1/zones/{zoneId}"): ANY,
    ("GET", "/api/v1/zones/{zoneId}/crowd"): ANY,
    ("GET", "/api/v1/zones/{zoneId}/crowd/history"): ANY,
    ("GET", "/api/v1/broadcasts"): ANY,
    ("GET", "/api/v1/staff/{staffId}"): ANY,
    ("POST", "/api/v1/broadcasts"): {"ops_manager"},
    ("POST", "/api/v1/broadcasts/{broadcastId}/accept"): {"floor_staff"},
    ("POST", "/api/v1/broadcasts/{broadcastId}/complete"): {"floor_staff"},
    ("POST", "/api/v1/broadcasts/{broadcastId}/finish"): {"ops_manager"},
    ("GET", "/api/v1/cameras/{cameraId}/layout"): {"admin"},
    ("PUT", "/api/v1/cameras/{cameraId}/layout"): {"admin"},
    ("GET", "/api/v1/cameras/{cameraId}/snapshot"): {"admin"},
    ("POST", "/api/v1/tickets"): {"admin"},
    ("POST", "/dev/cameras/{cameraId}/detections"): {"admin"},
}
BODIES = {
    ("PUT", "/api/v1/me/push-token"): {"pushToken": "t"},
    ("POST", "/api/v1/broadcasts"): {"zoneId": "z1", "staffNeeded": 1},
    ("PUT", "/api/v1/cameras/{cameraId}/layout"): {"anchor": "center", "zones": []},
    ("POST", "/api/v1/tickets"): {"scope": "crowd:live"},
    ("POST", "/dev/cameras/{cameraId}/detections"): {"counts": {"z1": 1}},
}


def _url(path: str) -> str:
    return path.format(**PARAMS)


@pytest.mark.parametrize("method,path", [r for r in ALL if r not in PUBLIC])
def test_route_rejects_anonymous(method, path) -> None:
    app.dependency_overrides[get_db] = lambda: NullSession()
    try:
        response = TestClient(app).request(method, _url(path))
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 401, (method, path)
    assert response.json()["error"]["code"] == "unauthenticated"


def test_every_bearer_route_is_in_the_role_matrix() -> None:
    worker_or_ticket = {
        r for r in ALL if r[1].startswith("/internal/") or r[1].endswith("/stream")
    }
    assert set(ALL) - PUBLIC - worker_or_ticket == set(ROLE_MATRIX)


@pytest.mark.parametrize("route", sorted(ROLE_MATRIX))
@pytest.mark.parametrize("role", sorted(ANY))
def test_role_matrix(route, role, tmp_path, monkeypatch, fake_push_sender) -> None:
    from conftest import FakeSessionFactory
    from plong_elven.api.deps import get_push_sender, get_session_factory

    monkeypatch.setenv("ZONES_PATH", str(tmp_path / "z.json"))
    app.dependency_overrides.update(
        {
            get_db: lambda: NullSession(),
            get_session_factory: lambda: FakeSessionFactory(NullSession()),
            get_push_sender: lambda: fake_push_sender,
            get_current_user: lambda: fake_user(role),
        }
    )
    method, path = route
    try:
        response = TestClient(app).request(method, _url(path), json=BODIES.get(route))
    finally:
        app.dependency_overrides.clear()
    if role in ROLE_MATRIX[route]:
        assert response.status_code not in (401, 403), (route, role, response.json())
    else:
        assert response.status_code == 403, (route, role)
        assert response.json()["error"]["code"] == "wrong_role"
```

Also replace `STAGE1_ROUTES` in `tests/test_route_table.py` with `STAGE2_ROUTES`. It holds 27 `(method, path)` pairs: the 25 HTTP routes in design §6.1 (GET and PUT `/layout` count separately), plus `GET /healthz` and `GET /readyz`. The WebSocket is not in OpenAPI.

- [ ] **Step 2: Run** `uv run pytest tests/test_route_auth.py tests/test_route_table.py -v`. Expected: all pass. A failure here means a route is open or mis-roled: fix the router, not the test.
- [ ] **Step 3: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.12: Docs

**Files:** see table. Content requirements:
- `docs/API_V1_MIGRATION.md`, for the iOS developer and written from the backend code:
  - every old route (all 24 from `STAGE1_ROUTES` plus `WS /ws/counts`) mapped to its new route by **full path**;
  - renamed fields: auth snake → camel, refresh/logout body `refreshToken`, WS `camera_id` → `cameraId`, zones lose `currentCount`/`status` (use `/crowd`), `broadcasts/current` → `GET /broadcasts?zoneId=..&status=open` (empty list, not 404), `respond` → `accept`;
  - the error shape and all codes;
  - WebSocket auth (bearer header on upgrade, or `?ticket=` for browsers);
  - the datetime `Z` form (**[TBD-7]**);
  - a ticket checked only at connect, so the client re-mints on reconnect;
  - an "Open items" section carrying **[TBD-2]**, **[TBD-3]** and **[TBD-7]** verbatim, for the iOS developer to confirm before release.
- `docs/DISPATCH_API.md`: rewrite for the new contract (curl examples with the new paths and camelCase).
- Fix the old-URL mentions found by `grep` on 2026-11-03:
  - `README.md:11,47-56,184,210-219`
  - `docs/SETUP_AUTH.md:9-23,194-317`
  - `docs/SETUP_CCTV.md:14,23,207-261`
  - `docs/AUTH_SCHEMA.md:142-145,182`
  - `docs/PUSH_NOTIFICATIONS.md:19,85,94`
  - `docs/SETUP_HLS.md:12,164`

  The last three files are beyond the design's list, but they name deleted routes. README also documents the `APP_ENV`/key startup rule and `alembic upgrade head` for the admin role.
- Each `docs/PROPOSAL_*.md` gets one line at the top: `> Implemented; see docs/superpowers/specs/2026-11-03-api-contract-rework-design.md (the design wins where they differ).`

- [ ] **Step 1: Write the docs.**
- [ ] **Step 2: Verify no stale route remains in docs**

Run: ``grep -rnE '/api/v1/dispatch|/auth/profile|/auth/push-token|/ws/counts|POST /detections|`/detections|/frames/\{camera_id\}|/stream/(default|cam|<camera-id>|\{camera_id\})|/ui/|\?key=' README.md docs/ | grep -v API_V1_MIGRATION.md | grep -v PROPOSAL_ | grep -v superpowers/``
Expected: no output.

- [ ] **Step 3: Checkpoint.** Leave in working tree; user commits.

---

### Task 2.13: Final verification

- [ ] **Step 1:** `uv run ruff check . && uv run ruff format --check .` → `All checks passed!`, nothing to reformat.
- [ ] **Step 2:** `uv run pytest -q -rs` → all pass; locally the only skip is `test_broadcast_race_pg`.
- [ ] **Step 3:** `grep -rn "include_in_schema" src/` → no output. The walker relies on OpenAPI, so no route may be hidden from it.
- [ ] **Step 4:** `grep -rnE '"/(detections|frames|stream|ui|zones)/|/ws/counts|/api/v1/dispatch|/auth/profile|/auth/push-token|Query\(.*key' src/ tests/` → no output.
- [ ] **Step 5: Semgrep.** Load the `trailofbits-semgrep` skill (`/Users/diuta/.kiro/crew/skills/trailofbits-semgrep/SKILL.md`), propose its ruleset for Python/FastAPI, and **wait for the user to approve the ruleset** before scanning `src/`. `semgrep` is not installed on this machine (`which semgrep` → not found, 2026-11-03): **[TBD-5]**. Report findings; fix only true positives in the code this plan touched.
- [ ] **Step 6:** `uv run pytest tests/test_route_table.py tests/test_route_auth.py -q` → all pass (live route table == the 27 `STAGE2_ROUTES` pairs, none open).
- [ ] **Step 7:** `git status --short`, then report to the user:
  - the file list;
  - the skipped-on-purpose list (Global Constraints);
  - the behaviour changes: 403-before-422 (Stage 1 Review Focus 5), datetime `Z`, blank dev key rejects;
  - the open **[TBD]** items.

  Leave in working tree; user commits.
