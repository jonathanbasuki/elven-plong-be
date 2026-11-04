# Broadcast snapshots

The API takes the latest annotated in-memory JPEG received from the CCTV worker.
Broadcast creation queues a `before` snapshot; successful finish after every
accepted staff member completes queues an `after` snapshot. Rows are stored in
`snapshots` and files are uploaded to the public `plong-snapshots` Supabase
bucket. Failures are recorded and do not fail dispatch.

When the last staff member completes, the broadcast becomes `complete` and a
five-minute crowd window starts immediately. If the average count exceeds the
zone's crowded threshold, another five-minute window starts. Otherwise the
latest annotated frame becomes the `after` snapshot and the broadcast becomes
`finished`. No MediaMTX recording or FFmpeg installation is required.
