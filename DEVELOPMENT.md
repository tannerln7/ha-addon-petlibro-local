# Development

This repository is the canonical workspace for the Petlibro backend package.
Make future camera changes under `petlibro-local/go2rtc` and controller changes
under `petlibro-local/appdaemon`; the original standalone repositories are
historical snapshots only.

## Components

| Path | Responsibility |
|---|---|
| `petlibro-local/go2rtc/` | Patched go2rtc source and Petlibro camera protocol |
| `petlibro-local/appdaemon/` | PLAF203 AppDaemon MQTT controller |
| `petlibro-local/` | Home Assistant add-on metadata, image, templates, and services |
| `docker/` | Host-network Docker Compose fallback |
| `scripts/` | Build, run, stream-test, log collection, and validation helpers |
| `tests/` | Backend packaging and configuration-rendering tests |

No submodule, subtree, or synchronization process links these sources to their
former repositories.

## Prerequisites

- Go 1.24 or newer for the imported go2rtc module
- Python 3.12 or newer
- Docker with Compose for image validation and local runtime testing
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
AppDaemon protocol tests when its dependency is installed, the Petlibro Go
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
