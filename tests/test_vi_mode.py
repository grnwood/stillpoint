import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def _ensure_qapp() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _force_initial_paint(widget: MarkdownEditor, app: QApplication) -> None:
    widget.resize(400, 300)
    widget.show()
    for _ in range(5):
        app.processEvents()
        QTest.qWait(10)
    widget.repaint()
    app.processEvents()


@pytest.fixture(scope="module")
def app() -> QApplication:
    return _ensure_qapp()


def test_vi_mode_defers_until_widget_paints(app: QApplication) -> None:
    editor = MarkdownEditor()
    editor.setPlainText("sample")
    editor.set_vi_mode_enabled(True)
    assert editor._vi_pending_activation is True
    assert editor._vi_mode_active is False
    _force_initial_paint(editor, app)
    assert editor._vi_has_painted is True
    assert editor._vi_pending_activation is False
    assert editor._vi_mode_active is True
    editor.close()


def test_vi_clipboard_cycle_tracks_selection(app: QApplication) -> None:
    editor = MarkdownEditor()
    _force_initial_paint(editor, app)
    editor.setPlainText("alpha beta")
    editor.set_vi_mode_enabled(True)
    cursor = editor.textCursor()
    cursor.setPosition(0)
    cursor.movePosition(QTextCursor.Right, QTextCursor.KeepAnchor, 5)
    editor.setTextCursor(cursor)
    assert editor._vi_copy_to_buffer() is True
    assert editor._vi_clipboard == "alpha"
    editor._vi_cut_selection_or_char()
    assert editor._vi_clipboard == "alpha"
    assert editor.toPlainText().startswith(" beta")
    editor._vi_paste_buffer()
    assert editor.toPlainText().startswith("alpha beta")
    editor.close()
