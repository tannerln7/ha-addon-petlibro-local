#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
compose_dir="${repo_root}/docker"

if [[ ! -f "${compose_dir}/.env" ]]; then
    printf 'Missing %s/.env; copy .env.example and add local values.\n' "${compose_dir}" >&2
    exit 2
fi

cd "${compose_dir}"
exec docker compose up --detach --build
