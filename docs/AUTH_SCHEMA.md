# Minimal Authentication Schema

This document defines the first database migration for user authentication.
It is intentionally limited to login/session support. Role-protected business
endpoints and password reset can be added in later migrations.

## Scope

The first migration creates:

```text
roles
users
refresh_tokens
```

Each user has exactly one role. The CCTV worker continues to authenticate with
the separate `X-Internal-Api-Key`; it does not use the user tables.

## Relationships

```text
roles 1 ──── * users 1 ──── * refresh_tokens
```

## PostgreSQL schema

The following SQL describes the expected result of the Alembic migration. The
application may implement the same structure with SQLAlchemy models.

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
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    email_verified_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ
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
    user_agent TEXT,
    ip_address INET
);

CREATE INDEX ix_refresh_tokens_user_id
    ON refresh_tokens (user_id);

CREATE INDEX ix_refresh_tokens_active
    ON refresh_tokens (user_id, expires_at)
    WHERE revoked_at IS NULL;
```

## Seed data

The initial migration should insert the two roles using an idempotent seed:

```sql
INSERT INTO roles (id, name)
VALUES
    (1, 'floor_staff'),
    (2, 'ops_manager')
ON CONFLICT (id) DO UPDATE
SET name = EXCLUDED.name;
```

Do not seed a real user password in the migration. Create the first manager
through a protected setup/seed command that hashes the supplied password with
Argon2id. Never place the password or its plaintext value in Git.

## Field rules

### `roles`

- `name` is one of `floor_staff` or `ops_manager` for the MVP.
- Role names are stable identifiers, not display labels.

### `users`

- Normalize `email` to lowercase before insert and lookup.
- `password_hash` must contain an Argon2id hash, never a plaintext password.
- `role_id` is required, so every user has exactly one role.
- `is_active = false` prevents login without deleting historical records.
- Application code updates `updated_at` whenever the user changes.

### `refresh_tokens`

- Generate a random refresh token for the client.
- Store only a SHA-256 hex digest in `token_hash`.
- Return the raw token only once in the login/refresh response.
- Revoke the old token when rotating during refresh.
- Set `revoked_at` on logout.
- Delete expired/revoked rows periodically, but do not rely on deletion for
  authorization.

## Migration order

The teammate implementing the database should use this order:

1. Configure the async SQLAlchemy engine from `DATABASE_URL`.
2. Configure Alembic to load the SQLAlchemy metadata.
3. Add the `roles`, `users`, and `refresh_tokens` models.
4. Generate or write one Alembic revision for these tables and indexes.
5. Run `alembic upgrade head` against the local PostgreSQL container.
6. Seed the two roles idempotently.
7. Add a separate setup command for the first `ops_manager` user.
8. Add migration tests that run against a temporary PostgreSQL database.

Expected command:

```bash
uv run alembic upgrade head
```

## Minimal endpoint mapping

The schema supports these initial endpoints:

```text
POST /api/v1/auth/login
GET  /api/v1/auth/profile
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
```

Before using the auth endpoints, set a random JWT secret in `.env`:

```bash
openssl rand -hex 32
```

```env
JWT_SECRET=<generated-value>
```

The API rejects missing or shorter-than-32-character JWT secrets.

Role authorization is intentionally out of scope for this first migration, but
`role_id` is included so the login response and future authorization checks do
not require a schema change.

## Later addition: dispatch fields on `users`

A later migration (`f9fe68ec2955_merge_staff_into_users`) added two columns
used by the dispatch/broadcast flow directly to `users`, rather than keeping
a separate `staff` table:

```sql
ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'idle';
ALTER TABLE users ADD COLUMN push_token TEXT;
ALTER TABLE users ADD CONSTRAINT users_status_check
    CHECK (status IN ('idle', 'delegating'));
```

A `floor_staff` user *is* staff for dispatch purposes — there is no separate
staff identity or id. `broadcast_responses.staff_id` references `users.id`.
This removed an earlier standalone `staff` table that duplicated the same
people with a second primary key.

## Not included yet

Do not add these tables to the first migration unless the endpoint contract is
already finalized:

```text
password_reset_tokens
audit_logs
user_devices
permissions
```

`password_reset_tokens` should be a later migration after the reset email
provider and token delivery flow are decided.
