#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
image="${IMAGE:-petlibro-local:local}"

exec docker build \
    --build-arg BUILD_ARCH=amd64 \
    --build-arg BUILD_VERSION=local \
    --tag "${image}" \
    "${repo_root}/petlibro-local"
