# Troubleshooting

## Add-on does not start

Check the Home Assistant app/add-on log. Configuration errors are reported
without echoing option values. Confirm:

- `device_ip` is a valid address reachable from the host;
- `uid` is exactly 20 letters or numbers;
- `serial` contains the feeder's MQTT `DL_DEVICE_ID`;
- MQTT and go2rtc ports are between 1 and 65535.

For Docker Compose, run:

```bash
cd docker
docker compose config --quiet
docker compose logs --follow petlibro-local
```

## Check go2rtc

Open the web interface at `http://HOME_ASSISTANT_HOST:1984/` or the configured
API port. Confirm that the configured stream exists and inspect its connection
status.

Test RTSP from a trusted host:

```bash
STREAM_NAME=petlibro_feeder ./scripts/test-stream.sh
```

The equivalent direct command is:

```bash
timeout 300s ffmpeg -hide_banner -rtsp_transport tcp \
  -i "rtsp://127.0.0.1:8554/petlibro_feeder" \
  -an -f null -
```

## Initial 640x360 stream before HD

Tested PLAF203 firmware can start an HD session with a 640x360 SPS and switch to
1920x1080 several seconds later. Keep `camera_quality: hd` and use the
recommended `hd_probe_wait_ms: 15000`. Increase it up to 60000 only when logs
show that the higher-resolution SPS arrives later.

## Corruption, choppy playback, or media stalls

Start with the validated settings:

```yaml
camera_quality: hd
ack_mode: hybrid
send_delay_ctrl: true
hd_probe_wait_ms: 15000
```

Then enable `verbose_logs`. Petlibro statistics report packet families, media
loss, ACK progress, SPS transitions, and assembler decisions without requiring
raw dumps.

Enable `enable_debug_dumps` only when repeatable packet evidence is necessary.
The add-on writes:

- `/data/petlibro_c2d.dat`
- `/data/petlibro_d2c.dat`

Disable dumping after a short reproduction because files grow continuously and
contain decrypted device/session traffic.

## MQTT controller is offline

Check that the broker is reachable and that the configured AppDaemon account
can subscribe and publish. Confirm the feeder itself is connecting to the local
broker and that topics beginning with
`dl/PLAF203/YOUR_DEVICE_SERIAL/device/` are present.

The backend does not change feeder DNS or provision its factory MQTT
credentials. Those network prerequisites must be completed separately.

## Collect diagnostics

For Docker Compose:

```bash
./scripts/collect-logs.sh
```

The script excludes generated YAML because it contains MQTT credentials and
local identifiers. If debug dumps exist, the archive includes them and must be
handled as sensitive data.
