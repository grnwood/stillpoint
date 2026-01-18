#!/usr/bin/env bash
set -e
cd ..
source venv/bin/activate
# Run the API server over HTTPS using uvicorn.
# Note: the Python entrypoint only accepts host/port/vault options (no SSL), so we use uvicorn directly.

export STILLPOINT_VAULTS_ROOT="./vaults"
export SERVER_ADMIN_PASSWORD="change-me-to-secure-password"
#export STILLPOINT_INSECURE=1

# Alternative: use python -m instead of uvicorn directly
# python -m sp.server.api --host 127.0.0.1 --port 8080

venv/bin/python -m sp.server.api --host 127.0.0.1 --port 8080
