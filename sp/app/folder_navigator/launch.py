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
    if frozen and sys.platform == "darwin":
        try:
            main_bundle = Path(sys.executable).resolve().parents[2]
            navigator_bundle = main_bundle.parent / "StillPoint Folder Navigator.app"
            if main_bundle.suffix == ".app" and navigator_bundle.is_dir():
                command = ["open", "-na", str(navigator_bundle), "--args", str(root.resolve())]
        except (IndexError, OSError):
            pass
    env = os.environ.copy()
    try:
        from sp.app.config import load_effective_theme_preference
        env["SP_THEME_OVERRIDE"] = load_effective_theme_preference()
    except Exception:
        pass
    if not frozen:
        # The child starts in the user's home directory, so a source checkout
        # is no longer importable through the parent's current working directory.
        # Preserve the checkout root explicitly for the detached interpreter.
        source_root = str(Path(__file__).resolve().parents[3])
        python_path = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (source_root if not python_path else
                             os.pathsep.join((source_root, python_path)))
    kwargs["env"] = env
    return subprocess.Popen(command, **kwargs)
