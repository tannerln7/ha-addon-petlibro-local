# Architecture

Petlibro Local is a backend boundary. It packages local device protocols and
media transport but does not provide the future Home Assistant frontend
integration.

```mermaid
flowchart LR
    UI[Future HACS integration] -->|Stable MQTT contracts and commands| Broker[MQTT broker]
    UI -->|RTSP / WebRTC / API| Go2rtc[Patched go2rtc]

    subgraph Backend[Petlibro Local backend]
        Discovery[Discovery coordinator]
        Registry[Private device registry]
        Controller[PLAF203 AppDaemon controller]
        Go2rtc
        Status[Per-stream camera status JSON]
    end

    Broker -->|Topic serial and DEVICE_START_EVENT UID| Discovery
    Discovery -->|Atomic identity and IP mapping| Registry
    Discovery -->|LAN_SEARCH3 UID lookup| Feeder
    Registry -->|Direct-IP stream config| Go2rtc
    Go2rtc -->|Runtime state, SPS, and health| Status
    Status -->|Health and address refresh trigger| Discovery
    Status -->|Validated per-stream polling| Controller
    Controller <-->|Local PLAF203 MQTT protocol| Broker
    Controller -->|Retained camera state and availability| Broker
    Broker <-->|Redirected plaintext MQTT| Feeder[PLAF203 feeder]
    Go2rtc <-->|LAN UDP/TUTK camera protocol| Feeder
```

## Runtime components

### Patched go2rtc

The imported go2rtc source registers the `petlibro://` stream scheme. It handles
LAN discovery or a fixed feeder address, the camera handshake, stream control,
media-window ACKs, alternate media headers, H.264/AAC assembly, and RTSP/WebRTC
output.

### AppDaemon controller

The controller connects to the configured MQTT broker through AppDaemon's MQTT
plugin. It responds to the feeder's PLAF203 protocol, publishes Home Assistant
MQTT discovery entities, accepts commands through its own MQTT topics, and
publishes the stable camera runtime contract for frontend integrations.

### Discovery coordinator

The coordinator subscribes to `dl/+/+/device/#`, filters supported PLAF203
topics, derives the serial from the topic, and accepts a 20-character UID only
from `DEVICE_START_EVENT.uuid`. It calls the companion `petlibro-resolve`
binary, which reuses the Go Petlibro LAN_SEARCH3 implementation instead of
duplicating it in Python.

The mode-0600 `/data/devices.json` registry is the durable source for generated
per-device AppDaemon entries and direct-IP go2rtc URLs. Writes are atomic and
allowlisted; broker credentials and product secrets cannot enter the registry.
Repeated events are debounced. The coordinator restarts only go2rtc and only
when the rendered `go2rtc.yaml` bytes change.

### Camera metadata bridge

Each Petlibro go2rtc client atomically replaces
`/data/petlibro_camera_status_<stream>.json` when lifecycle state, SPS
resolution, or health counters change. Its matching AppDaemon app polls this
internal file, validates and
allowlists its fields, merges configured product/stream information, and
publishes retained MQTT state only after a semantic change or heartbeat.

The file is a private service boundary. The versioned
[MQTT camera contract](mqtt-camera-contract.md) is the public interface for the
future HACS integration. Keeping MQTT publication in AppDaemon avoids giving
go2rtc broker credentials or coupling a frontend to go2rtc internals.

The renderer removes a previous session's status file when go2rtc starts, so
AppDaemon publishes offline until the new producer writes current evidence.

### Runtime configuration

Home Assistant stores add-on options in `/data/options.json`. Docker Compose
provides equivalent environment variables. `render_config.py` validates either
source and writes service-specific configuration atomically under `/data`.
One PLAF203 app and one stream are generated per sufficiently discovered
device; unresolved devices remain visible in readiness MQTT without producing
an invalid stream.

The services are independent s6 processes. A crash in one service does not
terminate or supervise the other in application code; s6 handles restart and
container shutdown behavior.

## Network boundaries

Host networking is used so UDP broadcast/LAN camera discovery and WebRTC behave
consistently. The AppDaemon controller reaches the broker over TCP, while the
camera client connects directly to the feeder on UDP port 32761.

The go2rtc web/API and media listeners are exposed directly on the host network.
They should be restricted to trusted clients outside the container.

## Source ownership

Both component trees are ordinary source files in this repository. There are no
Git submodules, subtrees, or runtime build references to the historical source
repositories.
