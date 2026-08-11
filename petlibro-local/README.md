# Petlibro Local backend

This experimental Home Assistant app/add-on runs two local PLAF203 services:

- patched go2rtc camera streaming over the feeder's LAN transport;
- an AppDaemon controller for the feeder's local MQTT protocol.

It does not provision an MQTT broker or the physical feeder's broker account.
The configured MQTT username and password authenticate this backend only.

## Before starting

Configure the MQTT broker and feeder LAN subnet on the **Configuration** tab.
Start the add-on, then reboot or power-cycle the feeder. The backend discovers
the serial from MQTT, the camera UID from `DEVICE_START_EVENT`, and the current
IP with LAN_SEARCH3. Open **Log** to follow discovery and stream setup.

The go2rtc web interface and API are exposed on port `1984`:

```text
http://HOME_ASSISTANT_HOST:1984/
```

Each feeder gets an RTSP stream derived from its product and serial:

```text
rtsp://HOME_ASSISTANT_HOST:8554/petlibro_plaf203_<serial>
```

Camera sessions start when a viewer opens a stream. See [DOCS.md](DOCS.md) for
all options, discovery readiness topics, manual override behavior, HD probe
behavior, and security notes.
