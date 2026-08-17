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
- The `autoChangeMode` / `autoThreshold` product behavior and recorded SD-card
  video format are not fully understood. Their Home Assistant labels avoid the
  earlier unsupported "button lock" claim.
- The state API exposes the feeding-audio URL, but writing it remains blocked:
  tested firmware can restart when the destination is unreachable.

## Prerequisites

- A PLAF203S feeder already joined to your local Wi-Fi network
- Home Assistant with its MQTT integration enabled
- AppDaemon 4 with the MQTT plugin configured under the `mqtt` namespace
- A local MQTT broker reachable by both AppDaemon and the feeder
- The read-only Petlibro state agent running on the feeder
- Local DNS or equivalent network routing that directs the feeder's Petlibro
  MQTT hostname to the local broker

The feeder uses factory-provisioned MQTT credentials. Keep it on a trusted or
isolated network, do not publish those credentials, and do not commit packet
captures or AppDaemon configuration containing a real device serial.

Use the matching State Agent 0.3.0 source under
[`../../feeder-state-agent`](../../feeder-state-agent/). Older agent schemas do
not expose the persistent/effective/runtime distinction needed for safe write
verification.

## Installation

1. Copy every Python module from [`src/`](src/) into the same AppDaemon `apps`
   directory. `plaf203.py` imports its sibling protocol, coordinator, command,
   state, and telemetry modules at runtime.
2. Copy the `plaf203` block from
   [`src/apps.example.yaml`](src/apps.example.yaml) into your AppDaemon
   `apps.yaml`.
3. Set `serial_number` to the feeder's `DL_DEVICE_ID`. Configure AppDaemon's
   MQTT plugin separately with the broker connection used by the controller.
4. Add `petlibro_state_agent_token` to AppDaemon's `secrets.yaml` and configure
   the feeder state-agent URL shown below.
5. Leave `persist_feeder_mqtt` disabled unless intentionally migrating the
   physical feeder to a durable LAN broker address.
6. Reload AppDaemon and inspect its log for `Initializing plaf203`.
7. Power on the feeder and confirm that `dl/PLAF203/<serial>/device/...` topics
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
| `petlibro_state_agent_url` | yes | — | Read-only feeder truth API; packaged installs derive it from discovered IP |
| `petlibro_state_agent_token` | yes | — | Bearer token loaded from AppDaemon secrets |
| `petlibro_state_agent_timeout_seconds` | no | `2` | Local API request timeout |
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
  petlibro_state_agent_url: 'http://192.0.2.100:8765'
  petlibro_state_agent_token: !secret petlibro_state_agent_token
  petlibro_state_agent_timeout_seconds: 2
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

Home Assistant exposes nine **Feeding schedule** slots as JSON text entities. This example
runs every day at 19:00 and dispenses three portions:

```json
{"id":1,"execution_time":{"hour":19,"minute":0},"scheduled_days":["MONDAY","TUESDAY","WEDNESDAY","THURSDAY","FRIDAY","SATURDAY","SUNDAY"],"grain_num":3}
```

The JSON `id` must match the Home Assistant slot number: Feeding schedule 1 uses
`"id":1`, Feeding schedule 2 uses `"id":2`, and so on through Feeding schedule 9. A
slot/ID mismatch is rejected without changing the feeder schedule. The plan ID
must already exist in the feeder state API; add/delete is not currently
supported.

JSON numbers must not contain leading zeroes: use `{"hour":7,"minute":1}`
for 07:01 and `{"hour":7,"minute":0}` for 07:00. Invalid JSON is rejected
without changing the stored or device schedule; the warning reports the error
location and payload length without logging the schedule itself.

On startup, the controller reads `/v1/core` and publishes the feeder's local
plans to Home Assistant without writeback. For every edit it reads a fresh
collection, changes only time/days/portions, carries the API's audio fields and
`skip_end_time` through the MQTT schema, and retains the ten-byte opaque tail
for collateral-mutation verification. The edited plan receives a fresh
`syncTime`; runtime `execution_state` and sync metadata are excluded from
schedule equality. It then sends the full collection and verifies the stable
result through `/v1/core` after the MQTT ack. Stored
or retained Home Assistant plan JSON is never a command source. If the API is
unavailable or verification disagrees, feeder-local state wins.

## Bowl configuration and portions

Home Assistant exposes **Bowl setup** as a non-optimistic select with friendly
**Single bowl** and **Dual bowl** options. MQTT and the controller continue to
use the protocol values `SINGLE_BOWL` and `DOUBLE_BOWL`; discovery templates
translate only what Home Assistant displays and sends from its UI. Changing the
select sends only the feeder's `bowlMode` attribute, and the displayed state
changes when the feeder reports the new value. The observed firmware reports
`SINGLE_BOWL`. `DOUBLE_BOWL` is the inferred matching wire value and should be
confirmed on a dual-tray feeder.

The controller does not multiply or divide `grain_num`. A scheduled or manual
feed quantity remains the total amount requested from the feeder. PETLIBRO's
[PLAF203 serving guidance](https://designlibro.zendesk.com/hc/en-us/articles/44154385755545-Pre-sale-inquiries-about-the-Granary-Camera-feeder-AF203-PLAF203)
says the dual-bowl attachment divides that output between the two bowls; the
configured quantity is not an amount per bowl.

## Camera resolution state

The Home Assistant **Feeder camera resolution** select reflects the PLAF203
`resolution` attribute as **720p** or **1080p**. The MQTT values remain `P720`
and `P1080`. The feeder may report P1080 while an HD TUTK session is active and
return to P720 when that session ends. This is separate
from the add-on's requested `camera_quality` and the SPS-derived
`actual_resolution` in camera runtime metadata. Sparse device events update
only the fields present in the event; unrelated state is not synthesized or
republished.

## Home Assistant labels

MQTT discovery uses human-readable names and select values without changing the
backend contract. For example, protocol schedule values
`NON_SCHEDULED_ENABLED` and `SCHEDULED_ENABLED` appear as **Always active** and
**Scheduled**, night vision appears as **Automatic**, **On**, or **Off**, and
feeding quantities are labeled as portions. Entity unique IDs, state topics,
command topics, and raw payload values remain unchanged so existing automations
and MQTT consumers continue to work.

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
