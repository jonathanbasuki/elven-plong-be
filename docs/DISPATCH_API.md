# Dispatch API

Covers the crowd-dispatch flow: zones, their live crowd status, 5-minute
history, and the broadcast lifecycle a manager uses to pull staff into a zone.
Assumes the repository is set up and seeded — see the main
[README](../README.md) steps 1–7, or run `scripts/seed_dispatch.py` for one
manager + three staff + one zone (`z2`).

Every endpoint below is under `/api/v1/dispatch` and requires a bearer access
token (`Authorization: Bearer <access_token>` from `POST /api/v1/auth/login`
— see [SETUP_AUTH.md](SETUP_AUTH.md)). A missing/invalid token gets `401`. A
few endpoints additionally require a specific role; a token with the wrong
role gets `403` with `error.code = "wrong_role"`.

## Zones

### `GET /api/v1/dispatch/zones`

Lists every zone with its live status. Used by the app at login time to
discover the real zone id instead of hardcoding one (this is a single-zone
POC, so the app just uses whichever zone comes back first).

```bash
curl -s http://127.0.0.1:8080/api/v1/dispatch/zones \
  -H "Authorization: Bearer $TOKEN"
```

```json
[
  {
    "zoneId": "z2",
    "name": "Zone 2",
    "currentCount": 3,
    "status": "safe",
    "mapImageUrl": "https://example.com/media/maps/placeholder.png",
    "polygon": [[0.0, 0.6], ...]
  }
]
```

### `GET /api/v1/dispatch/zones/{zoneId}`

Same shape as above, for one zone. `404` with `error.code = "not_found"` if
the zone id doesn't exist in Postgres.

### Status logic

`currentCount` comes from an in-memory dict (`latest_counts` in
`src/plong_elven/api/detections.py`), the most recent value the cctv worker
posted via `POST /detections` for that zone — not persisted, so it resets on
API restart. `status` is a pure, stateless function of that count against the
zone's own `warning_threshold`/`crowded_threshold` columns
(`src/plong_elven/common/zone_status.py`):

```python
if count < warning_threshold:
    "safe"
elif count < crowded_threshold:
    "warning"
else:
    "crowded"
```

Re-evaluated fresh on every request — no hysteresis, no cooldown. Use the
in-browser **Dev Tools** tab (see [SETUP_CCTV.md](SETUP_CCTV.md)) to drive a
zone through all three states without needing real people in frame.

### `GET /api/v1/dispatch/zones/{zoneId}/history`

Last 5-minute avg/peak buckets for a zone, most recent first.

```bash
curl -s "http://127.0.0.1:8080/api/v1/dispatch/zones/z2/history?limit=5" \
  -H "Authorization: Bearer $TOKEN"
```

```json
[
  {"bucketStart": "2026-10-26T16:05:00Z", "avgCount": 8.4, "peakCount": 11},
  {"bucketStart": "2026-10-26T16:00:00Z", "avgCount": 10.0, "peakCount": 15}
]
```

`limit` defaults to 12 (last hour), max 288 (last 24 hours). Each `POST
/detections` call accumulates one sample into the zone's current 5-minute
window (running sum/count for the average, running max for the peak); the
window is written to the `zone_count_history` table once a sample from the
*next* window arrives — there's no timer, so a window with no further
detections after it closes just sits in memory until the zone's next sample
rolls it over. See `_record_count_sample` in
`src/plong_elven/api/detections.py`.

## Broadcasts

A broadcast is a manager's call for staff in one zone. One zone can have at
most one `open` broadcast at a time (enforced by a partial unique index).

```text
create (ops_manager) ──> open ──> staff respond (floor_staff) ──┬──> expires after 20s, no one accepted ──> closed
                                                                  └──> accepted staff complete ──> manager finishes ──> finished
```

### `POST /api/v1/dispatch/broadcasts` — requires `ops_manager`

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/dispatch/broadcasts \
  -H "Authorization: Bearer $MANAGER_TOKEN" -H "Content-Type: application/json" \
  -d '{"zoneId": "z2", "staffNeeded": 2}'
```

`409 broadcast_open` if the zone already has an open broadcast. On success,
pushes every `floor_staff` user with a registered device token — see
[PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md). Expires 20 seconds after
creation (hardcoded; see `create_broadcast` in `src/plong_elven/api/dispatch.py`).

### `GET /api/v1/dispatch/broadcasts/current?zoneId={zoneId}`

Latest broadcast for a zone, open or not. Lazily flips `status` from `open`
to `closed` on read if the 20-second window has passed. `404 not_found` if
the zone has never had a broadcast. Both the manager and staff apps poll this
every ~1 second.

### `POST /api/v1/dispatch/broadcasts/{broadcastId}/respond` — requires `floor_staff`

The calling user accepts the broadcast. `409 expired` if the window passed,
`409 already_delegating` if that user is already on another active response,
`409 slots_full` if `staffNeeded` responses already exist. Idempotent — a
repeat call from the same user returns `200` without creating a duplicate row.

### `POST /api/v1/dispatch/broadcasts/{broadcastId}/complete`

The calling user marks their own acceptance as done. `403 not_your_response`
if they never accepted this broadcast.

### `POST /api/v1/dispatch/broadcasts/{broadcastId}/finish` — requires `ops_manager`

Closes out the broadcast. `409 not_all_done` unless every accepted staff
member has completed.

## Staff lookup

### `GET /api/v1/dispatch/staff/{staffId}`

Returns `{"staffId", "name", "status"}` for one user (`status` is `idle` or
`delegating`). `404 not_found` if the id doesn't exist. A `floor_staff` user
*is* staff for dispatch purposes — there's no separate staff identity; see
[AUTH_SCHEMA.md](AUTH_SCHEMA.md#later-addition-dispatch-fields-on-users).

## Error shape

Every error response from this router follows the same shape:

```json
{"error": {"code": "wrong_role", "message": "This action requires the ops_manager role."}}
```
