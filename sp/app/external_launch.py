"""Helpers for launching programs outside StillPoint's packaged runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping


_LOADER_PATH_KEYS = ("LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH")
_BUNDLED_RUNTIME_KEYS = (
    "PYTHONHOME",
    "QT_PLUGIN_PATH",
    "QML2_IMPORT_PATH",
    "QT_QPA_PLATFORM_PLUGIN_PATH",
)


def external_process_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    """Return an environment suitable for non-StillPoint child processes.

    PyInstaller adjusts the dynamic-loader search path for the packaged app and
    preserves the user's original value in ``*_ORIG``.  External terminals,
    file managers, and desktop applications must receive the original value,
    not the bundle's private library directory.
    """
    env = dict(os.environ if source is None else source)
    frozen = bool(getattr(sys, "frozen", False))

    for key in _LOADER_PATH_KEYS:
        original_key = f"{key}_ORIG"
        if original_key in env:
            original = env.pop(original_key)
            if original:
                env[key] = original
            else:
                env.pop(key, None)
        elif frozen:
            env.pop(key, None)

    if frozen:
        for key in _BUNDLED_RUNTIME_KEYS:
            env.pop(key, None)
    return env


def open_path_in_system(path: str | Path) -> bool:
    """Open a local file/folder with the OS default app using a clean environment."""
    target = Path(path).expanduser()
    if not target.exists():
        return False
    if sys.platform.startswith("darwin"):
        command = ["open", str(target)]
    elif sys.platform.startswith("win"):
        command = ["explorer", str(target)]
    else:
        command = ["xdg-open", str(target)]
    try:
        subprocess.Popen(
            command,
            env=external_process_environment(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=not sys.platform.startswith("win"),
        )
        return True
    except (OSError, ValueError):
        return False
