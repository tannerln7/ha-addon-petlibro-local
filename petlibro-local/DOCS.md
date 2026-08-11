# Petlibro Local add-on options

The add-on generates go2rtc and AppDaemon configuration in `/data` each time a
service starts. Users should edit options through Home Assistant rather than
changing files inside the container.

## Installation and updates

The repository configuration points Supervisor at the prebuilt amd64 image:

```text
ghcr.io/tannerln7/ha-addon-petlibro-local:<add-on-version>
```

Normal installation and updates pull that image instead of compiling go2rtc
and installing AppDaemon dependencies on the Home Assistant machine. The GHCR
packages must be public so Supervisor can pull them without registry
credentials.

Local builds are intended only for development. In a copied local add-on,
comment out the `image:` field in `config.yaml` to make Supervisor build the
Dockerfile. Such builds can consume all available CPU temporarily, particularly
on Raspberry Pi-class and other low-resource installations.

## Discovery-first setup

| Option | Default | Description |
|---|---|---|
| `device_discovery` | `true` | Observe supported feeder MQTT topics and configure discovered devices |
| `product_filter` | `PLAF203` | Supported product family; currently fixed to PLAF203 |
| `lan_cidr` | `192.168.1.0/24` | IPv4 network searched by the UID-specific LAN probe; maximum size `/16` |
| `ip_resolve_timeout_seconds` | `15` | Timeout for each UID-to-IP lookup; covers one paced `/24` sweep at the default rate |
| `ip_discovery_broadcast_seconds` | `2` | Time reserved for low-impact broadcast discovery before fallback |
| `ip_discovery_max_unicast_per_second` | `32` | Maximum paced fallback probes per second |
| `ip_refresh_interval_minutes` | `360` | Maximum age of a healthy cached address |
| `ip_retry_backoff_seconds` | `60` | Minimum delay after a failed lookup |
| `devices` | `[]` | Optional advanced manual overrides |

Normal setup does not require a serial, UID, or IP address. Start the backend,
then reboot the feeder so the coordinator can observe `DEVICE_START_EVENT`.
The generated stream appears after serial, UID, and LAN address discovery.
The resolver checks the cached address first, starts its receiver before
sending broadcast probes, tries other known Petlibro addresses, and scans the
configured subnet at most once. Each target receives the firmware-required
`LAN_SEARCH3(w3=1)`, `LAN_SEARCH3(w3=2)`, `KNOCK2` sequence; a matching
`KNOCK_RR2` UID and nonce identifies the feeder. All stages share one absolute
deadline.

An override item may contain `name`, `product`, `serial`, `uid`, and
`ip_address`. `serial` identifies the record; supplied UID/IP fields win over
automatic values. Existing installs with the former top-level `serial`, `uid`,
and `device_ip` options are migrated into this registry when all three exist.

## MQTT

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | `core-mosquitto` | Broker hostname or address |
| `mqtt_port` | `1883` | Broker TCP port |
| `mqtt_username` | empty | AppDaemon's broker username |
| `mqtt_password` | empty | AppDaemon's broker password; stored in a mode-0600 secrets file |
| `mqtt_client_id` | `petlibro_local_backend` | Unique MQTT client ID for this backend instance |

These credentials authenticate the backend to the broker. They do not provision
the feeder or replace its factory-provisioned broker credentials. The feeder's
own username and product-secret-based password must be configured as a separate
account in the external broker.

## Camera and go2rtc

| Option | Default | Description |
|---|---|---|
| `go2rtc_stream_name` | `petlibro_feeder` | Compatibility name for a migrated legacy device; discovered devices derive a product/serial name |
| `camera_quality` | `hd` | Requested `hd` or `sd` stream |
| `ack_mode` | `hybrid` | Petlibro media-window ACK mapping: `high`, `contig`, or `hybrid` |
| `send_delay_ctrl` | `true` | Sends the AVAPI data-delay control before `IPCAM_START` |
| `hd_probe_wait_ms` | `15000` | Bounded wait for a higher-resolution SPS in HD mode; maximum 60000 |
| `go2rtc_api_port` | `1984` | Web interface and API TCP port |
| `go2rtc_rtsp_port` | `8554` | RTSP TCP port |
| `go2rtc_webrtc_port` | `8555` | WebRTC TCP/UDP port |

The tested HD stream may first emit a 640x360 SPS and switch to 1920x1080 later.
The default 15-second probe wait advertises the higher resolution only when that
transition occurs within the window. Some observed sessions transitioned after
several minutes and were initially advertised as 640x360.

## Camera metadata publishing

| Option | Default | Description |
|---|---|---|
| `publish_camera_metadata` | `true` | Publish retained camera state and availability for frontend integrations |
| `camera_metadata_topic_prefix` | empty | Optional prefix; empty derives `petlibro_local/<product>/<serial>/camera` |
| `camera_metadata_interval_seconds` | `30` | Retained heartbeat interval; allowed range 5–300 seconds |

The backend marks metadata stale after three heartbeat intervals. It publishes
state at `<prefix>/state` and availability at `<prefix>/availability`, both
retained with QoS 1. See the [versioned MQTT contract](../docs/mqtt-camera-contract.md)
for payload fields and offline behavior.

## Diagnostics

| Option | Default | Description |
|---|---|---|
| `log_level` | `info` | `critical`, `error`, `warning`, `info`, `debug`, or `trace` |
| `verbose_logs` | `false` | Deprecated compatibility key; `true` migrates normal `info` logging to `debug` |
| `enable_debug_dumps` | `false` | Writes decrypted C2D/D2C protocol dumps under `/data` |

`info` contains operational milestones. `debug` adds bounded resolver stats,
registry changes, camera metadata transitions, and five-second go2rtc summaries
without raw MQTT payloads. `trace` enables raw MQTT messages and the existing
targeted packet, ACK, fragment, and frame-info traces and can be extremely
noisy. AppDaemon itself remains at info level so its scheduler and state engine
do not overwhelm application diagnostics.

Debug dumps are independent of logging and may contain device and session
data. Disable the option and delete the files after collecting the evidence
needed for diagnosis.

## Network exposure

Host networking is required for reliable UDP camera discovery and WebRTC. The
generated go2rtc API and RTSP listeners do not use authentication. Restrict
ports 1984, 8554, and 8555 to trusted clients with network policy or a firewall.
