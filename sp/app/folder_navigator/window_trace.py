"""Short-lived Windows window trace around Folder Navigator previews."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
from datetime import datetime
import os
from pathlib import Path
import tempfile
import time

from PySide6.QtCore import QTimer


EVENT_SYSTEM_FOREGROUND = 0x0003
EVENT_OBJECT_SHOW = 0x8002
EVENT_OBJECT_NAMECHANGE = 0x800C
OBJID_WINDOW = 0
GA_ROOT = 2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def trace_log_path() -> Path:
    configured = os.environ.get("STILLPOINT_WINDOW_TRACE_LOG")
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "stillpoint-window-trace.log"


class WindowTrace:
    """Record transient top-level windows near a file selection on Windows."""

    def __init__(self, owner) -> None:
        self.path = trace_log_path()
        self._until = 0.0
        self._selection = ""
        self._seen: set[tuple[int, int, str]] = set()
        self._shown: set[int] = set()
        self._pending: list[str] = []
        self._events = 0
        self._in_callback = False
        self._closed = False
        self._hooks = []
        self._flush_timer = QTimer(owner)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self.flush)

        if os.name == "nt":
            try:
                self._install_hooks()
            except Exception as exc:
                for hook in self._hooks:
                    try:
                        self._user32.UnhookWinEvent(hook)
                    except Exception:
                        pass
                self._hooks.clear()
                self._append(f"WinEvent trace unavailable: {exc!r}")

    def _install_hooks(self) -> None:
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        callback_type = ctypes.WINFUNCTYPE(
            None, wintypes.HANDLE, wintypes.DWORD, wintypes.HWND,
            wintypes.LONG, wintypes.LONG, wintypes.DWORD, wintypes.DWORD,
        )
        self._callback = callback_type(self._on_window_event)
        self._user32.SetWinEventHook.argtypes = [
            wintypes.DWORD, wintypes.DWORD, wintypes.HMODULE, callback_type,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
        ]
        self._user32.SetWinEventHook.restype = wintypes.HANDLE
        self._user32.UnhookWinEvent.argtypes = [wintypes.HANDLE]
        self._user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self._user32.GetAncestor.restype = wintypes.HWND
        self._user32.IsWindowVisible.argtypes = [wintypes.HWND]
        self._user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
        self._user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        self._user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        self._kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self._kernel32.OpenProcess.restype = wintypes.HANDLE
        self._kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        for event in (EVENT_OBJECT_SHOW, EVENT_OBJECT_NAMECHANGE, EVENT_SYSTEM_FOREGROUND):
            hook = self._user32.SetWinEventHook(event, event, None, self._callback, 0, 0, 0)
            if hook:
                self._hooks.append(hook)
        self._append(f"trace started pid={os.getpid()} hooks={len(self._hooks)} log={self.path}")
        if not self._hooks:
            self._append(f"WinEvent hook failed error={ctypes.get_last_error()}")

    def arm(self, path: Path) -> None:
        """Capture windows for two seconds after a file is selected or opened."""
        if not self._hooks:
            return
        now = time.monotonic()
        selected = Path(path).name
        if selected != self._selection or now >= self._until:
            self._selection = selected
            self._seen.clear()
            self._shown.clear()
            self._events = 0
            self._append(f"preview file={selected!r}")
        self._until = now + 2.0

    def _process_image(self, pid: int) -> str:
        handle = self._kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return "unavailable"
        try:
            buffer = ctypes.create_unicode_buffer(32768)
            length = wintypes.DWORD(len(buffer))
            if self._kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(length)):
                return buffer.value
            return "unavailable"
        finally:
            self._kernel32.CloseHandle(handle)

    def _on_window_event(self, _hook, event, hwnd, object_id, child_id, _thread, _time) -> None:
        if self._in_callback or not hwnd or time.monotonic() >= self._until:
            return
        if object_id != OBJID_WINDOW or child_id != 0 or self._events >= 40:
            return
        self._in_callback = True
        try:
            window_id = int(getattr(hwnd, "value", hwnd))
            if self._user32.GetAncestor(hwnd, GA_ROOT) != window_id:
                return
            if not self._user32.IsWindowVisible(hwnd):
                return
            if event == EVENT_OBJECT_NAMECHANGE and window_id not in self._shown:
                return
            title = ctypes.create_unicode_buffer(512)
            window_class = ctypes.create_unicode_buffer(256)
            self._user32.GetWindowTextW(hwnd, title, len(title))
            self._user32.GetClassNameW(hwnd, window_class, len(window_class))
            key = (window_id, event, title.value)
            if key in self._seen:
                return
            self._seen.add(key)
            if event == EVENT_OBJECT_SHOW:
                self._shown.add(window_id)
            pid = wintypes.DWORD()
            self._user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            rect = wintypes.RECT()
            self._user32.GetWindowRect(hwnd, ctypes.byref(rect))
            image = self._process_image(pid.value) if pid.value else "unavailable"
            event_name = {
                EVENT_OBJECT_SHOW: "show",
                EVENT_OBJECT_NAMECHANGE: "title",
                EVENT_SYSTEM_FOREGROUND: "foreground",
            }[event]
            self._events += 1
            self._append(
                f"file={self._selection!r} event={event_name} hwnd={window_id:#x} "
                f"pid={pid.value} image={image!r} class={window_class.value!r} "
                f"title={title.value!r} size={rect.right - rect.left}x{rect.bottom - rect.top}"
            )
        except Exception:
            # Never let diagnostics disrupt the editor's native event loop.
            pass
        finally:
            self._in_callback = False

    def _append(self, message: str) -> None:
        timestamp = datetime.now().astimezone().isoformat(timespec="milliseconds")
        self._pending.append(f"{timestamp} [Folder Navigator window trace] {message}\n")
        if not self._flush_timer.isActive():
            self._flush_timer.start(150)

    def flush(self) -> None:
        if not self._pending:
            return
        lines, self._pending = self._pending, []
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as log:
                log.writelines(lines)
        except OSError:
            pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for hook in self._hooks:
            self._user32.UnhookWinEvent(hook)
        self._hooks.clear()
        self._flush_timer.stop()
        self.flush()
