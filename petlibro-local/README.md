# Petlibro Local backend

This experimental Home Assistant app/add-on runs two local PLAF203 services:

- patched go2rtc camera streaming over the feeder's LAN transport;
- an AppDaemon controller for the feeder's local MQTT protocol.

It does not provision an MQTT broker or the physical feeder's broker account.
The configured MQTT username and password authenticate this backend only.

Normal Home Assistant installations pull the prebuilt amd64 image from
`ghcr.io/tannerln7/ha-addon-petlibro-local`. They do not compile the patched
go2rtc source or install Python dependencies on the Home Assistant host. Local
Supervisor builds are development-only: comment out `image:` in `config.yaml`
to force one, and expect high CPU use while it runs on a low-resource system.

## Before starting

Configure the MQTT broker and feeder LAN subnet on the **Configuration** tab.
Start the add-on, then reboot or power-cycle the feeder. The backend discovers
the serial from MQTT, the camera UID from `DEVICE_START_EVENT`, and the current
IP with the UID-specific LAN_SEARCH3/KNOCK2 exchange. Open **Log** to follow
discovery and stream setup.
Discovery checks cached and broadcast targets first and uses one paced subnet
fallback only when necessary, so an unavailable feeder cannot hold an
AppDaemon callback open.

The go2rtc web interface and API are exposed on port `1984`:

```text
http://HOME_ASSISTANT_HOST:1984/
```

Each feeder gets an RTSP stream derived from its product and serial:

```text
rtsp://HOME_ASSISTANT_HOST:8554/petlibro_plaf203_<serial>
```

Camera sessions start when a viewer opens a stream. See [DOCS.md](DOCS.md) for
all options, discovery readiness topics, log levels, manual override behavior,
HD probe behavior, and security notes. Keep `log_level: info` for normal use,
select `debug` for bounded diagnostics, and reserve noisy `trace` for short
captures.
