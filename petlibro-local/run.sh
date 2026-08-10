#!/usr/bin/env bash
set -euo pipefail

component="${1:-all}"
exec /usr/local/bin/petlibro-render-config "${component}"
