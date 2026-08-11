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
4. Configure the MQTT broker connection and the feeder LAN subnet.
5. Start the app, then reboot or power-cycle the feeder so its startup event is
   visible to the discovery coordinator.
6. Watch the app log until MQTT identity discovery, LAN address resolution, and
   stream configuration complete.

For local development before a remote exists, copy the
[`petlibro-local`](petlibro-local/) directory into
`/addons/petlibro-local` on Home Assistant OS, reload the app store, and install
it from **Local apps**.

Serial, camera UID, and device IP are normally automatic. The backend reads the
serial from `dl/PLAF203/<serial>/device/...`, reads the UID from the feeder's
`DEVICE_START_EVENT`, and resolves the current address with the camera's
LAN_SEARCH3 protocol over `lan_cidr`. It persists this mapping in a private
registry and creates one direct-IP go2rtc stream per discovered feeder. The
optional `devices` list is an advanced fallback for manual overrides.

The default broker hostname is `core-mosquitto`. A broker must already exist,
and it must authenticate both the backend identity configured here and the
physical feeder's separately provisioned identity. See the
[add-on option reference](petlibro-local/DOCS.md) for every setting.

The complete first-run sequence is:

1. Install and configure the MQTT broker, including the physical feeder's own
   account.
2. Add the local DNS redirects or routing needed for the feeder to reach that
   broker.
3. Install, configure, and start Petlibro Local Backend.
4. Reboot or power-cycle the feeder after the backend is subscribed.
5. The backend discovers the serial from the MQTT topic and the UID from the
   startup event.
6. It resolves the IP over `lan_cidr`, saves `/data/devices.json`, and generates
   the direct-IP go2rtc stream.
7. Open the generated stream to start the lazy camera producer.

If the feeder was rebooted before the backend started, reboot it once more; the
UID startup event is not assumed to be retained by the broker.

The backend publishes retained discovery progress under
`petlibro_local/<product>/<serial>/discovery/state` and retained camera runtime
state under `petlibro_local/<product>/<serial>/camera`. See the
[MQTT camera contract](docs/mqtt-camera-contract.md) for the versioned JSON
schema and availability behavior.

## Endpoints

With the default configuration and host networking:

| Service | URL or port |
|---|---|
| go2rtc web/API | `http://HOME_ASSISTANT_HOST:1984/` |
| RTSP | `rtsp://HOME_ASSISTANT_HOST:8554/petlibro_plaf203_<serial>` |
| WebRTC transport | TCP and UDP `8555` |

The generated camera source is equivalent to:

```yaml
streams:
  petlibro_plaf203_your_device_serial: petlibro://192.168.1.100?uid=YOUR_DEVICE_UID&quality=hd&ack=hybrid&send_delay_ctrl=1&hd_probe_wait_ms=15000
```

This generated URL is illustrative; users do not normally enter these values.

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
