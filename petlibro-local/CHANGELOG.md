# Changelog

## Unreleased

- Publish a versioned, retained MQTT camera runtime contract through AppDaemon,
  backed by atomic go2rtc lifecycle, SPS-resolution, and health status exports.
- Fix container startup by relying on the Home Assistant base image's `/init`
  entrypoint instead of passing a second `/init` argument.
- Remove the unused product-secret option and document the separate feeder and
  AppDaemon MQTT credential roles.
- Clarify that an HD SPS transition can occur after the configured probe window.

## 0.1.0

- Package patched go2rtc and the PLAF203 AppDaemon controller together.
- Generate runtime configuration from Home Assistant options or Docker Compose
  environment values.
- Add amd64 Home Assistant and Debian LXC deployment paths.
- Add privacy-safe validation, troubleshooting, and debug collection tooling.
