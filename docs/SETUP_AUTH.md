# Authentication Setup

This guide covers only the initial authentication setup and verification. It
assumes the repository has already been set up locally.

The current authentication flow supports:

```text
POST /api/v1/auth/login
GET  /api/v1/auth/profile
POST /api/v1/auth/refresh
POST /api/v1/auth/logout
```

The current roles are:

```text
floor_staff
ops_manager
```

Role-protected business endpoints are not implemented yet.

## 1. Configure local authentication

Create the local environment file if it does not exist:

```bash
cp .env.example .env
```

Generate a strong JWT secret:

```bash
openssl rand -hex 32
```

Put the generated value in `.env`:

```env
JWT_SECRET=<generated-value>
ACCESS_TOKEN_MINUTES=15
REFRESH_TOKEN_DAYS=30
```

The API rejects a missing or shorter-than-32-character `JWT_SECRET`.

Do not commit `.env`, passwords, JWT secrets, refresh tokens, or other local
credentials.

## 2. Install authentication dependencies

If the project dependencies have not been synchronized after the auth changes:

```bash
uv sync --group api --group dev
```

The API authentication implementation uses:

- SQLAlchemy async sessions
- PostgreSQL
- Argon2id password hashing
- JWT access tokens
- Hashed opaque refresh tokens

## 3. Start PostgreSQL

Make sure Docker or OrbStack is running, then execute:

```bash
make db-up
```

Check the container:

```bash
docker compose --env-file .env -f infra/docker-compose.yml ps
```

The database should be running or healthy.

## 4. Apply the database migration

Run the authentication migration:

```bash
make db-migrate
```

This creates:

```text
roles
users
refresh_tokens
alembic_version
```

The migration seeds the two roles:

```text
floor_staff
ops_manager
```

Verify the migration state:

```bash
uv run --env-file .env alembic current
uv run --env-file .env alembic check
```

`alembic check` should report that no new upgrade operations are detected.

## 5. Create a local user

Create an initial manager user:

```bash
uv run --env-file .env python scripts/create_user.py \
  --email manager@example.com \
  --full-name "Ops Manager" \
  --role ops_manager
```

The script asks for the password twice. It stores only an Argon2id password
hash in PostgreSQL.

Create a floor staff user when needed:

```bash
uv run --env-file .env python scripts/create_user.py \
  --email staff@example.com \
  --full-name "Floor Staff" \
  --role floor_staff
```

Verify the users and roles directly in PostgreSQL:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T db \
  psql -U plong_elven -d plong_elven \
  -c "SELECT email, full_name, role_id, is_active FROM users;" \
  -c "SELECT id, name FROM roles ORDER BY id;"
```

## 6. Start the API

Start the API:

```bash
make api
```

Keep the API terminal running. Open the API documentation in a browser:

```text
http://127.0.0.1:8080/docs
```

## 7. Check database readiness

Run:

```bash
curl -i http://127.0.0.1:8080/readyz
```

Expected response:

```json
{
  "status": "ready",
  "database": "connected"
}
```

## 8. Test login

In Swagger, open `POST /api/v1/auth/login`, click **Try it out**, and submit:

```json
{
  "email": "manager@example.com",
  "password": "the-password-used-during-user-setup"
}
```

The response contains:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "token_type": "bearer",
  "expires_in": 900,
  "user": {
    "email": "manager@example.com",
    "role": "ops_manager"
  }
}
```

Keep both tokens temporarily for the following tests. Never commit or share
them.

## 9. Test the profile endpoint

Copy the `access_token` from the login response. Click the Swagger **Authorize**
button and paste the access token without the `Bearer` prefix:

```text
eyJhbGciOiJIUzI1NiIs...
```

Then execute:

```text
GET /api/v1/auth/profile
```

Expected response:

```json
{
  "id": "...",
  "email": "manager@example.com",
  "full_name": "Ops Manager",
  "role": "ops_manager",
  "is_active": true
}
```

The same request can be tested with curl:

```bash
curl -i http://127.0.0.1:8080/api/v1/auth/profile \
  -H "Authorization: Bearer <access-token>"
```

## 10. Test refresh token rotation

Open `POST /api/v1/auth/refresh` and submit the `refresh_token` from login:

```json
{
  "refresh_token": "<refresh-token>"
}
```

The response contains a new access token and a new refresh token. The old
refresh token is revoked and must not be accepted again.

## 11. Test logout

Open `POST /api/v1/auth/logout` and submit the newest refresh token:

```json
{
  "refresh_token": "<latest-refresh-token>"
}
```

Expected response:

```text
204 No Content
```

The refresh token is marked as revoked in the database.

## 12. Verify token records

Check refresh token records directly:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T db \
  psql -U plong_elven -d plong_elven \
  -c "SELECT user_id, expires_at, revoked_at, created_at FROM refresh_tokens ORDER BY created_at;"
```

Expected behavior:

- Login creates a refresh token row.
- Refresh sets `revoked_at` on the old row and creates a new row.
- Logout sets `revoked_at` on the latest row.
- Raw refresh token values are never stored in the database.

## Authentication endpoint summary

| Endpoint | Authentication | Database behavior |
|---|---|---|
| `POST /api/v1/auth/login` | None | Reads user, updates login time, creates refresh token |
| `GET /api/v1/auth/profile` | Bearer access token | Reads active user and role |
| `POST /api/v1/auth/refresh` | Refresh token in body | Revokes old token and creates a new one |
| `POST /api/v1/auth/logout` | Refresh token in body | Revokes refresh token |
| `GET /readyz` | None | Executes `SELECT 1` |

## Troubleshooting

### `JWT authentication is not configured with a strong secret`

Set `JWT_SECRET` in `.env`, restart the API, and try again:

```bash
openssl rand -hex 32
```

### `Invalid email or password`

Check that the user exists and use the password entered during
`create_user.py`. The password is case-sensitive.

### `Not authenticated`

The profile request does not contain an access token. Use Swagger's
**Authorize** button or send:

```http
Authorization: Bearer <access-token>
```

### `Invalid refresh token`

The token may have expired, been used during rotation, or been revoked during
logout. Login again to obtain a new token pair.

## Stop local services

Stop the API with `Ctrl+C`. Stop PostgreSQL when it is no longer needed:

```bash
make db-down
```
