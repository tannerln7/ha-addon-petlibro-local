# Petlibro Local backend

Petlibro Local packages two working local-control components into one backend
appliance:

- a patched go2rtc build for the PLAF203 camera's LAN/TUTK protocol;
- the PLAF203 AppDaemon MQTT controller for feeder state and commands.

The repository is installable as a Home Assistant add-on repository and can
also run with Docker Compose in a Debian LXC or other Linux host. It is the
canonical source for both imported backend components going forward.

This is the backend only. A separate HACS integration may later provide a
polished Home Assistant frontend and consume the MQTT entities and go2rtc URLs
exposed here.

## Support status

- Device: Petlibro PLAF203 / MQTT-based PLAF203S controller behavior
- Architecture: `amd64`
- Camera: H.264 SD/HD, optional AAC support in the imported go2rtc component
- Stage: experimental

The camera implementation has been validated with hybrid ACK behavior,
`send_delay_ctrl`, alternate 44-byte media headers, and a delayed HD probe that
reaches 1920x1080 on tested firmware.

## Home Assistant OS installation

When this repository is hosted on GitHub or another Git server:

1. Open **Settings → Apps → App store** in Home Assistant.
2. Open the repository menu and add this repository's URL.
3. Install **Petlibro Local backend**.
4. Configure the feeder and MQTT options before starting it.
5. Start the app and inspect its log for both the go2rtc and AppDaemon startup
   messages.

For local development before a remote exists, copy the
[`petlibro-local`](petlibro-local/) directory into
`/addons/petlibro-local` on Home Assistant OS, reload the app store, and install
it from **Local apps**.

Required device-specific options are:

- `device_ip`: feeder/camera LAN address, for example `192.168.1.100`
- `serial`: feeder MQTT device serial (`DL_DEVICE_ID`)
- `uid`: exact 20-character camera UID

The default broker hostname is `core-mosquitto`. See the
[add-on option reference](petlibro-local/DOCS.md) for every setting.

## Endpoints

With the default configuration and host networking:

| Service | URL or port |
|---|---|
| go2rtc web/API | `http://HOME_ASSISTANT_HOST:1984/` |
| RTSP | `rtsp://HOME_ASSISTANT_HOST:8554/petlibro_feeder` |
| WebRTC transport | TCP and UDP `8555` |

The generated camera source is equivalent to:

```yaml
streams:
  petlibro_feeder: petlibro://192.168.1.100?uid=PLAF20300000000ABCD0&quality=hd&ack=hybrid&send_delay_ctrl=1&hd_probe_wait_ms=15000
```

The UID above is synthetic. Replace it with the exact 20-character value from
the device.

## Docker / Debian LXC fallback

```bash
cp docker/.env.example docker/.env
# Edit docker/.env with local values.
./scripts/run-local.sh
./scripts/test-stream.sh
```

Docker Compose uses host networking and persists generated configuration and
debug dumps under `docker/data/`. See the
[Docker and Proxmox guide](docker/README.md) for prerequisites and operations.

## Security

- Keep `product_secret`, MQTT credentials, feeder serials, camera UIDs, and raw
  protocol dumps out of Git and issue reports.
- The current backends do not consume `product_secret`; it is retained as a
  password-type option for future provisioning work and is deliberately not
  rendered into generated files or child-process environments.
- go2rtc's web/API and RTSP endpoints have no authentication in the generated
  configuration. Host networking makes them reachable from the host network;
  use a trusted VLAN and firewall access appropriately.
- The feeder's MQTT transport is plaintext on tested firmware. Keep feeder and
  broker traffic on a trusted or isolated network.
- Debug dumps contain decrypted protocol traffic. Enable them only while
  diagnosing a problem and delete them afterward.

## Documentation

- [Architecture](docs/architecture.md)
- [Configuration](docs/configuration.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development](DEVELOPMENT.md)
- [Add-on options](petlibro-local/DOCS.md)
- [Docker / LXC deployment](docker/README.md)

The imported components retain their own documentation and licenses under
[`petlibro-local/go2rtc`](petlibro-local/go2rtc/) and
[`petlibro-local/appdaemon`](petlibro-local/appdaemon/).

## License

Repository packaging code and documentation use the [MIT License](LICENSE).
Imported go2rtc and PLAF203 sources retain the licenses included in their
respective component directories.
