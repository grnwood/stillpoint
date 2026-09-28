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
    frozen = getattr(sys, "frozen", False)
    if sys.platform == "win32":
        # Keep a source-checkout launch out of the invoking console as well.
        # pythonw is the same interpreter without a console subsystem.
        executable = sys.executable
        if not frozen:
            pythonw = Path(executable).with_name("pythonw.exe")
            if Path(executable).name.lower() == "python.exe" and pythonw.is_file():
                executable = str(pythonw)
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True
    command = [executable if sys.platform == "win32" and not frozen else sys.executable,
               "--folder-navigator", str(root.resolve())] if frozen else [
               executable if sys.platform == "win32" else sys.executable,
               "-m", "sp.app.folder_navigator", str(root.resolve())]
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
        from sp.app.config import get_active_vault, load_effective_theme_preference
        env["SP_THEME_OVERRIDE"] = load_effective_theme_preference()
        active_vault = get_active_vault()
        if active_vault:
            env["SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT"] = active_vault
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
