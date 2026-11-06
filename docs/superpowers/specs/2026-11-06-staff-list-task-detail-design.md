# Staff list, task detail, multi-status filter: design

Date: 2026-11-06. Branch: `dev/readjust-erd` (builds on the uncommitted task
schema rework: `task`, `task_status`, `task_respondents`, `zone.is_active_task`,
`users.zone_id`).

## Intent

The iOS app needs three things it cannot do today:

1. A manager picks staff from a zone, so the app needs **every floor staff
   member**, optionally narrowed to one home zone.
2. Tapping a task opens a **detail screen**: what the task is, how the zone is
   doing, and who is involved (the zone's own staff and the staff delegated to
   help). While the task runs it shows the live crowd count; once it is over it
   shows how the crowd went from the broadcast to the end.
3. The task list can filter on **several statuses at once** (e.g.
   `open` and `closed`) in one call.

"Ongoing" means status `open` only, for now (user decision, 2026-11-06).

## Goals

- `GET /api/v1/staff?zoneId=` lists floor staff, optionally by home zone.
- `GET /api/v1/tasks/{taskId}` returns one task with zone, crowd and staff data.
- `GET /api/v1/tasks?status=open&status=closed` filters on several statuses.

## Non-goals

- Cancelling a task or broadcast (dropped by the user on 2026-11-06).
- Creating, editing or deleting zones outside the dev editor.
- A new way to see who accepted a task: `acceptedStaff` already does it.
- A per-task crowd log table. The log reuses the 5-minute history.
- Adding `createdAt` / `finishedAt` to the existing `TaskOut` list items.
- Pushing anything to managers.

## Anti-goals

- Changing any existing response shape or route. Every current call keeps
  working exactly as it does now (`?status=open` alone included).

## Constraints

- Statuses are lookup tables with fixed ids (`TaskStatusId` in `db/models.py`,
  seeded by migration `5c8d2e1f9a63`; the partial index
  `one_open_task_per_zone` uses id 1).
- Services import no FastAPI (`tests/test_task_service.py` checks this).
- JSON is camelCase, datetimes are UTC ending in `Z` (`UtcDatetime`).
- Task expiry is lazy (`close_expired_tasks`): any read of a task first closes
  it if it is `open` and past `expires_at`.
- Single API process: live counts and the current 5-minute bucket are in
  memory (`LiveState`).

## Approaches considered

### Crowd log source (chosen by the user: A)

- **A. Reuse the 5-minute history (`zone_logs`).** No new table; a short task
  may have only one or two points.
- B. A new per-task log sampled every 10 s. Finer chart, one more table.

### A closed task nobody accepted (settled by the user)

The reviewer noted such a task stays `closed` forever. Two fixes were offered
(auto-`finished`, or a new `unanswered` status). The user chose neither: for
now "ongoing" is `open` only, so a closed task is shown as completed and
nothing about storing statuses changes.

## Design

### 1. `GET /api/v1/staff?zoneId=` (any role)

- Returns active users whose role is `floor_staff`; with `zoneId`, only those
  whose home zone (`users.zone_id`) equals it. Ordered by `full_name`.
- Each item is the existing `StaffOut`: `staffId`, `name`, `status`
  (`idle`/`delegating`), `zoneId`.
- An unknown `zoneId` returns `[]` (same as `/crowd/history`), not 404.
- Lives in `routers/staff.py` beside `GET /staff/{staffId}`.

### 2. `GET /api/v1/tasks/{taskId}` (any role)

Applies lazy expiry to the task first, then returns `TaskDetailOut`:

```json
{
  "taskId": "uuid",
  "zoneId": "z1",
  "zoneName": "Zone 1",
  "zoneStatus": "crowded",
  "instruction": "Guide people to exit B",
  "status": "closed",
  "phase": "ongoing",
  "createdAt": "2026-11-06T17:32:00Z",
  "expiresAt": "2026-11-06T17:32:20Z",
  "finishedAt": null,
  "totalStaff": 2,
  "acceptedCount": 1,
  "completedCount": 0,
  "liveCrowd": {"count": 12, "observedAt": "2026-11-06T17:40:01Z"},
  "crowdLog": [],
  "zoneStaff": [{"staffId": "uuid", "name": "Dedi", "status": "idle", "zoneId": "z1"}],
  "delegatedStaff": [
    {"staffId": "uuid", "name": "Bunga", "status": "ongoing", "completedAt": null}
  ]
}
```

- `status` is the stored status.
- `phase`: `ongoing` for `open` only; `completed` for `closed` and `finished`.
- `zoneStatus`: `safe`/`crowded` from the zone's current live count and
  `threshold` (`helpers/zone_status.classify`); `safe` before any detection.
- **Crowd, by phase:**
  - `ongoing`: `liveCrowd` is the live count (`LiveState.counts`, 0 if none)
    and `observedAt` (null if none); `crowdLog` is `[]`.
  - `completed`: `liveCrowd` is `null`; `crowdLog` holds the 5-minute buckets
    (`bucketStart`, `avgCount`, `peakCount`, the existing `HistoryBucketOut`),
    **oldest first**, from the bucket containing `createdAt` to the bucket
    containing the task's end time, inclusive. Example: broadcast 17:32,
    finished 18:12 → buckets 17:30, 17:35 … 18:10 (covering 17:30–18:15).
  - The end time is (my call): `finishedAt` if set; otherwise, for a `closed`
    task with respondents (staff still working), the current time; for a
    `closed` task nobody accepted, `expiresAt` (it ended when the window shut,
    and this keeps old dead tasks from returning an ever-growing log).
  - Buckets come from `zone_logs`. If the zone's in-memory current bucket
    (`LiveState.buckets[zoneId]`, not yet flushed) falls in the range and is
    not already in `zone_logs`, it is added (avg rounded to 2 places, as the
    flush does). Buckets with no samples simply do not appear.
- `zoneStaff`: active `floor_staff` users whose home zone is the task's zone,
  ordered by name, as `StaffOut`.
- `delegatedStaff`: the respondents, same items and order as today's
  `acceptedStaff`.
- 404 `not_found` for an unknown task; 422 for a malformed UUID.

### 3. Multi-status filter on `GET /api/v1/tasks`

- `status` becomes a repeatable query param:
  `list[Literal["open","closed","finished"]] | None`. Kept although "ongoing"
  is now just `?status=open`: it was agreed and costs a few lines.
- Service filters with `task_status_id IN (...)`. No `status` → all statuses.
  An unknown value → 422. Duplicates are fine.

### Tests (one per risky rule)

- Staff list: floor staff only, inactive excluded, `zoneId` filter, unknown
  zone `[]`.
- Detail: `open` shows `liveCrowd` and empty `crowdLog`; `closed` and
  `finished` show a log; the bucket range from the 17:32/18:12 example
  (buckets outside excluded); the end-time rule for closed tasks with and
  without respondents; the
  unflushed in-memory bucket is included once; `zoneStaff` vs `delegatedStaff`;
  404.
- Filter: `open&closed` excludes finished; single value unchanged; bad value 422.
- Real Postgres (`test_task_race_pg.py`): the crowd-log range query against
  `zone_logs`.
- Docs: `API_REFERENCE.md`, `TASK_API.md`, `FRONTEND_GUIDE.md`.

## Left to the builder

- Whether staff-list logic sits in `TaskService` or a new `StaffService`.
- Internal helper names and file split.
