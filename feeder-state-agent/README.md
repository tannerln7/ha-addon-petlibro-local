# PLAF203 feeder state agent

`plaf203-state-agent` is the read-only, feeder-resident source of persistent
configuration truth for the Petlibro Local Backend. It serves an authenticated
HTTP API over an allowlist of files below `/user/data`; it has no write API,
does not execute a shell, and never reads MQTT, TUTK, or camera credentials.

This directory is the canonical source for the agent, its build definition,
startup example, tests, and deployment documentation. Do not maintain a second
out-of-tree copy or commit locally compiled binaries.

The Home Assistant add-on consumes this API but does not install the binary on
the feeder. Build and deploy the agent separately after obtaining authorized
shell access to your own device.

## Binary-backed schema

Version `0.2.0` requires `/user/data/attr/state.bin` to be exactly 236 bytes.
Short, long, or unreadable files make `/health`, `/v1/rev`, and `/v1/core`
report an error; no partial state is promoted as truth. Each request reads every
source file once, then derives both decoded values and revisions from those
immutable buffers.

Decoded settings are classified as:

- `persistent`: user configuration used for post-command verification;
- `effective_cached`: firmware-calculated RAM state that may be stale on disk;
- `runtime`: sensors, telemetry, and machine state.

The API exposes these classes in `setting_classes`. In particular, the
persistent switches are `light_switch`, `sound_switch`, `camera_switch`,
`video_record_switch`, `motion_detection_switch`, and
`sound_detection_switch`. Their adjacent `*_effective_cached` fields are
informational and must not verify user commands.

Feeding plans are exact 47-byte records. The semantic response includes the
32-bit plan ID, `one_shot`, audio fields, 32-bit `execution_state`, 64-bit
`sync_time`, 64-bit `skip_end_time`, and the ten-byte opaque tail. Schedule
revision/equality excludes runtime execution state and regenerated sync
metadata.

`feed_rec.bin` is decoded as 51 queue slots of 93 bytes. Each slot contains
three 31-byte phases: `GRAIN_START`, `GRAIN_END`, and `GRAIN_BLOCKING`.
`/v1/feed-events` iterates pending slots from ring head to tail and explicitly
reports `pending_outbound_events_not_history`; acknowledged entries can be
cleared by firmware and must not be treated as durable feeding history.

## Build

Host validation build:

```bash
make LDFLAGS=
```

Static ARM build with an available musl cross-compiler:

```bash
make CC=armv7l-linux-musleabihf-gcc
```

or:

```bash
make CC=arm-linux-musleabihf-gcc
```

## Install or upgrade on the feeder

Create a private token and install the binary:

```sh
mkdir -p /user/data/local-state-agent
cp plaf203-state-agent /user/data/local-state-agent/
chmod 700 /user/data/local-state-agent/plaf203-state-agent
umask 077
test -s /user/data/local-state-agent/token || \
  head -c 32 /dev/urandom | xxd -p > /user/data/local-state-agent/token
chmod 600 /user/data/local-state-agent/token
touch /user/data/enable_state_agent
sync
```

Use [`app_start_snippet.sh`](app_start_snippet.sh) as the guarded startup
pattern before the firmware process starts. Replace the example `--allow-ip`
value with the Home Assistant host address that will originate API requests.
Keep the token only on the feeder and in the add-on secret option; never paste
it into logs or issue reports.

After replacing an older agent binary, restart the feeder or terminate only the
old agent process and allow the guarded startup command to launch the new one.

## Read-only checks

```bash
TOKEN="$(ssh root@FEEDER_IP -p 2222 'cat /user/data/local-state-agent/token')"
curl -fsS -H "Authorization: Bearer $TOKEN" \
  http://FEEDER_IP:8765/health | jq
curl -fsS -H "Authorization: Bearer $TOKEN" \
  http://FEEDER_IP:8765/v1/rev | jq
curl -fsS -H "Authorization: Bearer $TOKEN" \
  http://FEEDER_IP:8765/v1/core | jq
curl -fsS -H "Authorization: Bearer $TOKEN" \
  'http://FEEDER_IP:8765/v1/core?raw=1' | jq
curl -fsS -H "Authorization: Bearer $TOKEN" \
  'http://FEEDER_IP:8765/v1/feed-events?raw=1' | jq
```

Raw responses contain feeder configuration bytes and should be used only for
bounded local diagnostics. They do not expose camera authentication material.
