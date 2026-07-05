"""Standalone Qt WebEngine host for Excalidraw windows."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QByteArray, QSettings, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow

from sp.app.main import get_app_icon
from sp.app.ui.webengine_env import configure_linux_webengine_env, env_truthy


_GEOMETRY_KEY = "excalidraw/editorGeometry"


class ExcalidrawWindow(QMainWindow):
    def __init__(self, title: str) -> None:
        super().__init__()
        self._settings = QSettings("StillPoint", "StillPoint")
        self.setWindowTitle(title)
        icon = get_app_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        if not self._restore_saved_geometry():
            self.resize(1200, 800)
            self._center_on_screen()

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

    def closeEvent(self, event) -> None:  # type: ignore[override]
        try:
            geometry = self.saveGeometry().toBase64().data().decode("utf-8")
            self._settings.setValue(_GEOMETRY_KEY, geometry)
        except Exception:
            pass
        super().closeEvent(event)


def main() -> int:
    parser = argparse.ArgumentParser(description="StillPoint Excalidraw WebEngine host")
    parser.add_argument("--url", required=True)
    parser.add_argument("--title", default="StillPoint Excalidraw")
    args = parser.parse_args()

    configure_linux_webengine_env(disable_env_var="SP_DISABLE_EXCALIDRAW_WEBENGINE")
    if env_truthy("SP_DISABLE_EXCALIDRAW_WEBENGINE"):
        app = QApplication(sys.argv[:1])
        QDesktopServices.openUrl(QUrl(args.url))
        return 0

    from PySide6.QtWebEngineWidgets import QWebEngineView

    app = QApplication(sys.argv[:1])
    icon = get_app_icon()
    if not icon.isNull():
        app.setWindowIcon(icon)
    window = ExcalidrawWindow(args.title)
    web = QWebEngineView(window)
    window.setCentralWidget(web)
    web.load(QUrl(args.url))
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
