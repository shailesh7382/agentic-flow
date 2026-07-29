#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
project_dir="$(cd "${script_dir}/.." && pwd)"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required: https://docs.astral.sh/uv/getting-started/installation/"
  exit 1
fi

if ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required: https://nodejs.org/"
  exit 1
fi

if [[ ! -f "${project_dir}/backend/.env" ]]; then
  cp "${project_dir}/backend/.env.example" "${project_dir}/backend/.env"
  echo "Created backend/.env from the example configuration."
fi

uv sync --project "${project_dir}/backend" --dev
npm install --prefix "${project_dir}/frontend"

echo
echo "Setup complete. Start LM Studio's local server, then run:"
echo "  ./scripts/dev.sh"

