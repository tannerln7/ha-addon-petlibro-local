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
- `/data/petlibro_camera_status.json` (created by an active go2rtc camera session)

The AppDaemon secrets file and all generated configuration files are mode 0600.
The backend does not accept the feeder product secret. Feeder broker account
provisioning is external to this package.

The camera status file is an internal, atomic service boundary. go2rtc owns
runtime observations and counters; AppDaemon validates and publishes the stable
MQTT contract documented in `docs/mqtt-camera-contract.md`. Frontend code must
not depend on the internal file schema.

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
