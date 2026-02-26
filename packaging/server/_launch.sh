#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STILLPOINT_BIN="${SCRIPT_DIR}/stillpoint-server"

if [[ ! -x "${STILLPOINT_BIN}" ]]; then
  # Backward-compatible fallback for older package naming.
  STILLPOINT_BIN="${SCRIPT_DIR}/stillpoint"
fi

if [[ ! -x "${STILLPOINT_BIN}" ]]; then
  echo "Error: server executable not found in ${SCRIPT_DIR}" >&2
  exit 1
fi

HOST="${STILLPOINT_SERVER_HOST:-127.0.0.1}"
PORT="${STILLPOINT_SERVER_PORT:-8000}"
VAULTS_ROOT="${STILLPOINT_VAULTS_ROOT:-}"
INSECURE="${STILLPOINT_SERVER_INSECURE:-0}"

if [[ -z "${SERVER_ADMIN_PASSWORD:-}" && "${INSECURE}" != "1" ]]; then
  echo "Error: SERVER_ADMIN_PASSWORD must be set unless STILLPOINT_SERVER_INSECURE=1 is used." >&2
  exit 1
fi

ARGS=(--host "${HOST}" --port "${PORT}")
if [[ -n "${VAULTS_ROOT}" ]]; then
  ARGS+=(--vaults-root "${VAULTS_ROOT}")
fi
if [[ "${INSECURE}" == "1" ]]; then
  ARGS+=(--insecure)
fi

# Run locally, allowing the user to see logs in real-time.
#   Comment the line below and uncomment the next section for VPS server usage.
exec "${STILLPOINT_BIN}" "${ARGS[@]}" "$@"
