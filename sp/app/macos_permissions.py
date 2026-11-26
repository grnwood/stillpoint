"""Read macOS permission declarations without modifying Python's installation."""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
import sys

MICROPHONE_USAGE = "Record voice notes and save them as page attachments."


def relaunch_development_bundle() -> None:
    """Keep the normal source GUI command usable with macOS permissions."""
    if sys.platform != "darwin" or getattr(sys, "frozen", False):
        return
    if os.environ.get("STILLPOINT_DEV_BUNDLE_ATTEMPTED") or microphone_usage_description():
        return
    root = Path(__file__).resolve().parents[2]
    launcher = root / "tools" / "run-macos-dev.py"
    if not launcher.is_file() or not (Path(sys.prefix) / "pyvenv.cfg").is_file():
        return
    # Run before the embedded server or any window is started. The marker
    # prevents a broken development bundle from causing a restart loop.
    environment = dict(os.environ, STILLPOINT_DEV_BUNDLE_ATTEMPTED="1")
    os.execve(sys.executable, [sys.executable, str(launcher), *sys.argv[1:]], environment)


def microphone_usage_description() -> str | None:
    if sys.platform != "darwin":
        return None
    try:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        pointer = ctypes.c_void_p
        cf.CFBundleGetMainBundle.argtypes = []
        cf.CFBundleGetMainBundle.restype = pointer
        cf.CFStringCreateWithCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_uint32]
        cf.CFStringCreateWithCString.restype = pointer
        cf.CFBundleGetValueForInfoDictionaryKey.argtypes = [pointer, pointer]
        cf.CFBundleGetValueForInfoDictionaryKey.restype = pointer
        cf.CFStringGetCString.argtypes = [pointer, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
        cf.CFStringGetCString.restype = ctypes.c_bool
        cf.CFRelease.argtypes = [pointer]
        cf.CFRelease.restype = None
        bundle = cf.CFBundleGetMainBundle()
        if not bundle:
            return None
        utf8 = 0x08000100
        key = cf.CFStringCreateWithCString(None, b"NSMicrophoneUsageDescription", utf8)
        try:
            value = cf.CFBundleGetValueForInfoDictionaryKey(bundle, key)
            if not value:
                return None
            buffer = ctypes.create_string_buffer(4096)
            if cf.CFStringGetCString(value, buffer, len(buffer), utf8):
                return buffer.value.decode("utf-8").strip() or None
        finally:
            cf.CFRelease(key)
    except (OSError, AttributeError):
        return None
    return None
