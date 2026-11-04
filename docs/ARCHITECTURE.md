# Architecture (as built)

This describes the code on `dev/api-contract-rework`. Routes are listed in
[API_REFERENCE.md](API_REFERENCE.md), settings in [CONFIGURATION.md](CONFIGURATION.md).

## Processes

| Process | Entry point | Talks to |
|---|---|---|
| API | `uvicorn plong_elven.api.main:app` (`make api`) | Postgres, APNs, browsers/iOS app |
| CCTV worker | `python -m plong_elven.cctv` (`make cctv`) | camera (RTSP/index/file), API over HTTP |
| Postgres 16 | `infra/docker-compose.yml` (`make db-up`) | API, Alembic |

## Layers

```
routers (api/routers/*)      HTTP only: parse, auth dependencies, call a service
   |                          dependencies wired in api/deps.py
   v
services (services/*)        business rules, raise AppError; no fastapi imports
   |
   v
db session (db/session.py)   SQLAlchemy async session / SessionFactory
state (state/live_state.py)  in-memory LiveState singleton
```

- `api/main.py` `create_app()` includes the routers, adds the `/dev` router only
  when `APP_ENV=dev`, and installs three exception handlers (`AppError`,
  `HTTPException`, `RequestValidationError`) that all return
  `{"error": {"code", "message"}}`.
- Services: `AuthService`, `BroadcastService`, `CrowdService`, `CameraService`,
  `AlertService`, `NotificationService`. `services/errors.py` defines `AppError(status_code, code, message)`.
- `tests/test_broadcast_service.py` checks that `broadcast_service.py` does not
  contain the string `fastapi`.

### Interfaces (`services/ports.py`)

| Protocol | Method | Implementations |
|---|---|---|
| `PushSender` | `async send(tokens, title, body)`; must log, never raise | `ApnsPushSender` (`services/apns_sender.py`, APNs HTTP/2 with an ES256 `.p8` JWT cached 50 min); `FakePushSender` in `tests/conftest.py` |
| `AlertRule` | `evaluate(zone, count, previous_status) -> Alert \| None` | `StatusChangeRule` (`services/alert_service.py`) |

`Alert` is a frozen dataclass: `key`, `cooldown`, `audience_role`, `title`, `body`.

`default_alert_rules()` returns two `StatusChangeRule`s: `warning` and `crowded`,
both with a 5-minute cooldown, audience `ops_manager`. A rule fires only when
the zone newly enters its status. `AlertService.on_count()` stores the new
status in `LiveState.last_status`, skips an alert whose `(zone, key)` cooldown
has not passed (`LiveState.last_alert_sent_at`), and calls
`NotificationService.notify_role()`. `NotificationService` opens its own
session, selects `push_token` of every user with that role, and calls the
`PushSender`.

Status is `common/zone_status.classify()`: `safe` below `warning_threshold`,
`warning` below `crowded_threshold`, else `crowded`.

### LiveState (`state/live_state.py`)

One module-level `LiveState` instance, returned by `get_live_state()` (tests
override it). Fields:

| Field | Key | Holds |
|---|---|---|
| `counts` | zone id | latest count |
| `observed_at` | zone id | time of latest count |
| `buckets` | zone id | current 5-minute `Bucket` (sum, n, peak) |
| `last_status` | zone id | last classified status |
| `last_alert_sent_at` | (zone id, alert key) | last push time |
| `frames` / `raw_frames` | camera id | latest annotated / raw JPEG |

## Detection flow

```
camera --(RTSP/index/file)--> cctv worker (YOLO person detection, zone assign)
   |  every --post-interval (1 s): POST /api/v1/cameras/{id}/detections  {"counts": {...}}
   |  every --frame-interval (0.2 s): POST /api/v1/cameras/{id}/frames and /frames/raw (JPEG)
   v
API cameras router (X-Internal-Api-Key)
   |-- ingest_counts():
   |     1. CrowdService.record_live -> LiveState.counts / observed_at   (sync, in request)
   |     2. publish_counts -> every WS /api/v1/crowd/live client gets {"cameraId","counts"}
   |     3. BackgroundTask process_counts_in_new_session (own session):
   |          for each zone id that exists in the DB `zones` table:
   |            record_sample -> 5-min bucket; on rollover INSERT zone_count_history
   |            AlertService.on_count -> NotificationService -> PushSender (APNs)
   v
GET /api/v1/zones/{id}/crowd reads LiveState; /crowd/history reads zone_count_history
```

- The worker's uploader (`cctv/uploader.py`) keeps only the newest pending item
  and backs off from 0.5 s to 10 s on failure; post errors are logged, not raised.
- A history bucket is flushed only when the next sample for that zone arrives in a
  new window. There is no timer.
- `POST /dev/cameras/{id}/detections` (admin) uses the same `ingest_counts()`.
- Frames go to `LiveState.frames` / `raw_frames`; the dev router serves them as
  `/dev/cameras/{id}/snapshot` and an MJPEG `/stream` (re-sent every 0.2 s).

## Broadcast (dispatch) flow

`services/broadcast_service.py`:

1. `ops_manager` creates (`POST /api/v1/broadcasts`): zone must exist; one open
   broadcast per zone (409 `broadcast_open`, also enforced by the partial unique
   index `one_open_broadcast_per_zone`); `expires_at = now + 20 s`. A background
   task pushes "New broadcast" to all `floor_staff`.
2. `floor_staff` accepts: `SELECT ... FOR UPDATE` locks the broadcast row first,
   so two staff racing for the last slot are serialized. Then: expired -> close,
   409 `expired`; already accepted -> return current state (idempotent); user
   `status == "delegating"` -> 409 `already_delegating`; slots taken -> 409
   `slots_full`. On success a `broadcast_responses` row is added and the user's
   status becomes `delegating`. `tests/test_broadcast_race_pg.py` proves the lock
   against real Postgres.
3. `floor_staff` completes: 403 `not_your_response` if they did not accept; sets
   `completed_at`, user status back to `idle`.
4. `ops_manager` finishes: 409 `not_all_done` unless at least one accepted and
   all accepted completed; sets status `finished` and `finished_at`.

Listing closes expired open broadcasts and commits before it queries, so
`status=open` never returns an expired one.

## Auth model

| Caller | Credential | Checked by |
|---|---|---|
| iOS app / API clients | `Authorization: Bearer <access JWT>` (HS256, `type=access`, `ACCESS_TOKEN_MINUTES`, default 15) | `deps.get_current_user` -> `AuthService.user_from_access_token`; user must be active |
| Role-gated routes | same bearer + role name | `deps.require_role(...)`; roles do not inherit (admin has no dispatch powers) |
| Token refresh | opaque refresh token (48 bytes, stored as SHA-256 in `refresh_tokens`, `REFRESH_TOKEN_DAYS`, default 30) | `/auth/refresh` revokes the old token and issues a new pair (rotation); `/auth/logout` revokes it and clears the user's `push_token` |
| CCTV worker | `X-Internal-Api-Key` header | `api/security.check_internal_key`, constant-time compare; a blank configured key rejects every call, even in dev; bearer tokens are not accepted here |
| Dev page stream / WS from a browser | `?ticket=` JWT (`type=ticket`, scope `stream:<cameraId>` or `crowd:live`, 60 s) | `AuthService.verify_ticket`; user must be active **and** admin. Tickets are minted only by `POST /dev/tickets`, which exists only with `APP_ENV=dev` |

`WS /api/v1/crowd/live` accepts either a bearer header or a `crowd:live` ticket,
closes the DB session before accepting the socket, and closes with code 1008 on
failure. Passwords are hashed with Argon2. If `JWT_SECRET` is shorter than 32
characters, any token operation returns 503 `auth_not_configured`.

Roles are seeded by migrations: `floor_staff` (1), `ops_manager` (2), `admin` (3).

## Directory tree

```
src/plong_elven/
  api/
    main.py            create_app, error handlers, /healthz, /readyz
    deps.py            DI: current user, roles, internal key, services
    security.py        X-Internal-Api-Key check
    routers/           auth, profile, zones, crowd (+WS), broadcasts, staff, cameras, dev
  services/            auth, broadcast, crowd, camera, alert, notification, apns_sender, ports, errors
  schemas/             Pydantic request/response models (camelCase)
  state/live_state.py  in-memory LiveState
  db/                  base, models (roles, users, refresh_tokens, zones, zone_count_history, broadcasts, broadcast_responses), session
  common/              settings, zones.json load/save/validate, zone_status.classify
  cctv/                worker: loop (CLI), detector (YOLO), zones (pixel math), overlay, client, uploader
alembic/versions/      5 migrations
scripts/               create_user, seed_dispatch, doctor, generate_mediamtx_config, benchmark_cctv
infra/docker-compose.yml  Postgres 16
tests/
```

## Known limits

- **Single process.** Live counts, alert cooldowns, history buckets, frames and
  the WebSocket client set are in-process memory. Running more than one uvicorn
  worker splits them (the `dev.py` docstring says to swap to Redis if that happens).
- **State is lost on restart.** Counts reset to 0 (`observedAt` null), an open
  history bucket is dropped, and cooldowns reset.
- **Two zone sources.** The worker counts zones from `zones.json` (`ZONES_PATH`),
  edited via `PUT /dev/cameras/{id}/layout`. Zone reads, thresholds, history and
  alerts use the DB `zones` table. Counts for an id not in the DB still update
  live state and the WebSocket, but get no history and no alerts. The layout file
  is one shared file for all cameras. `scripts/seed_dispatch.py` copies only the
  first zone of `zones.json` into the DB.
- Broadcast listing does N+1 count queries per broadcast (fine at limit <= 50, per
  a code comment).
