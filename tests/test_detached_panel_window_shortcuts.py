from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QShortcut
from PySide6.QtWidgets import QMainWindow

from sp.app.ui.main_window import MainWindow


def test_detached_window_standard_close_shortcut_closes_only_that_window(qtbot, monkeypatch) -> None:
    monkeypatch.setattr("sp.app.main.get_app_icon", lambda: QIcon())
    window = QMainWindow()
    qtbot.addWidget(window)

    MainWindow._prepare_top_level_window(None, window)  # type: ignore[arg-type]
    window.show()

    shortcut = window._close_window_shortcut  # type: ignore[attr-defined]
    assert isinstance(shortcut, QShortcut)
    assert shortcut.context() == Qt.WindowShortcut

    shortcut.activated.emit()

    assert not window.isVisible()
