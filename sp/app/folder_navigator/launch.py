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
    frozen = getattr(sys, "frozen", False)
    command = ([sys.executable, "--folder-navigator", str(root.resolve())]
               if frozen else
               [sys.executable, "-m", "sp.app.folder_navigator", str(root.resolve())])
    if not frozen:
        # The child starts in the user's home directory, so a source checkout
        # is no longer importable through the parent's current working directory.
        # Preserve the checkout root explicitly for the detached interpreter.
        env = os.environ.copy()
        source_root = str(Path(__file__).resolve().parents[3])
        python_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (source_root if not python_path else
                             os.pathsep.join((source_root, python_path)))
        kwargs["env"] = env
    return subprocess.Popen(command, **kwargs)
