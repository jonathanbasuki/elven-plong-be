> Implemented; see docs/superpowers/specs/2026-11-03-api-contract-rework-design.md (the design wins where they differ).

# Proposal: Cleaner API Contract

Status: **draft for team discussion**. Nothing in this document is implemented.
Companion document: [PROPOSAL_ARCHITECTURE.md](PROPOSAL_ARCHITECTURE.md).

Facts below come from reading the routers in `src/plong_elven/api/`. Anything
not confirmed by the code is marked `TBD`.

## 1. Current contract

| Method + path | Caller | Auth |
|---|---|---|
| `GET /healthz`, `GET /readyz` | Anyone | None |
| `POST /api/v1/auth/login` | App | None |
| `POST /api/v1/auth/refresh`, `/logout` | App | Refresh token in body |
| `GET /api/v1/auth/profile` | App | Bearer |
| `POST /api/v1/auth/push-token` | App | Bearer |
| `GET /api/v1/dispatch/zones` | App | Bearer |
| `GET /api/v1/dispatch/zones/{zoneId}` | App | Bearer |
| `GET /api/v1/dispatch/zones/{zoneId}/history` | App | Bearer |
| `GET /api/v1/dispatch/broadcasts/current?zoneId=` | App | Bearer |
| `POST /api/v1/dispatch/broadcasts` | Manager | Bearer + role |
| `POST /api/v1/dispatch/broadcasts/{id}/respond` | Staff | Bearer + role |
| `POST /api/v1/dispatch/broadcasts/{id}/complete` | Staff | Bearer |
| `POST /api/v1/dispatch/broadcasts/{id}/finish` | Manager | Bearer + role |
| `GET /api/v1/dispatch/staff/{staffId}` | App | Bearer |
| `POST /detections` | Worker | `X-Internal-Api-Key` |
| `WS /ws/counts` | Browser UI (app usage `TBD`) | **None** |
| `POST /frames/{cameraId}`, `/frames/{cameraId}/raw` | Worker | `X-Internal-Api-Key` |
| `GET /frames/{cameraId}/latest` | Browser UI | `?key=` |
| `GET /stream/{cameraId}` | Browser UI | `?key=` |
| `GET /zones/{cameraId}` | Browser UI | **None** |
| `PUT /zones/{cameraId}` | Browser UI | `X-Internal-Api-Key` |
| `GET /ui/{cameraId}` | Browser | None |

## 2. Problems

| # | Problem | Example |
|---|---|---|
| 1 | Crowd data lives under dispatch | `currentCount` and `status` come from `/dispatch/zones/{id}`; history is `/dispatch/zones/{id}/history` |
| 2 | Two different "zones" APIs | `/api/v1/dispatch/zones` (DB zone) vs `/zones/{cameraId}` (polygon file) |
| 3 | Staff lives under dispatch | `/dispatch/staff/{id}` is a user lookup |
| 4 | Worker routes are not versioned or grouped | `/detections`, `/frames/*` sit at the root |
| 5 | Two error shapes | Dispatch: `{"error":{"code","message"}}`. Auth and others: `{"detail": "..."}` |
| 6 | Two JSON casings | Dispatch: `zoneId`, `staffNeeded`. Auth: `access_token`, `full_name`, `refresh_token`. Push token request: `pushToken` |
| 7 | "Current broadcast" is a special path | `/broadcasts/current?zoneId=` returns the newest broadcast of any status, not only open ones |
| 8 | Verb names for actions | `respond` actually means "accept" |
| 9 | Unauthenticated routes expose data | `WS /ws/counts`, `GET /zones/{cameraId}` |
| 10 | Browser UI uses the key in the URL | `?key=...` ends up in logs and history |

## 3. Principles

1. **One domain, one prefix.** Crowd data belongs to crowd, not dispatch.
   Dispatch only reads it.
2. **One kind of caller, one prefix.** App, admin, and worker routes are separate.
3. **One error shape, one casing** everywhere.
4. **Resources are nouns.** Actions on a resource use a sub-path
   (`/broadcasts/{id}/accept`).
5. **Version everything** under `/api/v1` or `/internal/v1`.

## 4. Proposed contract

### 4.1 App routes (bearer token) — `/api/v1`

| Domain | Method + path | Replaces |
|---|---|---|
| Auth | `POST /auth/login` | same |
| Auth | `POST /auth/refresh` | same |
| Auth | `POST /auth/logout` | same |
| Me | `GET /me` | `GET /auth/profile` |
| Me | `PUT /me/push-token` | `POST /auth/push-token` |
| Zones | `GET /zones` | `GET /dispatch/zones` (without count/status) |
| Zones | `GET /zones/{zoneId}` | `GET /dispatch/zones/{zoneId}` (without count/status) |
| Crowd | `GET /zones/{zoneId}/crowd` | `currentCount` + `status` from dispatch zones |
| Crowd | `GET /zones/{zoneId}/crowd/history?limit=` | `GET /dispatch/zones/{zoneId}/history` |
| Crowd | `WS /crowd/live` | `WS /ws/counts` |
| Broadcasts | `POST /broadcasts` | `POST /dispatch/broadcasts` |
| Broadcasts | `GET /broadcasts?zoneId=&status=` | `GET /dispatch/broadcasts/current` |
| Broadcasts | `GET /broadcasts/{id}` | new |
| Broadcasts | `POST /broadcasts/{id}/accept` | `.../respond` |
| Broadcasts | `POST /broadcasts/{id}/complete` | same |
| Broadcasts | `POST /broadcasts/{id}/finish` | same |
| Staff | `GET /staff/{staffId}` | `GET /dispatch/staff/{staffId}` |
| Staff | `GET /staff` | new (`TBD`: needed by the app?) |

Role rules stay the same: `POST /broadcasts` and `/finish` need `ops_manager`;
`/accept` needs `floor_staff`.

### 4.2 Camera admin routes — `/api/v1/cameras/{cameraId}`

| Method + path | Replaces |
|---|---|
| `GET /layout` | `GET /zones/{cameraId}` |
| `PUT /layout` | `PUT /zones/{cameraId}` |
| `GET /snapshot` | `GET /frames/{cameraId}/latest` |
| `GET /stream` | `GET /stream/{cameraId}` |

These need real authentication (bearer + admin role, or the internal key sent as
a header). `TBD`: who is the admin user today?

### 4.3 Worker routes — `/internal/v1/cameras/{cameraId}`

| Method + path | Replaces |
|---|---|
| `POST /detections` | `POST /detections` (camera id moves from body to path) |
| `POST /frames` | `POST /frames/{cameraId}` |
| `POST /frames/raw` | `POST /frames/{cameraId}/raw` |

All use the `X-Internal-Api-Key` header.

### 4.4 Dev and system

| Method + path | Notes |
|---|---|
| `GET /dev/cameras/{cameraId}` | Browser editor page; only enabled when `APP_ENV=dev` |
| `GET /healthz`, `GET /readyz` | Unchanged |

### 4.5 Zones: two meanings, two names

| Concept | Where | Name in contract |
|---|---|---|
| Zone as business object (name, thresholds, map image) | Postgres `zones` | `/zones` |
| Zone polygons drawn on a camera image | `zones.json` | `/cameras/{id}/layout` |

This removes the clash between the two `/zones` APIs. Whether both should share
one source of truth is a separate question (see the architecture doc).

## 5. Shared response rules

### Error shape (all routes)

```json
{ "error": { "code": "wrong_role", "message": "This action requires the ops_manager role." } }
```

| Case | Status | `code` |
|---|---|---|
| Missing/invalid token | 401 | `unauthenticated` |
| Wrong role | 403 | `wrong_role` |
| Not found | 404 | `not_found` |
| Rule conflict | 409 | `broadcast_open`, `expired`, `slots_full`, `already_delegating`, `not_all_done` |
| Bad input | 422 | `validation_error` |

Implementation: one exception handler for the domain error type and one for
FastAPI's `HTTPException` and validation errors.

### Casing

All JSON fields use camelCase. Auth fields change:

| Now | Proposed |
|---|---|
| `access_token` | `accessToken` |
| `refresh_token` | `refreshToken` |
| `expires_in` | `expiresIn` |
| `full_name` | `fullName` |
| `is_active` | `isActive` |
| `token_type` | `tokenType` |

### Crowd response example

```json
GET /api/v1/zones/z2/crowd
{
  "zoneId": "z2",
  "count": 7,
  "status": "warning",
  "observedAt": "2026-11-03T18:00:00Z"
}
```

`observedAt` is new. The code already stores it (`latest_count_observed_at`) but
no route returns it. It lets the app tell a stale count from a live one.

## 6. Compatibility and rollout

These changes break the iOS app unless both sides move together.

| Step | Action |
|---|---|
| 1 | Add the new routes next to the old ones (old routes call the same code) |
| 2 | Mark old routes `deprecated=True` in OpenAPI |
| 3 | Update the iOS app and the cctv worker to the new routes |
| 4 | Remove old routes after the app version that uses them is released |

Casing and error-shape changes are the riskiest for the app. They can ship last,
as a `v2` of auth, if the team prefers.

## 7. Open questions

- Which routes does the iOS app call today, and does it use `WS /ws/counts`?
  `TBD` — needs the app team.
- Is HLS video (MediaMTX) part of this contract? It is an external process, so
  it is left out here.
- Do we want pagination on `GET /broadcasts` and `GET /staff`? Not needed for the
  current single-zone proof of concept.
- Should crowd status thresholds be returned in `GET /zones/{id}`? Likely yes
  (`warningThreshold`, `crowdedThreshold`); `TBD` with the app team.
