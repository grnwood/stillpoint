"""Standalone Qt WebEngine host for Excalidraw windows."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QByteArray, QSettings, QTimer, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow

from sp.app.ui.diagram_editor_icons import (
    configure_diagram_editor_application,
    configure_diagram_editor_process,
    get_diagram_editor_icon,
)
from sp.app.ui.webengine_env import configure_linux_webengine_env, env_truthy


_GEOMETRY_KEY = "excalidraw/editorGeometry"


class ExcalidrawWindow(QMainWindow):
    def __init__(self, title: str) -> None:
        super().__init__()
        self._settings = QSettings("StillPoint", "StillPoint")
        self.setWindowTitle(title)
        icon = get_diagram_editor_icon("excalidraw")
        if not icon.isNull():
            self.setWindowIcon(icon)
        if not self._restore_saved_geometry():
            self.resize(1200, 800)
            self._center_on_screen()
        self._geometry_timer = QTimer(self)
        self._geometry_timer.setSingleShot(True)
        self._geometry_timer.setInterval(400)
        self._geometry_timer.timeout.connect(self._save_geometry)

    def _restore_saved_geometry(self) -> bool:
        value = self._settings.value(_GEOMETRY_KEY, "")
        if not value:
            return False
        try:
            if isinstance(value, QByteArray):
                return bool(self.restoreGeometry(value))
            return bool(self.restoreGeometry(QByteArray.fromBase64(str(value).encode("utf-8"))))
        except Exception:
            return False

    def _center_on_screen(self) -> None:
        screen = QApplication.screenAt(self.cursor().pos()) or QApplication.primaryScreen()
        if screen is None:
            return
        available = screen.availableGeometry()
        self.move(available.center() - self.rect().center())

    def _save_geometry(self) -> None:
        try:
            geometry = self.saveGeometry().toBase64().data().decode("utf-8")
            self._settings.setValue(_GEOMETRY_KEY, geometry)
            self._settings.sync()
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        if hasattr(self, "_geometry_timer"):
            self._geometry_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._geometry_timer.stop()
        self._save_geometry()
        super().closeEvent(event)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="StillPoint Excalidraw WebEngine host")
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", default="StillPoint Excalidraw")
    args = parser.parse_args(argv)

    configure_linux_webengine_env(disable_env_var="SP_DISABLE_EXCALIDRAW_WEBENGINE")
    configure_diagram_editor_process("excalidraw")
    if env_truthy("SP_DISABLE_EXCALIDRAW_WEBENGINE"):
        app = QApplication(sys.argv[:1])
        configure_diagram_editor_application(app, "excalidraw")
        QDesktopServices.openUrl(QUrl(args.url))
        return 0

    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication(sys.argv[:1])
    configure_diagram_editor_application(app, "excalidraw")
    window = ExcalidrawWindow(args.title)
    web = QWebEngineView(window)
    window.setCentralWidget(web)
    web.load(QUrl(args.url))
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
