import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def _force_initial_paint(widget: MarkdownEditor, app: QApplication) -> None:
    widget.resize(400, 300)
    widget.show()
    for _ in range(5):
        app.processEvents()
        QTest.qWait(10)
    widget.repaint()
    app.processEvents()


def test_vi_mode_defers_until_widget_paints(qapp: QApplication) -> None:
    editor = MarkdownEditor()
    editor.setPlainText("sample")
    editor.set_vi_mode_enabled(True)
    assert editor._vi_pending_activation is True
    assert editor._vi_mode_active is False
    _force_initial_paint(editor, qapp)
    assert editor._vi_has_painted is True
    assert editor._vi_pending_activation is False
    assert editor._vi_mode_active is True
    editor.close()


def test_vi_clipboard_cycle_tracks_selection(qapp: QApplication) -> None:
    editor = MarkdownEditor()
    _force_initial_paint(editor, qapp)
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


def test_vi_command_prompt_runs_global_substitution(monkeypatch, qapp: QApplication) -> None:
    editor = MarkdownEditor()
    editor.setPlainText("apple apple")

    monkeypatch.setattr(
        "sp.app.ui.markdown_editor.QInputDialog.getText",
        lambda *args, **kwargs: ("%s/apple/orange/g", True),
    )

    called: dict[str, str] = {}

    def fake_replace_all(old: str, new: str) -> int:
        called["old"] = old
        called["new"] = new
        return 2

    monkeypatch.setattr(editor, "search_replace_all", fake_replace_all)

    editor._open_vi_command_prompt()

    assert called == {"old": "apple", "new": "orange"}
    editor.close()


def test_vi_command_prompt_rejects_invalid_substitution(monkeypatch, qapp: QApplication) -> None:
    editor = MarkdownEditor()

    monkeypatch.setattr(
        "sp.app.ui.markdown_editor.QInputDialog.getText",
        lambda *args, **kwargs: ("%s/noslash/g", True),
    )

    messages: list[str] = []
    monkeypatch.setattr(editor, "_status_message", lambda msg, duration=2000: messages.append(msg))

    editor._open_vi_command_prompt()

    assert "Invalid substitution command." in messages
    editor.close()
