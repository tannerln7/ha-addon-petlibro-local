# Petlibro go2rtc

> This source snapshot is maintained as part of the canonical Petlibro Local
> backend repository under `petlibro-local/go2rtc`. Historical standalone forks
> are references only; make future Petlibro changes here.

This repository is a fork of [go2rtc](https://github.com/AlexxIT/go2rtc) with a
clean-room, LAN-only input for Petlibro camera feeders. It connects directly to
the camera over its Kalay/TUTK-family UDP protocol and exposes the resulting
H.264/AAC stream through go2rtc's RTSP, WebRTC, and HTTP outputs.

The Petlibro implementation has been tested with the PLAF203. PLAF103 protocol
captures informed the implementation, but that model has not been verified
end-to-end. This project is not affiliated with Petlibro or TUTK.

## Requirements

- A Petlibro camera that has completed initial provisioning in the Petlibro app
- The camera's 20-character UID, printed near its QR code
- Network access from the go2rtc host to the camera on UDP port `32761`
- [Go 1.24 or newer](https://go.dev/doc/install) for a source build
- FFmpeg or ffprobe only if you want command-line stream verification

The phone app is not required during normal LAN streaming. The camera may still
depend on its vendor cloud connection for firmware-side session authorization.

## Install from source

From the root of the Petlibro Local repository:

```bash
cd petlibro-local/go2rtc
go build -o go2rtc .
```

Confirm the binary starts:

```bash
./go2rtc -version
```

## Configure a camera

Copy the tracked example, then edit the copy with your camera's LAN address and
UID:

```bash
cp go2rtc.example.yaml go2rtc.yaml
```

`go2rtc.yaml` is intentionally ignored by Git so device identifiers and local
addresses do not get committed. A minimal fixed-address configuration is:

```yaml
streams:
  petlibro_feeder: petlibro://192.168.1.42?uid=PLAF20300000000ABCD0&quality=hd&ack=hybrid&send_delay_ctrl=1&hd_probe_wait_ms=15000
```

Replace both placeholder values. If the camera and go2rtc are on the same
broadcast domain, the camera can instead be discovered by UID:

```yaml
streams:
  petlibro_feeder: petlibro://?uid=PLAF20300000000ABCD0&quality=hd&ack=hybrid&send_delay_ctrl=1&hd_probe_wait_ms=15000
```

For a routed camera network, add one or more `subnet=` query parameters, such as
`subnet=192.168.1.0/24`. Container deployments normally need host networking for
UID discovery; a fixed camera IP avoids broadcast discovery.

See [go2rtc.example.yaml](go2rtc.example.yaml) for documented user options and
[the Petlibro module reference](internal/petlibro/README.md) for supported
behavior and limitations.

## Run

```bash
./go2rtc -config ./go2rtc.yaml
```

The default endpoints are:

| Service | Address |
| --- | --- |
| Web interface | <http://localhost:1984/> |
| RTSP stream | `rtsp://localhost:8554/petlibro_feeder` |
| WebRTC signaling/media | TCP and UDP port `8555` |

The stream name in the RTSP URL must match the key under `streams:`.

To verify the negotiated video codec and resolution:

```bash
ffprobe -v error -rtsp_transport tcp \
  -show_entries stream=codec_name,width,height \
  -of default=noprint_wrappers=1 \
  rtsp://127.0.0.1:8554/petlibro_feeder
```

An HD camera may initially send a 640x360 SPS before switching to 1920x1080.
The example's `hd_probe_wait_ms=15000` gives the producer a bounded window to
advertise the later HD SPS instead of the startup resolution.

## Local Docker build

This fork is not the same as the upstream `alexxit/go2rtc` container. Build the
image from this checkout so the Petlibro source is included:

```bash
docker build -t petlibro-go2rtc -f docker/Dockerfile .
docker run --rm --network host \
  -v "$(pwd)/go2rtc.yaml:/config/go2rtc.yaml:ro" \
  petlibro-go2rtc
```

Host networking is the simplest Linux setup for camera discovery and go2rtc's
UDP services. If you use an isolated container network, configure a fixed camera
IP and explicitly publish the go2rtc ports you need.

## Troubleshooting and development

- [Petlibro debugging guide](docs/PETLIBRO_DEBUGGING.md) — health counters,
  trace controls, plaintext captures, replay, and common failure modes
- [Development guide](docs/DEVELOPMENT.md) — package architecture, protocol
  invariants, tests, build checks, and live-test workflow
- [Contributing](CONTRIBUTING.md) — useful issue evidence, privacy rules, and
  change-submission checklist
- [Upstream go2rtc documentation](https://github.com/AlexxIT/go2rtc#readme) —
  general output protocols, APIs, transcoding, and integrations

## License

This project retains go2rtc's [MIT license](LICENSE). See the upstream project
for the original implementation and broader go2rtc documentation.
