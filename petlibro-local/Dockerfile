ARG GO_VERSION=1.26.5
FROM golang:${GO_VERSION}-alpine3.23 AS go2rtc-builder

ARG GO_BUILD_PROCS=""

WORKDIR /src/go2rtc
COPY petlibro-local/go2rtc/go.mod petlibro-local/go2rtc/go.sum ./
RUN go mod download
COPY petlibro-local/go2rtc/ ./
RUN set -eu; \
    set --; \
    if [ -n "${GO_BUILD_PROCS}" ]; then \
        export GOMAXPROCS="${GO_BUILD_PROCS}"; \
        set -- "-p=${GO_BUILD_PROCS}"; \
    fi; \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
        go build "$@" -trimpath -ldflags="-s -w" -o /out/go2rtc .; \
    CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
        go build "$@" -trimpath -ldflags="-s -w" -o /out/petlibro-resolve ./cmd/petlibro-resolve

FROM ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev

RUN apk add --no-cache ffmpeg jq \
    && python -m venv /opt/appdaemon-venv \
    && /opt/appdaemon-venv/bin/pip install --no-cache-dir "AppDaemon>=4.5,<5" "cryptography>=45,<47"

COPY --from=go2rtc-builder /out/go2rtc /usr/local/bin/go2rtc
COPY --from=go2rtc-builder /out/petlibro-resolve /usr/local/bin/petlibro-resolve
COPY petlibro-local/appdaemon/src/ /opt/petlibro-local/appdaemon/
COPY feeder-state-agent/release-public-key.hex /opt/petlibro-local/release-public-key.hex
COPY petlibro-local/templates/ /opt/petlibro-local/templates/
COPY petlibro-local/render_config.py /usr/local/bin/petlibro-render-config
COPY petlibro-local/run.sh /usr/local/bin/petlibro-local-run
COPY petlibro-local/rootfs/ /

RUN chmod 0755 \
        /usr/local/bin/go2rtc \
        /usr/local/bin/petlibro-resolve \
        /usr/local/bin/petlibro-render-config \
        /usr/local/bin/petlibro-local-run \
        /etc/services.d/go2rtc/run \
        /etc/services.d/appdaemon/run

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.title="Petlibro Local backend" \
    org.opencontainers.image.description="Local PLAF203 MQTT control and camera streaming backend" \
    org.opencontainers.image.licenses="MIT" \
    org.opencontainers.image.source="https://github.com/tannerln7/ha-addon-petlibro-local"
