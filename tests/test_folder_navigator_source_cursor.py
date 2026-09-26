from __future__ import annotations

from PySide6.QtGui import QTextFormat

from sp.app.folder_navigator.editors import SourceEditor


def _cursor_selections(editor: SourceEditor, property_key: int):
    return [
        selection for selection in editor.extraSelections()
        if selection.format.property(property_key) is True
    ]


def test_source_editor_line_cursor_setting_highlights_current_line(qtbot) -> None:
    editor = SourceEditor("sample.py")
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("line")
    editor.set_vi_mode_enabled(True)

    selections = _cursor_selections(editor, editor._VI_LINE_EXTRA_KEY)

    assert len(selections) == 1
    assert selections[0].format.property(QTextFormat.FullWidthSelection) is True
    assert _cursor_selections(editor, editor._VI_BLOCK_EXTRA_KEY) == []
    assert editor.cursorWidth() == 2


def test_source_editor_block_cursor_setting_highlights_character(qtbot) -> None:
    editor = SourceEditor("sample.py")
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("block")
    editor.set_vi_mode_enabled(True)

    selections = _cursor_selections(editor, editor._VI_BLOCK_EXTRA_KEY)

    assert len(selections) == 1
    assert selections[0].format.property(QTextFormat.FullWidthSelection) is False
    assert selections[0].cursor.selectedText() == "o"
    assert _cursor_selections(editor, editor._VI_LINE_EXTRA_KEY) == []
    assert editor.cursorWidth() >= editor.fontMetrics().horizontalAdvance("M")


def test_source_editor_insert_mode_clears_navigation_cursor(qtbot) -> None:
    editor = SourceEditor("sample.py")
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("line")
    editor.set_vi_mode_enabled(True)

    editor._set_vi_insert_mode(True)

    assert _cursor_selections(editor, editor._VI_LINE_EXTRA_KEY) == []
    assert _cursor_selections(editor, editor._VI_BLOCK_EXTRA_KEY) == []
    assert editor.cursorWidth() == editor._default_cursor_width
