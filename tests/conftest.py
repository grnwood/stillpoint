from __future__ import annotations

import os
import sys
import gc
from pathlib import Path

# Configure Qt platform before importing any PySide modules.
# VS Code test workers can crash (SIGSEGV) when Qt picks a GUI backend
# incompatible with the runner environment.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Ensure `import sp` works even when pytest is launched from `tests/`.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
import shiboken6


def _flush_qt(app: QApplication, rounds: int = 2) -> None:
    """Drain queued Qt events, including DeferredDelete callbacks."""
    for _ in range(rounds):
        try:
            app.processEvents()
        except Exception:
            pass
        try:
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        except Exception:
            pass
        try:
            app.processEvents()
        except Exception:
            pass


@pytest.fixture(scope="session")
def qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Make teardown deterministic to avoid interpreter-shutdown crashes.
    try:
        app.closeAllWindows()
    except Exception:
        pass
    try:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.clear()
    except Exception:
        pass
    _flush_qt(app, rounds=3)
    try:
        app.quit()
    except Exception:
        pass
    _flush_qt(app, rounds=2)
    # Force Qt object destruction while Python runtime is still fully alive.
    try:
        shiboken6.delete(app)
    except Exception:
        pass
    try:
        for wrapper in list(shiboken6.Shiboken.getAllValidWrappers()):
            try:
                shiboken6.Shiboken.invalidate(wrapper)
            except Exception:
                pass
    except Exception:
        pass
    gc.collect()


@pytest.fixture(autouse=True)
def _cleanup_toplevel_widgets(qapp: QApplication):
    """Close and delete any leaked widgets between tests."""
    yield
    try:
        widgets = list(qapp.topLevelWidgets())
    except Exception:
        widgets = []

    for widget in reversed(widgets):
        try:
            if hasattr(widget, "close"):
                widget.close()
        except Exception:
            pass
        try:
            if hasattr(widget, "deleteLater"):
                widget.deleteLater()
        except Exception:
            pass

    _flush_qt(qapp, rounds=3)


@pytest.fixture(autouse=True)
def _cleanup_clipboard(qapp: QApplication):
    """Reset clipboard MIME ownership between tests to avoid Qt teardown crashes."""
    yield
    try:
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.clear()
    except Exception:
        pass
    _flush_qt(qapp, rounds=1)


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
    # Clean up widgets in reverse order
    for widget in reversed(bot._widgets):
        try:
            if hasattr(widget, 'close'):
                widget.close()
        except Exception:
            pass
    # Process events to complete cleanup
    try:
        qapp.processEvents()
    except Exception:
        pass
    # Delete widgets after processing events
    for widget in reversed(bot._widgets):
        try:
            if hasattr(widget, 'deleteLater'):
                widget.deleteLater()
        except Exception:
            pass
    _flush_qt(qapp, rounds=2)
