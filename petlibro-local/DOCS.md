# Petlibro Local add-on options

The add-on generates go2rtc and AppDaemon configuration in `/data` each time a
service starts. Users should edit options through Home Assistant rather than
changing files inside the container.

## Discovery-first setup

| Option | Default | Description |
|---|---|---|
| `device_discovery` | `true` | Observe supported feeder MQTT topics and configure discovered devices |
| `product_filter` | `PLAF203` | Supported product family; currently fixed to PLAF203 |
| `lan_cidr` | `192.168.1.0/24` | IPv4 network searched by LAN_SEARCH3; maximum size `/16` |
| `ip_resolve_timeout_seconds` | `10` | Timeout for each UID-to-IP lookup |
| `ip_refresh_interval_minutes` | `360` | Maximum age of a healthy cached address |
| `ip_retry_backoff_seconds` | `60` | Minimum delay after a failed lookup |
| `devices` | `[]` | Optional advanced manual overrides |

Normal setup does not require a serial, UID, or IP address. Start the backend,
then reboot the feeder so the coordinator can observe `DEVICE_START_EVENT`.
The generated stream appears after serial, UID, and LAN address discovery.

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
| `verbose_logs` | `false` | Enables Petlibro debug logging, `verbose=1`, and AppDaemon debug level |
| `enable_debug_dumps` | `false` | Writes decrypted C2D/D2C protocol dumps under `/data` |

Debug dumps may contain device and session data. Disable the option and delete
the files after collecting the evidence needed for diagnosis.

## Network exposure

Host networking is required for reliable UDP camera discovery and WebRTC. The
generated go2rtc API and RTSP listeners do not use authentication. Restrict
ports 1984, 8554, and 8555 to trusted clients with network policy or a firewall.
