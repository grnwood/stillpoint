"""Tests for the editor Move Text action (selection -> other page)."""

import pytest
from PySide6.QtWidgets import QApplication

from sp.app.ui.markdown_editor import MarkdownEditor


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_move_selected_text_replaces_with_link_and_sends_markdown(app, monkeypatch):
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_prefer_short_links", lambda: False)

    editor = MarkdownEditor()
    editor.set_context(None, "/Source/Source.md")
    editor.setPlainText("Hello world")

    captured: dict[str, str] = {}

    def callback(dest_path: str, markdown_text: str) -> bool:
        captured["dest_path"] = dest_path
        captured["markdown_text"] = markdown_text
        return True

    editor.set_move_text_callback(callback)

    cursor = editor.textCursor()
    cursor.setPosition(6)
    cursor.setPosition(11, cursor.MoveMode.KeepAnchor)
    editor.setTextCursor(cursor)
    QApplication.processEvents()

    ok = editor._move_selected_text_to_page("/Target/Target.md")
    assert ok is True

    assert captured["dest_path"] == "/Target/Target.md"
    assert captured["markdown_text"] == "world"

    markdown = editor.to_markdown()
    assert "world" not in markdown
    assert "[:Target|" in markdown

    editor.close()
