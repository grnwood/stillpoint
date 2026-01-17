#!/usr/bin/env bash
set -e
cd ..
source venv/bin/activate
# Run the API server over HTTPS using uvicorn.
# Note: the Python entrypoint only accepts host/port/vault options (no SSL), so we use uvicorn directly.

export STILPOINT_VAULTS_ROOT="/opt/stillpoint/vaults"

venv/bin/uvicorn sp.server.api:app \
  --host 127.0.0.1 \
  --port 8080