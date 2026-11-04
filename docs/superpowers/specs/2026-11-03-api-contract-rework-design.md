# Design: Layered backend + clean API contract + admin role

Branch: `dev/api-contract-rework`. Status: **draft, awaiting approval**.
Inputs: [PROPOSAL_ARCHITECTURE.md](../../PROPOSAL_ARCHITECTURE.md),
[PROPOSAL_API_CONTRACT.md](../../PROPOSAL_API_CONTRACT.md). Where this document
and those proposals differ, this document wins.

Marker **[call]** = a decision the author made, not the user. Marker **[TBD]** =
unknown, see "Left to the builder".

## 1. Intent

The backend is hard to read: every route function mixes HTTP, business rules and
SQL (`api/dispatch.py` is 353 lines), live state sits in loose module globals,
and the API contract is inconsistent: the read side of crowd data (count,
status, history) is served under `/api/v1/dispatch` while its write side
(`POST /detections`, `WS /ws/counts`) is unprefixed in `detections.py`; two
different `/zones` (DB zones under `/api/v1/dispatch/zones`, the polygon file
under `/zones/{cameraId}`); two error shapes (`DispatchError` vs FastAPI
`HTTPException` in `auth.py`, `zones.py`, `stream.py`); two JSON casings; several
unauthenticated routes.

The team wants a router → service → session structure like ASP.NET
controller/service, a clean versioned contract, and an `admin` role. The iOS app
and the camera worker move to the new routes **in the same release**, so old
routes are deleted, not kept.

## 2. Goals, non-goals, anti-goals

**Goals**

- Each route function only parses input, calls one service method, returns a DTO.
- All business rules live in services that do not import FastAPI.
- One contract: grouped by domain and caller, one error shape, camelCase JSON.
- Add an `admin` role that owns the camera/dev surface.
- Close the auth and input holes on every route that moves.
- A migration guide the iOS developer can follow without reading the backend.

**Non-goals**

- No database schema change except the new role row.
- No change to the cctv detection logic (`cctv/loop.py`, YOLO, zone math).
- No Redis or multi-process support. Live state stays in memory, one process.
- No repository layer. `AsyncSession` is used directly in services. **[call]**
- No interface per service. Only `PushSender` and `AlertRule` (see Stage 1,
  item 3). **[call]**
- No pagination framework. No new endpoints the app did not ask for.
- The `zones.json` vs `zones` table source-of-truth question is not solved here.

**Anti-goals (failure even if tests pass)**

- Any moved route that is *less* protected than before.
- The app or worker still calling a deleted route after release.
- A refactor that silently changes broadcast rules (slot limit, one open
  broadcast per zone, expiry after 20 s).
- Duplicate old/new route code left behind "for later".

## 3. Constraints

- Python 3.12, FastAPI, async SQLAlchemy, Alembic, Postgres 16. Keep the stack.
- The dev server runs from this branch on another machine; the app and worker
  switch together when it is deployed.
- CI today runs `ruff` and `pytest` with **no Postgres service** and sets a
  short `JWT_SECRET`. Any fail-closed startup rule must not break CI silently.
- The assistant does not run `git commit`; the user commits.
- Reuse what exists: `classify()`, `get_current_user`, `DispatchError`,
  `LatestUploader`, the cctv code. Do not rewrite them.

## 4. Approaches considered

| | Approach | Trade-off | Outcome |
|---|---|---|---|
| A | One big change | Fastest, but one huge diff; a break cannot be traced to a step | Rejected |
| **B** | **Two stages on this branch** | Two review rounds; each stage has one job | **Chosen** |
| C | New routes first, layers after | Touches the same files twice | Rejected |

## 5. Stage 1: layering, no URL changes

Done when: every existing test passes (imports may change, assertions may not),
`ruff` is clean, and no URL, status code or JSON field changed.

Target layout (from the architecture proposal):

```text
src/plong_elven/
├── api/            main.py, deps.py, routers/
├── schemas/        Pydantic DTOs per domain
├── services/       auth_service, crowd_service, alert_service,
│                   broadcast_service, notification_service, camera_service,
│                   ports.py (PushSender, AlertRule), apns_sender.py
├── state/          live_state.py
├── db/  common/  cctv/    (unchanged)
```

Work items, in order:

1. **`state/live_state.py`**: one `LiveState` class holding what is now
   `latest_counts`, `latest_count_observed_at`, `_buckets`, `_last_status`,
   `_last_alert_sent_at`, `_frames`, `_raw_frames`. A FastAPI dependency returns
   the single instance, so tests can override it. Plain dicts inside; no new
   abstraction beyond the class. **[call]**
2. **`schemas/`**: move DTOs out of routers.
3. **Two seams (SOLID O and D).** An interface is added only where a second
   implementation really exists or is likely. **[call]**
   - `PushSender` (a `typing.Protocol` in `services/ports.py`):
     `async def send(tokens, title, body) -> None`. `ApnsPushSender`
     (`services/apns_sender.py`) wraps today's `apns.send_push`. Tests use a
     `FakePushSender`. A later SMS sender is a new class.
   - `AlertRule` (Protocol): `evaluate(zone, count, previous_status) -> Alert | None`.
     `StatusChangeRule(status, cooldown, audience_role)` holds today's logic once;
     the existing `warning` and `crowded` alerts are two instances. A new alert
     type is a new class added to the rule list; no existing class is edited.
   - **`NotificationService`** (concrete) picks recipients by user or role,
     loads their push tokens and calls a `PushSender`. It is the only caller.
   - Wiring: one `Depends` provider each for `PushSender` and the rule list, so
     tests swap them with `dependency_overrides`.
4. **`BroadcastService`** from `dispatch.py` (takes `NotificationService`);
   **`CrowdService`** from `detections.py` (ingest and 5-minute buckets only);
   **`AlertService`** (new; runs the `AlertRule` list on each ingest and sends
   through `NotificationService`, owns the cooldown state in `LiveState`);
   **`AuthService`** from `auth.py`; **`CameraService`** for layout/frames.
   Services other than the two seams get **no** interface: they have one
   implementation and no second use.
5. **`api/deps.py`**: `get_db`, `get_current_user`, `require_role(*names)`,
   `require_internal_key`.
6. Routers become thin. Error type `DispatchError` is renamed to a general
   `AppError(status, code, message)` (same handler).

Fixes done in Stage 1 because the code is being moved anyway. None change the
contract. **[call]**

| Fix | Where | How |
|---|---|---|
| XSS | `api/ui.py` | `camera_id` is validated (see 6.7); page no longer string-replaces unescaped input |
| Accept-broadcast race | respond/accept | Lock the broadcast row (`SELECT … FOR UPDATE`) before counting responses |
| Background task reuses request session | `post_detection` | Task opens its own session from `SessionFactory` |
| Non-constant-time key compare | `security.py` | `hmac.compare_digest` |

Tests added in Stage 1:

- One contract test runs the same cases against `ApnsPushSender` (APNs call
  mocked) and `FakePushSender`, proving both follow `PushSender`.
- `AlertService` test: a second, test-only `AlertRule` is added to the list and
  fires without editing `StatusChangeRule` (proves the O seam).

- One concurrency test for accept against real Postgres. A `FOR UPDATE` lock
  cannot be proven with fakes or SQLite. Add a `postgres:16-alpine` service to
  `.github/workflows/ci.yml` (reusing the `ci-password` already in its env) and
  run migrations before the test. **[call]** If this proves impossible, the test
  is skipped without a DB and the PR says the race fix is not exercised in CI.

## 6. Stage 2: contract cutover, admin role, security

Done when: old routes are gone, the contract below is live, tests cover every
route's auth, and `docs/API_V1_MIGRATION.md` exists.

### 6.1 Routes

App routes use a bearer access token. Prefix `/api/v1`.

| Method + path | Role | Replaces |
|---|---|---|
| `POST /auth/login`, `/auth/refresh`, `/auth/logout` | public / refresh token | same paths |
| `GET /me` | any | `GET /auth/profile` |
| `PUT /me/push-token` | any | `POST /auth/push-token` |
| `GET /zones`, `GET /zones/{zoneId}` | any | `/dispatch/zones*` without count/status |
| `GET /zones/{zoneId}/crowd` | any | `currentCount`+`status` |
| `GET /zones/{zoneId}/crowd/history?limit=` | any | `/dispatch/zones/{id}/history` |
| `WS /crowd/live` | any logged-in | `WS /ws/counts` |
| `POST /broadcasts` | ops_manager | `POST /dispatch/broadcasts` |
| `GET /broadcasts?zoneId=&status=&limit=` | any | `/dispatch/broadcasts/current` |
| `POST /broadcasts/{id}/accept` | floor_staff | `…/respond` |
| `POST /broadcasts/{id}/complete` | floor_staff (must have accepted) | same |
| `POST /broadcasts/{id}/finish` | ops_manager | same |
| `GET /staff/{staffId}` | any | `/dispatch/staff/{id}` |

Reading the "Replaces" column: a path starting `/dispatch/` means
`/api/v1/dispatch/…`; `/auth/…` means `/api/v1/auth/…`. These old paths are
unprefixed today: `/ws/counts`, `/detections`, `/zones/{cameraId}`,
`/frames/…`, `/stream/…`, `/ui/…`. The migration guide (6.9) lists every old
route with its full path.

Camera admin routes, prefix `/api/v1/cameras/{cameraId}`, role `admin`:

| Method + path | Replaces |
|---|---|
| `GET /layout`, `PUT /layout` | `GET/PUT /zones/{cameraId}` |
| `GET /snapshot` | `GET /frames/{cameraId}/latest` |
| `GET /stream` (ticket, see 6.4) | `GET /stream/{cameraId}` |
| `POST /api/v1/tickets` (admin) | new |

Worker routes, prefix `/internal/v1/cameras/{cameraId}`, header
`X-Internal-Api-Key`:

| Method + path | Replaces |
|---|---|
| `POST /detections` | `POST /detections` (camera id moves from body to path) |
| `POST /frames`, `POST /frames/raw` | `POST /frames/{cameraId}[/raw]` |

Dev only (`APP_ENV=dev`, router not registered otherwise). The page itself
(`GET /dev/cameras/{cameraId}`) needs no login, because a browser cannot send a
bearer header on navigation and the login form is on the page. Every API call
the page makes needs role `admin`, including
`POST /dev/cameras/{cameraId}/detections` (manual count, feeds `CrowdService`;
replaces the page posting to the worker route with the worker key).

`/healthz` and `/readyz` unchanged.

**Skipped on purpose** (not asked for, name them in the PR): `GET /staff` list,
`GET /broadcasts/{id}`, pagination beyond `limit`, threshold fields on zones,
APNs dead-token cleanup (410), `users.updated_at` maintenance.

### 6.2 Response rules

- **Error shape for every error**, including 401/403/422 from FastAPI. This
  covers the `HTTPException`s now raised in `auth.py`, `zones.py` and
  `stream.py`, not only the dispatch routes (one handler for `HTTPException`,
  one for request-validation errors):
  `{"error": {"code": "...", "message": "..."}}`. Codes: `unauthenticated`
  (401, keeps `WWW-Authenticate`), `wrong_role` (403), `not_found` (404),
  `validation_error` (422), and existing 409 codes (`broadcast_open`, `expired`,
  `slots_full`, `already_delegating`, `not_all_done`).
- **camelCase** for all JSON, including auth: `accessToken`, `refreshToken`,
  `tokenType`, `expiresIn`, `fullName`, `isActive`.
- `GET /zones/{zoneId}/crowd` returns `{zoneId, count, status, observedAt}`;
  `observedAt` is `null` before the first detection (count `0`, status `safe`,
  as today).
- `GET /zones` / `/zones/{id}`: `{zoneId, name, mapImageUrl, polygon}`
  (count and status removed).
- `GET /broadcasts` returns a list, newest first, `limit` default 20, max 50.
  Empty list instead of 404. The app's old "current" call becomes
  `?zoneId=z2&status=open`; to see the latest of any status, omit `status`
  and read the first item. **[call]**
- `GET /broadcasts` keeps the old read-time side effect: any returned `open`
  broadcast past `expiresAt` is set to `closed` and committed before the
  response is built (same as `_close_if_expired` today). **[call]**
- The zone list no longer carries live status, so the app makes one extra call
  per zone to `/crowd`. Acceptable because the system is a single-zone proof of
  concept (`GET /zones` returns one zone today). **[call]**
- Auth camelCase is a deliberate breaking change; the app is updated in the same
  release (confirmed by the user).
- WebSocket message: `{"cameraId": "...", "counts": {"z2": 3}}`.

### 6.3 Admin role

- Alembic migration inserts role `id=3, name='admin'` (existing: 1
  `floor_staff`, 2 `ops_manager`). Downgrade deletes it.
- `scripts/create_user.py` accepts `--role admin` (its argparse `choices`
  currently lists only `floor_staff` and `ops_manager`); `scripts/seed_dispatch.py`
  adds `admin@plongelven.io` (same local dev password rule as the others).
- Admin has **no** dispatch powers. Roles do not inherit. **[call]**
- `require_role(*names)` replaces `_require_role` and returns `wrong_role` 403.

### 6.4 Fail-closed security (from the sharp-edges review)

| Today | After |
|---|---|
| Blank `INTERNAL_API_KEY` turns worker auth off | Server refuses to start unless `APP_ENV=dev`. Outside dev the key must also be ≥ 32 chars. **[call]** |
| `JWT_SECRET` < 32 chars is found per request (503) | Same startup check, same dev exception |
| `APP_ENV` is in `.env` but nothing reads it | Add `app_env` to `common/settings.py`, default `"prod"` (fail closed). The dev server's `.env` already has `APP_ENV=dev`; any other deployment must set a strong key and secret |
| `?key=` in stream/snapshot URLs | Removed. See tickets below |
| `WS /ws/counts`, `GET /zones/{cameraId}` open | Require auth |
| Frame and count uploads unbounded | Frame body ≤ 5 MB; `counts` ≤ 50 entries, each int ≥ 0 (≤ 10000). **[call]** |

**Tickets** (needed because `<img>` and browser `WebSocket` cannot send an
`Authorization` header): `POST /api/v1/tickets` with `{"scope": "stream:<cameraId>"}`
or `{"scope": "crowd:live"}`, admin only, returns `{ticket, expiresIn: 60}`.
A ticket is a JWT signed with the existing secret, with `type="ticket"` and a
`scope` claim. It is accepted only as `?ticket=` on the stream and WebSocket
endpoints, only for its own scope, and `get_current_user` already rejects any
token whose `type` is not `access`. The iOS app uses a normal bearer header on
the WebSocket upgrade instead. **[call]**

### 6.5 Dev page

`/dev/cameras/{cameraId}` gets a small admin login form (calls `/auth/login`,
keeps the access token in a JS variable, not `localStorage`), mints tickets for
the stream and WebSocket, and uses the bearer header for layout and snapshot.
On 401 it asks to log in again. Dev only.

### 6.6 Worker

Only `cctv/client.py` changes: new URLs and `cameraId` in the path. No other
cctv file changes. The detections body drops its `camera_id` field, so the
`Detection` model in the API changes to `{counts}` only. `README`, `SETUP_CCTV.md`, `Makefile` targets are updated if
they mention old URLs.

### 6.7 `cameraId` validation

All camera routes use a path pattern `^[A-Za-z0-9_-]{1,64}$`. This is also what
keeps attacker-chosen strings out of the in-memory dict keys and the HTML page.

### 6.8 Tests and CI

- A test that walks `app.routes` and asserts every route outside an explicit
  public list (`/healthz`, `/readyz`, `login`, `refresh`, `logout`) returns 401
  without credentials. This keeps future routes from being added open.
- Role matrix per route: admin, ops_manager, floor_staff each get 200/403 as in
  the table.
- Error-shape test for 401, 403, 404, 422, 409.
- Ticket tests: wrong scope, wrong camera, expired, access token used as ticket.
- Startup test: blank key + not dev refuses; dev allows.
- `tests/conftest.py` and `.github/workflows/ci.yml` set `APP_ENV=dev` where
  needed so the fail-closed default does not break existing tests.
- Final check on the diff: `ruff`, `pytest`, and a Semgrep scan (the
  `trailofbits-semgrep` skill asks for approval of the ruleset first).

### 6.9 Docs

- New `docs/API_V1_MIGRATION.md`: old route → new route, renamed fields,
  new error shape, WebSocket auth, for the iOS developer.
- Rewrite `docs/DISPATCH_API.md` for the new contract; update `README.md`,
  `SETUP_AUTH.md`, `SETUP_CCTV.md`.
- The two `PROPOSAL_*.md` files get a one-line "implemented, see design" note.

## 7. Left to the builder

- Exact module and function names inside `services/`.
- How tickets are wired into FastAPI dependencies.
- Pydantic model names; how camelCase is produced (aliases vs field names).
- Order of commits inside each stage.
- Whether `LiveState` is one class or a few small ones.

## 8. Open items

- **[TBD]** Exact JSON fields the iOS app reads today; the migration doc is
  written from the backend code. The iOS developer must confirm it before release.
- **[TBD]** Whether the iOS app uses the WebSocket at all. Both auth paths
  (bearer header and `?ticket=`) are built either way; if the app does not use
  it, app-side WebSocket work is zero.
- **[TBD]** Deployment of the dev server: it needs `APP_ENV=dev`, a real
  `INTERNAL_API_KEY`, and `alembic upgrade head` before the new build starts.
