#!/usr/bin/env bash
set -euo pipefail

stream_name="${STREAM_NAME:-petlibro_feeder}"
rtsp_port="${GO2RTC_RTSP_PORT:-8554}"

exec timeout 300s ffmpeg -hide_banner -rtsp_transport tcp \
    -i "rtsp://127.0.0.1:${rtsp_port}/${stream_name}" \
    -an -f null -
