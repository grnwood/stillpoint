#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
ENV_FILE="${STILLPOINT_ENV_FILE:-${REPO_ROOT}/.env}"
SERVER_BIN="${SCRIPT_DIR}/stillpoint-server"
PYTHON_BIN="${STILLPOINT_PYTHON_BIN:-${REPO_ROOT}/venv/bin/python}"

if [[ -f "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ -x "${SERVER_BIN}" ]]; then
  exec "${SERVER_BIN}" --run-gc "$@"
fi

if [[ ! -x "${PYTHON_BIN}" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

cd "${REPO_ROOT}"
exec "${PYTHON_BIN}" -m sp.server.api --run-gc "$@"