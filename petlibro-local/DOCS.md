# Petlibro Local add-on options

The add-on generates go2rtc and AppDaemon configuration in `/data` each time a
service starts. Users should edit options through Home Assistant rather than
changing files inside the container.

## Required device values

| Option | Default | Description |
|---|---|---|
| `device_ip` | empty | PLAF203 feeder/camera LAN address |
| `serial` | empty | MQTT device serial (`DL_DEVICE_ID`) used in feeder topics |
| `uid` | empty | Exact 20-character camera UID used by the LAN/TUTK session |
| `product` | `PLAF203` | Supported product family; currently fixed to PLAF203 |

Startup fails with a configuration error until `device_ip`, `serial`, and
`uid` contain valid values.

## MQTT

| Option | Default | Description |
|---|---|---|
| `mqtt_host` | `core-mosquitto` | Broker hostname or address |
| `mqtt_port` | `1883` | Broker TCP port |
| `mqtt_username` | empty | AppDaemon's broker username |
| `mqtt_password` | empty | AppDaemon's broker password; stored in a mode-0600 secrets file |

These credentials authenticate the backend to the broker. They do not provision
the feeder or replace its factory-provisioned broker credentials.

## Camera and go2rtc

| Option | Default | Description |
|---|---|---|
| `go2rtc_stream_name` | `petlibro_feeder` | Name used in go2rtc and RTSP URLs |
| `camera_quality` | `hd` | Requested `hd` or `sd` stream |
| `ack_mode` | `hybrid` | Petlibro media-window ACK mapping: `high`, `contig`, or `hybrid` |
| `send_delay_ctrl` | `true` | Sends the AVAPI data-delay control before `IPCAM_START` |
| `hd_probe_wait_ms` | `15000` | Bounded wait for a higher-resolution SPS in HD mode; maximum 60000 |
| `go2rtc_api_port` | `1984` | Web interface and API TCP port |
| `go2rtc_rtsp_port` | `8554` | RTSP TCP port |
| `go2rtc_webrtc_port` | `8555` | WebRTC TCP/UDP port |

The tested HD stream may first emit a 640x360 SPS and switch to 1920x1080 after
several seconds. The default 15-second probe wait lets go2rtc advertise the
higher resolution when that transition occurs.

## Diagnostics

| Option | Default | Description |
|---|---|---|
| `verbose_logs` | `false` | Enables Petlibro debug logging, `verbose=1`, and AppDaemon debug level |
| `enable_debug_dumps` | `false` | Writes decrypted C2D/D2C protocol dumps under `/data` |

Debug dumps may contain device and session data. Disable the option and delete
the files after collecting the evidence needed for diagnosis.

## Reserved secret

`product_secret` is defined as a password field because it may be needed by a
future provisioning workflow. The imported, working camera and MQTT backends do
not consume it. The renderer therefore never writes it to generated files,
URLs, logs, or child-process environments.

## Network exposure

Host networking is required for reliable UDP camera discovery and WebRTC. The
generated go2rtc API and RTSP listeners do not use authentication. Restrict
ports 1984, 8554, and 8555 to trusted clients with network policy or a firewall.
