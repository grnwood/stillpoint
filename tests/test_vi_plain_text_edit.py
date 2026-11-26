from __future__ import annotations

from PySide6.QtGui import QFont, QTextFormat

from sp.app.ui.plantuml_editor_window import (
    ViPlainTextEdit,
    diagram_code_font,
    style_diagram_code_editor,
)


def _vi_line_selections(editor: ViPlainTextEdit):
    key = editor._VI_LINE_EXTRA_KEY
    return [s for s in editor.extraSelections() if s.format.property(key) is True]


def _vi_block_selections(editor: ViPlainTextEdit):
    key = editor._VI_BLOCK_EXTRA_KEY
    return [s for s in editor.extraSelections() if s.format.property(key) is True]


def test_vi_navigation_line_mode_outlines_without_filling(qtbot) -> None:
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText("one\ntwo\nthree\n")
    editor.set_vi_cursor_style("line")
    default_width = editor.cursorWidth()
    editor.set_vi_mode_enabled(True)

    selections = _vi_line_selections(editor)

    assert selections == []
    assert not editor._vi_line_outline_rect().isEmpty()
    assert editor._vi_line_outline_rect().width() == editor.viewport().width() - 2
    assert editor.cursorWidth() == default_width
    assert all(selection.format.property(editor._CURRENT_LINE_EXTRA_KEY) is not True
               for selection in editor.extraSelections())


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


def test_diagram_code_editor_uses_coder_typography_and_workbench_style(qtbot) -> None:
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setFont(diagram_code_font("DejaVu Sans Mono", 13))

    style_diagram_code_editor(editor, "mermaid_editor", "mermaid")
    editor.setPlainText("flowchart TD\n  A --> B\n")
    editor._diagram_syntax_highlighter.rehighlight()

    assert editor.objectName() == "diagramSourceEditor"
    assert editor.font().pointSize() == 13
    assert editor.font().fixedPitch()
    assert editor.font().weight() == QFont.Weight.Medium
    assert editor.cursorWidth() == 2
    assert editor.diagram_theme_prefix() == "mermaid_editor"
    assert "border-left: 3px solid" in editor.styleSheet()
    assert editor._diagram_syntax_highlighter.language == "mermaid"
    assert editor._diagram_syntax_highlighter.rules[0][0].match("flowchart TD").hasMatch()
    assert any(
        selection.format.property(editor._CURRENT_LINE_EXTRA_KEY) is True
        for selection in editor.extraSelections()
    )


def test_vi_line_outline_uses_vault_color_and_preserves_row_background(qtbot, monkeypatch):
    from PySide6.QtGui import QColor
    from PySide6.QtTest import QTest
    from sp.app import config

    monkeypatch.setattr(config, "load_vault_accent_color", lambda: "#88cc22")
    editor = ViPlainTextEdit()
    editor.setStyleSheet("QPlainTextEdit { background: #14202b; color: #e5eaf2; }")
    editor.setPlainText("one\ntwo\n")
    editor.resize(500, 250)
    qtbot.addWidget(editor)
    editor.set_vi_cursor_style("line")
    editor.set_vi_mode_enabled(True)
    QTest.qWait(20)
    outline = editor._vi_line_outline_rect()
    pixels = editor.viewport().grab().toImage().scaled(editor.viewport().size())
    assert pixels.pixelColor(outline.center().x(), outline.top()) == QColor("#88cc22")
    assert pixels.pixelColor(outline.center().x(), outline.center().y()) == QColor("#14202b")
    editor._enter_vi_insert_mode()
    assert editor._vi_line_outline_rect().isEmpty()


def test_vi_block_width_tracks_font_zoom(qtbot):
    editor = ViPlainTextEdit()
    qtbot.addWidget(editor)
    editor.setPlainText("diagram source")
    editor.set_vi_cursor_style("block")
    editor.set_vi_mode_enabled(True)
    font = editor.font()
    font.setPointSize(24)
    editor.setFont(font)
    assert editor.cursorWidth() == max(2, editor.fontMetrics().horizontalAdvance("M"))
