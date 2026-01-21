#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STILLPOINT_BIN="${SCRIPT_DIR}/stillpoint"

if [[ ! -x "${STILLPOINT_BIN}" ]]; then
  echo "Error: stillpoint executable not found at ${STILLPOINT_BIN}" >&2
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

ARGS=(--server --host "${HOST}" --port "${PORT}")
if [[ -n "${VAULTS_ROOT}" ]]; then
  ARGS+=(--vaults-root "${VAULTS_ROOT}")
fi
if [[ "${INSECURE}" == "1" ]]; then
  ARGS+=(--insecure)
fi

exec "${STILLPOINT_BIN}" "${ARGS[@]}" "$@"
