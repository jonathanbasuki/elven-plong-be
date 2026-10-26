# Local CCTV HLS Setup

This guide starts with connecting the CCTV camera and ends with opening its HLS
preview on an iPhone connected to the same LAN as the Mac server.

```text
Tapo camera --RTSP--> MediaMTX --HLS--> iPhone / iOS AVPlayer
                       |
                       +--RTSP--> CCTV worker --> API detections
```

MediaMTX serves the camera's original video. The API's `/stream/default` is a
separate annotated preview produced by the worker; HLS does not depend on the
API being online.

## 1. Connect and configure the camera

1. Connect the Tapo camera to the same local network as the Mac.
2. Give the camera a DHCP reservation in the router so its IP address remains
   stable.
3. In the Tapo app, enable a **Camera Account** for RTSP access. This account
   may be separate from the Tapo cloud account.
4. Note the camera's LAN IP address and confirm the RTSP stream path. Tapo
   cameras commonly use `/stream1` for the high-resolution stream and
   `/stream2` for the lower-resolution stream.

The RTSP URL format is:

```text
rtsp://<camera-account-user>:<camera-account-password>@<camera-ip>:554/stream1
```

If the username or password contains URL-reserved characters such as `@`, `:`
or `#`, URL-encode those characters before putting the URL in `.env`.

## 2. Configure the local RTSP source

Set `RTSP_URL` in the local `.env` file:

```env
RTSP_URL=rtsp://<camera-account-user>:<camera-account-password>@<camera-ip>:554/stream1
```

Do not commit or share `.env`. The generated `infra/mediamtx.yml` also contains
the camera credential and is ignored by Git. The next step starts MediaMTX
using this URL; its logs will show whether it can connect to the camera.

## 3. Generate the MediaMTX config and start it

From the repository root, run:

```bash
make mediamtx
```

This generates the ignored `infra/mediamtx.yml` from `RTSP_URL` in `.env`, then
starts MediaMTX in the foreground. Keep this terminal open. Press `Ctrl+C` to
stop MediaMTX.

The generated config sets `hlsVariant: mpegts` (classic segmented HLS). This
is required for AVPlayer on iOS: MediaMTX's default low-latency fMP4 HLS mode
can drift its part duration on a jittery camera feed, which AVPlayer does not
report as an error — it just shows a black view indefinitely. MPEG-TS HLS has
no parts and does not hit this, at the cost of a couple extra seconds of
latency.

**Only one RTSP client can read the camera at a time.** If you also run
`make cctv` (which connects straight to `RTSP_URL`, i.e. the camera) while
MediaMTX is running, the two will fight over the camera's single RTSP slot —
MediaMTX's logs will show the stream repeatedly going "available" then `EOF`
every few seconds, and HLS will never stabilize. Use `make cctv-relay`
instead (step 7 below); it reads from MediaMTX's own relay
(`rtsp://127.0.0.1:8554/cam1`), so only MediaMTX ever talks to the camera.

The local endpoints are:

```text
RTSP relay: rtsp://127.0.0.1:8554/cam1
HLS preview: http://127.0.0.1:8888/cam1/index.m3u8
```

To regenerate the config without starting the container:

```bash
make mediamtx-config
```

If `RTSP_URL` changes, regenerate the config and restart MediaMTX.

## 4. Verify MediaMTX on the Mac

In another terminal, verify the HLS stream:

```bash
ffmpeg -i "http://127.0.0.1:8888/cam1/index.m3u8" \
  -t 10 -an -f null -
```

It should report H.264 video and process frames. HLS introduces latency; a few
seconds of delay is expected with the default settings.

## 5. Find the Mac's LAN IP address

On the Mac connected to Wi-Fi:

```bash
ipconfig getifaddr en0
```

For example, if it returns `192.168.1.20`, the iPhone HLS URL will be:

```text
http://192.168.1.20:8888/cam1/index.m3u8
```

The IP can change when the Mac reconnects to the network. Set a DHCP reservation
for the Mac in the router if the iOS app needs a stable URL.

## 6. Test playback on the iPhone

1. Connect the iPhone to the same Wi-Fi/LAN as the Mac. Avoid a guest network
   that blocks devices from connecting to each other.
2. Open Safari and visit `http://<mac-lan-ip>:8888/cam1/index.m3u8`.
3. Confirm the live camera video plays. The URL must use the Mac's LAN IP, not
   `127.0.0.1` or `localhost`; those addresses refer to the iPhone itself.
4. If it does not load, check the Mac firewall and allow Docker/MediaMTX to
   accept incoming connections on port `8888`.

For iOS app playback, pass the same LAN URL to `AVPlayer`. The app also needs a
Local Network usage description (`NSLocalNetworkUsageDescription`) so iOS can
ask the user for permission to access the Mac on the LAN. Because this local
HLS URL uses HTTP, App Transport Security may require an appropriate development
exception. Avoid broad insecure-load exceptions in production; use HTTPS for a
deployed service.

The Plong app plays this feed through `AVPlayerViewController` with
`showsPlaybackControls = false` (see `CCTVPreview`/`LivePlayerView` in
`ContentView.swift`) rather than SwiftUI's `VideoPlayer`, which always shows a
pause/scrub bar. This keeps the preview always live — a manager can't pause it
and mistake a frozen frame for the current state.

## 7. Run the API and CCTV worker

HLS playback itself does not require the API or detection worker. Start them if
you also want detection counts and the annotated API preview.

In separate terminals:

```bash
make api
```

```bash
make cctv-relay
```

The worker reads the RTSP relay from MediaMTX and posts detection data and
annotated/raw preview frames to the API. The HLS URL remains available if the
API is stopped, as long as MediaMTX and the CCTV camera remain online.

The annotated API preview is separate:

```text
http://127.0.0.1:8080/stream/default
```

## Security notes

- Keep `.env` and `infra/mediamtx.yml` out of Git; both contain the camera URL
  and may contain its credentials.
- Do not configure router port forwarding for MediaMTX ports `8554` or `8888`.
- The current HLS endpoint is intended for trusted local-network testing. Do
  not expose it to the public internet; add access control and HTTPS before
  deployment.
- If camera credentials have been shared or committed, rotate them in the Tapo
  Camera Account settings and update `.env`.

## Stop services

Stop the worker and API with `Ctrl+C` in their terminals. Stop MediaMTX with
`Ctrl+C` in its terminal. The `--rm` Docker option removes the container after
it stops; it does not alter camera settings or source code.
