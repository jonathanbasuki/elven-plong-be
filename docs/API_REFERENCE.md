# API reference (as built)

Derived from `src/plong_elven/api/routers/*` and `src/plong_elven/schemas/*`,
cross-checked against the live route table (`walk_routes` in
`tests/test_route_auth.py`, `APP_ENV=dev`): 26 routes plus FastAPI's
`/openapi.json`, `/docs`, `/docs/oauth2-redirect`, `/redoc`.

## Conventions

- JSON field names are camelCase.
- Datetimes are UTC ISO-8601 ending in `Z` (`UtcDatetime` in `schemas/zones.py`).
- Every error has this shape:

```json
{"error": {"code": "slots_full", "message": "This broadcast already has enough staff."}}
```

- A 401 response also carries `WWW-Authenticate: Bearer`.

### Error codes

| HTTP | Code | Source |
|---|---|---|
| 401 | `unauthenticated` | missing/invalid bearer, refresh token, ticket or internal key |
| 403 | `wrong_role` | `require_role` |
| 403 | `not_your_response` | complete a broadcast you did not accept |
| 404 | `not_found` | unknown zone, broadcast, staff, or no frame yet |
| 409 | `broadcast_open` | zone already has an open broadcast |
| 409 | `expired` | accept after `expiresAt`, or a broadcast already `closed`/`finished` |
| 409 | `already_delegating` | staff is already on another broadcast |
| 409 | `slots_full` | `staffNeeded` already accepted |
| 409 | `not_all_done` | finish before every accepted staff completed |
| 413 | `payload_too_large` | frame body over 5 MB |
| 422 | `validation_error` | request validation, bad layout |
| 503 | `auth_not_configured` | `JWT_SECRET` shorter than 32 chars |
| other | `http_error` | any other `HTTPException` (e.g. 405) |

### Limits

| What | Limit | Where |
|---|---|---|
| `cameraId` path param, zone keys, layout zone ids | `^[A-Za-z0-9_-]{1,64}$` | `schemas/common.py` |
| zones per detections post / per layout | 50 | `DetectionIn`, `ZonesIn` |
| count per zone | int 0..10000 | `DetectionIn` |
| frame body | 5 MB (5 * 1024 * 1024 bytes), checked on `Content-Length` and while streaming | `routers/cameras.py` |
| broadcast expiry | 20 s after create | `BroadcastService.create` |
| ticket lifetime | 60 s | `auth_service.TICKET_SECONDS` |

## App routes (`/api/v1`, bearer access token)

"Any role" means any active user with a valid access token.

| Method | Path | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| POST | `/api/v1/auth/login` | none | `email` (email), `password` (min 1) | `accessToken`, `refreshToken`, `tokenType` ("bearer"), `expiresIn` (s), `user` | 401, 422, 503 |
| POST | `/api/v1/auth/refresh` | refresh token in body | `refreshToken` | `accessToken`, `refreshToken` (new; old one revoked), `tokenType`, `expiresIn` | 401, 422, 503 |
| POST | `/api/v1/auth/logout` | refresh token in body | `refreshToken` | `{"status": "ok"}` (also for unknown tokens); revokes it and clears the user's push token | 422 |
| GET | `/api/v1/profile` | any role | - | `user` object | 401 |
| PUT | `/api/v1/profile/push-token` | any role | `pushToken` (min 1) | `{"status": "ok"}` | 401, 422 |
| GET | `/api/v1/zones` | any role | - | list of zone objects, ordered by `zoneId` | 401 |
| GET | `/api/v1/zones/{zoneId}` | any role | - | zone object | 401, 404 |
| GET | `/api/v1/zones/{zoneId}/crowd` | any role | - | `zoneId`, `count`, `status` (`safe`/`warning`/`crowded`), `observedAt` (null before the first detection) | 401, 404 |
| GET | `/api/v1/zones/{zoneId}/crowd/history` | any role | query `limit` 1..288, default 12 | list of `bucketStart`, `avgCount`, `peakCount`, newest first; `[]` for unknown zone | 401, 422 |
| WS | `/api/v1/crowd/live` | `Authorization: Bearer` header or `?ticket=` (scope `crowd:live`) | client messages ignored | server sends `{"cameraId", "counts": {zoneId: n}}` per ingest | close 1008 on auth failure |
| GET | `/api/v1/broadcasts` | any role | query `zoneId`, `status` (`open`/`closed`/`finished`), `limit` 1..50 default 20 | list of broadcast objects, newest first | 401, 422 |
| POST | `/api/v1/broadcasts` | `ops_manager` | `zoneId`, `staffNeeded` (> 0) | 201, broadcast object | 401, 403, 404, 409 `broadcast_open`, 422 |
| POST | `/api/v1/broadcasts/{broadcastId}/accept` | `floor_staff` | - | broadcast object (idempotent for the same user) | 401, 403, 404, 409 `expired`/`already_delegating`/`slots_full`, 422 |
| POST | `/api/v1/broadcasts/{broadcastId}/complete` | `floor_staff` | - | broadcast object | 401, 403 `wrong_role`/`not_your_response`, 404, 422 |
| POST | `/api/v1/broadcasts/{broadcastId}/finish` | `ops_manager` | - | `broadcastId`, `status` ("finished"), `finishedAt` | 401, 403, 404, 409 `not_all_done`, 422 |
| GET | `/api/v1/staff/{staffId}` | any role | - | `staffId`, `name`, `status` (`idle`/`delegating`) | 401, 404, 422 |

`broadcastId` and `staffId` are UUIDs (422 if malformed).

### Objects

| Object | Fields |
|---|---|
| user | `id` (UUID), `email`, `fullName`, `role` (`floor_staff`/`ops_manager`/`admin`), `isActive` |
| zone | `zoneId`, `name`, `mapImageUrl`, `polygon` (list of `[x, y]`) |
| broadcast | `broadcastId`, `zoneId`, `status` (`open`/`closed`/`finished`), `staffNeeded`, `expiresAt`, `acceptedCount`, `completedCount`, `acceptedStaff` (list of `staffId`, `name`, `completedAt` or null) |

## Worker routes (`/api/v1/cameras/{cameraId}`, `X-Internal-Api-Key`)

Every route requires the `X-Internal-Api-Key` header to equal `INTERNAL_API_KEY`.
A blank configured key rejects every call. A bearer token is not accepted.

| Method | Path | Request | Response | Errors |
|---|---|---|---|---|
| POST | `/api/v1/cameras/{cameraId}/detections` | JSON `counts`: `{zoneId: int}` | `{"status": "ok"}`; updates live state, WebSocket, then history and alerts in the background | 401, 422 |
| POST | `/api/v1/cameras/{cameraId}/frames` | raw JPEG body (annotated frame) | `{"status": "ok"}` | 401, 413, 422 |
| POST | `/api/v1/cameras/{cameraId}/frames/raw` | raw JPEG body (plain frame) | `{"status": "ok"}` | 401, 413, 422 |

## Dev-only routes (`/dev`, only when `APP_ENV=dev`)

| Method | Path | Auth | Request | Response | Errors |
|---|---|---|---|---|---|
| GET | `/dev/cameras/{cameraId}` | none | - | HTML page (login form, live view, zone editor, manual counts) | 422 |
| POST | `/dev/cameras/{cameraId}/detections` | bearer, `admin` | same as worker `detections` | `{"status": "ok"}` | 401, 403, 422 |
| POST | `/dev/tickets` | bearer, `admin` | `scope`: `stream:<cameraId>` or `crowd:live` | `ticket`, `expiresIn` (60) | 401, 403, 422, 503 |
| GET | `/dev/cameras/{cameraId}/layout` | bearer, `admin` | - | contents of `zones.json` (`anchor`, `zones`, ...) or `{"anchor": "bottom_center", "zones": []}` if missing | 401, 403, 422 |
| PUT | `/dev/cameras/{cameraId}/layout` | bearer, `admin` | `anchor` (`bottom_center`/`center`/`top_center`), `zones`: list of `id`, `name` (1..100 chars), `polygon` (3..100 points, each `[x, y]` in 0..1) | `{"status": "ok", "zones": <count>}`; writes one shared file for all cameras | 401, 403, 422 |
| GET | `/dev/cameras/{cameraId}/snapshot` | bearer, `admin` | - | latest raw frame, `image/jpeg` | 401, 403, 404, 422 |
| GET | `/dev/cameras/{cameraId}/stream` | `?ticket=` with scope `stream:<cameraId>` (admin) | - | `multipart/x-mixed-replace; boundary=frame` MJPEG, one frame every 0.2 s | 401, 404 before the first frame, 422 |

## System routes

| Method | Path | Auth | Response |
|---|---|---|---|
| GET | `/healthz` | none | `{"status": "ok"}` (no DB) |
| GET | `/readyz` | none | `{"status": "ready", "database": "connected"}`, or `{"status": "not_ready", "database": "unavailable"}` (still HTTP 200) |
