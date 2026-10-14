"""Platform-specific window icons for StillPoint diagram editors."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
import ctypes
import sys

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication


_ICON_STEMS = {
    "plantuml": "PlantUML",
    "mermaid": "Mermaid",
    "excalidraw": "Excalidraw",
}

_DISPLAY_NAMES = {
    "plantuml": "StillPoint PlantUML",
    "mermaid": "StillPoint Mermaid",
    "excalidraw": "StillPoint Excalidraw",
}


def _resource_roots() -> list[Path]:
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
    return roots


def _icon_candidates(editor: str) -> list[Path]:
    try:
        stem = _ICON_STEMS[editor]
    except KeyError as exc:
        raise ValueError(f"Unknown diagram editor: {editor}") from exc

    png = f"linux-png/{editor}-512x512.png"
    if sys.platform == "win32":
        names = (f"{stem}.ico", png)
    elif sys.platform == "darwin":
        names = (f"{stem}.icns", png)
    else:
        names = (png, f"{stem}.ico")

    candidates: list[Path] = []
    for root in _resource_roots():
        for prefix in (Path("sp/assets/icons"), Path("assets/icons")):
            candidates.extend(root / prefix / name for name in names)
    return candidates


def get_diagram_editor_icon_path(editor: str) -> Path | None:
    """Return the preferred icon asset for an editor on this platform."""
    for path in _icon_candidates(editor):
        if path.is_file():
            return path
    return None


@lru_cache(maxsize=len(_ICON_STEMS))
def get_diagram_editor_icon(editor: str) -> QIcon:
    """Load a distinct window icon for a diagram editor."""
    path = get_diagram_editor_icon_path(editor)
    if path is not None:
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon()


def configure_diagram_editor_process(editor: str) -> None:
    """Set process identity before creating a standalone editor QApplication."""
    if editor not in _ICON_STEMS:
        raise ValueError(f"Unknown diagram editor: {editor}")
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            f"com.stillpoint.{editor}"
        )
    except Exception:
        pass


def configure_diagram_editor_application(app: QApplication, editor: str) -> QIcon:
    """Apply the visible identity for a standalone editor process."""
    try:
        display_name = _DISPLAY_NAMES[editor]
    except KeyError as exc:
        raise ValueError(f"Unknown diagram editor: {editor}") from exc
    app.setApplicationName(display_name)
    app.setApplicationDisplayName(display_name)
    if sys.platform.startswith("linux"):
        app.setDesktopFileName(f"stillpoint-{editor}")
    icon = get_diagram_editor_icon(editor)
    if not icon.isNull():
        app.setWindowIcon(icon)
    if sys.platform == "darwin":
        icon_path = get_diagram_editor_icon_path(editor)
        if icon_path is not None:
            # The native call is needed for the Dock and Cmd-Tab; Qt's window
            # icon alone does not reliably replace a bundled application icon.
            from sp.app.folder_navigator.icon import _set_macos_application_icon

            _set_macos_application_icon(icon_path)
    return icon
