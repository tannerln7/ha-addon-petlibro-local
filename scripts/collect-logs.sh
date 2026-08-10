#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${repo_root}/docker"
data_dir="${PETLIBRO_DATA_DIR:-${compose_dir}/data}"
timestamp="$(date -u +%Y%m%d_%H%M%S)"
work_dir="$(mktemp -d "/tmp/petlibro-local-collect.${timestamp}.XXXXXX")"
archive="${OUTPUT:-/tmp/petlibro-local-debug-${timestamp}.tar.gz}"

cleanup() {
    rm -rf -- "${work_dir}"
}
trap cleanup EXIT

if command -v docker >/dev/null 2>&1; then
    (
        cd "${compose_dir}"
        docker compose logs --no-color --timestamps petlibro-local 2>&1 || true
    ) >"${work_dir}/container.log"
fi

for dump in petlibro_c2d.dat petlibro_d2c.dat; do
    if [[ -f "${data_dir}/${dump}" ]]; then
        cp -a -- "${data_dir}/${dump}" "${work_dir}/${dump}"
    fi
done

cat >"${work_dir}/README.txt" <<'EOF'
This archive contains container logs and any enabled decrypted protocol dumps.
Generated configuration files are intentionally excluded because they contain
local identifiers or MQTT credentials. Protocol dumps can still contain device
and session data; share and retain this archive only as needed for debugging.
EOF

tar -C "${work_dir}" -czf "${archive}" .
printf '%s\n' "${archive}"
