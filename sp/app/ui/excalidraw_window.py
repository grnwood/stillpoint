"""Minimal Excalidraw POC window."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget

from sp.logging_flags import log_enabled

from .webengine_env import configure_linux_webengine_env, env_truthy

_QWEBENGINE_VIEW_CLASS = None
_QWEBENGINE_IMPORT_ATTEMPTED = False

POC_PATH = "/excalidraw/poc"
FALLBACK_URL = "about:blank"


def _log(message: str) -> None:
    if log_enabled("diagrams"):
        print(f"[Excalidraw] {message}")


def _configure_linux_webengine_env() -> None:
    configure_linux_webengine_env(disable_env_var="SP_DISABLE_EXCALIDRAW_WEBENGINE")


def _load_qwebengine_view_class():
    global _QWEBENGINE_VIEW_CLASS, _QWEBENGINE_IMPORT_ATTEMPTED
    if env_truthy("SP_DISABLE_EXCALIDRAW_WEBENGINE"):
        _log("WebEngine disabled by SP_DISABLE_EXCALIDRAW_WEBENGINE")
        _QWEBENGINE_IMPORT_ATTEMPTED = True
        _QWEBENGINE_VIEW_CLASS = None
        return None
    if _QWEBENGINE_IMPORT_ATTEMPTED:
        return _QWEBENGINE_VIEW_CLASS
    _QWEBENGINE_IMPORT_ATTEMPTED = True
    try:
        _configure_linux_webengine_env()
        _log(
            "Importing PySide6.QtWebEngineWidgets.QWebEngineView "
            f"profile={os.getenv('SP_WEBENGINE_PROFILE', 'safe')} "
            f"qt_opengl={os.getenv('QT_OPENGL', '')} "
            f"qt_qpa={os.getenv('QT_QPA_PLATFORM', '')} "
            f"flags={os.getenv('QTWEBENGINE_CHROMIUM_FLAGS', '')}"
        )
        from PySide6.QtWebEngineWidgets import QWebEngineView as _QWebEngineView  # type: ignore

        _QWEBENGINE_VIEW_CLASS = _QWebEngineView
    except Exception as exc:
        _log(f"WebEngine import failed: {exc}")
        _QWEBENGINE_VIEW_CLASS = None
    return _QWEBENGINE_VIEW_CLASS


class ExcalidrawWindow(QMainWindow):
    """POC window that proves .excalidraw attachments can launch WebEngine."""

    def __init__(
        self,
        file_path: str,
        parent=None,
        url: str | None = None,
        base_url: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.file_path = Path(file_path)
        self.url = url or self._poc_url(base_url)
        self.web = None

        try:
            from sp.app.main import get_app_icon

            self.setWindowIcon(get_app_icon())
        except Exception:
            pass

        self.setWindowTitle(f"Excalidraw POC - {self.file_path.name}")
        self.resize(1200, 800)

        web_class = _load_qwebengine_view_class()
        if web_class is None:
            self._show_external_browser_fallback()
            return

        self.web = web_class(self)
        self.setCentralWidget(self.web)
        _log(f"Loading POC URL: {self.url}")
        self.web.load(QUrl(self.url))

    @staticmethod
    def _poc_url(base_url: str | None) -> str:
        cleaned = (base_url or "").strip().rstrip("/")
        if not cleaned:
            return FALLBACK_URL
        return f"{cleaned}{POC_PATH}"

    def _show_external_browser_fallback(self) -> None:
        widget = QWidget(self)
        layout = QVBoxLayout(widget)
        label = QLabel(
            "Qt WebEngine is not available in this environment.\n"
            "Open the Excalidraw POC URL in your browser instead."
        )
        label.setWordWrap(True)
        layout.addWidget(label)
        button = QPushButton("Open in Browser")
        button.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(self.url)))
        layout.addWidget(button)
        layout.addStretch()
        self.setCentralWidget(widget)
