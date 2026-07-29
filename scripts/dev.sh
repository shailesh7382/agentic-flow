#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend_pid=""
frontend_pid=""

cleanup() {
  trap - EXIT INT TERM
  if [[ -n "${backend_pid}" ]]; then
    kill "${backend_pid}" 2>/dev/null || true
  fi
  if [[ -n "${frontend_pid}" ]]; then
    kill "${frontend_pid}" 2>/dev/null || true
  fi
  wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

"${script_dir}/backend.sh" &
backend_pid=$!

"${script_dir}/frontend.sh" &
frontend_pid=$!

echo "Backend: http://127.0.0.1:8000"
echo "Frontend: http://127.0.0.1:5173"
echo "Press Ctrl+C to stop both processes."

wait -n "${backend_pid}" "${frontend_pid}"

