"""Standalone Qt WebEngine host for Excalidraw windows."""

from __future__ import annotations

import argparse
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication, QMainWindow

from sp.app.ui.webengine_env import configure_linux_webengine_env, env_truthy


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
    window = QMainWindow()
    window.setWindowTitle(args.title)
    window.resize(1200, 800)
    web = QWebEngineView(window)
    window.setCentralWidget(web)
    web.load(QUrl(args.url))
    window.show()
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
