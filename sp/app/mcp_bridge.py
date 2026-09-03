"""Authenticated stdio-to-HTTP bridge for StillPoint's external MCP API."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


def _state_file(name: str) -> Path:
    return Path.home() / ".stillpoint" / name


def _read_state(name: str) -> str:
    try:
        return _state_file(name).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _http_json(
    url: str,
    payload: dict,
    *,
    bearer_token: str = "",
    local_ui_token: str = "",
    session_id: str = "",
    timeout: float = 60,
) -> tuple[int, Optional[dict]]:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if bearer_token:
        headers["Authorization"] = f"Bearer {bearer_token}"
    if local_ui_token:
        headers["X-Local-Ui-Token"] = local_ui_token
    if session_id:
        headers["X-StillPoint-Window-Id"] = session_id
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"StillPoint HTTP {exc.code}: {detail}") from exc
    if not body:
        return status, None
    decoded = json.loads(body.decode("utf-8"))
    return status, decoded if isinstance(decoded, dict) else None


def _discover_connection() -> tuple[str, str, str, str]:
    """Return MCP URL, bearer token, session id, and local bootstrap token."""
    configured_url = str(os.environ.get("STILLPOINT_MCP_URL") or "").strip()
    api_base = _read_state("api-base").rstrip("/")
    url = configured_url or (f"{api_base}/mcp" if api_base else "")
    bearer_token = str(os.environ.get("STILLPOINT_MCP_TOKEN") or "").strip()
    session_id = str(os.environ.get("STILLPOINT_MCP_SESSION") or "").strip()
    local_ui_token = ""

    if not url:
        raise RuntimeError(
            "StillPoint is not running and STILLPOINT_MCP_URL is not set. "
            "Open the desktop app or configure a remote MCP URL and token."
        )
    if bearer_token:
        return url, bearer_token, session_id, local_ui_token
    if configured_url:
        raise RuntimeError("STILLPOINT_MCP_TOKEN is required with an explicit MCP URL.")

    local_ui_token = str(os.environ.get("STILLPOINT_LOCAL_UI_TOKEN") or "").strip()
    if not local_ui_token:
        local_ui_token = _read_state("local-ui-token")
    if not local_ui_token or not api_base:
        raise RuntimeError(
            "No MCP bearer token was supplied and the running StillPoint desktop "
            "could not be discovered."
        )

    session_id = session_id or f"external-mcp-{os.getpid()}"
    _status, issued = _http_json(
        f"{api_base}/auth/mcp-token",
        {
            "ttl_seconds": 43200,
            "session_id": session_id,
            "vault_path": str(Path.cwd().resolve()),
        },
        local_ui_token=local_ui_token,
        timeout=10,
    )
    bearer_token = str((issued or {}).get("token") or "").strip()
    if not bearer_token:
        raise RuntimeError("StillPoint did not issue an MCP token.")
    return url, bearer_token, session_id, local_ui_token


def _post_message(url: str, token: str, message: dict, session_id: str = "") -> Optional[dict]:
    status, response = _http_json(
        url,
        message,
        bearer_token=token,
        session_id=session_id,
    )
    return None if status == 202 else response


def _revoke_token(api_base: str, token: str, local_ui_token: str) -> None:
    if not api_base or not token or not local_ui_token:
        return
    try:
        _http_json(
            f"{api_base.rstrip('/')}/auth/mcp-token/revoke",
            {"token": token},
            local_ui_token=local_ui_token,
            timeout=2,
        )
    except Exception:
        pass


def run_stdio_bridge() -> int:
    """Forward newline-delimited MCP JSON-RPC to a running StillPoint instance."""
    try:
        url, token, session_id, local_ui_token = _discover_connection()
    except Exception as exc:
        print(f"stillpoint-mcp: {exc}", file=sys.stderr, flush=True)
        return 2

    api_base = url.rsplit("/mcp", 1)[0] if url.endswith("/mcp") else ""
    try:
        for raw_line in sys.stdin:
            line = raw_line.strip()
            if not line:
                continue
            request_id = None
            try:
                message = json.loads(line)
                if not isinstance(message, dict):
                    raise ValueError("JSON-RPC message must be an object")
                request_id = message.get("id")
                response = _post_message(url, token, message, session_id)
                if response is not None:
                    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                    sys.stdout.flush()
            except Exception as exc:
                error = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32000, "message": str(exc)},
                }
                sys.stdout.write(json.dumps(error, separators=(",", ":")) + "\n")
                sys.stdout.flush()
    finally:
        _revoke_token(api_base, token, local_ui_token)
    return 0


if __name__ == "__main__":
    raise SystemExit(run_stdio_bridge())
