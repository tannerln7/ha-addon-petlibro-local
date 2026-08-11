# Docker Compose deployment

This fallback runs the same backend image with host networking and persistent
data. It is intended for a Debian LXC on Proxmox or another trusted Linux host.

## Proxmox / LXC prerequisites

- amd64 Debian LXC with current security updates
- Docker Engine and the Docker Compose plugin
- LXC nesting enabled when Docker runs inside an unprivileged container
- network reachability from the LXC to the feeder and MQTT broker
- firewall access for TCP 1984/8554 and TCP+UDP 8555 as needed

Docker-in-LXC configuration varies by Proxmox version and security policy. Use
an unprivileged container where possible and grant only the features Docker
requires.

## Configure

From the repository root:

```bash
cp docker/.env.example docker/.env
chmod 600 docker/.env
```

Edit `docker/.env` with the broker address and the feeder network in
`LAN_CIDR`. Serial, UID, and IP are discovered automatically. The file is
ignored by Git.

`MQTT_USERNAME` and `MQTT_PASSWORD` authenticate AppDaemon to the broker. The
feeder uses its own factory/device MQTT account, which must be provisioned in
the external broker separately; this backend does not accept or configure the
feeder's product secret.

`MQTT_HOST` is likewise only AppDaemon's connection address. Keep
`PERSIST_FEEDER_MQTT=0` for normal operation. If intentionally migrating the
physical feeder to a durable local broker address, use the separate
`FEEDER_MQTT_HOST` and `FEEDER_MQTT_PORT` values, restart the container, then
reboot the feeder. The preflight
rejects container-only names and addresses and requires a reachable TCP
listener before the controller can send an endpoint update.

Start the container before rebooting the feeder so it can observe the
`DEVICE_START_EVENT` UID. Advanced installations can set `DEVICES_JSON` to a
JSON array of manual device overrides; leave it as `[]` for normal setup.

## Build and run

This Docker/LXC path remains a source build for development and self-hosted
deployments; it does not pull the Home Assistant GHCR release image. Building
patched go2rtc can saturate a small host temporarily. Compose defaults
`GO_BUILD_PROCS` to `2` to limit Go compiler concurrency. Set a different value
in `docker/.env` only when the build host has suitable CPU and memory.

The direct Compose workflow is:

```bash
cd docker
docker compose up -d --build
docker compose logs -f
```

To use more build parallelism on a capable development host:

```bash
GO_BUILD_PROCS=4 docker compose up -d --build
```

From the repository root, the maintained wrappers provide the same build and
startup behavior plus a separate stream check:

```bash
./scripts/build-local.sh
./scripts/run-local.sh
docker compose -f docker/docker-compose.yml logs -f
./scripts/test-stream.sh
```

Compose uses `network_mode: host`; published-port mappings are therefore not
needed. Runtime files persist under `docker/data/`.

Check status and logs:

```bash
cd docker
docker compose ps
docker compose logs --follow petlibro-local
```

Override the stream name when needed:

```bash
STREAM_NAME=another_name ./scripts/test-stream.sh
```

## Stop or update

```bash
cd docker
docker compose down
docker compose up --detach --build
```

`docker compose down` preserves `docker/data/`. Delete that directory only when
you intentionally want to remove generated configuration, AppDaemon state, and
debug dumps.
