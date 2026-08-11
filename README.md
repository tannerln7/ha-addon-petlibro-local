# Petlibro Local Backend

Petlibro Local packages two working local-control components into one backend
appliance:

- a patched go2rtc build for the PLAF203 camera's LAN/TUTK protocol;
- the PLAF203 AppDaemon MQTT controller for feeder state and commands.

The repository is installable as a Home Assistant add-on repository and can
also run with Docker Compose in a Debian LXC or other Linux host. It is the
canonical source for both imported backend components going forward.

This is the backend only. It does not include a HACS frontend integration,
provision an MQTT broker or feeder account, or perform Petlibro cloud account
setup. A separate HACS integration may later provide a polished Home Assistant
frontend using the MQTT contract and go2rtc URLs exposed here.

## Support status

- Device: Petlibro PLAF203 / MQTT-based PLAF203S controller behavior
- Architecture: `amd64`
- Camera: H.264 SD/HD, optional AAC support in the imported go2rtc component
- Stage: experimental

The camera implementation has been validated with hybrid ACK behavior,
`send_delay_ctrl`, alternate 44-byte media headers, and delayed HD SPS
transitions on tested firmware. A session can remain at 640x360 beyond the
configured probe window before later producing 1920x1080 media.

## Home Assistant OS installation

Repository URL:

```text
https://github.com/tannerln7/ha-addon-petlibro-local
```

To install from a repository that Home Assistant can access:

1. Open **Settings → Apps → App store** in Home Assistant. Older releases label
   this area **Settings → Add-ons → Add-on Store**.
2. Open the three-dot menu, choose **Repositories**, and add the URL above.
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

The default broker hostname is `core-mosquitto`. A broker must already exist,
and it must authenticate both the backend identity configured here and the
physical feeder's separately provisioned identity. See the
[add-on option reference](petlibro-local/DOCS.md) for every setting.

The backend also publishes retained camera runtime state for frontend
integrations under `petlibro_local/<product>/<serial>/camera`. See the
[MQTT camera contract](docs/mqtt-camera-contract.md) for the versioned JSON
schema and availability behavior.

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
  petlibro_feeder: petlibro://192.168.1.100?uid=YOUR_DEVICE_UID&quality=hd&ack=hybrid&send_delay_ctrl=1&hd_probe_wait_ms=15000
```

Replace `YOUR_DEVICE_UID` with the exact 20-character value from the device.

## Docker / Debian LXC fallback

```bash
cd docker
cp .env.example .env
# Edit .env with local values.
docker compose up -d --build
docker compose logs -f
```

Docker Compose uses host networking and persists generated configuration and
debug dumps under `docker/data/`. See the
[Docker and Proxmox guide](docker/README.md) for prerequisites and operations.

## Security

- Keep MQTT credentials, feeder serials, camera UIDs, product secrets, and raw
  protocol dumps out of Git and issue reports.
- The feeder's product-secret-based MQTT credential belongs in the external
  broker's device account. This backend does not provision that account;
  `mqtt_username` and `mqtt_password` authenticate AppDaemon itself.
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
- [MQTT camera contract](docs/mqtt-camera-contract.md)
- [Troubleshooting](docs/troubleshooting.md)
- [Development](DEVELOPMENT.md)
- [Add-on options](petlibro-local/DOCS.md)
- [Docker / LXC deployment](docker/README.md)
- [Release history](petlibro-local/CHANGELOG.md)

The imported components retain their own documentation and licenses under
[`petlibro-local/go2rtc`](petlibro-local/go2rtc/) and
[`petlibro-local/appdaemon`](petlibro-local/appdaemon/).

## License

Repository packaging code and documentation use the [MIT License](LICENSE).
Imported go2rtc and PLAF203 sources retain the licenses included in their
respective component directories.
