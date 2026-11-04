> Implemented; see docs/superpowers/specs/2026-11-03-api-contract-rework-design.md (the design wins where they differ).

# Proposal: Layered Architecture (Router → Service → Session)

Status: **draft for team discussion**. Nothing in this document is implemented.
Companion document: [PROPOSAL_API_CONTRACT.md](PROPOSAL_API_CONTRACT.md).

Facts below come from reading the source in `src/plong_elven/`. Anything not
confirmed by the code is marked `TBD`.

## 1. Current state

The backend is a FastAPI app with one file per feature. It is not MVC or MVVM.
Each route function handles HTTP, business rules, and database queries together
("fat router").

| Concern | Where it lives today |
|---|---|
| HTTP routes | `api/auth.py`, `api/dispatch.py`, `api/detections.py`, `api/stream.py`, `api/zones.py`, `api/ui.py` |
| Request/response models (DTOs) | Pydantic classes inside the same router files |
| Business rules | Inside route functions (e.g. broadcast rules in `dispatch.py`) |
| Data access | `select(...)` calls inside route functions, via `AsyncSession` |
| Entities | `db/models.py` (SQLAlchemy) |
| Live state | Module-level dicts: `latest_counts`, `_buckets`, `_last_status` (`detections.py`), `_frames`, `_raw_frames` (`stream.py`) |
| Dependency injection | FastAPI `Depends(get_db)`, `Depends(get_current_user)` |
| Camera worker | `cctv/` (separate process; talks to the API over HTTP) |

### Problems this causes

1. **Hard to read.** `dispatch.py` (353 lines) mixes route code, rules, and SQL.
2. **Hard to test.** Tests replace the whole DB session with a fake
   (`tests/test_dispatch.py`). The real broadcast rules (slot limits, one open
   broadcast per zone) are not tested against a database.
3. **Cross-file coupling.** `dispatch.py` imports `latest_counts` from
   `detections.py`. A "dispatch" file reads crowd state it does not own.
4. **Hidden global state.** Live counts and frames are loose module globals.
   Tests must clear them by hand.
5. **Business logic is tied to HTTP.** Rules cannot be reused from a script or
   a background job without calling a route function.

## 2. Proposal

Adopt a controller → service → data-access split, the same shape as ASP.NET.

| ASP.NET term | Proposed here | Notes |
|---|---|---|
| Controller | `routers/` | Thin: parse request, call service, return DTO |
| DTO / ViewModel | `schemas/` | Pydantic models only |
| Service | `services/` | All business rules. No FastAPI imports |
| Repository | **Not by default** | See decision below |
| DbContext | `AsyncSession` | Already plays the role of EF's `DbContext` |
| Entity | `db/models.py` | Unchanged |
| Singleton service | `state/` | Replaces module-level dicts |

### Decision: repositories

`AsyncSession` already is a unit of work and a query API, like `DbContext`.
A repository on top of it only adds a pass-through class for most queries.

Recommendation: **add a repository only for queries that are long or reused**
(e.g. the broadcast summary query with counts and joined staff). Keep simple
`session.get(...)` calls in services.

If the team prefers a repository for every table for consistency, that works
too. It costs more files and does not remove any current problem.

### Target layout

```text
src/plong_elven/
├── api/
│   ├── main.py             # app wiring, error handlers
│   ├── deps.py             # get_db, get_current_user, require_role, require_internal_key
│   └── routers/
│       ├── auth.py
│       ├── me.py
│       ├── zones.py
│       ├── crowd.py
│       ├── broadcasts.py
│       ├── staff.py
│       ├── cameras.py      # layout, stream (admin side)
│       └── internal.py     # worker-facing: detections, frames
├── schemas/                # Pydantic DTOs, one file per domain
├── services/
│   ├── auth_service.py
│   ├── crowd_service.py    # ingest counts, status, history buckets, alert decision
│   ├── broadcast_service.py
│   ├── notification_service.py   # wraps APNs
│   └── camera_service.py
├── state/
│   └── live_state.py       # LiveCounts, FrameStore (in-memory, one class each)
├── db/                     # models.py, session.py (unchanged)
├── common/                 # settings, zone_status
└── cctv/                   # unchanged
```

### Rules for each layer

| Layer | May do | Must not do |
|---|---|---|
| Router | Parse input, call one service method, map to DTO, set status code | Run queries, hold rules |
| Service | Business rules, transactions, call other services | Import `fastapi`, return HTTP objects |
| State | Hold in-memory values behind methods | Call the database |
| Models | Describe tables | Hold rules |

Services raise domain errors (e.g. `BroadcastFull`, `NotFound`). One handler in
`main.py` turns them into the standard error JSON (see the API contract doc).

### Before / after (one endpoint)

Before, in `api/dispatch.py` (abridged): the route checks the role, loads the
zone, checks for an open broadcast, inserts, commits, queries tokens, and queues
the push.

After:

```python
# routers/broadcasts.py
@router.post("", status_code=201)
async def create(
    body: CreateBroadcast,
    user: User = Depends(require_role("ops_manager")),
    svc: BroadcastService = Depends(get_broadcast_service),
) -> BroadcastOut:
    return await svc.create(zone_id=body.zoneId, staff_needed=body.staffNeeded)
```

```python
# services/broadcast_service.py
class BroadcastService:
    def __init__(self, session: AsyncSession, notifier: NotificationService): ...

    async def create(self, zone_id: str, staff_needed: int) -> BroadcastOut:
        zone = await self._get_zone_or_404(zone_id)
        await self._close_expired_open_broadcast(zone_id)
        broadcast = await self._insert_or_409(zone_id, staff_needed)
        await self._notifier.broadcast_created(zone, broadcast)
        return await self._to_out(broadcast)
```

### State object instead of globals

```python
# state/live_state.py
class LiveCounts:
    def update(self, counts: dict[str, int]) -> None: ...
    def get(self, zone_id: str) -> int: ...
    def observed_at(self, zone_id: str) -> datetime | None: ...
```

One instance is created at app start and injected with `Depends`. Tests get a
fresh instance each time, so no manual `clear()` calls.

## 3. Migration plan

Small steps. Each step is one PR, behavior unchanged, tests green.

| Step | Change | Risk |
|---|---|---|
| 1 | Add `state/live_state.py`; point `detections.py`, `dispatch.py`, `stream.py` at it | Low |
| 2 | Move Pydantic DTOs to `schemas/` | Low |
| 3 | Extract `BroadcastService` from `dispatch.py` | Medium |
| 4 | Extract `CrowdService` from `detections.py` | Medium |
| 5 | Extract `AuthService` | Medium |
| 6 | Extract `NotificationService` from `apns.py` | Low |
| 7 | Add DB-backed tests for `BroadcastService` (real Postgres in CI) | Medium |
| 8 | Rename/move routers once the API contract is agreed | Depends on contract |

Steps 1–7 do not change any URL, so the iOS app is not affected.

## 4. Things to fix while refactoring (found in review)

These are separate from the layering, but easier to fix once logic is in services.

| # | Location | Issue |
|---|---|---|
| 1 | `api/ui.py` | `camera_id` is put into HTML and JS without escaping (XSS) |
| 2 | `dispatch.py` respond | Count-then-insert is not atomic; two staff at once can exceed `staff_needed`. Needs a row lock or a DB constraint |
| 3 | `detections.py` | The request's DB session is passed to a background task after the response is sent. Use a fresh session in the task |
| 4 | `security.py` | Key comparison is not constant-time; an empty `INTERNAL_API_KEY` turns auth off silently |
| 5 | `/ws/counts`, `GET /zones/{camera_id}` | No authentication |
| 6 | `apns.py` | Invalid device tokens (HTTP 410) are never removed |
| 7 | `users.updated_at` | Never updated after insert |

## 5. Open questions

- Do we keep one process only? Live state in memory means yes. Multiple
  workers would need Redis or similar. `TBD` — depends on deployment plan.
- `zones.json` (worker) and the `zones` table (API) both describe zones. Which
  is the source of truth? Not decided in this proposal.
- Is a repository layer required by team convention? If yes, add it in step 3.

## 6. Out of scope

- Changing the database schema.
- Changing the cctv worker.
- Switching frameworks.
