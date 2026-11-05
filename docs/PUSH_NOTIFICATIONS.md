# Push Notifications (APNs)

Real push notifications to staff and manager iPhones, sent directly from this
backend to Apple's APNs HTTP/2 provider API using a token-based `.p8` Auth
Key — no certificates to renew, no third-party push service. Implementation:
`src/plong_elven/adapters/apns_sender.py`.

This is optional: without it configured, the app still works end to end —
pushes are just skipped (logged as a warning), nothing breaks.

## When a push is sent

| Trigger | Recipients | Code |
|---|---|---|
| An `ops_manager` broadcasts a task (`POST /api/v1/tasks/broadcast`) | every `floor_staff` user with a non-null `push_token` whose home zone (`users.zone_id`) is not the task's zone; users with no home zone are included | `TaskService.broadcast` in `services/task_service.py` |
| A zone newly becomes `crowded` (count reaches its `threshold`) | every `ops_manager` user with a non-null `push_token` | `AlertService.on_count` in `services/internal/alert_service.py` |

`admin` users never receive pushes. Recipients are selected by role in
`NotificationService.notify_role` (`services/internal/notification_service.py`). Both
pushes run as FastAPI background tasks after the response, so a push failure
never changes the API response.

Task push copy: title `New task`, body `{zone}: {instruction}` (the
manager's instruction, at most 50 characters).

The crowded push only fires on the transition into `crowded`,
not on every count post while the zone stays busy, and not on the way back
down to `safe`. Counts come from the camera route
`POST /api/v1/cameras/{cameraId}/detections` or, in dev, the manual
`POST /dev/cameras/{cameraId}/detections`; both use the same ingest path. Only
zones that exist in the `zone` table are checked. It has a 5-minute cooldown
per zone, so a count hovering around the threshold does not re-fire it. State is kept in memory
(`LiveState.last_status` and `last_alert_sent_at` in `state/live_state.py`), so
it resets on API restart.

Alert copy (`default_alert_rules` in `services/internal/alert_service.py`):

| Status | Title | Body |
|---|---|---|
| `crowded` | Zone crowded — act now | `{zone} is crowded ({count} people). Broadcast a task now.` |

## 1. Generate an APNs Auth Key

You need an Apple Developer account (free or paid).

1. [developer.apple.com → Certificates, Identifiers & Profiles → Keys](https://developer.apple.com/account/resources/authkeys/list)
2. **+** → name it, check **Apple Push Notifications service (APNs)** → Continue → Register
3. **Download** the `.p8` file — Apple only lets you download it once, save it
   somewhere safe
4. Note the **Key ID** shown next to it (also embedded in the filename,
   `AuthKey_<KEYID>.p8`)
5. Your **Team ID** is on the account's Membership page (also visible in
   Xcode's project settings, `DEVELOPMENT_TEAM`)

## 2. Place the key in this repo

`.gitignore` already excludes `secrets/` and `*.p8`, so this is safe:

```bash
mkdir -p secrets
mv ~/Downloads/AuthKey_*.p8 secrets/
```

## 3. Configure `.env`

```env
APNS_KEY_PATH=./secrets/AuthKey_<KEYID>.p8
APNS_KEY_ID=<KEYID>
APNS_TEAM_ID=<your-team-id>
APNS_TOPIC=<app-bundle-id>        # e.g. com.elven.Plong — must match exactly
APNS_USE_SANDBOX=true             # true for Xcode development builds, false for TestFlight/App Store
```

`APNS_TOPIC` must exactly equal the iOS app's bundle identifier
(`PRODUCT_BUNDLE_IDENTIFIER` in the frontend's `.xcodeproj`) — if it's wrong,
Apple will not deliver the push. All values are read via
`src/plong_elven/common/settings.py` (`APNS_USE_SANDBOX` defaults to `true`);
restart the API after changing `.env` (`--reload` only watches `.py` files).

`APNS_USE_SANDBOX=true` sends to `api.sandbox.push.apple.com`, `false` to
`api.push.apple.com`. The signing JWT (ES256, `kid` = `APNS_KEY_ID`, `iss` =
`APNS_TEAM_ID`) is cached in memory for 50 minutes. Each push is an `alert`
with `sound: default`, sent over HTTP/2 with a 5-second timeout.

## 4. Enable the capability in the iOS app

In Xcode: select the `Plong` target → **Signing & Capabilities** → **+
Capability** → **Push Notifications**. This has to be done in Xcode itself —
it registers the capability with Apple's servers using your signed-in Apple
ID, which can't be done by editing files directly. It generates
`Plong.entitlements` (`aps-environment: development`, matching
`APNS_USE_SANDBOX=true`) and wires `CODE_SIGN_ENTITLEMENTS` into the build
settings automatically.

Real push delivery only works on a **physical device**, not the simulator.

## 5. Verify

Log in on a device. The app must send its APNs device token to
`PUT /api/v1/profile/push-token` with body `{"pushToken": "<token>"}` (which
iOS code does this: TBD, frontend repo not checked here). Logout clears the
stored token. Check it reached the database:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T db \
  psql -U plong_elven -d plong_elven \
  -c "SELECT email, (push_token IS NOT NULL) AS has_push_token FROM users;"
```

Trigger a real push: broadcast a task (`POST /api/v1/tasks/broadcast` with an
`ops_manager` token, see [TASK_API.md](TASK_API.md)) with at least one
`floor_staff` device registered, or drive a zone's count into
`crowded` from the **Dev Tools** tab of the dev page
`/dev/cameras/{cameraId}` (`APP_ENV=dev`, admin login; see
[README](../README.md) step 11) with an `ops_manager` device registered.

## Troubleshooting

### Nothing arrives, no error anywhere

Check `APNS_KEY_PATH` actually points at a file that exists. A wrong path
fails inside a background task and is only visible as a server log warning
(`could not load APNs signing key, skipping push`), never as an API error.
Also check that the recipients have a `push_token` (query above) and the right
role.

### `APNs not configured, skipping push to N device(s)` in the logs

One of `APNS_KEY_PATH`/`APNS_TEAM_ID`/`APNS_KEY_ID` is empty in `.env`. This
check does not look at `APNS_TOPIC`.

### `push to xxxxxxxx...xxxx failed: <status> <body>` in the logs

Apple answered with a non-`200` status (or the request failed). The log line
includes Apple's response body, which names the reason.

### Push accepted (`200` from Apple) but never arrives on the phone

A `200` only means Apple accepted the payload, not that it was delivered.
Check that `APNS_TOPIC` matches the app's bundle id and that
`APNS_USE_SANDBOX` matches how the app was built (`true` for a normal Xcode
run, `false` for TestFlight/App Store). Also check that the stored
`push_token` is current: a new `PUT /api/v1/profile/push-token` replaces it,
and logout clears it.

### Banner doesn't show while the app is open

iOS hides push banners while the app is in the foreground unless the app opts
in. Whether the iOS app opts in: TBD (frontend repo, not checked here).
