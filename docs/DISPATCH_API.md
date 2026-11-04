# Dispatch API

Covers the crowd-dispatch flow: zones, their live crowd count and status,
5-minute history, staff lookup, and the broadcast lifecycle a manager uses to
pull staff into a zone. Assumes the repository is set up and seeded: see the
main [README](../README.md) steps 1–7, or run `scripts/seed_dispatch.py` for one
admin, one manager, three staff and one zone (`z2`).

Moving from the old dispatch routes? See
[API_V1_MIGRATION.md](API_V1_MIGRATION.md).

Every endpoint below is under `/api/v1` and needs a bearer access token
(`Authorization: Bearer <accessToken>` from `POST /api/v1/auth/login`; see
[SETUP_AUTH.md](SETUP_AUTH.md)). A missing or invalid token gets `401
unauthenticated`. Some endpoints need a specific role; any other role gets `403
wrong_role`. Roles do not inherit, and `admin` has no dispatch powers.

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
    "zoneId": "z2",
    "name": "Zone 2",
    "mapImageUrl": "https://example.com/media/maps/placeholder.png",
    "polygon": [[0.0, 0.6], ...]
  }
]
```

### `GET /api/v1/zones/{zoneId}` (any role)

Same shape, one zone. `404 not_found` if the zone id is not in Postgres.

### `GET /api/v1/zones/{zoneId}/crowd` (any role)

```bash
curl -s http://127.0.0.1:8080/api/v1/zones/z2/crowd -H "Authorization: Bearer $TOKEN"
```

```json
{"zoneId": "z2", "count": 3, "status": "safe", "observedAt": "2026-11-03T19:00:00Z"}
```

Before the first detection for the zone: `count` is `0`, `status` is `safe`,
`observedAt` is `null`. `404 not_found` for an unknown zone.

`count` is the most recent value the cctv worker posted for the zone
(`LiveState.counts` in `src/plong_elven/state/live_state.py`). It is kept in
memory only, so it resets when the API restarts. `status` is computed on every
request from that count and the zone's `warning_threshold` and
`crowded_threshold` columns (`src/plong_elven/common/zone_status.py`):

```python
if count < warning_threshold:
    "safe"
elif count < crowded_threshold:
    "warning"
else:
    "crowded"
```

There is no hysteresis or cooldown. Use the **Dev Tools** tab of the dev page
(README step 11) to drive a zone through all three states without real people
in frame.

### `GET /api/v1/zones/{zoneId}/crowd/history?limit=` (any role)

Most recent 5-minute avg/peak buckets for a zone, newest first.

```bash
curl -s "http://127.0.0.1:8080/api/v1/zones/z2/crowd/history?limit=5" \
  -H "Authorization: Bearer $TOKEN"
```

```json
[
  {"bucketStart": "2026-10-26T16:05:00Z", "avgCount": 8.4, "peakCount": 11},
  {"bucketStart": "2026-10-26T16:00:00Z", "avgCount": 10.0, "peakCount": 15}
]
```

`limit` defaults to 12 (last hour), range 1 to 288 (last 24 hours). Each worker
detection post adds one sample to the zone's current 5-minute window. The
window is written to `zone_count_history` once a sample from the *next* window
arrives. There is no timer, so a closed window with no later sample stays in
memory until the zone's next sample. See `CrowdService.record_sample` in
`src/plong_elven/services/crowd_service.py`.

### `WS /api/v1/crowd/live` (any role)

Pushes every worker detection post as `{"cameraId": "default", "counts": {"z2": 3}}`.
The iOS app sends `Authorization: Bearer <accessToken>` on the upgrade request;
a browser uses `?ticket=` instead (see
[API_V1_MIGRATION.md §6](API_V1_MIGRATION.md#6-authentication-for-the-websocket-and-the-stream)).
Without valid credentials the socket is closed with code `1008`.

## Broadcasts

A broadcast is a manager's call for staff in one zone. A zone has at most one
`open` broadcast at a time (partial unique index).

```text
create (ops_manager) ──> open ──> staff accept (floor_staff) ──┬──> 20 s pass, not enough staff ──> closed
                                                                 └──> accepted staff complete ──> manager finishes ──> finished
```

The broadcast object returned by list, create, accept and complete:

```json
{
  "broadcastId": "6b1f...",
  "zoneId": "z2",
  "status": "open",
  "staffNeeded": 2,
  "expiresAt": "2026-11-03T19:00:20Z",
  "acceptedCount": 1,
  "completedCount": 0,
  "acceptedStaff": [{"staffId": "9c2e...", "name": "Bunga", "completedAt": null}]
}
```

### `POST /api/v1/broadcasts` (`ops_manager`)

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/broadcasts \
  -H "Authorization: Bearer $MANAGER_TOKEN" -H "Content-Type: application/json" \
  -d '{"zoneId": "z2", "staffNeeded": 2}'
```

`201` with the broadcast object. `404 not_found` for an unknown zone, `409
broadcast_open` if the zone already has an open broadcast (an expired one is
closed first), `422 validation_error` if `staffNeeded` is not above 0. Expires
20 seconds after creation (`BroadcastService.create` in
`src/plong_elven/services/broadcast_service.py`). On success it pushes every
`floor_staff` user with a device token; see
[PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

The role check runs before body validation, so a `floor_staff` user with an
invalid body gets `403`, not `422`.

### `GET /api/v1/broadcasts?zoneId=&status=&limit=` (any role)

```bash
curl -s "http://127.0.0.1:8080/api/v1/broadcasts?zoneId=z2&status=open" \
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

The calling user accepts the broadcast; returns the broadcast object. `404
not_found` for an unknown id, `409 expired` if the 20 seconds passed, `409
already_delegating` if the user is on another broadcast, `409 slots_full` if
`staffNeeded` staff already accepted. A repeat call from the same user returns
`200` without a duplicate row.

### `POST /api/v1/broadcasts/{broadcastId}/complete` (`floor_staff`)

The calling user marks their own acceptance done and goes back to `idle`;
returns the broadcast object. `403 not_your_response` if they never accepted
this broadcast.

### `POST /api/v1/broadcasts/{broadcastId}/finish` (`ops_manager`)

```json
{"broadcastId": "6b1f...", "status": "finished", "finishedAt": "2026-11-03T19:02:00Z"}
```

`409 not_all_done` unless at least one staff member accepted and every
accepted staff member completed.

## Staff lookup

### `GET /api/v1/staff/{staffId}` (any role)

Returns `{"staffId", "name", "status"}` (`status` is `idle` or `delegating`).
`404 not_found` for an unknown id. A `floor_staff` user *is* staff for
dispatch; there is no separate staff identity (see
[AUTH_SCHEMA.md](AUTH_SCHEMA.md#later-addition-dispatch-fields-on-users)).

## Errors and datetimes

Every error has the same shape:

```json
{"error": {"code": "wrong_role", "message": "This action requires the ops_manager role."}}
```

The full code list is in
[API_V1_MIGRATION.md §4](API_V1_MIGRATION.md#4-errors). Every datetime is UTC
and ends in `Z`.
