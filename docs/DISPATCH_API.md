# Dispatch API

Covers the crowd-dispatch flow: zones, their live crowd count and status,
5-minute history, staff lookup, and the broadcast lifecycle a manager uses to
pull staff into a zone. Assumes the repository is set up and seeded: see the
main [README](../README.md) steps 1–7. `scripts/seed_dispatch.py` creates one
admin, one manager, three staff and one zone: the first zone in `zones.json`
(`ZONES_PATH`). The examples use `z1`, the zone id in `zones.example.json`;
replace it with your own zone id.

Moving from the old `/api/v1/dispatch/*` routes? See
[API_V1_MIGRATION.md](API_V1_MIGRATION.md).

Every endpoint below is under `/api/v1` and needs a bearer access token
(`Authorization: Bearer <accessToken>` from `POST /api/v1/auth/login`; see
[SETUP_AUTH.md](SETUP_AUTH.md)). A missing or invalid token gets `401
unauthenticated`. Some endpoints need a specific role; any other role gets `403
wrong_role`. Roles do not inherit, and `admin` has no dispatch powers.

Route source: `src/plong_elven/api/routers/{zones,crowd,broadcasts,staff}.py`.
Logic: `src/plong_elven/services/{crowd_service,broadcast_service}.py`.

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

Zones live in the Postgres `zones` table. The zone editor's `zones.json` is a
separate file used by the cctv worker; a count posted for a zone id that is not
in Postgres updates the live count but is not written to history and raises no
alert.

### `GET /api/v1/zones` (any role)

Lists every zone, ordered by `zoneId`. The app uses it at login to discover the
real zone id instead of hardcoding one (single-zone POC: use the first zone).
Live count and status are not here; see `/crowd` below.

```bash
curl -s http://127.0.0.1:8080/api/v1/zones -H "Authorization: Bearer $TOKEN"
```

```json
[
  {
    "zoneId": "z1",
    "name": "Zone 1",
    "mapImageUrl": "https://example.com/media/maps/placeholder.png",
    "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]
  }
]
```

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

`count` is the most recent value posted for the zone, by the cctv worker or by
the dev page's manual count (`LiveState.counts` in
`src/plong_elven/state/live_state.py`). It is kept in memory only, so it resets
when the API restarts. `status` is computed on every request from that count
and the zone's `warning_threshold` and `crowded_threshold` columns
(`src/plong_elven/common/zone_status.py`):

```python
if count < warning_threshold:
    "safe"
elif count < crowded_threshold:
    "warning"
else:
    "crowded"
```

The status itself has no hysteresis. The push alerts that fire on a status
change have a 5-minute cooldown per zone and status (see
[PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md)). The seed script sets
`warning_threshold=5` and `crowded_threshold=10`. Use the **Dev Tools** tab of
the dev page (README step 11) to drive a zone through all three states without
real people in frame.

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
The window is written to `zone_count_history` once a sample from the *next*
window arrives. There is no timer, so a closed window with no later sample
stays in memory until the zone's next sample. See `CrowdService.record_sample`
in `src/plong_elven/services/crowd_service.py`.

### `WS /api/v1/crowd/live` (any role, or an admin ticket)

Sends every count post (worker or dev page) to every connected client as
`{"cameraId": "default", "counts": {"z1": 3}}`. There is no per-camera or
per-zone filter. Messages from the client are ignored.

The iOS app sends `Authorization: Bearer <accessToken>` on the upgrade request;
any role is accepted. A browser uses `?ticket=` instead: a `crowd:live` ticket
from `POST /dev/tickets`, which only an `admin` can mint and which exists only
with `APP_ENV=dev` (see
[API_V1_MIGRATION.md §6](API_V1_MIGRATION.md#6-authentication-for-the-websocket-and-the-stream)).
Without valid credentials the socket is closed with code `1008` before it is
accepted.

## Broadcasts

A broadcast is a manager's call for staff in one zone. A zone has at most one
`open` broadcast at a time (partial unique index on `broadcasts`, `status =
'open'`).

```text
create (ops_manager) ──> open ──┬──> 20 s pass ──> closed
                                │
staff accept (floor_staff) ─────┤
accepted staff complete ────────┤
                                └──> manager finishes (open or closed) ──> finished
```

- A broadcast `expiresAt` 20 seconds after creation. After that it becomes
  `closed`, whether or not enough staff accepted. There is no timer: the switch
  to `closed` is written when the broadcast is next read by list, create (same
  zone), accept or finish.
- `finish` works on an `open` or `closed` broadcast, as long as at least one
  staff member accepted and every accepted staff member completed.

The broadcast object returned by list, create, accept and complete:

```json
{
  "broadcastId": "6b1f...",
  "zoneId": "z1",
  "status": "open",
  "staffNeeded": 2,
  "expiresAt": "2026-11-03T19:00:20Z",
  "acceptedCount": 1,
  "completedCount": 0,
  "acceptedStaff": [{"staffId": "9c2e...", "name": "Bunga", "completedAt": null}]
}
```

`status` is `open`, `closed` or `finished`. `acceptedStaff` is ordered by
accept time.

### `POST /api/v1/broadcasts` (`ops_manager`)

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/broadcasts \
  -H "Authorization: Bearer $MANAGER_TOKEN" -H "Content-Type: application/json" \
  -d '{"zoneId": "z1", "staffNeeded": 2}'
```

`201` with the broadcast object. `404 not_found` for an unknown zone, `409
broadcast_open` if the zone already has an open broadcast that has not expired
(an expired one is closed first), `422 validation_error` if `staffNeeded` is
not above 0. On success it pushes every `floor_staff` user that has a push
token; see [PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

The role check runs before body validation, so a `floor_staff` user with an
invalid body gets `403`, not `422`.

### `GET /api/v1/broadcasts?zoneId=&status=&limit=` (any role)

```bash
curl -s "http://127.0.0.1:8080/api/v1/broadcasts?zoneId=z1&status=open" \
  -H "Authorization: Bearer $STAFF_TOKEN"
```

A list of broadcast objects, newest first. All parameters are optional:
`zoneId`, `status` (`open`, `closed` or `finished`), `limit` (default 20, range
1 to 50). No match is `200 []`. Before the list is built, any `open` broadcast
past `expiresAt` (within the `zoneId` filter) is set to `closed` and committed,
so `status=open` never returns an expired broadcast. For the latest broadcast
of any status, omit `status` and read the first item.

### `POST /api/v1/broadcasts/{broadcastId}/accept` (`floor_staff`)

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/broadcasts/$BROADCAST_ID/accept \
  -H "Authorization: Bearer $STAFF_TOKEN"
```

The calling user accepts the broadcast and their status becomes `delegating`;
returns the broadcast object. Checks run in this order:

1. `404 not_found` for an unknown id (`broadcastId` must be a UUID, otherwise
   `422`).
2. `409 expired` if the broadcast is still `open` and past `expiresAt` (it is
   closed in the same call).
3. A repeat call from a user who already accepted returns `200` with no
   duplicate row.
4. `409 already_delegating` if the user's status is `delegating` (on another
   broadcast).
5. `409 slots_full` if `staffNeeded` staff already accepted.

The broadcast row is locked (`SELECT ... FOR UPDATE`) so two staff cannot both
take the last slot.

Note: the `expired` check only looks at broadcasts whose status is still
`open`. A broadcast that was already set to `closed` by an earlier read is not
rejected with `expired` by this code path.

### `POST /api/v1/broadcasts/{broadcastId}/complete` (`floor_staff`)

The calling user marks their own acceptance done and goes back to `idle`;
returns the broadcast object. `404 not_found` for an unknown id, `403
not_your_response` if they never accepted this broadcast. A repeat call keeps
the first `completedAt`.

### `POST /api/v1/broadcasts/{broadcastId}/finish` (`ops_manager`)

```json
{"broadcastId": "6b1f...", "status": "finished", "finishedAt": "2026-11-03T19:02:00Z"}
```

`404 not_found` for an unknown id. `409 not_all_done` unless at least one staff
member accepted and every accepted staff member completed. Finishing an
already finished broadcast returns the same body with the original
`finishedAt`.

## Staff lookup

### `GET /api/v1/staff/{staffId}` (any role)

Returns `{"staffId", "name", "status"}` (`status` is `idle` or `delegating`).
`404 not_found` for an unknown id; `staffId` must be a UUID, otherwise `422`.
There is no separate staff table: `staffId` is a user id, and the lookup does
not check the user's role (see
[AUTH_SCHEMA.md](AUTH_SCHEMA.md#later-addition-dispatch-fields-on-users)).
`name` is the user's `fullName`.

## Errors and datetimes

Every error has the same shape:

```json
{"error": {"code": "wrong_role", "message": "This action requires the ops_manager role."}}
```

The full code list is in
[API_V1_MIGRATION.md §4](API_V1_MIGRATION.md#4-errors). Every datetime is UTC
and ends in `Z`.
