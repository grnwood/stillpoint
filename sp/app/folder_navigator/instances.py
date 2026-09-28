"""Cross-process discovery and activation for Folder Navigator windows."""

from __future__ import annotations

import json
import os
from pathlib import Path
import secrets


def _instances_dir() -> Path:
    return Path.home() / ".stillpoint" / "folder_navigators"


def list_instances() -> list[dict]:
    """Read registered navigator windows and discard dead-process records."""
    directory = _instances_dir()
    try:
        records = []
        for record_path in directory.glob("*.json"):
            try:
                record = json.loads(record_path.read_text(encoding="utf-8"))
                pid = int(record.get("pid", 0))
                if pid <= 0:
                    raise ValueError("invalid process id")
                os.kill(pid, 0)
                record["pid"] = pid
                record["record_path"] = str(record_path)
                records.append(record)
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try:
                    record_path.unlink(missing_ok=True)
                except OSError:
                    pass
        return records
    except OSError:
        return []


def activate_instance(record: dict, timeout_ms: int = 250) -> bool:
    """Ask a registered navigator process to show and activate its window."""
    socket_name = str(record.get("socket") or "")
    if not socket_name:
        return False
    from PySide6.QtNetwork import QLocalSocket

    socket = QLocalSocket()
    socket.connectToServer(socket_name)
    if not socket.waitForConnected(timeout_ms):
        return False
    socket.write(b"activate\n")
    delivered = socket.waitForBytesWritten(timeout_ms)
    socket.disconnectFromServer()
    return delivered


def activate_existing(root: Path, *, exclude_pid: int | None = None) -> bool:
    """Activate an open navigator rooted at *root*, if one is registered."""
    try:
        target = str(Path(root).expanduser().resolve(strict=True))
    except (OSError, RuntimeError):
        target = str(Path(root).expanduser().resolve(strict=False))
    for record in list_instances():
        if record.get("root") != target or record["pid"] == exclude_pid:
            continue
        if activate_instance(record):
            return True
        # A live process can temporarily have an unavailable IPC endpoint
        # (notably while starting on Windows). Do not discard its registry
        # entry; let the caller launch another navigator as a safe fallback.
    return False


class InstanceRegistration:
    """Publish this window and handle local activation requests."""

    def __init__(self, window, root: Path) -> None:
        from PySide6.QtNetwork import QLocalServer

        self.window = window
        self.server = QLocalServer(window)
        self.server.newConnection.connect(self._accept_connections)
        self.pid = os.getpid()
        self.record_path = _instances_dir() / f"{self.pid}.json"
        self.socket_name = f"stillpoint-folder-navigator-{self.pid}-{secrets.token_hex(4)}"
        self.registered = False
        try:
            self.record_path.parent.mkdir(parents=True, exist_ok=True)
            if not self.server.listen(self.socket_name):
                return
            resolved_root = str(Path(root).resolve(strict=True))
            record = {
                "pid": self.pid,
                "root": resolved_root,
                "name": Path(resolved_root).name,
                "socket": self.socket_name,
            }
            temporary = self.record_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(record), encoding="utf-8")
            temporary.replace(self.record_path)
            self.registered = True
        except (OSError, RuntimeError):
            self.server.close()

    def _accept_connections(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is None:
                return
            socket.readyRead.connect(lambda connection=socket: self._read_request(connection))
            if socket.bytesAvailable():
                self._read_request(socket)

    def _read_request(self, socket) -> None:
        data = bytes(socket.readAll())
        if data:
            if self.window.isMinimized():
                self.window.showNormal()
            self.window.show()
            self.window.raise_()
            self.window.activateWindow()
            if os.name == "nt":
                # Qt activation alone can be ignored by Windows when another
                # process initiated the request. Explicitly ask the window
                # manager to restore and foreground this process's window.
                try:
                    import ctypes

                    hwnd = int(self.window.winId())
                    ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                    ctypes.windll.user32.SetForegroundWindow(hwnd)
                except (AttributeError, OSError, ValueError):
                    pass
        socket.disconnectFromServer()
        socket.deleteLater()

    def close(self) -> None:
        self.server.close()
        if self.registered:
            try:
                record = json.loads(self.record_path.read_text(encoding="utf-8"))
                if record.get("socket") == self.socket_name:
                    self.record_path.unlink(missing_ok=True)
            except (OSError, ValueError, TypeError):
                pass
            self.registered = False
