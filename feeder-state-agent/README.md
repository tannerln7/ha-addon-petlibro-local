# PLAF203 feeder state agent

`plaf203-state-agent` is the feeder-resident source of persistent
configuration truth for the Petlibro Local Backend. It serves an authenticated
HTTP API over an allowlist of files below `/user/data`. Its only write operation
is a fixed-path, signed OTA staging endpoint; it does not execute a shell or
caller-selected command and never reads MQTT, TUTK, or camera credentials.

This directory is the canonical source for the agent, its build definition,
startup example, tests, and deployment documentation. Do not maintain a second
out-of-tree copy or commit locally compiled binaries.

The Home Assistant add-on consumes this API but does not install the binary on
the feeder. Build and deploy the agent separately after obtaining authorized
shell access to your own device.

## Binary-backed schema

Version `0.3.0` requires `/user/data/attr/state.bin` to be exactly 236 bytes.
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
make clean all
```

Static Linux ARMv7 hard-float release build requires
`arm-linux-gnueabihf-gcc`:

```bash
make arm-release
```

The build emits `plaf203-state-agent` and the narrow fixed-path
`plaf203-update-fs` durability helper. It embeds the version from `VERSION` and the 32-byte Ed25519 public key
from `release-public-key.hex`. `vendor/monocypher/` is compiled into the agent;
the release binary must not depend on OpenSSL or another dynamic crypto library.

`release-public-key.hex` is the candidate embedded trust anchor for the next
State Agent build. It is distinct from the private key that authorizes the
manifest for that build: normal releases use a matching private/public pair and
omit trust-anchor rotation, while an explicit rotation release embeds the next
trust anchor and is signed by the previous private key.

## Bootstrap and signed updates

The first installation is manual. OTA cannot create its own runit services or
replace an incompatible pre-OTA agent. On an authorized feeder shell, install
the `arm-release` binary, the `runit/` directory, and the startup integration
before enabling updates:

```sh
mkdir -p /user/data/local-state-agent
cp plaf203-state-agent /user/data/local-state-agent/
cp plaf203-update-fs /user/data/local-state-agent/
chmod 700 /user/data/local-state-agent/plaf203-state-agent
chmod 700 /user/data/local-state-agent/plaf203-update-fs
cp -R runit /user/data/local-state-agent/runit
find /user/data/local-state-agent/runit -type f -exec chmod 700 {} \;
umask 077
test -s /user/data/local-state-agent/token || \
  head -c 32 /dev/urandom | xxd -p > /user/data/local-state-agent/token
chmod 600 /user/data/local-state-agent/token
touch /user/data/enable_state_agent
sync
```

Integrate [`app_start_snippet.sh`](app_start_snippet.sh) in the feeder startup
path before the firmware process starts. It maintains a private runsvdir tree
for both the State Agent and its update supervisor. Replace the example
`--allow-ip` value in `runit/plaf203-state-agent/run` with the Home Assistant
host address that will originate API requests. Keep the token only on the
feeder and in the add-on secret option; never paste it into logs or issue
reports. Verify ordinary API behavior before enabling add-on updates.

Bootstrap also requires executable `/usr/bin/flock`, `/usr/bin/nc`, and
`/usr/bin/sv`. The supervisor uses the first two fixed absolute paths for the
kernel transaction lock and authenticated local probation probes. A missing
probe tool is reported as `probe_tool_unavailable`; missing transaction tooling
is reported as `transaction_tool_unavailable`.

The add-on downloads and verifies the release; the feeder never downloads an
artifact. A release publisher must publish an immutable artifact first, then
create `latest.json` and `latest.json.sig` with:

```bash
python3 scripts/build_release_manifest.py \
  --artifact-path plaf203-state-agent \
  --artifact-url https://updates.example.invalid/plaf203/0.3.0/plaf203-state-agent \
  --release-url https://example.invalid/releases/plaf203-state-agent-0.3.0 \
  --signing-key /secure/path/ed25519-private.pem \
  --public-key-file release-public-key.hex \
  --output-dir release
```

Pass the private signing key only through `--signing-key` or the
`PETLIBRO_STATE_AGENT_SIGNING_KEY` environment variable; never commit it. The
tool reports SHA-256 fingerprints for the signer-derived public key and the
candidate trust anchor. Normal mode requires those fingerprints to match and
verifies the signature with the signer-derived public key; rotation mode
requires them to differ and uses `--rotate-trust-anchor` so the current
signer authorizes the transition candidate, and that candidate independently
embeds the next trust anchor.

Normal release example:

```bash
python3 scripts/build_release_manifest.py \
  --artifact-path plaf203-state-agent \
  --artifact-url https://updates.example.invalid/plaf203/0.3.1/plaf203-state-agent \
  --release-url https://example.invalid/releases/plaf203-state-agent-0.3.1 \
  --signing-key /secure/path/ed25519-private-B.pem \
  --public-key-file release-public-key.hex \
  --output-dir release
```

Rotation release example:

```bash
python3 scripts/build_release_manifest.py \
  --artifact-path plaf203-state-agent \
  --artifact-url https://updates.example.invalid/plaf203/0.3.2/plaf203-state-agent \
  --release-url https://example.invalid/releases/plaf203-state-agent-0.3.2 \
  --signing-key /secure/path/ed25519-private-A.pem \
  --public-key-file release-public-key.hex \
  --rotate-trust-anchor \
  --output-dir release
```

The signed manifest has schema version 1 and exactly these top-level fields:
`schema_version`, `product`, `channel`, `version`, `api_version`,
`update_api_version`, `platform`, `artifact`, and `release_url`. `artifact`
contains HTTPS `url`, lowercase 64-hex-character `sha256`, and positive `size`.
Artifact URLs must have a concrete path and no credentials, query, or fragment;
release URLs also reject credentials and fragments. AppDaemon rejects release
download redirects rather than allowing a fetch to change origin or downgrade
transport. SemVer 2.0.0 precedence is
used, including numeric prerelease ordering and build-metadata equivalence.
The manifest schema does not change during a trust-anchor rotation and carries
no replacement trust key; the next trust anchor remains compiled into the
executable. The agent accepts only product `plaf203-state-agent`, stable
channel, `linux-armv7-eabihf`, both API versions 1, and a strictly newer SemVer
version.

AppDaemon submits the fixed binary frame: ASCII `PLAFOTA1`, three big-endian
u32 lengths for manifest, signature, and artifact, exact manifest bytes, the
64-byte detached signature, then artifact bytes. The feeder verifies the
signature before manifest parsing, hashes the staged file through its fixed
`/usr/bin/sha256sum` invocation, and performs an ARMv7 ELF sanity check.

Operational sequence for a single-key A→B rotation:

1. A deployed State Agent trusts A.
2. B deployed AppDaemon trusts A.
3. Build a transition State Agent with `release-public-key.hex=B`.
4. Sign the transition manifest with private A and `--rotate-trust-anchor`.
5. The existing AppDaemon A verifies the manifest.
6. The existing State Agent A verifies the upload.
7. The candidate installs and now trusts B.
8. Confirm probation succeeded and rollback is no longer pending.
9. Update AppDaemon and package trust-anchor material to B.
10. Future releases are signed by B in normal mode.

If a feeder misses the A→B transition before AppDaemon moves to B, recover by
temporarily running an AppDaemon/release workflow that still trusts A or by
using manual SSH access to restore the feeder-side trust anchor before retrying.

Publication order is immutable artifact first, then signed manifest. The
release manifest never carries a remotely supplied trust anchor, and there is no
online keyring lookup on either side of the boundary.

The existing bearer token and source-IP ACL protect `/v1/version`,
`/v1/update-status`, and `POST /v1/update`; OTA has no separate token. The
runit supervisor and State Agent serialize staging/activation with a kernel
lock on `/user/data/local-state-agent/update/transaction.lock`; a conflicting
upload receives HTTP 409. Accepted sockets have a 30-second receive/send
inactivity timeout, which bounds stalled headers, bodies, and responses without
limiting an active streaming upload.

The supervisor owns stop/swap/start/probation and one rolling backup. Its
durable phases are `pending`, `activating` (`pre_swap` or
`backup_committed`), `candidate_active`, `probation_confirmed`,
`rollback_in_progress`, and the terminal `idle`, `rolled_back`, or `failed`
states. The durability helper copies through a same-directory temporary file,
fsyncs it, renames atomically, then fsyncs the containing directory. It uses
the same sequence for backup creation, candidate activation, backup restore,
and status replacement; transaction-significant cleanup unlinks files and
fsyncs the directory.

After both authenticated `/health` and exact `/v1/version` probes pass, the
supervisor durably commits `probation_confirmed` before cleanup. Reboot recovery
therefore finishes success rather than rolling back a proven candidate. A
failed candidate first commits `rollback_in_progress`; recovery resumes a
validated backup restore and never retries that candidate. Corrupt/missing
backups produce `backup_invalid` without overwriting the active binary.
Probation makes ten authenticated probe attempts with two seconds between
failed attempts, giving the candidate a bounded startup window even when a
refused netcat connection returns immediately.

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
