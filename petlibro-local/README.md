# Petlibro Local backend

Runs the patched Petlibro go2rtc camera client and the PLAF203 AppDaemon MQTT
controller together as one experimental Home Assistant backend app/add-on.

Configure the feeder LAN address, MQTT serial, 20-character camera UID, and
broker settings in the add-on UI. The default RTSP stream is available at:

```text
rtsp://HOME_ASSISTANT_HOST:8554/petlibro_feeder
```

See [DOCS.md](DOCS.md) for configuration and security details.
