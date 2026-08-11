# Local Petlibro PLAF203S integration

> This controller is maintained as part of the canonical Petlibro Local backend
> repository under `petlibro-local/appdaemon`. Historical standalone forks are
> references only; make future controller changes here.

An experimental [AppDaemon](https://appdaemon.readthedocs.io/) application for
controlling a Petlibro PLAF203S feeder locally through MQTT and exposing its
controls and state to Home Assistant MQTT discovery.

This project is based on reverse engineering and is not affiliated with or
supported by Petlibro. Test changes carefully: firmware variants may use
different message shapes, and some device actions are destructive.

## Project status

The integration supports the MQTT-based PLAF203S hardware. It was originally
developed against hardware `1.0.7` / firmware `3.0.14` and now includes protocol
compatibility verified against sanitized firmware `3.1.48` fixtures.

Implemented functionality includes:

- Home Assistant MQTT discovery and retained state
- device lifecycle, heartbeat, NTP, and configuration synchronization
- manual feeding and feeding plans
- feeder, audio, light, camera-setting, recording, and detection controls
- SD card, power, food, network, and diagnostic state
- reboot, Wi-Fi reconnect, factory reset, and SD card format commands

Known limitations:

- This remains a prototype and has not been tested across all hardware or
  firmware revisions.
- Scheduled aging modes, OTA updates, Wi-Fi provisioning, and models other than
  PLAF203S are not supported.
- Camera/audio streaming is outside this AppDaemon integration. Local streaming
  is provided by the [bundled Petlibro go2rtc backend](../go2rtc/).
- An unreachable feeding-audio URL can cause some feeder firmware to restart.
- The button auto-lock controls and recorded SD-card video format are not fully
  understood.

## Prerequisites

- A PLAF203S feeder already joined to your local Wi-Fi network
- Home Assistant with its MQTT integration enabled
- AppDaemon 4 with the MQTT plugin configured under the `mqtt` namespace
- A local MQTT broker reachable by both AppDaemon and the feeder
- Local DNS or equivalent network routing that directs the feeder's Petlibro
  MQTT hostname to the local broker

The feeder uses factory-provisioned MQTT credentials. Keep it on a trusted or
isolated network, do not publish those credentials, and do not commit packet
captures or AppDaemon configuration containing a real device serial.

## Installation

1. Copy [`src/plaf203.py`](src/plaf203.py) into your AppDaemon `apps` directory.
2. Copy the `plaf203` block from
   [`src/apps.example.yaml`](src/apps.example.yaml) into your AppDaemon
   `apps.yaml`.
3. Set `serial_number` to the feeder's `DL_DEVICE_ID` and set `mqtt_host` and
   `mqtt_port` to the local broker.
4. If needed for firmware 3.1.48 or later, set `https_addr` and
   `tutk_p2p_region` to the values appropriate for your environment.
5. Reload AppDaemon and inspect its log for `Initializing plaf203`.
6. Power on the feeder and confirm that `dl/PLAF203/<serial>/device/...` topics
   appear on the broker. Home Assistant should then discover a Petlibro feeder
   through MQTT.

Keep a working copy as `src/apps.yaml` when developing from this repository.
That file is ignored intentionally; only the placeholder example is tracked.

## Configuration

| Option | Required | Default | Description |
|---|---:|---|---|
| `serial_number` | yes | — | Feeder `DL_DEVICE_ID`; also used in MQTT topics and Home Assistant entity IDs |
| `mqtt_host` | yes | — | Broker hostname or address advertised to the feeder |
| `mqtt_port` | yes | — | Broker TCP port, normally `1883` |
| `https_addr` | no | `mqtt_host` | API endpoint sent by `DEVICE_CONFIG_SYNC` on newer firmware |
| `tutk_p2p_region` | no | `REGION_US` | TUTK region sent during device configuration sync |

Example:

```yaml
plaf203:
  module: plaf203
  class: Plaf203
  serial_number: '00000000000000000'
  mqtt_host: '127.0.0.1'
  mqtt_port: 1883
  https_addr: '127.0.0.1'
  tutk_p2p_region: 'REGION_US'
```

## Feeding plans

Home Assistant exposes feeding-plan slots as JSON text entities. This example
runs every day at 19:00, disables feeding audio, and dispenses three portions:

```json
{"id":1,"execution_time":{"hour":19,"minute":0},"scheduled_days":["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],"enable_audio":false,"play_audio_times":1,"grain_num":3}
```

Use a distinct `id` for each configured plan.

On startup, the controller synchronizes a feeding plan only when its persistent
state contains at least one configured plan. A newly initialized empty state is
not sent to the feeder, so first contact cannot clear an existing device
schedule. An explicit schedule update remains authoritative; explicitly setting
an empty collection clears the feeder schedule.

## Network notes

Observed US firmware attempts plaintext MQTT on port `1883` using these hostnames:

- `mqtt.us.petlibro.com`
- `us-mqtt-0.aiotlibro.com`
- `us-mqtt-0.dl-aiot.com`

Redirect the hostname used by your feeder to the local broker and block unwanted
internet access according to your network policy. Because the transport is not
encrypted, avoid routing it over an untrusted network.

MQTT topics follow this shape:

```text
dl/PLAF203/<device-serial>/device/<channel>/<post-or-sub>
```

`post` is feeder-to-server traffic; `sub` is server-to-feeder traffic.

## Documentation

- [Firmware 3.1.48 protocol analysis](docs/protocol-capture-analysis.md) documents
  message schemas, topic routing, acknowledgement behavior, and capture-backed
  implementation findings.
- [Development guide](docs/DEVELOPMENT.md) covers the repository layout, tests,
  safe fixture handling, and validation workflow.

## Project origin

This repository is a fork of
[icex2/plaf203](https://github.com/icex2/plaf203) and retains the original
project's reverse-engineering foundation and license.

## License

This project is released under the [Unlicense](LICENSE).
