# Development

This repository is the canonical workspace for the Petlibro backend package.
Make future camera changes under `petlibro-local/go2rtc` and controller changes
under `petlibro-local/appdaemon`; the original standalone repositories are
historical snapshots only.

## Components

| Path                        | Responsibility                                                  |
| --------------------------- | --------------------------------------------------------------- |
| `petlibro-local/go2rtc/`    | Patched go2rtc source and Petlibro camera protocol              |
| `petlibro-local/appdaemon/` | PLAF203 AppDaemon MQTT controller                               |
| `feeder-state-agent/`       | Authenticated read-only feeder-resident state decoder/API       |
| `petlibro-local/`           | Home Assistant add-on metadata, image, templates, and services  |
| `docker/`                   | Host-network Docker Compose fallback                            |
| `scripts/`                  | Build, run, stream-test, log collection, and validation helpers |
| `tests/`                    | Backend packaging and configuration-rendering tests             |

No submodule, subtree, or synchronization process links these sources to their
former repositories.

## Prerequisites

- Go 1.24 or newer for the imported go2rtc module
- Python 3.12 or newer
- Docker with Compose for image validation and local runtime testing
- A C99 compiler for the host State Agent build and decoder regression tests
- FFmpeg for `scripts/test-stream.sh`

Create a Python environment for the controller tests:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r petlibro-local/appdaemon/requirements-dev.txt
```

## Validation

Run the complete local validation suite:

```bash
./scripts/validate.sh
```

The script validates shell and Python syntax, configuration rendering,
the feeder State Agent host build with vendored Monocypher and generated release
metadata, AppDaemon protocol tests when its dependency is installed, the Petlibro Go
package, the complete go2rtc build, Compose interpolation, whitespace, and
tracked-file hygiene.

Build the production image separately:

```bash
./scripts/build-local.sh
```

Local image builds default to `GO_BUILD_PROCS=2`, which sets both
`GOMAXPROCS` and Go's package build parallelism for the compile stages. Override
it only on a development machine with enough CPU and memory:

```bash
GO_BUILD_PROCS=4 ./scripts/build-local.sh
```

The Dockerfile leaves compiler concurrency unrestricted when
`GO_BUILD_PROCS` is omitted, so the GitHub-hosted release build can use its
available runner resources.

Do not run a live feeder test unless device values are supplied through the
ignored `docker/.env` or Home Assistant options.

## Runtime configuration

`petlibro-local/render_config.py` reads `/data/options.json` under Home
Assistant. Without that file, it reads the upper-case environment variables
used by Docker Compose. It validates required values and atomically writes:

- `/data/go2rtc.yaml`
- `/data/appdaemon.yaml`
- `/data/apps.yaml`
- `/data/appdaemon-secrets.yaml`
- `/data/devices.json`
- `/data/petlibro_camera_status_<stream>.json` (created by an active go2rtc camera session)

The registry, AppDaemon secrets file, and all generated configuration files are mode 0600.
The backend does not accept the feeder product secret. Feeder broker account
provisioning is external to this package.

The camera status file is an internal, atomic service boundary. go2rtc owns
runtime observations and counters; AppDaemon validates and publishes the stable
MQTT contract documented in `docs/mqtt-camera-contract.md`. Frontend code must
not depend on the internal file schema.

`appdaemon/src/device_discovery.py` owns MQTT identity observation, persistent
registry updates, resolver invocation, readiness publication, and idempotent
runtime reconfiguration. `go2rtc/cmd/petlibro-resolve` is the small CLI
boundary around the package's LAN_SEARCH3/KNOCK2 implementation. Test
coordinator policy as pure Python where practical and discovery wire behavior
in Go.

The resolver uses a cached/broadcast/candidate/single-sweep strategy under one
socket deadline. Keep its receiver active while sends occur, preserve the
configured rate limit, and never add an unbounded per-target operation.
PLAF203 discovery requires both LAN_SEARCH3 legs and KNOCK2; accept KNOCK_RR2
only when its UID and nonce match. The AppDaemon coordinator uses
`submit_to_executor` for the blocking subprocess. The executor worker returns
data only, while the completion callback serializes registry, MQTT readiness,
and config changes. AppDaemon invokes that callback with the worker value as
the keyword argument `result`; regression tests must exercise
`callback(result=...)`. Keep the completion path guarded so malformed, stale,
or exceptional results release only their matching opaque attempt token and
cannot suppress later retries.

`appdaemon/src/petlibro_logging.py` owns application log filtering and secret
redaction. Do not start AppDaemon with global `-D DEBUG`: that exposes its
high-frequency scheduler and state internals. Add bounded state summaries at
`debug` and raw message or packet evidence at `trace`. Decrypted dump files
remain controlled exclusively by `enable_debug_dumps`.

`appdaemon/src/feeder_mqtt_validation.py` owns the safety gate for physical
feeder endpoint persistence. Keep add-on broker connection settings separate
from feeder-facing settings. Any new persistence path must preserve the
default no-sync behavior and pass DNS answers through the address policy and
bounded TCP reachability checks before sending `DEVICE_CONFIG_SYNC`.

`appdaemon/src/state_agent.py` is the blocking, read-only HTTP boundary for the
feeder truth API. Keep its errors sanitized, its bearer token private, and its
response models explicit. Calls belong in AppDaemon's executor, never the main
callback thread.

`appdaemon/src/state_agent_updates.py` owns optional signed State Agent release
checking and upload. It is independent from `FeederStateCoordinator`: it
downloads only HTTPS release files, verifies the Ed25519 signature over the
exact raw manifest before parsing it, and passes a fixed binary frame to the
feeder API. Status polling after an upload must query the feeder only, never
refetch the public manifest. The only repository trust-anchor source is
`feeder-state-agent/release-public-key.hex`; do not add a second key copy or a
private signing key. Treat that file as the candidate embedded trust anchor for
the next State Agent build; the private signer authorizing the current
candidate is separate. Normal releases use matching signer and candidate keys
and omit `--rotate-trust-anchor`. Rotation releases intentionally set
`release-public-key.hex` to the next key, sign with the previous private key,
and pass `--rotate-trust-anchor` so the current signer authorizes the transition
candidate, and that candidate independently embeds the next trust anchor. The
tool reports SHA-256 fingerprints for the signer-derived public key and
candidate trust anchor; normal mode requires equality, rotation mode requires
inequality, and self-verification uses the signer-derived public key.

`feeder-state-agent/plaf203_state_agent.c` owns the binary decoder and HTTP
schema. It must read only allowlisted files, require the exact 236-byte
`state.bin`, parse unaligned integers explicitly as little-endian, and derive
decoded values plus revisions from the same per-request buffers. Preserve the
`persistent`, `effective_cached`, and `runtime` distinction: only persistent
fields may verify a setting command. Its feed-event endpoint represents the
firmware's pending outbound ring, not durable history.

The State Agent's OTA routes are `/v1/version`, `/v1/update-status`, and
`POST /v1/update`. Keep frame parsing incremental, accept no caller-selected
paths or commands, and stage only below `/user/data/local-state-agent/update`.
The feeder-side runit supervisor, rather than the agent process, owns
stop/swap/start/probation and its single rolling rollback. Staging and
activation must hold the shared kernel lock at `update/transaction.lock`.
Every transaction-significant replacement must use same-directory temp write,
file fsync, rename, and directory fsync through the fixed-path
`plaf203-update-fs` helper; significant unlinks require directory fsync. Keep
the explicit `pending`, `activating`, `candidate_active`,
`probation_confirmed`, `rollback_in_progress`, and terminal phase semantics.
The feeder bootstrap requires `/usr/bin/flock`, `/usr/bin/nc`, `/usr/bin/sv`,
and both ARM binaries. See
[`feeder-state-agent/README.md`](feeder-state-agent/README.md) for the signed
release and one-time bootstrap procedure.

The manifest schema stays unchanged during trust-anchor rotation and carries no
replacement trust key. The next key remains compiled into the executable, not
supplied remotely or fetched from any online keyring. For a single-key A→B
rotation, the sequence is: deployed State Agent A trusts A; deployed AppDaemon
B trusts A; build candidate State Agent C with `release-public-key.hex=B`; sign
the transition manifest with private A plus `--rotate-trust-anchor`; existing
AppDaemon A verifies; existing State Agent A verifies; the candidate installs
and now trusts B; confirm probation succeeded and rollback is no longer
pending; update AppDaemon/package trust anchor to B; future releases are signed
by B normally. If a feeder misses the A→B hand-off before AppDaemon moves to B,
temporarily run an A-trusting AppDaemon/release workflow or use manual SSH
recovery before retrying.

`appdaemon/src/state_coordinator.py` is the only owner of persistent feeder
truth, revisions, pending writes, and plan collections. Do not reintroduce a
plan cache in `Backend`, read AppDaemon storage to build a command, or update
truth from an intended target. Plan writes must retain the fresh-core preflight
and complete-collection verification invariants. Add transition and failure
tests for any coordinator change; executor completion tests must invoke the
AppDaemon `callback(result=...)` form.

The controller runtime is intentionally split by responsibility:

| Module                            | Boundary                                                                                                              |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------- |
| `plaf203.py`                      | Thin AppDaemon lifecycle and dependency wiring                                                                        |
| `mqtt_client.py` / `protocol.py`  | MQTT transport and PLAF203 wire models                                                                                |
| `backend.py`                      | Required feeder protocol responses, low-level commands, acknowledgements, and telemetry callbacks; never feeder truth |
| `ha_entities.py`                  | MQTT discovery plus projection of verified truth into retained HA state                                               |
| `settings_map.py` / `commands.py` | Canonical value mappings and user-intent routing into coordinator requests                                            |
| `feed_plans.py`                   | Plan parsing, full-collection wire serialization, and HA display projection                                           |
| `telemetry.py`                    | Non-persistent operational state such as Wi-Fi, power, food, and SD-card status                                       |
| `storage.py`                      | Manual-feed preference and a stale diagnostic snapshot only                                                           |

Keep persistent-state changes on the path `commands -> state_coordinator ->
backend`. Acknowledgements return from `backend` to the coordinator, and only a
verified State Agent response reaches `HomeAssistantStatePublisher` as feeder
truth.

## Updating imported components

Edit and test the files in this repository directly. If a historical source
repository contains a useful later change, port the specific patch and retain
its license and attribution; do not recreate a synchronization relationship.

When changing protocol behavior:

1. keep raw captures outside Git;
2. reduce evidence to generic synthetic fixtures;
3. update focused tests in the owning component;
4. update the relevant component and top-level documentation;
5. run `./scripts/validate.sh` and the container build.

## Home Assistant packaging note

Current Home Assistant builders no longer consume `build.yaml`. The add-on uses
an explicit pinned Home Assistant base image in `petlibro-local/Dockerfile`, as
required by Supervisor 2026.04 and later.

Normal Home Assistant installations use the generic image configured in
`petlibro-local/config.yaml`:

```text
ghcr.io/tannerln7/ha-addon-petlibro-local
```

`.github/workflows/publish-addon-image.yml` runs on changes to `main` that
affect the add-on and can also be dispatched manually. It reads the architecture
and version from `config.yaml`, builds the validated `amd64` image with the
Home Assistant BuildKit actions, publishes versioned and `latest` per-arch
images, and publishes the generic manifest used by Supervisor. The workflow
uses `GITHUB_TOKEN`; no repository secret is required. After the package is
created for the first time, set both the generic and `amd64-` GHCR package
visibility to public so Home Assistant can pull the release anonymously.

For a Supervisor local build, copy the add-on directory into the local add-ons
location and comment out `image:` in that copy of `config.yaml`. Keep the image
field enabled in release commits. Home Assistant recommends prebuilt images
because local builds compile dependencies on the appliance; see the
[publishing guide](https://developers.home-assistant.io/docs/apps/publishing/).
