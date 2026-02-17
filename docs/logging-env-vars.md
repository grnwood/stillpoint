# Logging Environment Variables

StillPoint now supports functional-area logging flags using `SP_LOG_<AREA>`.

- Default behavior: detailed area logs are **off**.
- Important startup and error output remains visible by default.
- Enable all detailed logs with `SP_LOG_ALL=1`.
- These flags are honored in desktop and server contexts.

## New Area Flags

Set any area to `1` / `true` to enable, `0` / `false` to disable.

- `SP_LOG_STARTUP`
- `SP_LOG_API_CLIENT`
- `SP_LOG_API_SERVER`
- `SP_LOG_AUTH_SECURITY`
- `SP_LOG_VAULT_IO`
- `SP_LOG_AUTOSAVE`
- `SP_LOG_NAVIGATION`
- `SP_LOG_SORTING_REORDER`
- `SP_LOG_EDITOR_MARKDOWN`
- `SP_LOG_EDITOR_RENDER`
- `SP_LOG_ATTACHMENTS_MEDIA`
- `SP_LOG_SEARCH_INDEX`
- `SP_LOG_TASKS_CALENDAR`
- `SP_LOG_REMOTE_VAULTS`
- `SP_LOG_AI_CHAT`
- `SP_LOG_RAG_VECTOR`
- `SP_LOG_DIAGRAMS`
- `SP_LOG_UI_STATE`
- `SP_LOG_PERFORMANCE`
- `SP_LOG_ALL`

## Recommended Quiet Defaults

Leave all `SP_LOG_*` flags unset for normal usage. This keeps stdout focused on:

- startup lifecycle
- bound host/port
- security warnings
- errors

## Common Examples

```bash
# Trace desktop <-> API request/response calls
export SP_LOG_API_CLIENT=1

# Trace server endpoint activity and parameters
export SP_LOG_API_SERVER=1

# Trace left-nav and tree loading behavior
export SP_LOG_NAVIGATION=1

# Trace sorting/reorder internals
export SP_LOG_SORTING_REORDER=1

# Trace markdown editor save/load details
export SP_LOG_EDITOR_MARKDOWN=1

# Trace AI + RAG behavior
export SP_LOG_AI_CHAT=1
export SP_LOG_RAG_VECTOR=1
```

## Existing Non-Area Logging Env Vars

These are not area toggles but still affect logging/output behavior:

- `SP_DISABLE_FAULTHANDLER` (disable crash dump capture)
- `STILLPOINT_FAULTHANDLER_LOG` (faulthandler log path)
- `UVICORN_LOG_LEVEL` (embedded uvicorn verbosity)
