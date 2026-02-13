from __future__ import annotations

import os

import pytest
from PySide6.QtWidgets import QApplication

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    try:
        app.closeAllWindows()
    except Exception:
        pass
    app.processEvents()
    app.quit()


class _QtBot:
    def __init__(self, app: QApplication) -> None:
        self._app = app
        self._widgets = []

    def addWidget(self, widget) -> None:
        self._widgets.append(widget)
        widget.show()
        self._app.processEvents()


@pytest.fixture
def qtbot(qapp: QApplication):
    bot = _QtBot(qapp)
    yield bot
    for widget in reversed(bot._widgets):
        try:
            widget.close()
            widget.deleteLater()
        except Exception:
            pass
    qapp.processEvents()
