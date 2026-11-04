# API Contract Rework Implementation Plan (index)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Layer the backend (router → service → session) without contract change, then cut over to the clean `/api/v1` + `/internal/v1` contract with an admin role, tickets and fail-closed startup.

**Architecture:** Approach B from the design: two stages on `dev/api-contract-rework`. Stage 1 moves code and fixes four bugs while URLs, status codes and JSON stay the same. Stage 2 renames routes once, deletes the old ones, and closes the auth holes.

**Tech Stack:** Python 3.12, FastAPI 0.159.1, Pydantic 2.14.1, SQLAlchemy async + asyncpg, Alembic, Postgres 16, pytest 9 + pytest-asyncio 1.4, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-11-03-api-contract-rework-design.md` (approved; wins over `docs/PROPOSAL_*.md`).

| File | Content | Tasks |
|---|---|---|
| `docs/superpowers/plans/plan-stage1.md` | Layering, two SOLID seams, 4 fixes, Postgres race test | 1.1 – 1.10 (10) |
| `docs/superpowers/plans/plan-stage2.md` | Cutover, error shape, camelCase, admin, tickets, fail-closed, docs | 2.1 – 2.13 (13) |

Each stage file has its own Global Constraints, Review Focus, and **Files owned per task** table.

## Delegation order

```text
Stage 1:  1.1 → { 1.2 ‖ 1.3 ‖ 1.5 ‖ 1.6 } → 1.4 (after 1.3) → 1.7 → 1.8 → 1.9 → 1.10
          user approves Stage 1
Stage 2:  2.1 → { 2.2 ‖ 2.3 } → 2.4 → 2.5 → 2.6 → 2.7 → 2.8 → 2.9 → 2.10 → { 2.11 ‖ 2.12 } → 2.13
```

Shared files that force serial order:
- `src/plong_elven/api/main.py`
- `src/plong_elven/api/deps.py`
- `tests/conftest.py`
- `tests/test_route_table.py`
- `.github/workflows/ci.yml`

The Stage 2 cutover chain (2.4–2.10) is serial on purpose.

## Rules for every task

- Run tests with `uv run pytest` from the repo root, never with `--env-file .env`. The local `.env` sets `INTERNAL_API_KEY`, which makes 10 baseline tests fail.
- No `git commit`, no `--amend`. Every task ends with a checkpoint: *leave in working tree; user commits.*
- The suite must be green at the end of every task.

## Source of facts

- **From code (read 2026-11-03):** all file paths, line numbers, function names, messages, cooldowns, thresholds, migration head `f67711b4a0bb`, and role ids 1/2.
- **Verified by probe on this machine:**
  - Baseline is 23 tests passing.
  - FastAPI 0.159.1 keeps included routers as `_IncludedRouter` in `app.routes`, so route walks use `app.openapi()`.
  - Dependencies raise before body validation.
  - `TestClient` re-raises background-task errors.
  - A WebSocket closed before accept gives close code 1008.
  - Dict datetimes encode as `+00:00` and Pydantic datetimes as `Z`.
  - Pydantic `max_length` works on dicts.
  - `h2` is installed.
- **From the design doc:** contract, roles, limits, ticket rules, CI Postgres. Plan-made decisions are listed as P1–P7 in `plan-stage2.md`.

## [TBD] items

| Id | Item | Blocks |
|---|---|---|
| TBD-1 | Dev page `GET /dev/cameras/{cameraId}`: the design table says role `admin`, but a browser navigation cannot send a bearer header and §6.5 puts the login form on the page. **RESOLVED (user, 2026-11-03):** page is open in dev only; the API calls it makes still need an admin login. | none |
| TBD-2 | Exact JSON fields the iOS app reads today (design §8). | Release, not code |
| TBD-3 | Whether the iOS app uses the WebSocket at all (design §8). | Release, not code |
| TBD-4 | Dev server deployment: `APP_ENV=dev`, a real `INTERNAL_API_KEY`, `alembic upgrade head` before the new build (design §8). | Release |
| TBD-5 | `semgrep` is not installed here, and the `trailofbits-semgrep` ruleset needs user approval. | Task 2.13 step 5 |
| TBD-6 | No Docker or Postgres on this machine. The CI Postgres service, the race test, and the migration up/down round trip can only be verified in CI. PyYAML availability for the local YAML check is also unconfirmed. | Tasks 1.9, 2.3 |
| TBD-7 | Whether the iOS date parser accepts `...Z` (Pydantic DTOs) as well as today's `...+00:00`. | Release |
| TBD-8 | Whether `/docs`, `/redoc` and `/openapi.json` should stay public outside dev. The plan leaves them unchanged; they are not in OpenAPI paths, so the walker ignores them. | None |

## Execution handoff

The user has already chosen the execution method (Opus subagents). Before any task starts, review the plan and resolve TBD-1.
