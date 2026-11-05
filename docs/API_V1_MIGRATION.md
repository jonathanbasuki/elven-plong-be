# API v1 migration guide (for the iOS app)

> **Superseded in part.** This guide describes the move from the old routes to
> `/api/v1`. Since then, broadcasts became **tasks** (`/api/v1/broadcasts` →
> `/api/v1/tasks`, create is `POST /api/v1/tasks/broadcast`, `broadcastId` →
> `taskId`, `staffNeeded` → `totalStaff`, new required `instruction`), zones
> have one `threshold` (status is `safe`/`crowded`, no `warning`), and the zone
> object is `zoneId`, `name`, `threshold`, `isActiveTask`. For the current
> contract read [FRONTEND_GUIDE.md](FRONTEND_GUIDE.md) and
> [API_REFERENCE.md](API_REFERENCE.md).

This guide lists every change the iOS app must make for the new API contract.
It is written from the backend code and tests in this repository
(`src/plong_elven/api/routers/`, `src/plong_elven/schemas/`, `tests/`), not
from what the app reads today. Items the backend cannot confirm are marked
**[TBD-n]** and collected under [Open items](#open-items).

Design: [docs/superpowers/specs/2026-11-03-api-contract-rework-design.md](superpowers/specs/2026-11-03-api-contract-rework-design.md) §6.

## 1. Routes: old to new

Every old route below is removed. Calling an old path now returns
`404 {"error": {"code": "not_found", ...}}`.

"Any role" means any logged-in user with a bearer access token
(`Authorization: Bearer <accessToken>`).

| Old route | New route | Who may call it now |
|---|---|---|
| `POST /api/v1/auth/login` | `POST /api/v1/auth/login` | public |
| `POST /api/v1/auth/refresh` | `POST /api/v1/auth/refresh` | refresh token in body |
| `POST /api/v1/auth/logout` | `POST /api/v1/auth/logout` | refresh token in body |
| `GET /api/v1/auth/profile` | `GET /api/v1/profile` | any role |
| `POST /api/v1/auth/push-token` | `PUT /api/v1/profile/push-token` (method changes to `PUT`) | any role |
| `GET /api/v1/dispatch/zones` | `GET /api/v1/zones` | any role |
| `GET /api/v1/dispatch/zones/{zoneId}` | `GET /api/v1/zones/{zoneId}`, plus `GET /api/v1/zones/{zoneId}/crowd` for count and status | any role |
| `GET /api/v1/dispatch/zones/{zoneId}/history` | `GET /api/v1/zones/{zoneId}/crowd/history` | any role |
| `GET /api/v1/dispatch/broadcasts/current?zoneId=` | `GET /api/v1/broadcasts?zoneId=&status=open` | any role |
| `POST /api/v1/dispatch/broadcasts` | `POST /api/v1/broadcasts` | `ops_manager` |
| `POST /api/v1/dispatch/broadcasts/{broadcastId}/respond` | `POST /api/v1/broadcasts/{broadcastId}/accept` | `floor_staff` |
| `POST /api/v1/dispatch/broadcasts/{broadcastId}/complete` | `POST /api/v1/broadcasts/{broadcastId}/complete` | `floor_staff` (new: was any role) |
| `POST /api/v1/dispatch/broadcasts/{broadcastId}/finish` | `POST /api/v1/broadcasts/{broadcastId}/finish` | `ops_manager` |
| `GET /api/v1/dispatch/staff/{staffId}` | `GET /api/v1/staff/{staffId}` | any role |
| `WS /ws/counts` (no auth) | `WS /api/v1/crowd/live` | any role (bearer header), or an admin ticket |
| `POST /detections` | `POST /api/v1/cameras/{cameraId}/detections` | cctv worker (`X-Internal-Api-Key`) |
| `POST /frames/{camera_id}` | `POST /api/v1/cameras/{cameraId}/frames` | cctv worker |
| `POST /frames/{camera_id}/raw` | `POST /api/v1/cameras/{cameraId}/frames/raw` | cctv worker |
| `GET /frames/{camera_id}/latest?key=` | `GET /dev/cameras/{cameraId}/snapshot` | `admin`, `APP_ENV=dev` only |
| `GET /stream/{camera_id}?key=` | `GET /dev/cameras/{cameraId}/stream?ticket=` | `admin` ticket only, `APP_ENV=dev` only |
| `GET /zones/{camera_id}` (no auth) | `GET /dev/cameras/{cameraId}/layout` | `admin`, `APP_ENV=dev` only |
| `PUT /zones/{camera_id}` (worker key) | `PUT /dev/cameras/{cameraId}/layout` | `admin`, `APP_ENV=dev` only |
| `GET /ui/{camera_id}` | `GET /dev/cameras/{cameraId}` | open, `APP_ENV=dev` only (see [§7](#7-dev-page)) |
| `GET /healthz` | `GET /healthz` | public |
| `GET /readyz` | `GET /readyz` | public |

New routes with no old equivalent:

| Route | Who may call it |
|---|---|
| `POST /dev/tickets` | `admin`, `APP_ENV=dev` only |
| `POST /dev/cameras/{cameraId}/detections` | `admin`, `APP_ENV=dev` only |

`{cameraId}` must match `^[A-Za-z0-9_-]{1,64}$` on every camera route;
anything else is a `422`.

The routes the iOS app is expected to use are the `auth`, `profile`, `zones`,
`broadcasts`, `staff` and `crowd/live` rows. The camera, ticket, worker and dev
routes are for the cctv worker and the dev page. Which fields the app reads
today is not known to the backend: **[TBD-2]**.

## 2. Request and response fields

All JSON is camelCase now. Old snake_case request bodies are **not** accepted:
they get `422 validation_error` (`tests/test_auth_routes.py::test_refresh_rejects_snake_case_body`).
Unknown extra fields are ignored.

### Auth (`POST /api/v1/auth/login`, `/refresh`)

| Old field | New field |
|---|---|
| `access_token` | `accessToken` |
| `refresh_token` | `refreshToken` |
| `token_type` | `tokenType` (still `"bearer"`) |
| `expires_in` | `expiresIn` (seconds) |
| `user.full_name` | `user.fullName` |
| `user.is_active` | `user.isActive` |

`user.id`, `user.email` and `user.role` keep their names. `/refresh` returns the
same fields without `user`.

Request bodies for `/refresh` and `/logout` change from
`{"refresh_token": "..."}` to `{"refreshToken": "..."}`. `/logout` still returns
`200 {"status": "ok"}`.

### `GET /api/v1/profile`

Returns `{id, email, fullName, role, isActive}` (was `GET /auth/profile` with
`full_name` and `is_active`).

### `PUT /api/v1/profile/push-token`

Body is unchanged: `{"pushToken": "..."}`. Response: `{"status": "ok"}`.

### Zones and crowd

- `GET /api/v1/zones` and `GET /api/v1/zones/{zoneId}` return
  `{zoneId, name, mapImageUrl, polygon}`. **`currentCount` and `status` are
  removed.**
- The live values move to `GET /api/v1/zones/{zoneId}/crowd`, which returns
  `{zoneId, count, status, observedAt}`. `status` is `safe`, `warning` or
  `crowded`. Before the first detection: `count` is `0`, `status` is `safe`,
  `observedAt` is `null`.
- The app now makes one extra `/crowd` call per zone. `GET /api/v1/zones`
  returns one zone today (single-zone proof of concept).
- `GET /api/v1/zones/{zoneId}/crowd/history?limit=` returns
  `[{bucketStart, avgCount, peakCount}]`, newest first, same fields as the old
  `/history`. `limit` default 12, range 1 to 288. An unknown zone returns
  `200 []`, not `404`.

### Broadcasts

- `GET /api/v1/dispatch/broadcasts/current?zoneId=z1` becomes
  `GET /api/v1/broadcasts?zoneId=z1&status=open`. It returns a **list**, newest
  first. No match is `200 []`, **not** `404`. To get the latest broadcast of any
  status (what `current` used to return), omit `status` and read the first item.
- Query parameters: `zoneId` (optional), `status` (`open`, `closed`,
  `finished`, optional), `limit` (default 20, range 1 to 50).
- An `open` broadcast past `expiresAt` is set to `closed` before the list is
  built, so `status=open` never returns an expired one.
- Each item has the same fields as the old `current` response:
  `{broadcastId, zoneId, status, staffNeeded, expiresAt, acceptedCount,
  completedCount, acceptedStaff: [{staffId, name, completedAt}]}`.
- `POST .../respond` is renamed `POST .../accept`.
- `accept` and `complete` used to return `{"status": "ok"}`. They now return
  the full broadcast object above.
- `POST /api/v1/broadcasts` (body `{zoneId, staffNeeded}`, `201`) and
  `POST .../finish` (`{broadcastId, status: "finished", finishedAt}`) keep their
  fields.
- `GET /api/v1/staff/{staffId}` keeps `{staffId, name, status}`.

### WebSocket message

`WS /api/v1/crowd/live` sends `{"cameraId": "...", "counts": {"z1": 3}}` for every
count post (worker or dev page), to every connected client. The old
`/ws/counts` sent `camera_id`. Messages from the client are ignored.

## 3. Datetimes now end in `Z`

Every datetime in a response is UTC and ends in `Z`, for example
`2026-11-03T19:00:00Z`. This covers `expiresAt`, `completedAt`, `finishedAt`,
`observedAt` and `bucketStart`. Before this change, FastAPI encoded the same
values as `2026-11-03T19:00:00+00:00`. Pinned by
`tests/test_broadcasts.py::test_datetimes_are_iso8601_utc` and
`tests/test_zones_crowd.py::test_crowd_and_history_datetimes_are_iso8601_utc`.

Whether the iOS date parser accepts the `Z` form: **[TBD-7]**.

## 4. Errors

Every error, including 401, 403, 404 and 422 raised by the framework, has one
shape:

```json
{"error": {"code": "wrong_role", "message": "This action requires the ops_manager role."}}
```

Before, auth, stream and zone errors and all 422s used FastAPI's
`{"detail": ...}` shape. Only the dispatch routes used `error.code`.

| Status | `error.code` | When |
|---|---|---|
| 401 | `unauthenticated` | missing, invalid or expired token or ticket; a ticket whose user is no longer an active `admin`; wrong or missing `X-Internal-Api-Key`; wrong email or password. The response keeps `WWW-Authenticate: Bearer`. |
| 403 | `wrong_role` | the user's role may not call this route |
| 403 | `not_your_response` | `complete` by a `floor_staff` user who did not accept this broadcast |
| 404 | `not_found` | unknown zone, broadcast, staff, camera frame, or route |
| 409 | `broadcast_open` | the zone already has an open broadcast |
| 409 | `expired` | `accept` after the 20-second window |
| 409 | `slots_full` | `accept` when `staffNeeded` staff already accepted |
| 409 | `already_delegating` | `accept` while the user is on another broadcast |
| 409 | `not_all_done` | `finish` before every accepted staff member completed |
| 413 | `payload_too_large` | worker frame over 5 MB |
| 422 | `validation_error` | bad body, query or path value, or an invalid zone layout on `PUT /dev/cameras/{cameraId}/layout`. For framework validation, `message` lists each problem as `location: reason`, joined by `; ` |
| 503 | `auth_not_configured` | `JWT_SECRET` shorter than 32 characters (only reachable with `APP_ENV=dev`) |
| other | `http_error` | any other framework status, for example `405` |

Source: `src/plong_elven/api/main.py` (`_STATUS_TO_CODE` and the three handlers),
`tests/test_error_shape.py`.

## 5. Roles

- Roles are `floor_staff` (id 1), `ops_manager` (id 2) and the new `admin`
  (id 3). The admin role comes from migration
  `alembic/versions/a1b2c3d4e5f6_add_admin_role.py`.
- Roles do not inherit. `admin` has **no** dispatch powers: an admin calling
  `POST /broadcasts`, `accept`, `complete` or `finish` gets
  `403 wrong_role` (`tests/test_broadcasts.py::test_admin_cannot_create_broadcast`).
- `admin` is the only role that may mint tickets and use the `/dev/cameras` routes
  (layout, snapshot, stream, manual detections). All of them exist only when
  `APP_ENV=dev`.
- `complete` now requires `floor_staff`. Before, any role reached the handler,
  and an `ops_manager` got `403 not_your_response`; it now gets
  `403 wrong_role`.
- **Status change:** a `floor_staff` user who sends an *invalid* body to
  `POST /api/v1/broadcasts` used to get `422`. They now get `403 wrong_role`,
  because the role check runs before body validation. An `ops_manager` with an
  invalid body still gets `422`. Pinned by
  `tests/test_broadcast_service.py::test_role_check_runs_before_body_validation`.

## 6. Authentication for the WebSocket and the stream

### iOS: bearer header

The iOS app opens `WS /api/v1/crowd/live` with the normal access token in the
upgrade request:

```text
GET /api/v1/crowd/live
Authorization: Bearer <accessToken>
```

Any role may connect (`tests/test_crowd_live.py::test_accepts_bearer_header`).
Without valid credentials the server closes the socket with code `1008`
before accepting it. Whether the app uses the WebSocket at all: **[TBD-3]**.

### Browser: tickets

A browser `<img>` and the browser `WebSocket` API cannot send an
`Authorization` header, so the dev page uses tickets. Tickets can be minted only
by `POST /dev/tickets`, which exists only when `APP_ENV=dev`. A production
server cannot mint one, so the browser stream is unavailable there:

1. `POST /dev/tickets` with a bearer **admin** access token and
   `{"scope": "stream:<cameraId>"}` or `{"scope": "crowd:live"}`. Other roles
   get `403 wrong_role`; any other scope gets `422`.
2. The response is `{"ticket": "...", "expiresIn": 60}`.
3. Pass it as a query parameter:
   `GET /dev/cameras/{cameraId}/stream?ticket=...` or
   `WS /api/v1/crowd/live?ticket=...`.

Rules (from `src/plong_elven/services/auth_service.py` and `tests/test_tickets.py`):

- A ticket lives **60 seconds**.
- When the ticket is used, its user is loaded again and must still be active
  and `admin`; otherwise `401`.
- It works only for its own scope: a `stream:cam1` ticket fails on `cam2` and
  on the WebSocket; a `crowd:live` ticket fails on the stream.
- A ticket is not an access token, and an access token is not a ticket. Using
  one in place of the other is a `401`. The stream ignores a bearer header.
- The ticket is checked **only when the stream or socket opens**. A stream or
  socket that is already open keeps running after its ticket expires. A client
  that reconnects must mint a new ticket first.
- The ticket is in the URL, so it **appears in server access logs** (uvicorn
  logs the query string). The 60-second lifetime and the single scope limit
  what a logged ticket can be used for. This replaces the old `?key=`, which
  put the long-lived worker key in the URL.

## 7. Dev page

`GET /dev/cameras/{cameraId}` replaces `GET /ui/{camera_id}`.

**[TBD-1] RESOLVED (2026-11-03):** the page is open in dev only. It is served
without login, and only when `APP_ENV=dev` (the router is not registered
otherwise). Every API call the page makes needs an admin login: layout,
snapshot and manual count use the bearer header, and the stream and WebSocket
use tickets. The access token is kept in a JS variable, not `localStorage`.

## 8. cctv worker changes

Only relevant if you run the worker yourself.

- Routes move under `/api/v1/cameras/{cameraId}/` (see the table in §1). They are
  the one group under `/api/v1` that takes `X-Internal-Api-Key`, not a bearer
  token.
- The detections body loses `camera_id`: it is now `{"counts": {"z1": 3}}`, and
  the camera id is in the path. A `camera_id` field sent in the body is ignored.
- `counts` holds at most 50 zones; each count is an integer from 0 to 10000;
  each zone key matches `^[A-Za-z0-9_-]{1,64}$`. Otherwise `422`.
- A frame body is at most 5 MB, otherwise `413 payload_too_large`.
- `src/plong_elven/cctv/client.py` already uses the new routes.

## 9. Server configuration

- `APP_ENV` is read now and defaults to `prod`.
- Outside `APP_ENV=dev`, the API **refuses to start** unless
  `INTERNAL_API_KEY` and `JWT_SECRET` are each at least 32 characters
  (`check_security_settings` in `src/plong_elven/common/settings.py`).
- In dev the startup check is skipped, but a blank `INTERNAL_API_KEY` still
  rejects every worker call with `401`.
- Run `alembic upgrade head` before starting the new build, so the `admin`
  role exists. Dev server deployment: **[TBD-4]**.

## 10. Skipped on purpose

These are not part of this change:

- `GET /staff` list
- `GET /broadcasts/{id}`
- pagination beyond `limit`
- threshold fields on zones
- APNs dead-token cleanup (410)
- `users.updated_at` maintenance

## Open items

To confirm before release. Wording from
[the plan's TBD table](superpowers/plans/2026-11-03-api-contract-rework-plan.md).

| Id | Item | Status |
|---|---|---|
| TBD-1 | Dev page `GET /dev/cameras/{cameraId}`: the design table says role `admin`, but a browser navigation cannot send a bearer header and §6.5 puts the login form on the page. | **RESOLVED (user, 2026-11-03):** page is open in dev only; the API calls it makes still need an admin login. |
| **TBD-2** | Exact JSON fields the iOS app reads today (design §8). | Open: iOS developer to confirm |
| **TBD-3** | Whether the iOS app uses the WebSocket at all (design §8). | Open: iOS developer to confirm |
| TBD-4 | Dev server deployment: `APP_ENV=dev`, a real `INTERNAL_API_KEY`, `alembic upgrade head` before the new build (design §8). | Open: release |
| TBD-5 | `semgrep` is not installed here, and the `trailofbits-semgrep` ruleset needs user approval. | Open: backend only |
| TBD-6 | No Docker or Postgres on this machine. The CI Postgres service, the race test, and the migration up/down round trip can only be verified in CI. PyYAML availability for the local YAML check is also unconfirmed. | Open: backend only |
| **TBD-7** | Whether the iOS date parser accepts `...Z` (Pydantic DTOs) as well as today's `...+00:00`. | Open: iOS developer to confirm |
| TBD-8 | Whether `/docs`, `/redoc` and `/openapi.json` should stay public outside dev. The plan leaves them unchanged; they are not in OpenAPI paths, so the walker ignores them. | Open |
