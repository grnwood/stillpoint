#!/usr/bin/env bash
set -e
cd ..
source venv/bin/activate
# Run the API server over HTTPS using uvicorn.
# Note: the Python entrypoint only accepts host/port/vault options (no SSL), so we use uvicorn directly.

export ZIMX_VAULTS_ROOT="/home/grnwood/Desktop/StillPointServerVaults"

venv/bin/uvicorn sp.server.api:app \
  --host 0.0.0.0 \
  --port 8443 \
  --ssl-keyfile dev-assets/certs/local-key.pem \
  --ssl-certfile dev-assets/certs/local-cert.pem
