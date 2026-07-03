#!/usr/bin/env python3
"""Minimal Qt WebEngine crash probe for Linux diagnostics."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from PySide6.QtCore import QTimer, QUrl, qVersion

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sp.app.ui.webengine_env import configure_linux_webengine_env


def _log(message: str) -> None:
    print(f"[webengine-probe] {message}", flush=True)


def _env_summary() -> None:
    keys = (
        "SP_WEBENGINE_PROFILE",
        "QT_QPA_PLATFORM",
        "QT_OPENGL",
        "QTWEBENGINE_DISABLE_SANDBOX",
        "QTWEBENGINE_CHROMIUM_FLAGS",
        "FONTCONFIG_FILE",
        "DISPLAY",
        "WAYLAND_DISPLAY",
        "XDG_SESSION_TYPE",
    )
    for key in keys:
        value = os.getenv(key)
        if value:
            _log(f"{key}={value}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe PySide6 QtWebEngine crash stage.")
    parser.add_argument(
        "--stage",
        choices=("import", "app", "view", "html", "url"),
        default="html",
        help="How far to exercise QtWebEngine.",
    )
    parser.add_argument(
        "--url",
        default="about:blank",
        help="URL for --stage=url.",
    )
    parser.add_argument(
        "--ms",
        type=int,
        default=1500,
        help="Milliseconds to keep the event loop open for html/url stages.",
    )
    parser.add_argument(
        "--no-env",
        action="store_true",
        help="Do not apply StillPoint's WebEngine environment helper.",
    )
    args = parser.parse_args()

    if not args.no_env:
        configure_linux_webengine_env()
    _env_summary()

    import PySide6

    _log(f"PySide6={PySide6.__version__} Qt={qVersion()}")
    _log("importing PySide6.QtWebEngineWidgets")
    from PySide6.QtWebEngineWidgets import QWebEngineView

    _log("QtWebEngineWidgets import ok")
    if args.stage == "import":
        return 0

    from PySide6.QtWidgets import QApplication

    _log("creating QApplication")
    app = QApplication(sys.argv[:1])
    _log("QApplication ok")
    if args.stage == "app":
        return 0

    _log("creating QWebEngineView")
    view = QWebEngineView()
    view.resize(900, 600)
    view.show()
    _log("QWebEngineView ok")
    if args.stage == "view":
        QTimer.singleShot(args.ms, app.quit)
        return app.exec()

    if args.stage == "html":
        _log("calling setHtml")
        view.setHtml(
            "<!doctype html><html><body>"
            "<h1>QtWebEngine probe</h1><p>setHtml smoke page.</p>"
            "</body></html>",
            QUrl("http://127.0.0.1/"),
        )
    else:
        _log(f"calling load {args.url}")
        view.load(QUrl(args.url))

    QTimer.singleShot(args.ms, app.quit)
    rc = app.exec()
    _log(f"event loop exited rc={rc}")
    return int(rc)


if __name__ == "__main__":
    raise SystemExit(main())
