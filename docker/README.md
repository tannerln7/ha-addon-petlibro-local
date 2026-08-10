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

Edit `docker/.env` and replace the generic values. `UID` must be exactly 20
characters. The file is ignored by Git.

`PRODUCT_SECRET` is accepted for future compatibility but is unused by this
release and is not rendered into service configuration. Leave it empty unless a
future documented workflow requires it.

## Build and run

```bash
./scripts/build-local.sh
./scripts/run-local.sh
```

Compose uses `network_mode: host`; published-port mappings are therefore not
needed. Runtime files persist under `docker/data/`.

Check status and logs:

```bash
cd docker
docker compose ps
docker compose logs --follow petlibro-local
```

Test the default RTSP stream:

```bash
./scripts/test-stream.sh
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
