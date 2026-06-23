from __future__ import annotations

from PySide6.QtGui import QTextFormat

from sp.app.ui.plantuml_editor_window import ViPlainTextEdit


def _vi_line_selections(editor: ViPlainTextEdit):
    key = editor._VI_LINE_EXTRA_KEY
    return [s for s in editor.extraSelections() if s.format.property(key) is True]


def _vi_block_selections(editor: ViPlainTextEdit):
    key = editor._VI_BLOCK_EXTRA_KEY
    return [s for s in editor.extraSelections() if s.format.property(key) is True]


def test_vi_navigation_mode_highlights_full_current_line(qtbot) -> None:
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\nthree\n")
    editor.set_vi_cursor_style("line")
    default_width = editor.cursorWidth()
    editor.set_vi_mode_enabled(True)

    selections = _vi_line_selections(editor)

    assert len(selections) == 1
    assert selections[0].format.property(QTextFormat.FullWidthSelection) is True
    assert editor.cursorWidth() > default_width


def test_vi_navigation_block_cursor_uses_non_full_width_overlay(qtbot) -> None:
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\nthree\n")
    editor.set_vi_cursor_style("block")
    editor.set_vi_mode_enabled(True)

    selections = _vi_block_selections(editor)

    assert len(selections) == 1
    assert selections[0].format.property(QTextFormat.FullWidthSelection) is False
    assert _vi_line_selections(editor) == []


def test_vi_insert_mode_clears_line_highlight(qtbot) -> None:
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("line")
    editor.set_vi_mode_enabled(True)

    editor._enter_vi_insert_mode()

    assert _vi_line_selections(editor) == []
