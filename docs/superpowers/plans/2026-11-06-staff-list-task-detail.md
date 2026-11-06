# Staff list, task detail, multi-status filter: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use dev-skills:superpowers-subagent-driven-development (recommended) or dev-skills:superpowers-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `GET /api/v1/staff?zoneId=`, `GET /api/v1/tasks/{taskId}` (with live
crowd or a 5-minute crowd log), and a repeatable `status` filter on `GET /api/v1/tasks`.

**Architecture:** FastAPI routers stay thin and call `TaskService`
(`src/plong_elven/services/task_service.py`) and `CrowdService`
(`src/plong_elven/services/crowd_service.py`). The crowd log reads the existing
`zone_logs` table plus the in-memory current bucket (`LiveState.buckets`). No
migration.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2 async, PostgreSQL 16, pytest +
pytest-asyncio, ruff.

**Spec:** `docs/superpowers/specs/2026-11-06-staff-list-task-detail-design.md`

## Global Constraints

- Do **not** commit. The user reviews and commits the whole branch themselves.
- Services import no FastAPI (`tests/test_task_service.py` checks `task_service.py`).
- JSON fields camelCase; datetimes use `UtcDatetime` from `schemas/zones.py` (JSON ends in `Z`).
- No existing response shape or route changes; `?status=open` alone behaves as today.
- "Ongoing" = status `open` only; `closed` and `finished` are `completed`.
- Crowd log = 5-minute buckets (`BUCKET_SECONDS = 300` in `crowd_service.py`), oldest first.
- Staff lists contain active (`is_active`) users with role `floor_staff` only, ordered by `full_name`.
- Every task ends with: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` all green.
- Postgres tests run with `DATABASE_URL=<scratch db> REQUIRE_PG=1`; never against the user's `plong_elven` database (create a scratch DB in the `infra-db-1` container, `alembic upgrade head`, drop it after).

## Review Focus

1. A completed task whose zone has no `zone_logs` rows and no live bucket → `crowdLog` is `[]`, not an error. (Task 3, `test_detail_completed_without_logs_is_empty_list`)
2. A zone with no detection since the API started → `liveCrowd` `{"count": 0, "observedAt": null}` and `zoneStatus` `"safe"`. (Task 3, `test_detail_open_task_before_any_detection`)
3. A `closed` task nobody accepted, read days later → log ends at `expiresAt`, so it returns at most one or two buckets. (Task 3, `test_crowd_log_end_rules`)
4. `?status=` with an empty or unknown value → `422 validation_error`; repeated identical values are fine. (Task 1)
5. Inactive floor staff and non-floor-staff users in the zone never appear in `zoneStaff` or the staff list. (Task 2 and Task 3)

---

### Task 1: Repeatable `status` filter on `GET /api/v1/tasks`

**Files:**
- Modify: `src/plong_elven/api/routers/tasks.py` (`list_tasks`)
- Modify: `src/plong_elven/services/task_service.py` (`TaskService.list`)
- Modify: `tests/test_tasks.py` (`_OPS`, new tests), `tests/test_task_service.py:70`
- Modify docs: `docs/API_REFERENCE.md`, `docs/TASK_API.md`, `docs/FRONTEND_GUIDE.md` (task list section: show `?status=open&status=closed`)

**Interfaces:**
- Produces: `TaskService.list(zone_id: str | None, statuses: list[TaskStatus] | None, limit: int) -> list[TaskOut]` (`TaskStatus` from `schemas/tasks.py`).

- [ ] **Step 1: Write the failing tests** in `tests/test_tasks.py`

```python
def test_list_filters_on_several_statuses(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    o = session.add_task(status=TaskStatusId.OPEN)
    c = session.add_task(status=TaskStatusId.CLOSED, expires_in=-60)
    session.add_task(status=TaskStatusId.FINISHED)
    body = client.get("/api/v1/tasks?status=open&status=closed").json()
    assert {t["taskId"] for t in body} == {str(o.task_id), str(c.task_id)}


def test_list_repeated_status_is_fine(session, client_as) -> None:
    client, _ = client_as("floor_staff")
    session.add_task()
    assert len(client.get("/api/v1/tasks?status=open&status=open").json()) == 1


@pytest.mark.parametrize("query", ["status=open&status=bogus", "status="])
def test_list_bad_status_is_422(client_as, query) -> None:
    client, _ = client_as("floor_staff")
    _err(client.get(f"/api/v1/tasks?{query}"), 422, "validation_error")
```

Also add `operators.in_op: lambda a, b: a in b` to `_OPS` (the in-memory session must evaluate `IN`).

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_tasks.py -k "several_statuses or repeated_status or bad_status" -v`
Expected: `several_statuses` FAIL (only the last `status` is applied); others may pass or fail.

- [ ] **Step 3: Implement**

Router: `status: list[Literal["open", "closed", "finished"]] | None = Query(None)`, passed as `statuses`. Service: filter `Task.task_status_id.in_({TaskStatusId[s.upper()] for s in statuses})` when `statuses` is given. Update `tests/test_task_service.py:70` to `.list("z1", ["open"], 20)`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest tests/test_tasks.py tests/test_task_service.py -q`
Expected: PASS, including the existing single-status tests.

- [ ] **Step 5: Update the three docs**, then run the Global Constraints check.

---

### Task 2: `GET /api/v1/staff?zoneId=`

**Files:**
- Modify: `src/plong_elven/services/task_service.py` (new `list_staff`, shared `_staff_out`)
- Modify: `src/plong_elven/api/routers/staff.py` (new `list_staff` route at `""`)
- Modify: `tests/conftest.py` (receives the `factory` fixture), `tests/test_task_race_pg.py` (fixture removed from here)
- Create: `tests/test_staff_pg.py`
- Modify: `tests/test_route_table.py` (`ROUTES` += `("GET", "/api/v1/staff")`), `tests/test_route_auth.py` (`ROLE_MATRIX` += `("GET", "/api/v1/staff"): ANY`)
- Modify docs: `docs/API_REFERENCE.md`, `docs/TASK_API.md`, `docs/FRONTEND_GUIDE.md` (new staff list route)

**Interfaces:**
- Produces: `TaskService.list_staff(zone_id: str | None) -> list[StaffOut]`; module function `_staff_out(user: User) -> StaffOut` (reused by `get_staff` and Task 3).
- Produces: pytest fixture `factory` (async_sessionmaker on `DATABASE_URL`, skips without a DB unless `REQUIRE_PG=1`) in `tests/conftest.py`, unchanged in behavior.

- [ ] **Step 1: Move the `factory` fixture** from `tests/test_task_race_pg.py` into `tests/conftest.py` as-is (with its imports). Run `uv run pytest tests/test_task_race_pg.py -q` with a scratch DB → same tests pass.

- [ ] **Step 2: Write the failing Postgres test** in `tests/test_staff_pg.py`

```python
@pytest.mark.asyncio
async def test_list_staff_floor_staff_only_by_home_zone(factory) -> None:
    # zone A and B (unique ids); users, all with a unique email tag:
    #   "Ani" floor_staff home A active; "Budi" floor_staff home B active;
    #   "Cici" floor_staff home A INACTIVE; "Mgr" ops_manager home A; "Dodi" floor_staff no home
    ...
    mine = lambda rows: [s.name for s in rows if s.staffId in created_ids]
    assert mine(await TaskService(s, None).list_staff(zone_a)) == ["Ani"]
    assert mine(await TaskService(s, None).list_staff(None)) == ["Ani", "Budi", "Dodi"]
    assert await TaskService(s, None).list_staff("no-such-zone") == []
```

Clean up users and zones in `finally` (same style as `tests/test_task_race_pg.py::_cleanup`).

- [ ] **Step 3: Run to verify it fails**

Run: `DATABASE_URL=... REQUIRE_PG=1 uv run pytest tests/test_staff_pg.py -v`
Expected: FAIL with `AttributeError: ... 'list_staff'`.

- [ ] **Step 4: Implement**

`list_staff`: `select(User).join(Role, Role.role_id == User.role_id).where(Role.name == "floor_staff", User.is_active.is_(True))`, plus `User.zone_id == zone_id` when given, `order_by(User.full_name)`. Route: `@router.get("")`, `zoneId: str | None = Query(None)`, any role (`get_current_user`), returns `list[StaffOut]`. `get_staff` uses `_staff_out`.

- [ ] **Step 5: Add route entries** to `ROUTES` and `ROLE_MATRIX` (see Files).

- [ ] **Step 6: Run to verify**

Run: `DATABASE_URL=... REQUIRE_PG=1 uv run pytest -q`
Expected: PASS, nothing skipped.

- [ ] **Step 7: Update the three docs**, then run the Global Constraints check.

---

### Task 3: `GET /api/v1/tasks/{taskId}` with live crowd or crowd log

**Files:**
- Modify: `src/plong_elven/schemas/tasks.py` (new `LiveCrowdOut`, `TaskDetailOut`)
- Modify: `src/plong_elven/services/crowd_service.py` (new `CrowdService.log_between`)
- Modify: `src/plong_elven/services/task_service.py` (new `task_phase`, `crowd_log_end`, `TaskService.detail`)
- Modify: `src/plong_elven/api/routers/tasks.py` (new `get_task` route at `/{taskId}`)
- Modify: `tests/test_task_service.py` (pure-function tests), `tests/test_tasks.py` (404 / 422 / auth)
- Create: `tests/test_task_detail_pg.py`
- Modify: `tests/test_route_table.py` (`ROUTES` += `("GET", "/api/v1/tasks/{taskId}")`), `tests/test_route_auth.py` (`ROLE_MATRIX` += same, `ANY`)
- Modify docs: `docs/API_REFERENCE.md`, `docs/TASK_API.md`, `docs/FRONTEND_GUIDE.md` (detail route + example body from the spec)

**Interfaces:**
- Consumes: `_staff_out(user)` and `TaskService.list_staff(zone_id)` (Task 2); existing `TaskService._find_task`, `_close_if_expired`, `_respondent_count`, `_task_out`; `classify(count, threshold)`; `HistoryBucketOut`; `LiveState`, `Bucket`.
- Produces:
  - `class LiveCrowdOut(BaseModel): count: int; observedAt: UtcDatetime | None`
  - `class TaskDetailOut(BaseModel)` with exactly: `taskId: UUID`, `zoneId: str`, `zoneName: str`, `zoneStatus: Literal["safe","crowded"]`, `instruction: str`, `status: TaskStatus`, `phase: Literal["ongoing","completed"]`, `createdAt: UtcDatetime`, `expiresAt: UtcDatetime`, `finishedAt: UtcDatetime | None`, `totalStaff: int`, `acceptedCount: int`, `completedCount: int`, `liveCrowd: LiveCrowdOut | None`, `crowdLog: list[HistoryBucketOut]`, `zoneStaff: list[StaffOut]`, `delegatedStaff: list[AcceptedStaffOut]`
  - `def task_phase(task_status_id: int) -> Literal["ongoing", "completed"]` — `ongoing` only for `TaskStatusId.OPEN`
  - `def crowd_log_end(task: Task, respondent_count: int, now: datetime) -> datetime` — `finished_at` if set; else `now` if `respondent_count > 0`; else `expires_at`
  - `async def CrowdService.log_between(self, zone_id: str, start: datetime, end: datetime) -> list[HistoryBucketOut]` — oldest first
  - `async def TaskService.detail(self, task_id: UUID, live: LiveState) -> TaskDetailOut`

- [ ] **Step 1: Write the failing pure tests** in `tests/test_task_service.py`

```python
def test_task_phase_is_ongoing_only_when_open() -> None:
    assert task_phase(TaskStatusId.OPEN) == "ongoing"
    assert task_phase(TaskStatusId.CLOSED) == "completed"
    assert task_phase(TaskStatusId.FINISHED) == "completed"


def test_crowd_log_end_rules() -> None:
    t0 = datetime(2026, 11, 6, 17, 32, tzinfo=UTC)
    now = datetime(2026, 11, 9, 9, 0, tzinfo=UTC)
    done = Task(
        created_at=t0,
        expires_at=t0 + timedelta(seconds=20),
        finished_at=datetime(2026, 11, 6, 18, 12, tzinfo=UTC),
    )
    assert crowd_log_end(done, 1, now) == done.finished_at
    working = Task(
        created_at=t0, expires_at=t0 + timedelta(seconds=20), finished_at=None
    )
    assert crowd_log_end(working, 2, now) == now
    assert crowd_log_end(working, 0, now) == working.expires_at  # nobody came
```

- [ ] **Step 2: Write the failing Postgres tests** in `tests/test_task_detail_pg.py` (use `factory`; unique zone id per test; clean up in `finally`)

```python
# Zone Z, threshold 10. Finished task: created 2026-11-06 17:32 UTC, expires +20 s,
# finished 18:12, instruction "Guide people to exit B", one completed respondent "Bunga"
# (home zone other). Floor staff "Dedi" home Z active; "Eko" home Z inactive.
# zone_logs rows for Z at 17:25, 17:30, 17:50, 18:10, 18:15.

async def test_detail_completed_task_returns_bucket_range():
    out = await TaskService(s, None).detail(task_id, LiveState())
    assert out.phase == "completed" and out.liveCrowd is None
    assert [b.bucketStart.strftime("%H:%M") for b in out.crowdLog] == ["17:30", "17:50", "18:10"]
    assert [p.name for p in out.zoneStaff] == ["Dedi"]
    assert [p.name for p in out.delegatedStaff] == ["Bunga"]
    assert out.createdAt.strftime("%H:%M") == "17:32" and out.instruction == "Guide people to exit B"

async def test_detail_includes_unflushed_bucket_once():
    # delete the 18:10 row; live.buckets[Z] = Bucket(start=18:10, sum_count=18, n=2, peak=11)
    # -> last log item is 18:10 with avgCount 9.0, peakCount 11
    # with the 18:10 row present AND the live bucket -> 18:10 appears exactly once (the DB row)

async def test_detail_completed_without_logs_is_empty_list():
    # finished task in a zone with no zone_logs rows and no live bucket -> crowdLog == []

async def test_detail_open_task_shows_live_crowd():
    # open task (expires in 60 s); live.counts[Z] = 12, live.observed_at[Z] = now
    # -> phase "ongoing", liveCrowd.count 12, zoneStatus "crowded", crowdLog == []

async def test_detail_open_task_before_any_detection():
    # open task, empty LiveState -> liveCrowd == LiveCrowdOut(count=0, observedAt=None), zoneStatus "safe"
```

And in `tests/test_tasks.py`:

```python
def test_task_detail_unknown_is_404(client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.get(f"/api/v1/tasks/{uuid4()}"), 404, "not_found")


def test_task_detail_bad_id_is_422(client_as) -> None:
    client, _ = client_as("floor_staff")
    _err(client.get("/api/v1/tasks/not-a-uuid"), 422, "validation_error")
```

- [ ] **Step 3: Run to verify they fail**

Run: `DATABASE_URL=... REQUIRE_PG=1 uv run pytest tests/test_task_service.py tests/test_task_detail_pg.py tests/test_tasks.py -k "phase or crowd_log or detail" -v`
Expected: FAIL (import errors / 404 on the new route).

- [ ] **Step 4: Implement schemas, `task_phase`, `crowd_log_end`, `log_between`, `detail`, route**

- `log_between`: rows where `ZoneLog.zone_id == zone_id`, `bucket_start >= _bucket_start(start)`, `bucket_start <= end`, ordered ascending. Then, if `live.buckets.get(zone_id)` has `n > 0`, `start` bucket ≤ its `start` ≤ `end`, and no row has that `bucket_start`, append it as `HistoryBucketOut(bucketStart=b.start, avgCount=round(b.avg, 2), peakCount=b.peak)`. `CrowdService(session, live)` already holds `live`.
- `detail`: `_find_task`; if `_close_if_expired` → commit; load zone; `phase = task_phase(...)`; `zoneStatus = classify(live.counts.get(zone_id, 0), zone.threshold)`; ongoing → `liveCrowd` from `live.counts` / `live.observed_at`, `crowdLog=[]`; completed → `liveCrowd=None`, `crowdLog = await CrowdService(self._session, live).log_between(zone_id, task.created_at, crowd_log_end(task, accepted, datetime.now(UTC)))`; `zoneStaff = await self.list_staff(zone_id)`; counts and `delegatedStaff` from `_task_out(task)`.
- Route: `@router.get("/{taskId}")`, `taskId: UUID`, any role, `live: LiveState = Depends(get_live_state)`, returns `TaskDetailOut`. Declare it after `/broadcast` so `GET /tasks/broadcast` is not matched (it is POST-only anyway).

- [ ] **Step 5: Add route entries** to `ROUTES` and `ROLE_MATRIX` (see Files).

- [ ] **Step 6: Run to verify**

Run: `DATABASE_URL=... REQUIRE_PG=1 uv run pytest -q`
Expected: PASS, nothing skipped.

- [ ] **Step 7: Update the three docs** (detail route, the spec's example body, the end-time rule), then run the Global Constraints check.
