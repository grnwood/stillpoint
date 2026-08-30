"""Stdio-to-HTTP bridge for StillPoint's terminal-scoped MCP endpoint."""

from __future__ import annotations

import atexit
import json
import os
import shlex
import shutil
import stat
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional


_LAUNCHER_DIRECTORY: Optional[Path] = None


def _post_message(url: str, token: str, message: dict, session_id: str = "") -> Optional[dict]:
    payload = json.dumps(message, separators=(",", ":")).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }
    if session_id:
        headers["X-StillPoint-Window-Id"] = session_id
    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers=headers,
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if response.status == 202:
                return None
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"StillPoint MCP HTTP {exc.code}: {detail}") from exc
    if not body:
        return None
    decoded = json.loads(body.decode("utf-8"))
    return decoded if isinstance(decoded, dict) else None


def run_stdio_bridge() -> int:
    """Forward newline-delimited MCP JSON-RPC messages over authenticated HTTP."""
    url = str(os.environ.get("STILLPOINT_MCP_URL") or "").strip()
    token = str(os.environ.get("STILLPOINT_MCP_TOKEN") or "").strip()
    session_id = str(os.environ.get("STILLPOINT_MCP_SESSION") or "").strip()
    if not url or not token:
        print(
            "stillpoint-mcp requires STILLPOINT_MCP_URL and STILLPOINT_MCP_TOKEN "
            "from an embedded StillPoint terminal session.",
            file=sys.stderr,
            flush=True,
        )
        return 2
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise ValueError("JSON-RPC message must be an object")
            response = _post_message(url, token, message, session_id)
            if response is not None:
                sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
                sys.stdout.flush()
        except Exception as exc:
            request_id = None
            try:
                request_id = message.get("id")  # type: ignore[possibly-undefined]
            except Exception:
                pass
            error = {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32000, "message": str(exc)},
            }
            sys.stdout.write(json.dumps(error, separators=(",", ":")) + "\n")
            sys.stdout.flush()
    return 0


def ensure_bridge_launcher() -> Path:
    """Create a token-free per-process launcher suitable for prepending to PATH."""
    global _LAUNCHER_DIRECTORY
    if _LAUNCHER_DIRECTORY is not None:
        name = "stillpoint-mcp.cmd" if os.name == "nt" else "stillpoint-mcp"
        return _LAUNCHER_DIRECTORY / name
    directory = Path(tempfile.mkdtemp(prefix="stillpoint-mcp-"))
    try:
        directory.chmod(stat.S_IRWXU)
    except OSError:
        pass
    executable = str(Path(sys.executable).resolve())
    frozen = bool(getattr(sys, "frozen", False))
    # Source/dev launches run with the vault as cwd, so ``python -m
    # sp.app.mcp_bridge`` cannot assume the StillPoint checkout is importable
    # from that directory. This module is self-contained; invoke its absolute
    # path instead. Frozen builds continue through the executable dispatcher.
    bridge_args = [executable, "--mcp-bridge"] if frozen else [executable, str(Path(__file__).resolve())]
    if os.name == "nt":
        launcher = directory / "stillpoint-mcp.cmd"
        launcher.write_text(
            "@echo off\r\n" + subprocess_list2cmdline(bridge_args) + " %*\r\n",
            encoding="utf-8",
        )
    else:
        launcher = directory / "stillpoint-mcp"
        launcher.write_text(
            "#!/bin/sh\nexec " + shlex.join(bridge_args) + ' "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(stat.S_IRWXU)
    _LAUNCHER_DIRECTORY = directory
    atexit.register(lambda: shutil.rmtree(directory, ignore_errors=True))
    return launcher


def subprocess_list2cmdline(arguments: list[str]) -> str:
    import subprocess

    return subprocess.list2cmdline(arguments)


if __name__ == "__main__":
    raise SystemExit(run_stdio_bridge())
