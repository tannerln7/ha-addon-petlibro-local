#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python_bin="${PYTHON_BIN:-python3}"

cd "${repo_root}"

"${python_bin}" -m unittest discover -s tests -v
"${python_bin}" -m py_compile \
    petlibro-local/render_config.py \
    petlibro-local/appdaemon/src/camera_metadata.py \
    petlibro-local/appdaemon/src/device_discovery.py \
    petlibro-local/appdaemon/src/feeder_mqtt_validation.py \
    petlibro-local/appdaemon/src/plaf203.py \
    petlibro-local/appdaemon/tests/test_camera_metadata.py \
    petlibro-local/appdaemon/tests/test_device_discovery.py \
    petlibro-local/appdaemon/tests/test_feeder_mqtt_validation.py \
    tests/test_render_config.py

if "${python_bin}" -c 'import appdaemon' >/dev/null 2>&1; then
    "${python_bin}" -m pytest petlibro-local/appdaemon/tests -q
else
    printf 'Skipping AppDaemon controller tests: install petlibro-local/appdaemon/requirements-dev.txt\n'
fi

(
    cd petlibro-local/go2rtc
    go test ./pkg/petlibro -count=1
    go test -race ./pkg/petlibro -count=1
    go test ./cmd/petlibro-resolve -count=1
    go vet ./pkg/petlibro ./cmd/petlibro-resolve
    go build -o /tmp/petlibro-local-go2rtc .
    go build -o /tmp/petlibro-resolve ./cmd/petlibro-resolve
)

docker compose --env-file docker/.env.example \
    -f docker/docker-compose.yml config --quiet

for script in scripts/*.sh petlibro-local/run.sh petlibro-local/rootfs/etc/services.d/*/run; do
    bash -n "${script}"
done

if command -v shellcheck >/dev/null 2>&1; then
    shellcheck \
        scripts/*.sh \
        petlibro-local/run.sh \
        petlibro-local/rootfs/etc/services.d/*/run
fi

git diff --check
git diff --cached --check

printf "\nAll configured repository checks passed.\n"
