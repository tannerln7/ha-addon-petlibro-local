# Configuration reference

Home Assistant users configure the backend in the add-on options UI. Docker
users configure the same values in the ignored `docker/.env` file.

## Option mapping

| Home Assistant option | Docker variable | Consumer |
|---|---|---|
| `mqtt_host` | `MQTT_HOST` | AppDaemon MQTT plugin and PLAF203 config sync |
| `mqtt_port` | `MQTT_PORT` | AppDaemon MQTT plugin and PLAF203 config sync |
| `mqtt_username` | `MQTT_USERNAME` | AppDaemon MQTT plugin |
| `mqtt_password` | `MQTT_PASSWORD` | AppDaemon MQTT plugin secrets file |
| `device_ip` | `DEVICE_IP` | go2rtc Petlibro URL host |
| `product` | `PRODUCT` | Validation; currently PLAF203 only |
| `serial` | `SERIAL` | PLAF203 MQTT topics and Home Assistant entity IDs |
| `uid` | `UID` | go2rtc Petlibro camera session |
| `go2rtc_stream_name` | `GO2RTC_STREAM_NAME` | go2rtc stream and RTSP URL |
| `camera_quality` | `CAMERA_QUALITY` | Petlibro URL `quality` query |
| `ack_mode` | `ACK_MODE` | Petlibro URL `ack` query |
| `send_delay_ctrl` | `SEND_DELAY_CTRL` | Petlibro URL data-delay option |
| `hd_probe_wait_ms` | `HD_PROBE_WAIT_MS` | Petlibro HD SPS stabilization |
| `go2rtc_api_port` | `GO2RTC_API_PORT` | go2rtc API listener |
| `go2rtc_rtsp_port` | `GO2RTC_RTSP_PORT` | go2rtc RTSP listener |
| `go2rtc_webrtc_port` | `GO2RTC_WEBRTC_PORT` | go2rtc WebRTC listener |
| `publish_camera_metadata` | `PUBLISH_CAMERA_METADATA` | go2rtc status export and AppDaemon MQTT publisher |
| `camera_metadata_topic_prefix` | `CAMERA_METADATA_TOPIC_PREFIX` | Optional MQTT camera topic override |
| `camera_metadata_interval_seconds` | `CAMERA_METADATA_INTERVAL_SECONDS` | Retained state/availability heartbeat and staleness basis |
| `verbose_logs` | `VERBOSE_LOGS` | Petlibro and AppDaemon diagnostics |
| `enable_debug_dumps` | `ENABLE_DEBUG_DUMPS` | Decrypted protocol dump output |

See [the add-on option guide](../petlibro-local/DOCS.md) for defaults and
behavior.

## Generated files

| Path | Contents |
|---|---|
| `/data/go2rtc.yaml` | Listener configuration and generated Petlibro stream URL |
| `/data/appdaemon.yaml` | AppDaemon core, namespace, and MQTT plugin configuration |
| `/data/apps.yaml` | PLAF203 app configuration |
| `/data/appdaemon-secrets.yaml` | MQTT username and password only |
| `/data/appdaemon/` | AppDaemon app links and persistent namespace state |
| `/data/petlibro_camera_status.json` | Internal atomic go2rtc camera runtime status; mode 0600 |
| `/data/petlibro_c2d.dat` | Optional decrypted client-to-device dump |
| `/data/petlibro_d2c.dat` | Optional decrypted device-to-client dump |

Generated YAML and secrets files are written with mode 0600. Do not copy them
into Git or attach them to public issues.

## Camera metadata topics

Metadata publishing is enabled by default. When
`camera_metadata_topic_prefix` is empty, the renderer derives:

```text
petlibro_local/<product>/<serial>/camera
```

The default 30-second heartbeat refreshes retained state and availability.
AppDaemon considers the go2rtc status stale after three heartbeat intervals.
The supported interval range is 5–300 seconds. A custom prefix may contain only
letters, numbers, underscores, hyphens, and slash-separated path segments; MQTT
wildcards are rejected.

See the [MQTT camera contract](mqtt-camera-contract.md) before implementing a
consumer. The generated status file is internal and should not be parsed by a
frontend integration.

## Feeder connectivity

This backend does not onboard the feeder, rewrite DNS, or configure the feeder's
factory MQTT account. The feeder must already be connected to Wi-Fi, its MQTT
hostname must resolve or route to the local broker, and its own username and
product-secret-based password must be provisioned in that broker. Do not enter
the feeder credential as the backend's MQTT password.

The configured `mqtt_username` and `mqtt_password` authenticate AppDaemon. That
account must be authorized to subscribe and publish on both the feeder's
`dl/PLAF203/...` topics and the controller's Home Assistant discovery/command
topics.
