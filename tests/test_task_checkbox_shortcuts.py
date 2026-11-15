from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor
from sp.webserver.server import WebServer


@pytest.mark.parametrize("marker", ["☐", "☑"])
@pytest.mark.parametrize("indent", ["", "    "])
def test_enter_before_checkbox_inserts_plain_newline(qtbot, marker, indent):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    text = indent + marker + " existing task"
    editor.setPlainText(text)
    cursor = editor.textCursor()
    cursor.setPosition(len(indent))
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key_Return)
    assert editor.toPlainText() == "\n" + text
    editor.undo()
    assert editor.toPlainText() == text


@pytest.mark.parametrize("position", [2, 7, 15])
def test_enter_after_checkbox_keeps_task_continuation(qtbot, position):
    editor = MarkdownEditor()
    qtbot.addWidget(editor)
    editor.setPlainText("☐ existing task")
    cursor = editor.textCursor()
    cursor.setPosition(position)
    editor.setTextCursor(cursor)
    QTest.keyClick(editor, Qt.Key_Return)
    assert editor.toPlainText().rstrip() == "☐ existing task\n☐"


def test_editor_renders_paren_checkbox_shortcut_as_task_symbol(qtbot) -> None:
    editor = MarkdownEditor()
    qtbot.addWidget(editor)

    display = editor._to_display("() follow up\n")
    assert display == "☐ follow up\n"

    is_task, indent, state, content = editor._is_task_line("() follow up")
    assert is_task is True
    assert indent == ""
    assert state == " "
    assert content == "follow up"



def test_webserver_renders_paren_checkbox_shortcut() -> None:
    rendered = WebServer._rewrite_task_and_dash_markers("() follow up")
    assert 'md-checkbox md-checkbox--unchecked' in rendered
    assert rendered.endswith("follow up")
