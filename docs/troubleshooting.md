# Troubleshooting

## Add-on does not start

Check the Home Assistant app/add-on log. Configuration errors are reported
without echoing option values. Confirm that `lan_cidr` is a valid IPv4 network
no larger than `/16`, the MQTT client ID contains only letters, numbers,
underscores, or hyphens, and MQTT and go2rtc ports are between 1 and 65535.

For Docker Compose, run:

```bash
cd docker
docker compose config --quiet
docker compose logs --follow petlibro-local
```

## Installation uses high CPU or appears stuck

Repository installs should pull
`ghcr.io/tannerln7/ha-addon-petlibro-local:<version>` and should not compile on
the Home Assistant host. If Supervisor reports a Docker build or the host CPU
stays near 100%, refresh the repository and confirm its `config.yaml` still has
the `image:` field. A copied local add-on whose `image:` field was removed or
commented out intentionally falls back to a local build.

Local builds compile patched go2rtc and install Python/AppDaemon dependencies.
They can temporarily make a Raspberry Pi-class or otherwise constrained Home
Assistant system unresponsive. Prefer the repository install. For development
Docker builds, use the maintained default limit or lower it further:

```bash
GO_BUILD_PROCS=1 ./scripts/build-local.sh
```

If Supervisor reports that it cannot pull the GHCR image, verify that the tag
matches the add-on version and that the GHCR package is public. Public GHCR
container packages can be pulled anonymously; private packages cannot be used
by an ordinary add-on installation without registry authentication.

## Check go2rtc

Open the web interface at `http://HOME_ASSISTANT_HOST:1984/` or the configured
API port. Confirm that the configured stream exists and inspect its connection
status.

Test RTSP from a trusted host:

```bash
STREAM_NAME=petlibro_plaf203_your_device_serial ./scripts/test-stream.sh
```

The equivalent direct command is:

```bash
timeout 300s ffmpeg -hide_banner -rtsp_transport tcp \
  -i "rtsp://127.0.0.1:8554/petlibro_plaf203_your_device_serial" \
  -an -f null -
```

Automatic stream names have the form
`petlibro_plaf203_<sanitized_serial>`. Read the retained discovery state or
open the go2rtc web interface to obtain the exact name.

## Device remains in discovery

Inspect these retained topics:

```text
petlibro_local/discovery/devices
petlibro_local/PLAF203/YOUR_DEVICE_SERIAL/discovery/state
```

- No device entry: verify feeder topics reach this broker and begin with
  `dl/PLAF203/<serial>/device/`.
- `uid_discovered: false`: keep the backend running and reboot the feeder. The
  startup event is not assumed to be retained, so a reboot performed before
  the backend subscribed may need to be repeated. The add-on emits this same
  power-cycle guidance once after a topic-discovered feeder remains without a
  startup UID; it is not repeated on every heartbeat.
- `ip_resolved: false`: confirm `lan_cidr`, host networking, UDP reachability,
  and that the feeder is on the same routed LAN.
- `stream_configured: false`: both a valid UID and resolved address are needed.

Publish `all` to `petlibro_local/discovery/refresh` for a manual address refresh.
The backend keeps the last known IP after failure and retries with configured
backoff.

At `log_level: debug`, the completion line reports the resolver method, elapsed
time, each LAN_SEARCH3/KNOCK2 send leg, KNOCK_RR2 receipts, UID/nonce
rejections, broadcast and unicast counts, received and rejected responses,
send errors, and final error code. `deadline_exceeded` means the bounded lookup
finished normally without a match. `helper_timeout` means the child resolver
failed to honor its deadline and the Python safety guard terminated it; this
is an implementation failure rather than proof that the feeder is offline.
Resolver completions use an opaque attempt marker and always release the
in-progress guard for success, failure, malformed output, callback errors, and
stale identity results. A failed attempt therefore cannot permanently suppress
the configured retry or a manual refresh.

## A food-plan value reverts after refreshing Home Assistant

Each of the nine Feeding schedule text entities has a matching plan ID. Feeding
schedule 1 must contain `"id":1`, continuing through `"id":9` for Feeding
schedule 9. The
add-on rejects a mismatch and logs `feeding-plan slot/id mismatch ignored`
without logging the schedule contents.

The slot must already exist in the feeder's `/v1/core` plan collection. The
controller does not create or delete plans. A valid update logs a pending write,
performs a fresh full-state preflight, waits for the matching MQTT ack, and then
logs `persistent feeder write verified` only after `/v1/core` contains the
complete expected collection.

If the UI restores an older value, that is intentional when verification
failed: feeder-local truth wins. Look for `feed-plan preflight`, `state API
unavailable`, `persistent feeder state diverged`, or `persistent feeder write
failed`. Do not republish retained plan JSON as a workaround; retained and
stored Home Assistant schedules are deliberately ignored as command sources.

## Persistent controls are unavailable

The camera and required feeder MQTT responses can remain active while the
read-only state API is unavailable, but persistent settings and plans are
blocked. Confirm the state agent responds from the add-on network at
`http://FEEDER_IP:8765/health`, that `petlibro_state_agent_token` matches its
bearer token, and that a custom `petlibro_state_agent_url` does not point to a
stale address. Never paste the token into logs or issue reports.

At `debug` level, a healthy recovery shows `feeder state API recovered`, a
transition through `RECONCILING`, and `feeder reconciliation complete`. HTTP
authentication failures are reported by type without exposing the token.

If `/health` reports `state_decode.ok: false`, do not bypass the check. Confirm
`/user/data/attr/state.bin` is readable and exactly 236 bytes. If `/v1/core`
lacks fields such as `sound_switch`, `setting_classes`, or the full 47-byte plan
metadata, upgrade the feeder to the tracked State Agent 0.2.0 build. The
controller intentionally blocks persistent writes rather than using an older,
ambiguous decoder.

## Initial 640x360 stream before HD

Tested PLAF203 firmware can start an HD session with a 640x360 SPS and switch to
1920x1080 later. Some observed sessions transitioned after several minutes,
beyond the configurable 60000 ms probe limit. Keep `camera_quality: hd` and use
the recommended `hd_probe_wait_ms: 15000`; the runtime metadata reports both
the first and latest SPS even when go2rtc initially advertises 640x360.

## Feeder resolution returns to P720 when viewing stops

The Home Assistant **Feeder camera resolution** select mirrors the device
attribute, which can change to 1080p for an active HD TUTK session and return to
720p after the viewer disconnects. MQTT still carries the protocol values
`P1080` and `P720`. This does not mean a feeding plan
or unrelated setting changed the requested go2rtc quality. Use
`actual_resolution` from the camera runtime metadata to determine the SPS
resolution of an active stream.

Sparse device attribute events are applied field by field. A resolution-only
event must not change food, audio, recording, detection, or other state. At
debug level, `feeder-reported camera settings updated` indicates an inbound
device report; it is not evidence that the backend sent a resolution command.

## Corruption, choppy playback, or media stalls

Start with the validated settings:

```yaml
camera_quality: hd
ack_mode: hybrid
send_delay_ctrl: true
hd_probe_wait_ms: 15000
```

Then set `log_level: debug`. Petlibro statistics report packet families, media
loss, ACK progress, and SPS transitions without including raw packets or MQTT
payloads. Escalate to `log_level: trace` only for a short reproduction that
needs per-packet, ACK, fragment, frame-info, or raw MQTT evidence; trace can
produce a very large volume of output.

Enable `enable_debug_dumps` only when repeatable packet evidence is necessary.
The add-on writes one pair per stream:

- `/data/petlibro_c2d_<stream>.dat`
- `/data/petlibro_d2c_<stream>.dat`

Disable dumping after a short reproduction because files grow continuously and
contain decrypted device/session traffic.

Changing `log_level` does not enable or disable dump files. The two controls are
deliberately independent.

## Backend cannot connect to MQTT

Check that the broker is reachable and that the configured AppDaemon account
can subscribe and publish. The `mqtt_username` and `mqtt_password` options are
for this backend identity, not the physical feeder.

## Feeder does not connect to MQTT

Confirm the feeder itself is connecting to the local broker and that topics
beginning with
`dl/PLAF203/YOUR_DEVICE_SERIAL/device/` are present.

The backend does not change feeder DNS or provision its factory MQTT
credentials. The feeder identity is separate from the backend identity and must
already exist in the broker. Those network prerequisites must be completed
separately.

Normal startup logs `Feeder MQTT persistence disabled; preserving existing
feeder MQTT config`. If the feeder unexpectedly queries an internal name such
as `core-mosquitto`, that value may have been persisted by an older backend
version. Restore temporary DNS reachability first, then configure a durable LAN
IP or multi-label DNS name under `feeder_mqtt_host`, enable
`persist_feeder_mqtt`, restart the add-on, then reboot the feeder. Wait for the
validation, send-attempt, and acknowledgement log lines before removing the
temporary DNS record.

If validation is blocked, the reason is included without sending
`DEVICE_CONFIG_SYNC`. Confirm that the name resolves from the add-on to a
feeder-routable address and that the configured MQTT port accepts TCP
connections. `core-mosquitto`, single-label names, loopback/link-local/
multicast/unspecified addresses, and known Home Assistant internal container
networks are intentionally rejected. After a successful migration, disable
`persist_feeder_mqtt` again to return to preserve-only startup behavior.

## Camera metadata is offline or missing

Camera producers start lazily. Open the configured RTSP or WebRTC stream before
expecting `camera/availability` to become `online`. Then check the retained
topics using generic placeholders:

```text
petlibro_local/PLAF203/YOUR_DEVICE_SERIAL/camera/state
petlibro_local/PLAF203/YOUR_DEVICE_SERIAL/camera/availability
```

If availability stays offline, inspect the add-on log for a short camera
metadata warning and confirm
`/data/petlibro_camera_status_<stream>.json` exists inside the container. A
missing file means go2rtc has not started a producer or could not write status.
A malformed file publishes `status: error`; a file older than three configured
heartbeat intervals publishes `status: offline`.

Before a stream has ever been opened, a missing status file is an informational
startup condition. It becomes a warning if status disappears after a working
producer was previously observed.

Do not point frontend integrations at the file. Use the documented
[MQTT camera contract](mqtt-camera-contract.md).

## Collect diagnostics

For Docker Compose:

```bash
./scripts/collect-logs.sh
```

The script excludes generated YAML because it contains MQTT credentials and
local identifiers. If debug dumps exist, the archive includes them and must be
handled as sensitive data.
