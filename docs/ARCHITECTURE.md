# Architecture (as built)

This describes the code on `dev/readjust-erd`. Routes are listed in
[API_REFERENCE.md](API_REFERENCE.md), settings in [CONFIGURATION.md](CONFIGURATION.md).

## Processes

| Process | Entry point | Talks to |
|---|---|---|
| API | `uvicorn plong_elven.api.main:app` (`make api`) | Postgres, APNs, browsers/iOS app |
| Camera process | `python -m plong_elven.cctv` (`make cctv`) | CCTV device (RTSP/index/file), API over HTTP |
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
- Services (one per feature, in `services/`): `AuthService`, `TaskService`,
  `CrowdService`, `ZoneService`, `CameraService`. `profile`, `staff` and `dev` have no
  service of their own: they reuse `AuthService`, `TaskService` and others.
- Internal services (no route of their own, in `services/internal/`): `AlertService`,
  `NotificationService`. Only other services call them.
- Adapters (talk to the outside world, in `adapters/`): `ApnsPushSender`.
- Helpers (plain functions, no state, in `helpers/`): `zone_status.classify`.
- `services/errors.py` defines `AppError(status_code, code, message)`.
- `tests/test_task_service.py` checks that `task_service.py` does not
  contain the string `fastapi`.

### Interfaces (`services/ports.py`)

| Protocol | Method | Implementations |
|---|---|---|
| `PushSender` | `async send(tokens, title, body)`; must log, never raise | `ApnsPushSender` (`adapters/apns_sender.py`, APNs HTTP/2 with an ES256 `.p8` JWT cached 50 min); `FakePushSender` in `tests/conftest.py` |
| `AlertRule` | `evaluate(zone, count, previous_status) -> Alert \| None` | `StatusChangeRule` (`services/internal/alert_service.py`) |

`Alert` is a frozen dataclass: `key`, `cooldown`, `audience_role`, `title`, `body`.

`default_alert_rules()` returns one `StatusChangeRule`, `crowded`, with a
5-minute cooldown, audience `ops_manager`. It fires only when the zone newly
becomes crowded. `AlertService.on_count()` stores the new
status in `LiveState.last_status`, skips an alert whose `(zone, key)` cooldown
has not passed (`LiveState.last_alert_sent_at`), and calls
`NotificationService.notify_role()`. `NotificationService` opens its own
session, selects `push_token` of every user with that role (optionally
skipping users whose home zone is a given zone), and calls the `PushSender`.

Status is `helpers/zone_status.classify()`: `safe` below the zone's
`threshold`, else `crowded`.

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
CCTV device --(RTSP/index/file)--> camera process (YOLO person detection, zone assign)
   |  every 5 s (background thread): GET /api/v1/cameras/{id}/zones  (polygons from the DB)
   |  every --post-interval (1 s): POST /api/v1/cameras/{id}/detections  {"counts": {...}}
   |  every --frame-interval (0.2 s): POST /api/v1/cameras/{id}/frames and /frames/raw (JPEG)
   v
API cameras router (X-Internal-Api-Key)
   |-- ingest_counts():
   |     1. CrowdService.record_live -> LiveState.counts / observed_at   (sync, in request)
   |     2. publish_counts -> every WS /api/v1/crowd/live client gets {"cameraId","counts"}
   |     3. BackgroundTask process_counts_in_new_session (own session):
   |          for each zone id that exists in the DB `zone` table:
   |            record_sample -> 5-min bucket; on rollover INSERT zone_logs
   |            AlertService.on_count -> NotificationService -> PushSender (APNs)
   v
GET /api/v1/zones/{id}/crowd reads LiveState; /crowd/history reads zone_logs
```

- The camera's uploader (`cctv/uploader.py`) keeps only the newest pending item
  and backs off from 0.5 s to 10 s on failure; post errors are logged, not raised.
- A history bucket is flushed only when the next sample for that zone arrives in a
  new window. There is no timer.
- `POST /dev/cameras/{id}/detections` (admin) uses the same `ingest_counts()`.
- Frames go to `LiveState.frames` / `raw_frames`; the dev router serves them as
  `/dev/cameras/{id}/snapshot` and an MJPEG `/stream` (re-sent every 0.2 s).

## Task flow

`services/task_service.py`. Statuses are lookup tables with fixed ids
(`TaskStatusId`, `TaskRespondentStatusId`, `UserStatusId` in `db/models.py`,
seeded by migration `5c8d2e1f9a63`); the API shows their lowercase names.

1. `ops_manager` broadcasts (`POST /api/v1/tasks/broadcast`): the zone row is
   locked (`SELECT ... FOR UPDATE`), expired tasks in the zone are closed, then
   `409 broadcast_open` if `zone.is_active_task`. Otherwise a task is created
   (`open`, `expires_at = now + 20 s`, required `instruction` ≤ 50 chars) and
   `zone.is_active_task` becomes true. A background task pushes "New task" to
   all `floor_staff` whose home zone (`users.zone_id`) is not this zone. The
   partial unique index `one_open_task_per_zone` (`task_status_id = 1`) is a
   second guard.
2. `floor_staff` accepts: `SELECT ... FOR UPDATE` locks the task row first, so
   two staff racing for the last slot are serialized. Then: not open (or just
   expired, which closes it) -> 409 `expired`; already accepted -> return
   current state (idempotent); home zone is the task's zone -> 409 `own_zone`;
   user `delegating` -> 409 `already_delegating`; slots taken -> 409
   `slots_full`. On success a `task_respondents` row (`ongoing`) is added and
   the user becomes `delegating`.
3. `floor_staff` completes: 403 `not_your_response` if they did not accept;
   the first call sets the respondent `completed` + `completed_at` and the
   user back to `idle`; a repeat call changes nothing.
4. `ops_manager` finishes: 409 `not_all_done` unless at least one accepted and
   all accepted completed; sets `finished`, `finished_at`, and
   `zone.is_active_task = false`.

Expiry is lazy (`close_expired_tasks`): reading tasks, reading zones,
broadcasting, accepting or finishing closes open tasks past `expires_at`. A
task that closes with no respondents also clears `zone.is_active_task`.
`tests/test_task_race_pg.py` proves both row locks against real Postgres.

## Auth model

| Caller | Credential | Checked by |
|---|---|---|
| iOS app / API clients | `Authorization: Bearer <access JWT>` (HS256, `type=access`, `ACCESS_TOKEN_MINUTES`, default 15) | `deps.get_current_user` -> `AuthService.user_from_access_token`; user must be active |
| Role-gated routes | same bearer + role name | `deps.require_role(...)`; roles do not inherit (admin has no dispatch powers) |
| Token refresh | opaque refresh token (48 bytes, stored as SHA-256 in `refresh_tokens`, `REFRESH_TOKEN_DAYS`, default 30) | `/auth/refresh` revokes the old token and issues a new pair (rotation); `/auth/logout` revokes it and clears the user's `push_token` |
| Camera process | `X-Internal-Api-Key` header | `api/security.check_internal_key`, constant-time compare; a blank configured key rejects every call, even in dev; bearer tokens are not accepted here |
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
    routers/           auth, profile, zones, crowd (+WS), tasks, staff, cameras, dev
  services/            auth, task, crowd, zone, camera, ports, errors
    internal/          alert, notification (no route of their own)
  adapters/            apns_sender (talks to Apple)
  helpers/             zone_status.classify (plain functions)
  schemas/             Pydantic request/response models (camelCase)
  state/live_state.py  in-memory LiveState
  db/                  base, models (roles, users, user_status, refresh_tokens, zone, zone_logs, task, task_status, task_respondents, task_respondent_status), session
  common/              settings
  cctv/                camera process: loop (CLI), detector (YOLO), zones (pixel math), zones_poller, overlay, client, uploader
alembic/versions/      8 migrations
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
- **One shared zone set.** Zones live only in the DB `zone` table. Every
  camera reads the same set (there is no camera-to-zone mapping yet). Counts
  for an id not in the DB still update live state and the WebSocket, but get
  no history and no alerts. A zone with tasks cannot be deleted.
- **A task with an unfinished respondent keeps its zone active.** If a
  respondent never completes, the zone cannot be broadcast again; there is no
  cancel action yet.
- Task listing does one respondent query per task (fine at limit <= 50, per
  a code comment).
