from __future__ import annotations

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPaintEvent, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTextEdit

from sp.app.ui.markdown_editor import MarkdownEditor


@pytest.fixture
def editor(qtbot):
    view = MarkdownEditor()
    qtbot.addWidget(view)
    view.resize(700, 400)
    view.show()
    return view


def select_document(editor):
    cursor = editor.textCursor()
    cursor.select(QTextCursor.Document)
    editor.setTextCursor(cursor)


def test_ctrl_t_blocks_reentrant_paint_until_edit_and_selection_settle(editor, monkeypatch):
    editor.set_markdown("* Review decision\n  * Fetch data\n\tEvaluate order")
    QTest.qWait(160)
    select_document(editor)
    paints = []
    monkeypatch.setattr(QTextEdit, "paintEvent", lambda *args: paints.append(True))
    notifications = []

    def observe_edit():
        notifications.append(True)
        assert editor._cursor_events_blocked
        assert editor._enforce_display_guard
        assert not editor.updatesEnabled()
        editor.paintEvent(QPaintEvent(editor.viewport().rect()))
        assert not paints

    editor.textChanged.connect(observe_edit)
    QTest.keyClick(editor, Qt.Key_T, Qt.ControlModifier)
    editor.textChanged.disconnect(observe_edit)
    assert notifications
    assert editor.updatesEnabled()
    assert not editor._cursor_events_blocked
    assert not editor._enforce_display_guard
    assert editor._post_load_paint_guard_active()
    assert editor.textCursor().selectedText() == editor.toPlainText().replace("\n", "\u2029")
    assert editor._pending_hr_block_numbers
    QTest.qWait(160)
    editor.paintEvent(QPaintEvent(editor.viewport().rect()))
    assert paints


def test_ctrl_t_selection_uses_utf16_positions_and_round_trips_undo(editor):
    source = "\tReview 🚀 order\n\t\tFetch 😀 data"
    editor.set_markdown(source)
    source = editor.to_markdown()
    select_document(editor)
    QTest.keyClick(editor, Qt.Key_T, Qt.ControlModifier)
    formatted = "\t`Review 🚀 order`\n\t\t`Fetch 😀 data`\n"
    assert editor.to_markdown() == formatted
    assert editor.textCursor().selectionEnd() == editor.document().characterCount() - 1
    assert editor.textCursor().selectedText().replace("\u2029", "\n") == formatted
    QTest.keyClick(editor, Qt.Key_T, Qt.ControlModifier)
    assert editor.to_markdown() == source
    editor.undo()
    assert editor.to_markdown() == formatted
    editor.undo()
    assert editor.to_markdown() == source
    editor.redo()
    assert editor.to_markdown() == formatted


def test_repeated_ctrl_t_edits_can_be_drawn_after_undo_redo(editor):
    source = "Runs on create\n\tif initial:\n\t\tEvaluate decision\n\telse:\n\t\tLoad records"
    editor.set_markdown(source)
    source = editor.to_markdown()
    QTest.qWait(160)
    for _ in range(30):
        select_document(editor)
        QTest.keyClick(editor, Qt.Key_T, Qt.ControlModifier)
        formatted = editor.to_markdown()
        editor.undo()
        assert editor.to_markdown() == source
        editor.redo()
        assert editor.to_markdown() == formatted
        QTest.qWait(20)
        assert not editor.grab().isNull()
        select_document(editor)
        QTest.keyClick(editor, Qt.Key_T, Qt.ControlModifier)
        assert editor.to_markdown() == source
