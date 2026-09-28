from __future__ import annotations

import ctypes
from ctypes import wintypes
from types import SimpleNamespace

from sp.app.folder_navigator import instances


def test_windows_instance_probe_queries_exit_code_without_signaling(monkeypatch):
    calls = []

    def open_process(access, inherit, pid):
        calls.append(("open", access, inherit, pid))
        return 123

    def get_exit_code(_handle, pointer):
        ctypes.cast(pointer, ctypes.POINTER(wintypes.DWORD)).contents.value = 259
        return True

    def close_handle(handle):
        calls.append(("close", handle))
        return True

    kernel32 = SimpleNamespace(
        OpenProcess=open_process,
        GetExitCodeProcess=get_exit_code,
        CloseHandle=close_handle,
    )
    monkeypatch.setattr(instances, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ctypes, "windll", SimpleNamespace(kernel32=kernel32), raising=False)

    assert instances._process_is_alive(456)
    assert calls == [("open", 0x1000, False, 456), ("close", 123)]
