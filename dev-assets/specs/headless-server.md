## Headless + split UI/server (future plan)

Goal: run StillPoint as a standalone server (FastAPI/uvicorn) that multiple clients (desktop UI, mobile, web) can connect to, while keeping the current desktop UI able to spawn or point at a server.

### Shape of the split
- Introduce a dedicated entry point/binary for the server (e.g. `stillpoint-server`) that only hosts the API. It accepts `--vault /path`, `--host`, `--port` (0 = ephemeral), and later auth/config flags.
- Turn the API into a factory (`create_app(vault_root: Path, config: ...) -> FastAPI`) so each server instance gets its own state instead of relying on module globals.
- Keep a UI entry point (`stillpoint`) that can either:
  1) spawn a local server process (for desktop convenience), or
  2) connect to a remote base URL provided via `--api https://host:port`.

### Deployment/infra considerations
- Add TLS/proxy support: allow binding to `0.0.0.0`, run behind nginx/traefik/caddy, and honor `X-Forwarded-Proto` if needed.
- Make vault storage pluggable: local FS first; later S3/FS abstraction so the server can live in the cloud.
- Add authentication/authorization (API tokens at minimum; later per-user vault access) since remote access removes implicit localhost trust.

### Client expectations
- Desktop UI: prefer local spawn for single-user; if `--api` is set, skip spawning and point HTTP client to that base.
- Mobile/web clients: speak the same API; consider websocket/poll endpoints for live updates if needed.

### Migration path
- Start by extracting the API factory and a headless CLI that mirrors current startup.
- Teach the desktop UI to accept `--api` and to skip local server start when provided.
- Later, add auth/storage options to the headless CLI and tighten CORS/config accordingly.
