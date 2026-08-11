ARG GO_VERSION=1.26.5
FROM golang:${GO_VERSION}-alpine3.23 AS go2rtc-builder

WORKDIR /src/go2rtc
COPY go2rtc/go.mod go2rtc/go.sum ./
RUN go mod download
COPY go2rtc/ ./
RUN CGO_ENABLED=0 GOOS=linux GOARCH=amd64 \
    go build -trimpath -ldflags="-s -w" -o /out/go2rtc .

FROM ghcr.io/home-assistant/amd64-base-python:3.13-alpine3.23

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev

RUN apk add --no-cache ffmpeg jq \
    && python -m venv /opt/appdaemon-venv \
    && /opt/appdaemon-venv/bin/pip install --no-cache-dir "AppDaemon>=4.5,<5"

COPY --from=go2rtc-builder /out/go2rtc /usr/local/bin/go2rtc
COPY appdaemon/src/plaf203.py /opt/petlibro-local/appdaemon/plaf203.py
COPY appdaemon/src/camera_metadata.py /opt/petlibro-local/appdaemon/camera_metadata.py
COPY templates/ /opt/petlibro-local/templates/
COPY render_config.py /usr/local/bin/petlibro-render-config
COPY run.sh /usr/local/bin/petlibro-local-run
COPY rootfs/ /

RUN chmod 0755 \
        /usr/local/bin/go2rtc \
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
