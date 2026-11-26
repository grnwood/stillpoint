#!/usr/bin/env bash
# Quick start for the sample vault, including macOS microphone permissions.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"
STILLPOINT_PYTHON="$REPO_ROOT/venv/bin/python"
if [[ ! -x "$STILLPOINT_PYTHON" ]]; then
  STILLPOINT_PYTHON=python
fi
if [[ "$(uname -s)" == "Darwin" ]]; then
  exec "$STILLPOINT_PYTHON" tools/run-macos-dev.py --vault ./dev-assets/vault-samplep "$@"
fi
exec "$STILLPOINT_PYTHON" -m sp.app.main --vault ./dev-assets/vault-samplep "$@"
