# Repository instructions

This repository contains **Petlibro Local Backend**: A self-contained Home Assistant backend add-on repository combining the patched Petlibro go2rtc camera client and PLAF203 AppDaemon MQTT controller.

@DEVELOPMENT.md

## Component boundaries

- `petlibro-local/go2rtc`: imported Go camera backend and its tests
- `petlibro-local/appdaemon`: imported Python/AppDaemon controller and its tests
- `petlibro-local`: Home Assistant packaging, runtime configuration, and s6 services
- `docker`: Docker Compose deployment fallback
- `scripts` and `tests`: repository-level tooling and packaging tests

## Working rules

- Use the package manager, manifests, lockfiles, and commands documented above.
- Make changes within the owning component unless an interface change requires coordinated edits.
- Do not commit secrets, local environments, caches, generated build output, or runtime artifacts.
- Keep lockfiles synchronized through the owning package manager; do not hand-edit them.
- Add or update tests with behavior changes.
- Run `./scripts/validate.sh` before claiming completion.
- Update README and DEVELOPMENT.md when setup, commands, architecture, or behavior changes.
