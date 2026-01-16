import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor, QKeyEvent
from PySide6.QtCore import QEvent, Qt
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


def test_shift_n_at_end_selects_to_document_end(app: QApplication) -> None:
    editor = MarkdownEditor()
    # three lines, no trailing newline on final line
    editor.setPlainText("line1\nline2\nlastline")
    editor.set_vi_mode_enabled(True)
    _force_initial_paint(editor, app)

    # place cursor at start of last line
    cursor = editor.textCursor()
    doc = editor.document()
    last_block = doc.findBlockByNumber(doc.blockCount() - 1)
    start_pos = last_block.position()
    cursor.setPosition(start_pos)
    editor.setTextCursor(cursor)

    # Simulate Shift+N keypress
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.ShiftModifier, text='N')
    editor._handle_vi_keypress(ev)

    c = editor.textCursor()
    # selection should extend to document end
    assert c.hasSelection()
    assert c.selectionEnd() == doc.characterCount() - 1
    editor.close()


def test_shift_u_at_start_selects_to_document_start(app: QApplication) -> None:
    editor = MarkdownEditor()
    editor.setPlainText("first line\nsecond line\nthird line")
    editor.set_vi_mode_enabled(True)
    _force_initial_paint(editor, app)

    # place cursor in first line, middle
    cursor = editor.textCursor()
    cursor.setPosition(5)
    editor.setTextCursor(cursor)

    # Simulate Shift+U keypress
    ev = QKeyEvent(QEvent.KeyPress, Qt.Key_U, Qt.ShiftModifier, text='U')
    editor._handle_vi_keypress(ev)

    c = editor.textCursor()
    assert c.hasSelection()
    # selection should start at 0 (document start)
    assert c.selectionStart() == 0
    # selection end should be original cursor pos
    assert c.selectionEnd() == 5
    editor.close()
