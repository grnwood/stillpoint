from __future__ import annotations

from PySide6.QtCore import QByteArray, Qt
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtTest import QTest

from sp.app.ui.page_editor_window import PageEditorWindow


def _new_editor(monkeypatch) -> PageEditorWindow:
    monkeypatch.setattr(PageEditorWindow, "_load_content", lambda self: None)
    return PageEditorWindow(
        api_base="http://127.0.0.1:1",
        vault_root="/tmp/test-vault",
        page_path="/Page/Page.md",
        read_only=False,
        open_in_main_callback=lambda _path: None,
    )


def test_page_editor_restores_last_saved_geometry(qtbot, monkeypatch) -> None:
    encoded = QByteArray(b"saved-window-frame").toBase64().data().decode("ascii")
    restored: list[bytes] = []
    monkeypatch.setattr(
        "sp.app.ui.page_editor_window.config.load_popup_editor_geometry",
        lambda: encoded,
    )
    monkeypatch.setattr(
        PageEditorWindow,
        "restoreGeometry",
        lambda self, value: restored.append(bytes(value)) or True,
    )
    window = _new_editor(monkeypatch)
    qtbot.addWidget(window)

    assert restored == [b"saved-window-frame"]


def test_page_editor_geometry_timer_persists_resize_and_move_state(qtbot, monkeypatch) -> None:
    saved: list[str] = []
    monkeypatch.setattr(
        "sp.app.ui.page_editor_window.config.load_popup_editor_geometry",
        lambda: None,
    )
    monkeypatch.setattr(
        "sp.app.ui.page_editor_window.config.save_popup_editor_geometry",
        lambda geometry: saved.append(geometry),
    )
    window = _new_editor(monkeypatch)
    qtbot.addWidget(window)

    assert window._geometry_timer.isSingleShot()
    assert window._geometry_timer.interval() == 400

    window.show()
    QTest.qWait(30)
    window._geometry_timer.stop()
    saved.clear()
    window.resize(window.width() + 40, window.height() + 25)
    window.move(window.x() + 10, window.y() + 10)

    assert window._geometry_timer.isActive()
    QTest.qWait(450)

    assert len(saved) == 1
    assert QByteArray.fromBase64(saved[0].encode("ascii")) == window.saveGeometry()


def test_page_editor_has_window_close_command(qtbot, qapp, monkeypatch) -> None:
    window = _new_editor(monkeypatch)
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_save_current_file", lambda **_kwargs: None)
    monkeypatch.setattr(window, "_save_geometry", lambda: None)
    window.show()
    window.editor.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()

    action = window._close_window_action
    assert isinstance(action, QAction)
    assert action.shortcut() == QKeySequence("Ctrl+W")
    assert action.shortcutContext() == Qt.WindowShortcut

    QTest.keyClick(window.editor, Qt.Key_W, Qt.ControlModifier)
    qapp.processEvents()

    assert not window.isVisible()
