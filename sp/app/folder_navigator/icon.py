"""Cross-platform application identity for Folder Navigator processes."""

from __future__ import annotations

import ctypes
import os
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
    path = get_folder_navigator_icon_path()
    if path is not None:
        icon = QIcon(str(path))
        if not icon.isNull():
            return icon
    return QIcon()


def get_folder_navigator_icon_path() -> Path | None:
    """Return the first usable platform-specific icon asset path."""
    for path in _icon_candidates():
        if path.is_file():
            return path
    return None


def _set_macos_application_icon(icon_path: Path) -> bool:
    """Set the native NSApplication icon used by Dock and Cmd-Tab.

    Qt's window icon API does not reliably replace the bundle icon in macOS's
    application switcher, especially when the companion launcher ultimately
    executes the shared StillPoint binary.  Use the small Objective-C runtime
    surface directly so PyObjC is not required.
    """
    if sys.platform != "darwin":
        return False
    try:
        objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        objc.objc_getClass.argtypes = [ctypes.c_char_p]
        objc.objc_getClass.restype = ctypes.c_void_p
        objc.sel_registerName.argtypes = [ctypes.c_char_p]
        objc.sel_registerName.restype = ctypes.c_void_p

        send_address = ctypes.cast(objc.objc_msgSend, ctypes.c_void_p).value
        if not send_address:
            return False
        send_id = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(send_address)
        send_id_arg = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(send_address)
        send_id_utf8 = ctypes.CFUNCTYPE(
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_char_p
        )(send_address)
        send_void_arg = ctypes.CFUNCTYPE(
            None, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
        )(send_address)
        send_void = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(send_address)

        def cls(name: str) -> int:
            return int(objc.objc_getClass(name.encode("ascii")) or 0)

        def sel(name: str) -> int:
            return int(objc.sel_registerName(name.encode("ascii")) or 0)

        ns_string = send_id_utf8(
            cls("NSString"),
            sel("stringWithUTF8String:"),
            os.fsencode(str(icon_path)),
        )
        image = send_id_arg(
            send_id(cls("NSImage"), sel("alloc")),
            sel("initWithContentsOfFile:"),
            ns_string,
        )
        if not image:
            return False
        application = send_id(cls("NSApplication"), sel("sharedApplication"))
        send_void_arg(application, sel("setApplicationIconImage:"), image)
        send_void(image, sel("release"))
        return True
    except Exception:
        return False


def configure_folder_navigator_process() -> None:
    """Apply process identity that must be set before QApplication exists."""
    # Folder Navigator hosts several long-lived Qt widgets and background
    # workers. Never import QtWebEngine into this process: on Linux/macOS its
    # teardown can abort or segfault the entire navigator. This disables only
    # the WebEngine backend; Mermaid still uses its native inline SVG preview.
    os.environ["SP_MERMAID_DISABLE_INPROCESS_WEBENGINE"] = "1"
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
    if sys.platform == "darwin":
        icon_path = get_folder_navigator_icon_path()
        if icon_path is not None:
            _set_macos_application_icon(icon_path)
    return icon
