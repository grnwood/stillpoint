#!/usr/bin/env python3
"""Run source StillPoint in a macOS app bundle with microphone declarations.

Usage: venv/bin/python tools/run-macos-dev.py [StillPoint arguments]
       venv/bin/python tools/run-macos-dev.py --check

The generated bundle uses the current virtualenv's packages and live source.
It does not change the system Python, request permission, or record audio.
"""
from __future__ import annotations

import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sp.app.macos_permissions import MICROPHONE_USAGE


def prepare_bundle(root: Path = ROOT) -> Path:
    prefix = Path(sys.prefix)
    config = prefix / "pyvenv.cfg"
    if not config.is_file():
        raise RuntimeError("Run this launcher with the project virtualenv: venv/bin/python tools/run-macos-dev.py")
    contents = root / "build" / "StillPoint Dev.app" / "Contents"
    macos = contents / "MacOS"
    macos.mkdir(parents=True, exist_ok=True)
    executable = macos / "stillpoint-dev"
    source = Path(sys._base_executable).resolve()
    if not executable.exists() or executable.stat().st_mtime_ns != source.stat().st_mtime_ns:
        shutil.copy2(source, executable)
    shutil.copy2(config, contents / "pyvenv.cfg")
    library = contents / "lib"
    target = prefix / "lib"
    if library.is_symlink() and library.resolve() != target.resolve():
        library.unlink()
    if not library.exists():
        library.symlink_to(target, target_is_directory=True)
    with (contents / "Info.plist").open("wb") as output:
        plistlib.dump({
            "CFBundleExecutable": "stillpoint-dev",
            "CFBundleIdentifier": "app.stillpoint.development",
            "CFBundleName": "StillPoint Dev",
            "CFBundleDisplayName": "StillPoint Dev",
            "CFBundlePackageType": "APPL",
            "CFBundleVersion": "1",
            "CFBundleShortVersionString": "1.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription": MICROPHONE_USAGE,
        }, output)
    return executable


def main():
    if sys.platform != "darwin":
        raise SystemExit("This launcher is for macOS. Use python -m sp.app.main on other platforms.")
    executable = prepare_bundle()
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(ROOT), env.get("PYTHONPATH"))))
    if sys.argv[1:] == ["--check"]:
        # Check the actual process bundle and virtualenv imports, without
        # launching a window or asking for microphone permission.
        probe = (
            "from sp.app.macos_permissions import microphone_usage_description; "
            "from PySide6.QtCore import qVersion; "
            "description = microphone_usage_description(); "
            "assert description, 'Microphone usage declaration is missing'; "
            "print('Microphone declaration:', description); print('Qt:', qVersion())"
        )
        raise SystemExit(subprocess.run([str(executable), "-c", probe], env=env).returncode)
    os.execve(executable, [str(executable), "-m", "sp.app.main", *sys.argv[1:]], env)


if __name__ == "__main__":
    main()
