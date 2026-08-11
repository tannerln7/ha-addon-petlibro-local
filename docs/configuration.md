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
| `mqtt_client_id` | `MQTT_CLIENT_ID` | Unique AppDaemon broker client ID |
| `device_discovery` | `DEVICE_DISCOVERY` | MQTT identity discovery coordinator |
| `product_filter` | `PRODUCT_FILTER` | Accepted feeder topic product |
| `lan_cidr` | `LAN_CIDR` | LAN_SEARCH3 camera-address search network |
| `ip_resolve_timeout_seconds` | `IP_RESOLVE_TIMEOUT_SECONDS` | Per-search timeout |
| `ip_refresh_interval_minutes` | `IP_REFRESH_INTERVAL_MINUTES` | Healthy address refresh age |
| `ip_retry_backoff_seconds` | `IP_RETRY_BACKOFF_SECONDS` | Failed-search retry floor |
| `devices` | `DEVICES_JSON` | Optional manual device override array |
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
| `/data/apps.yaml` | Discovery coordinator and one PLAF203 app per device |
| `/data/appdaemon-secrets.yaml` | MQTT username and password only |
| `/data/devices.json` | Atomic mode-0600 device identity/address registry |
| `/data/appdaemon/` | AppDaemon app links and persistent namespace state |
| `/data/petlibro_camera_status_<stream>.json` | Per-stream atomic go2rtc camera runtime status; mode 0600 |
| `/data/petlibro_c2d_<stream>.dat` | Optional decrypted client-to-device dump |
| `/data/petlibro_d2c_<stream>.dat` | Optional decrypted device-to-client dump |

Generated YAML and secrets files are written with mode 0600. Do not copy them
into Git or attach them to public issues.

## Device discovery and overrides

The coordinator builds `serial -> UID -> IP -> stream` from feeder MQTT traffic
and LAN_SEARCH3. A valid device gets a direct-IP URL, so normal playback does
not repeat the slower subnet scan. It periodically refreshes cached addresses
and re-resolves after camera status becomes stale, offline, or error. A failed
lookup retains the last address, marks readiness false, and retries with
backoff. go2rtc restarts only when the rendered config bytes change.

For Docker, `DEVICES_JSON` is a JSON array. For Home Assistant, use the
structured `devices` list. Example advanced override:

```yaml
devices:
  - name: kitchen_feeder
    product: PLAF203
    serial: YOUR_DEVICE_SERIAL
    uid: YOUR_DEVICE_UID
    ip_address: 192.168.1.100
```

Omit the list for normal automatic setup.

The image includes the same resolver used by the coordinator:

```bash
petlibro-resolve \
  --uid YOUR_DEVICE_UID \
  --subnet 192.168.1.0/24 \
  --timeout 10s \
  --json
```

It emits one JSON result with `resolved`, `ip_address`, `method`, and elapsed
time fields and exits nonzero when resolution fails. The coordinator invokes it
without a shell and does not log the UID or raw result.

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
