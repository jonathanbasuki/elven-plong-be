# Task API

Covers the crowd-task flow: zones, their live crowd count and status, 5-minute
history, staff lookup, the task lifecycle and task video footage. A manager starts a task by
**broadcasting** it to floor staff from other zones, who accept, go to the
crowded zone, and complete it. Assumes the repository is set up and seeded:
see the main [README](../README.md) steps 1–7. `scripts/seed_dispatch.py`
creates one admin, one manager, three staff and one zone, `z1`, with a
full-frame camera polygon and `threshold=10`. The examples use `z1`.

Every endpoint below is under `/api/v1` and needs a bearer access token
(`Authorization: Bearer <accessToken>` from `POST /api/v1/auth/login`; see
[SETUP_AUTH.md](SETUP_AUTH.md)). A missing or invalid token gets `401
unauthenticated`. Some endpoints need a specific role; any other role gets `403
wrong_role`. Roles do not inherit, and `admin` has no task powers.

Route source: `src/plong_elven/api/routers/{zones,crowd,tasks,staff}.py`.
Logic: `src/plong_elven/services/{zone_service,crowd_service,task_service}.py`.

Get tokens for the curl examples:

```bash
login() {
  curl -s -X POST http://127.0.0.1:8080/api/v1/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\": \"$1\", \"password\": \"password123\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["accessToken"])'
}
MANAGER_TOKEN=$(login manager@plongelven.io)
STAFF_TOKEN=$(login bunga@plongelven.io)
TOKEN=$MANAGER_TOKEN
```

## Zones

Zones live only in the Postgres `zone` table. The camera reads their polygons
from the API, and the dev page's zone editor writes them back (see
[SETUP_CCTV.md](SETUP_CCTV.md)). The polygon is in camera-frame coordinates and
is not part of the app contract: the frontend draws each zone on its own
floorplan and matches it by `zoneId`.

### `GET /api/v1/zones` (any role)

Lists every zone, ordered by `zoneId`. Live count and status are not here; see
`/crowd` below.

```bash
curl -s http://127.0.0.1:8080/api/v1/zones -H "Authorization: Bearer $TOKEN"
```

```json
[{"zoneId": "z1", "name": "Zone 1", "threshold": 10, "isActiveTask": false}]
```

`isActiveTask` is `true` while the zone has a task running (see
[Tasks](#tasks)). While it is `true`, the zone cannot be broadcast again.

### `GET /api/v1/zones/{zoneId}` (any role)

Same shape, one zone. `404 not_found` if the zone id is not in Postgres.

### `GET /api/v1/zones/{zoneId}/crowd` (any role)

```bash
curl -s http://127.0.0.1:8080/api/v1/zones/z1/crowd -H "Authorization: Bearer $TOKEN"
```

```json
{"zoneId": "z1", "count": 3, "status": "safe", "observedAt": "2026-11-03T19:00:00Z"}
```

Before the first detection for the zone: `count` is `0`, `status` is `safe`,
`observedAt` is `null`. `404 not_found` for an unknown zone.

`count` is the most recent value posted for the zone, by the camera or by the
dev page's manual count (`LiveState.counts` in
`src/plong_elven/state/live_state.py`). It is kept in memory only, so it resets
when the API restarts. `status` is computed on every request from that count
and the zone's `threshold` (`src/plong_elven/helpers/zone_status.py`):

```python
"safe" if count < threshold else "crowded"
```

The status itself has no hysteresis. The push alert that fires when a zone
becomes crowded has a 5-minute cooldown per zone (see
[PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md)). Use the **Dev Tools** tab of
the dev page (README step 11) to drive a zone between both states without real
people in frame.

### `GET /api/v1/zones/{zoneId}/crowd/history?limit=` (any role)

Most recent 5-minute avg/peak buckets for a zone, newest first.

```bash
curl -s "http://127.0.0.1:8080/api/v1/zones/z1/crowd/history?limit=5" \
  -H "Authorization: Bearer $TOKEN"
```

```json
[
  {"bucketStart": "2026-10-26T16:05:00Z", "avgCount": 8.4, "peakCount": 11},
  {"bucketStart": "2026-10-26T16:00:00Z", "avgCount": 10.0, "peakCount": 15}
]
```

`limit` defaults to 12 (last hour), range 1 to 288 (last 24 hours); outside
that range is `422 validation_error`. An unknown zone returns `200 []`, not
`404`. Each count post adds one sample to the zone's current 5-minute window.
The window is written to `zone_logs` once a sample from the *next* window
arrives. There is no timer, so a closed window with no later sample stays in
memory until the zone's next sample. See `CrowdService.record_sample` in
`src/plong_elven/services/crowd_service.py`.

### `WS /api/v1/crowd/live` (any role, or an admin ticket)

Sends every count post (camera or dev page) to every connected client as
`{"cameraId": "default", "counts": {"z1": 3}}`. There is no per-camera or
per-zone filter. Messages from the client are ignored.

The iOS app sends `Authorization: Bearer <accessToken>` on the upgrade request;
any role is accepted. A browser uses `?ticket=` instead: a `crowd:live` ticket
from `POST /dev/tickets`, which only an `admin` can mint and which exists only
with `APP_ENV=dev`. Without valid credentials the socket is closed with code
`1008` before it is accepted.

## Tasks

A task is a manager's call for staff from **other** zones to help in one
crowded zone. Every staff user may have a home zone (`users.zone_id`); staff
whose home zone is the task's zone are not notified and cannot accept it.

```text
broadcast (ops_manager) ──> open ──── 20 s pass ──┬──> ongoing  (someone accepted)
       │                     │                    └──> closed   (nobody accepted)
       │              staff accept                         │
       │              (floor_staff)              each helper completes
       │                                                   │
       │                                   last helper completes ──> complete
       └── manager cancels (open or ongoing) ──> closed
```

| Task `status` | Meaning |
|---|---|
| `open` | The 20-second window: staff can accept |
| `ongoing` | The window ended and at least one person accepted; they are working |
| `closed` | The window ended with nobody accepting, or a manager cancelled it |
| `complete` | Every helper pressed complete (set automatically) |
| `finished` | A manager finished it with `/finish` (older tasks; see below) |

- A task takes acceptances until `expiresAt`, 20 seconds after the broadcast.
  There is no timer: the switch to `ongoing` or `closed` is written when the
  task is next read (task list, task detail, zone read, broadcast in the same
  zone, accept or finish).
- When the last helper completes, the task becomes `complete` on its own,
  `finishedAt` is set to that moment, the zone is freed, and its video
  footage starts building (see `/footage` below).

**The zone's active flag.** A broadcast sets the zone's `isActiveTask` to
`true`. It goes back to `false` when the task becomes `complete`, is
finished or cancelled, or closes with nobody having accepted it. While it is
`true`, another broadcast into that zone gets `409 broadcast_open`.

The task object returned by list, broadcast, accept, complete and cancel:

```json
{
  "taskId": "6b1f...",
  "zoneId": "z1",
  "status": "ongoing",
  "totalStaff": 2,
  "instruction": "Guide people to exit B",
  "expiresAt": "2026-11-03T19:00:20Z",
  "acceptedCount": 1,
  "completedCount": 0,
  "acceptedStaff": [
    {"staffId": "9c2e...", "name": "Bunga", "status": "ongoing", "completedAt": null}
  ]
}
```

`status` is one of the five task statuses above. Each `acceptedStaff` entry
has its own `status`, `ongoing` or `completed` (a person's part, not the
task's). `acceptedStaff` is ordered by accept time.

### `POST /api/v1/tasks/broadcast` (`ops_manager`)

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/tasks/broadcast \
  -H "Authorization: Bearer $MANAGER_TOKEN" -H "Content-Type: application/json" \
  -d '{"zoneId": "z1", "totalStaff": 2, "instruction": "Guide people to exit B"}'
```

`201` with the task object. `instruction` is required, 1 to 50 characters
(surrounding spaces are trimmed). Errors:

- `404 not_found` for an unknown zone;
- `409 broadcast_open` if the zone has an active task (an expired task with
  nobody on it is closed first, which frees the zone);
- `422 validation_error` for `totalStaff` not above 0 or a missing, empty or
  too long `instruction`.

On success it pushes "New task" with `"<zone name>: <instruction>"` to every
`floor_staff` user with a push token whose home zone is not this zone; see
[PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

The role check runs before body validation, so a `floor_staff` user with an
invalid body gets `403`, not `422`.

### `GET /api/v1/tasks?zoneId=&status=&limit=` (any role)

```bash
curl -s "http://127.0.0.1:8080/api/v1/tasks?zoneId=z1&status=open&status=ongoing" \
  -H "Authorization: Bearer $STAFF_TOKEN"
```

A list of task objects, newest first. All parameters are optional: `zoneId`,
`status` (`open`, `ongoing`, `closed`, `complete` or `finished`), `limit`
(default 20, range 1 to 50). Repeat `status` to match several at once; the
tasks in progress are `?status=open&status=ongoing`. An empty or unknown value
is `422`. No match is `200 []`. Before the list is built, any `open` task past
`expiresAt` (within the `zoneId` filter) is moved to `ongoing` or `closed` and
committed, so `status=open` never returns an expired task.

### `GET /api/v1/tasks/{taskId}` (any role)

One task with everything its detail screen needs:

```json
{
  "taskId": "6b1f...",
  "zoneId": "z1",
  "zoneName": "Zone 1",
  "zoneStatus": "safe",
  "instruction": "Guide people to exit B",
  "status": "complete",
  "phase": "completed",
  "createdAt": "2026-11-06T17:32:00Z",
  "expiresAt": "2026-11-06T17:32:20Z",
  "finishedAt": "2026-11-06T18:12:00Z",
  "totalStaff": 2,
  "acceptedCount": 1,
  "completedCount": 1,
  "liveCrowd": null,
  "crowdLog": [
    {"bucketStart": "2026-11-06T17:30:00Z", "avgCount": 11.2, "peakCount": 15},
    {"bucketStart": "2026-11-06T18:10:00Z", "avgCount": 4.0, "peakCount": 6}
  ],
  "zoneStaff": [{"staffId": "1a2b...", "name": "Dedi", "status": "idle", "zoneId": "z1"}],
  "delegatedStaff": [
    {"staffId": "9c2e...", "name": "Bunga", "status": "completed", "completedAt": "2026-11-06T18:12:00Z"}
  ]
}
```

- `phase` is `ongoing` while the task is `open` or `ongoing`; `closed`,
  `complete` and `finished` are `completed`.
- While ongoing, `liveCrowd` holds the zone's current count (as `/crowd` does)
  and `crowdLog` is `[]`.
- Once completed, `liveCrowd` is `null` and `crowdLog` holds the zone's
  5-minute buckets, oldest first, from the bucket containing `createdAt` to
  the bucket containing the task's end: broadcast 17:32, completed 18:12 gives
  the buckets 17:30 … 18:10. The end is `finishedAt` (set by complete, finish
  and cancel); for a task that closed with nobody accepting, `expiresAt`. A
  bucket that is not saved yet is included from memory. A window with no
  detections has no bucket.
- `zoneStatus` comes from the zone's current live count and `threshold`.
- `zoneStaff` is the active floor staff whose home zone is this zone;
  `delegatedStaff` is the people who accepted (same as `acceptedStaff`).
- `404 not_found` for an unknown id, `422` if it is not a UUID.

### `POST /api/v1/tasks/{taskId}/accept` (`floor_staff`)

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/tasks/$TASK_ID/accept \
  -H "Authorization: Bearer $STAFF_TOKEN"
```

The calling user accepts the task, their part's status is `ongoing`, and
their user status becomes `delegating`; returns the task object. Checks run in
this order:

1. `404 not_found` for an unknown id (`taskId` must be a UUID, otherwise `422`).
2. A repeat call from a user who already accepted returns `200` with no
   duplicate row, even after the 20 seconds.
3. `409 expired` if the task is no longer `open`, or is still `open` but past
   `expiresAt` (it moves on in the same call).
4. `409 own_zone` if the user's home zone is the task's zone.
5. `409 already_delegating` if the user's status is `delegating` (on another
   task).
6. `409 slots_full` if `totalStaff` staff already accepted.

The task row is locked (`SELECT ... FOR UPDATE`) so two staff cannot both take
the last slot.

### `POST /api/v1/tasks/{taskId}/complete` (`floor_staff`)

The calling user marks their own part done: part status `completed`, user
status back to `idle`; returns the task object. If they were the last helper
still working, the task becomes `complete` (see above). `404 not_found` for an
unknown id, `403 not_your_response` if they never accepted this task, `409
expired` if the task was cancelled. A repeat call changes nothing: it keeps
the first `completedAt` and does not reset a user who has since accepted
another task.

### `POST /api/v1/tasks/{taskId}/cancel` (`ops_manager`)

Cancels a task that is `open` or `ongoing`. The task becomes `closed` with
`finishedAt` set to now, the zone is freed, and every helper who had not
completed goes back to `idle` (so they can accept other tasks). Their parts
stay in `acceptedStaff` as the record. Returns the task object. `409 not_open`
for a task that is already `closed`, `complete` or `finished`.

### `POST /api/v1/tasks/{taskId}/finish` (`ops_manager`)

```json
{"taskId": "6b1f...", "status": "finished", "finishedAt": "2026-11-03T19:02:00Z"}
```

Normally not needed: a task completes on its own when the last helper
completes. `409 not_all_done` unless at least one staff member accepted and
every accepted staff member completed. On an already `complete` or `finished`
task it changes nothing and returns `"status": "finished"` with the original
`finishedAt`.

### `GET /api/v1/tasks/{taskId}/footage` (any role)

```json
{"taskId": "6b1f...", "status": "ready", "url": "https://<project>.supabase.co/storage/v1/object/public/plong-task-footage/tasks/6b1f....mp4", "error": null}
```

The task's video, cut from the MediaMTX recordings between the broadcast and
the end, built in the background after the task becomes `complete`. `status`
is `processing`, `ready` (with `url`) or `failed` (with `error`). `404
not_found` until the task has completed. A job cut off by a server restart is
marked `failed` when the API starts again. Setup: the footage settings in
[CONFIGURATION.md](CONFIGURATION.md) and recording in
[SETUP_HLS.md](SETUP_HLS.md).

## Staff lookup

### `GET /api/v1/staff?zoneId=` (any role)

```bash
curl -s "http://127.0.0.1:8080/api/v1/staff?zoneId=z2" -H "Authorization: Bearer $TOKEN"
```

```json
[{"staffId": "9c2e...", "name": "Bunga", "status": "idle", "zoneId": "z2"}]
```

Every active `floor_staff` user, ordered by name. `zoneId` is optional and
keeps only staff whose home zone is that zone; an unknown zone gives `[]`.
Managers and admins are never listed.

### `GET /api/v1/staff/{staffId}` (any role)

Returns `{"staffId", "name", "status", "zoneId"}`: `status` is `idle` or
`delegating`, `zoneId` is the home zone or `null`. `404 not_found` for an
unknown id; `staffId` must be a UUID, otherwise `422`. There is no separate
staff table: `staffId` is a user id, and the lookup does not check the user's
role. `name` is the user's `fullName`.

Home zones are set directly in the database for now (`users.zone_id`); there
is no endpoint for it yet. `users.last_location` (the zone a beacon last saw
the user in) exists but is not used yet and is always `null`.

## Errors and datetimes

Every error has the same shape:

```json
{"error": {"code": "wrong_role", "message": "This action requires the ops_manager role."}}
```

The full code list is in [API_REFERENCE.md](API_REFERENCE.md#error-codes).
Every datetime is UTC and ends in `Z`.
