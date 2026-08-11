# Petlibro Local backend

This experimental Home Assistant app/add-on runs two local PLAF203 services:

- patched go2rtc camera streaming over the feeder's LAN transport;
- an AppDaemon controller for the feeder's local MQTT protocol.

It does not provision an MQTT broker or the physical feeder's broker account.
The configured MQTT username and password authenticate this backend only.

## Before starting

Configure the MQTT broker, feeder LAN address, feeder serial, and exact
20-character camera UID on the **Configuration** tab. Then start the add-on and
open **Log** to confirm that both go2rtc and AppDaemon started successfully.

The go2rtc web interface and API are exposed on port `1984`:

```text
http://HOME_ASSISTANT_HOST:1984/
```

The default RTSP stream is:

```text
rtsp://HOME_ASSISTANT_HOST:8554/petlibro_feeder
```

Camera sessions start when a viewer opens a stream. See [DOCS.md](DOCS.md) for
all options, MQTT identity requirements, HD probe behavior, and security notes.
