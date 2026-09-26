"""Cross-platform application identity for Folder Navigator processes."""

from __future__ import annotations

from pathlib import Path
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


WINDOWS_APP_ID = "com.stillpoint.foldernavigator"
LINUX_DESKTOP_FILE = "stillpoint-folder-navigator"


def _icon_candidates() -> list[Path]:
    if sys.platform == "win32":
        names = ("FolderNavigator.ico", "linux-png/folder-navigator-512x512.png")
    elif sys.platform == "darwin":
        names = ("FolderNavigator.icns", "linux-png/folder-navigator-512x512.png")
    else:
        names = ("linux-png/folder-navigator-512x512.png", "FolderNavigator.ico")

    roots: list[Path] = []
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        roots.extend((Path(frozen_root), Path(frozen_root) / "_internal"))
    try:
        executable_root = Path(sys.executable).resolve().parent
        roots.extend((executable_root, executable_root / "_internal"))
    except (OSError, RuntimeError):
        pass
    roots.append(Path(__file__).resolve().parents[2])

    candidates: list[Path] = []
    for root in roots:
        for prefix in (Path("sp/assets/icons"), Path("assets/icons")):
            candidates.extend(root / prefix / name for name in names)
    return candidates


def get_folder_navigator_icon() -> QIcon:
    """Load the platform-preferred Folder Navigator icon from source or a bundle."""
    for path in _icon_candidates():
        if path.is_file():
            icon = QIcon(str(path))
            if not icon.isNull():
                return icon
    return QIcon()


def configure_folder_navigator_process() -> None:
    """Apply process identity that must be set before QApplication exists."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(WINDOWS_APP_ID)
    except Exception:
        pass


def configure_folder_navigator_application(app: QApplication) -> QIcon:
    """Apply the visible application identity and return its icon."""
    app.setApplicationName("StillPoint Folder Navigator")
    app.setApplicationDisplayName("StillPoint Folder Navigator")
    if sys.platform.startswith("linux"):
        app.setDesktopFileName(LINUX_DESKTOP_FILE)
    icon = get_folder_navigator_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    return icon
