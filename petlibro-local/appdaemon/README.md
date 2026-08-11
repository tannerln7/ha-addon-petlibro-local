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
3. Set `serial_number` to the feeder's `DL_DEVICE_ID`. Configure AppDaemon's
   MQTT plugin separately with the broker connection used by the controller.
4. Leave `persist_feeder_mqtt` disabled unless intentionally migrating the
   physical feeder to a durable LAN broker address.
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
| `device_uid` | no | empty | Previously discovered camera UID used to seed retained controller identity |
| `persist_feeder_mqtt` | no | `false` | Explicitly allow a validated feeder endpoint update |
| `feeder_mqtt_host` | when enabled | empty | Stable LAN IP or multi-label DNS name advertised to the feeder |
| `feeder_mqtt_port` | no | `1883` | Broker TCP port advertised to the feeder |
| `feeder_https_addr` | no | empty | Optional API endpoint included in the explicit update |
| `tutk_p2p_region` | no | `REGION_US` | TUTK region sent during device configuration sync |
| `petlibro_log_level` | no | `info` | Petlibro application threshold: `critical`, `error`, `warning`, `info`, `debug`, or `trace` |

Use `petlibro_log_level`, not AppDaemon's reserved `log_level` setting. The
Petlibro logger accepts lowercase values and adds the non-standard `trace`
threshold without changing AppDaemon's own logger configuration.

Example:

```yaml
plaf203:
  module: plaf203
  class: Plaf203
  serial_number: '00000000000000000'
  device_uid: ''
  persist_feeder_mqtt: false
  feeder_mqtt_host: ''
  feeder_mqtt_port: 1883
  feeder_https_addr: ''
  tutk_p2p_region: 'REGION_US'
  petlibro_log_level: 'info'
```

With persistence disabled, the controller acknowledges feeder startup but does
not send `DEVICE_CONFIG_SYNC`, so it cannot replace stored MQTT or HTTPS
values. When enabled, it rejects single-label/container-only names, unsafe or
Home Assistant internal addresses, DNS failures, and an unreachable MQTT port
before sending. An empty `feeder_https_addr` is omitted rather than derived
from another setting. The tested startup messages do not expose the feeder's
current endpoint values, so the safe preservation strategy is to avoid an
update when they cannot be copied. A persistence acknowledgement is reported
only when the feeder returns `code: 0` with the same `msgId` as the outstanding
`DEVICE_CONFIG_SYNC` request.

Heartbeat count resets and watchdog timeouts produce idempotent offline/online
transitions. Initial clock drift starts one NTP correction request; repeated
drift observations are deduplicated while it is pending, and failure is
reported only when the acknowledgement remains outside tolerance or the
correction times out.

## Feeding plans

Home Assistant exposes feeding-plan slots as JSON text entities. This example
runs every day at 19:00, disables feeding audio, and dispenses three portions:

```json
{"id":1,"execution_time":{"hour":19,"minute":0},"scheduled_days":["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],"enable_audio":false,"play_audio_times":1,"grain_num":3}
```

Use a distinct `id` for each configured plan.
JSON numbers must not contain leading zeroes: use `{"hour":7,"minute":1}`
for 07:01 and `{"hour":7,"minute":0}` for 07:00. Invalid JSON is rejected
without changing the stored or device schedule; the warning reports the error
location and payload length without logging the schedule itself.

On startup, the controller synchronizes a feeding plan only when its persistent
state contains at least one configured plan. A newly initialized empty state is
not sent to the feeder, so first contact cannot clear an existing device
schedule. An explicit schedule update remains authoritative; explicitly setting
an empty collection clears the feeder schedule. AppDaemon stores this state in
the persistent `plaf203` namespace, so configured plans and the manual-feed
portion default survive add-on restarts.

## Bowl configuration and portions

Home Assistant exposes **Bowl configuration** as a non-optimistic select with
`SINGLE_BOWL` and `DOUBLE_BOWL` options. Changing it sends only the feeder's
`bowlMode` attribute; the displayed state changes when the feeder reports the
new value. The observed firmware reports `SINGLE_BOWL`. `DOUBLE_BOWL` is the
inferred matching wire value and should be confirmed on a dual-tray feeder.

The controller does not multiply or divide `grain_num`. A scheduled or manual
feed quantity remains the total amount requested from the feeder. PETLIBRO's
[PLAF203 serving guidance](https://designlibro.zendesk.com/hc/en-us/articles/44154385755545-Pre-sale-inquiries-about-the-Granary-Camera-feeder-AF203-PLAF203)
says the dual-bowl attachment divides that output between the two bowls; the
configured quantity is not an amount per bowl.

## Camera resolution state

The Home Assistant **Feeder-reported camera resolution** select reflects the
PLAF203 `resolution` attribute. The feeder may report P1080 while an HD TUTK
session is active and return to P720 when that session ends. This is separate
from the add-on's requested `camera_quality` and the SPS-derived
`actual_resolution` in camera runtime metadata. Sparse device events update
only the fields present in the event; unrelated state is not synthesized or
republished.

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
