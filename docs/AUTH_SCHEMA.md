# Authentication Schema

This document describes the authentication tables as the Alembic migrations
and `src/plong_elven/db/models.py` create them today.

## Scope

The first migration (`ad950fffbf32_create_auth_tables`) creates:

```text
roles
users
refresh_tokens
```

Each user has exactly one role. The CCTV worker authenticates with the separate
`X-Internal-Api-Key` header; it does not use the user tables.

Migration chain (oldest first):

```text
ad950fffbf32  create auth tables (roles, users, refresh_tokens; seeds roles 1, 2)
7b5b2f7c1d21  create dispatch tables (zones, staff, broadcasts, broadcast_responses)
f9fe68ec2955  merge staff into users (adds users.status, users.push_token; drops staff)
f67711b4a0bb  add zone_count_history
a1b2c3d4e5f6  add admin role (seeds role 3)
```

## Relationships

```text
roles 1 ──── * users 1 ──── * refresh_tokens
```

## PostgreSQL schema

The following SQL matches what the migrations produce for the auth tables.

```sql
CREATE TABLE roles (
    id SMALLINT PRIMARY KEY,
    name VARCHAR(32) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
    id UUID PRIMARY KEY,
    role_id SMALLINT NOT NULL REFERENCES roles(id) ON DELETE RESTRICT,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    full_name VARCHAR(120) NOT NULL,
    is_active BOOLEAN NOT NULL,           -- no DB default; the ORM sets TRUE
    email_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    -- added by f9fe68ec2955:
    status TEXT NOT NULL DEFAULT 'idle',
    push_token TEXT,
    CONSTRAINT users_status_check CHECK (status IN ('idle', 'delegating'))
);

CREATE UNIQUE INDEX uq_users_email_lower
    ON users (lower(email));

CREATE INDEX ix_users_role_id
    ON users (role_id);

CREATE TABLE refresh_tokens (
    id UUID PRIMARY KEY,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash CHAR(64) NOT NULL UNIQUE,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    revoked_at TIMESTAMPTZ,
    user_agent VARCHAR,
    ip_address INET
);

CREATE INDEX ix_refresh_tokens_user_id
    ON refresh_tokens (user_id);

CREATE INDEX ix_refresh_tokens_active
    ON refresh_tokens (user_id, expires_at)
    WHERE revoked_at IS NULL;
```

`id` values for `users` and `refresh_tokens` are generated in Python
(`uuid4`), not by the database.

## Seed data

The migrations insert three roles with plain inserts (no `ON CONFLICT`):

| id | name | migration |
|---|---|---|
| 1 | `floor_staff` | `ad950fffbf32` |
| 2 | `ops_manager` | `ad950fffbf32` |
| 3 | `admin` | `a1b2c3d4e5f6` |

The downgrade of `a1b2c3d4e5f6` deletes role 3 and fails while admin users
exist (`ON DELETE RESTRICT`).

No migration seeds a user. Create users with `scripts/create_user.py` (hashes
the prompted password with Argon2id), or for local testing with
`scripts/seed_dispatch.py` (fixed dev password). Never put a password in Git.

## Field rules

### `roles`

- `name` is one of `floor_staff`, `ops_manager` or `admin`.
- Role names are stable identifiers, not display labels.
- Roles do not inherit. Routes check the exact role name
  (`require_role` in `api/deps.py`); a mismatch returns `403 wrong_role`.

### `users`

- `scripts/create_user.py` lowercases `email` before insert. Login looks the
  user up with `lower(email)`, so lookup is case-insensitive.
- `password_hash` holds an Argon2id hash, never a plaintext password.
- `role_id` is required, so every user has exactly one role.
- `is_active = false` blocks login, refresh and access-token use without
  deleting the row.
- `last_login_at` is set on each successful login.
- `updated_at` is set on insert only; no code updates it later.
- `email_verified_at` is not read or written by any code yet.
- `status` and `push_token` are described under
  [Dispatch fields](#dispatch-fields-on-users).

### `refresh_tokens`

- The raw token is `secrets.token_urlsafe(48)`. Only its SHA-256 hex digest
  is stored in `token_hash`.
- The raw token is returned once, in the login or refresh response.
- `expires_at` is `now + REFRESH_TOKEN_DAYS` (default 30 days).
- Refresh sets `revoked_at` on the old row and inserts a new row.
- Logout sets `revoked_at` on the submitted token's row and clears the user's
  `push_token`.
- `user_agent` and `ip_address` are never written.
- Cleanup of expired or revoked rows: TBD (no job exists). Authorization never
  relies on deletion; it checks `revoked_at` and `expires_at`.

## Tokens

- Access token: HS256 JWT signed with `JWT_SECRET`, claims `sub` (user id),
  `type: "access"`, `iat`, `exp`. Lifetime `ACCESS_TOKEN_MINUTES` (default 15).
- Ticket: HS256 JWT with `type: "ticket"`, a `scope` (`stream:<cameraId>` or
  `crowd:live`), 60-second lifetime. Minted by `POST /dev/tickets` for `admin`
  users only, and accepted only as `?ticket=` on the dev stream and
  `WS /api/v1/crowd/live`.

Outside `APP_ENV=dev` (code default `prod`), the API refuses to start when
`JWT_SECRET` or `INTERNAL_API_KEY` is shorter than 32 characters. With
`APP_ENV=dev` it starts, and token calls return `503 auth_not_configured` until
`JWT_SECRET` has at least 32 characters. Generate one with:

```bash
openssl rand -hex 32
```

## Endpoint mapping

```text
POST /api/v1/auth/login
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
GET  /api/v1/profile
PUT  /api/v1/profile/push-token
POST /dev/tickets             (APP_ENV=dev only, admin)
```

Old route names are listed in [API_V1_MIGRATION.md](API_V1_MIGRATION.md).

Apply all migrations with:

```bash
make db-migrate    # uv run --env-file .env alembic upgrade head
```

## Dispatch fields on `users`

Migration `f9fe68ec2955_merge_staff_into_users` added two columns to `users`
and dropped the separate `staff` table created by `7b5b2f7c1d21`:

```sql
ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE users ADD COLUMN push_token TEXT;
ALTER TABLE users ADD CONSTRAINT users_status_check
    CHECK (status IN ('idle', 'delegating'));
```

A `floor_staff` user is the staff member for dispatch; there is no separate
staff id. `broadcast_responses.staff_id` references `users.id`.

`push_token` is written by `PUT /api/v1/profile/push-token` (one token per
user; a new call replaces it), cleared by `POST /api/v1/auth/logout`, and read
by `NotificationService.notify_role` when it pushes to `floor_staff` or
`ops_manager` users. See [PUSH_NOTIFICATIONS.md](PUSH_NOTIFICATIONS.md).

## Not included

These tables do not exist:

```text
password_reset_tokens
audit_logs
user_devices
permissions
```

Password reset is not implemented.
