import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


@pytest.fixture
def editor(qapp):
    ed = MarkdownEditor()
    yield ed
    ed.close()


@pytest.mark.parametrize(
    ("input_text", "expected"),
    [
        ("- simple dash", (False, "", "")),
        ("  - indented dash", (False, "", "")),
        ("+ simple plus", (False, "", "")),
        ("* simple asterisk", (True, "", "simple asterisk")),
        ("  * indented asterisk", (True, "  ", "indented asterisk")),
        ("• simple bullet", (True, "", "simple bullet")),
        ("  • indented bullet", (True, "  ", "indented bullet")),
        ("*bold text*", (False, "", "")),
        ("-hyphenated-word", (False, "", "")),
    ],
)
def test_is_bullet_line(editor, input_text, expected):
    assert editor._is_bullet_line(input_text) == expected


def test_backspace_clears_empty_checkbox_line(editor, qapp):
    editor.show()
    editor.setPlainText("() ")
    editor._enforce_display_symbols()

    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.End)
    editor.setTextCursor(cursor)

    assert editor.toPlainText().startswith("☐")

    QTest.keyClick(editor, Qt.Key_Backspace)
    qapp.processEvents()

    assert editor.toPlainText() == ""
