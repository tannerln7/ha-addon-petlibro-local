# Changelog

## 0.2.3

- Pull a prebuilt amd64 add-on image from GHCR for normal Home Assistant
  installs, while retaining an explicitly documented local-development build
  path.
- Fix automatic IP resolution with a bounded cached, broadcast, candidate, and
  single-sweep strategy using the feeder's complete LAN_SEARCH3/KNOCK2 probe
  sequence.
- Run resolver subprocesses through AppDaemon's executor so MQTT callbacks stay
  responsive, with structured timeout and helper-error handling.
- Add conventional `log_level` controls, secret redaction, and trace-only raw
  MQTT and per-packet diagnostics while preserving `verbose_logs` migration.
- Report per-leg discovery sends, LAN_SEARCH_R/KNOCK_RR2 receipts, UID and nonce
  rejections, aggregate traffic, send errors, and deadline state in resolver
  JSON and debug logs.

## 0.2.2

- Fix the first-heartbeat timestamp-drift error path so a controller callback
  cannot crash before camera UID discovery and go2rtc configuration complete.
- Prevent a newly initialized empty feeding-plan collection from being pushed
  to the feeder on first contact, while preserving explicit schedule updates
  and intentional schedule clearing.

## 0.2.1

- Fix automatic feeder discovery by registering the AppDaemon MQTT callback
  with a wildcard filter, allowing concrete `dl/PLAF203/<serial>/device/...`
  topics to reach the discovery coordinator.

## 0.2.0

- Make discovery-first setup the default so users no longer need to configure
  a feeder serial, camera UID, or device IP for normal installations.
- Discover feeder serials and camera UIDs from MQTT startup traffic.
- Resolve and refresh device IP addresses with the existing LAN_SEARCH3 Go
  implementation, then persist a private device registry.
- Generate direct-IP go2rtc streams and per-device controller/status mappings
  for multiple discovered feeders.
- Publish retained discovery readiness and restart go2rtc only when its
  generated configuration changes.
- Keep legacy single-device settings and explicit per-device overrides as
  compatibility fallbacks.

## 0.1.0

- Package patched go2rtc and the PLAF203 AppDaemon controller together.
- Generate runtime configuration from Home Assistant options or Docker Compose
  environment values.
- Publish a versioned, retained MQTT camera runtime contract through AppDaemon,
  backed by atomic go2rtc lifecycle, SPS-resolution, and health status exports.
- Fix container startup by relying on the Home Assistant base image's `/init`
  entrypoint instead of passing a second `/init` argument.
- Keep the feeder and AppDaemon MQTT credential roles separate; feeder broker
  account provisioning remains external to the add-on.
- Clarify that an HD SPS transition can occur after the configured probe window.
- Add amd64 Home Assistant and Debian LXC deployment paths.
- Add privacy-safe validation, troubleshooting, and debug collection tooling.
