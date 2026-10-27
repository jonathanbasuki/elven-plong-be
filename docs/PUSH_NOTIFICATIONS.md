# Push Notifications (APNs)

Real push notifications to staff and manager iPhones, sent directly from this
backend to Apple's APNs HTTP/2 provider API using a token-based `.p8` Auth
Key — no certificates to renew, no third-party push service. Implementation:
`src/plong_elven/api/apns.py`.

This is optional: without it configured, the app still works end to end —
pushes are just skipped (logged as a warning), nothing breaks.

## When a push is sent

| Trigger | Recipients | Code |
|---|---|---|
| Manager creates a broadcast | every `floor_staff` with a registered device token | `create_broadcast` in `dispatch.py` |
| A zone's status newly crosses into `warning` or `crowded` | every `ops_manager` with a registered device token | `_process_zone_update` in `detections.py` |

The status-change push only fires on the transition into `warning`/`crowded`
— not on every `POST /detections` while the zone stays busy (the worker
posts roughly once a second), and not on the way back down to `safe`. State
is tracked in-memory per zone (`_last_status`), so it resets on API restart.

Alert copy (`_ALERT_MESSAGES` in `detections.py`) — crowded is intentionally
more urgent than warning:

| Status | Title | Body |
|---|---|---|
| `warning` | Crowd warning | `{zone} is getting busy ({count} people detected).` |
| `crowded` | Zone crowded — act now | `{zone} is crowded ({count} people). Dispatch staff immediately.` |

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
Apple silently rejects every push with no error visible server-side. All
values are read via `src/plong_elven/common/settings.py`; restart the API
after changing `.env` (`--reload` only watches `.py` files).

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

Log in on a device (the app requests notification permission and registers
its device token automatically — see `PushNotifications.swift` and
`POST /api/v1/auth/push-token` in the frontend/backend respectively), then
check it reached the database:

```bash
docker compose --env-file .env -f infra/docker-compose.yml exec -T db \
  psql -U plong_elven -d plong_elven \
  -c "SELECT email, (push_token IS NOT NULL) AS has_push_token FROM users;"
```

Trigger a real push: create a broadcast (`POST /api/v1/dispatch/broadcasts`,
see [DISPATCH_API.md](DISPATCH_API.md)) as a manager with at least one
`floor_staff` device registered, or drive a zone's count into `warning`/
`crowded` via the in-browser **Dev Tools** tab (see
[SETUP_CCTV.md](SETUP_CCTV.md)) with a manager device registered.

## Troubleshooting

### Nothing arrives, no error anywhere

Check `APNS_KEY_PATH` actually points at a file that exists — a wrong path
fails inside a background task and is only visible as a server log warning
(`could not load APNs signing key, skipping push`), never as an API error,
since a push failure must never block broadcast creation.

### `APNs not configured, skipping push to N device(s)` in the logs

One of `APNS_KEY_PATH`/`APNS_TEAM_ID`/`APNS_KEY_ID` is empty in `.env`.

### Push accepted (`200` from Apple) but never arrives on the phone

Almost always `APNS_TOPIC` not matching the app's real bundle id, or
`APNS_USE_SANDBOX` not matching how the app was built (`true` for a normal
Xcode run, `false` for TestFlight/App Store). Both fail silently from Apple's
side — a `200` response only means the payload was *accepted*, not delivered.

### Banner doesn't show while the app is open

iOS suppresses push banners by default while the app is foregrounded, unless
the app opts in. See `userNotificationCenter(_:willPresent:)` in
`PushNotifications.swift` (frontend) — it requests `.banner, .sound, .badge`
even in foreground.
