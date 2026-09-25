"""Detached launcher shared by Stillpoint and Folder Navigator."""
from pathlib import Path
import os
import subprocess
import sys


def launch(root: Path) -> subprocess.Popen:
    if not root.is_dir():
        raise ValueError(f"Folder does not exist: {root}")
    kwargs = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
              "stderr": subprocess.DEVNULL, "cwd": str(Path.home())}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    command = ([sys.executable, "--folder-navigator", str(root.resolve())]
               if getattr(sys, "frozen", False) else
               [sys.executable, "-m", "sp.app.folder_navigator", str(root.resolve())])
    return subprocess.Popen(command, **kwargs)
