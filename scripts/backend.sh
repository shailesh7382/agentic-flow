#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "${script_dir}/.." && pwd)"

exec uv run \
  --project "${project_dir}/backend" \
  uvicorn app.main:app \
  --app-dir "${project_dir}/backend" \
  --host 127.0.0.1 \
  --port "${BACKEND_PORT:-8000}" \
  --reload

