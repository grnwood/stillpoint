from __future__ import annotations

from PySide6.QtGui import QTextFormat
from PySide6.QtTest import QTest

from sp.app.ui.markdown_editor import MarkdownEditor


def _force_initial_paint(editor: MarkdownEditor, qapp) -> None:
    editor.resize(400, 300)
    editor.show()
    for _ in range(5):
        qapp.processEvents()
        QTest.qWait(10)
        editor.repaint()
    qapp.processEvents()


def _force_vi_navigation_mode(editor: MarkdownEditor, qapp) -> None:
    editor.set_vi_mode_enabled(True)
    for _ in range(15):
        if editor._vi_mode_active:
            return
        qapp.processEvents()
        QTest.qWait(10)
    editor._vi_has_painted = True
    editor._vi_pending_activation = False
    editor._enter_vi_navigation_mode(force_emit=True)


def _vi_line_selections(editor: MarkdownEditor):
    key = editor._VI_LINE_EXTRA_KEY
    return [s for s in editor.extraSelections() if s.format.property(key) is True]


def _vi_block_selections(editor: MarkdownEditor):
    key = editor._VI_EXTRA_KEY
    return [s for s in editor.extraSelections() if s.format.property(key) is True]


def test_markdown_editor_vi_line_cursor_uses_full_width_accent(monkeypatch, qapp) -> None:
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_vault_accent_color", lambda: "#88cc22")
    editor = MarkdownEditor()
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("line")
    default_width = editor.cursorWidth()
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)

    selections = _vi_line_selections(editor)

    assert len(selections) == 1
    assert selections[0].format.property(QTextFormat.FullWidthSelection) is True
    assert selections[0].format.background().color().name().lower() == "#88cc22"
    assert selections[0].format.foreground().color().name().lower() == "#111111"
    assert editor.cursorWidth() > default_width
    editor.close()


def test_markdown_editor_vi_block_cursor_uses_contrast_text(monkeypatch, qapp) -> None:
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_vault_accent_color", lambda: "#224488")
    editor = MarkdownEditor()
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("block")
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)

    selections = _vi_block_selections(editor)

    assert len(selections) == 1
    assert selections[0].format.property(QTextFormat.FullWidthSelection) is False
    assert selections[0].format.background().color().name().lower() == "#224488"
    assert selections[0].format.foreground().color().name().lower() == "#ffffff"
    assert _vi_line_selections(editor) == []
    editor.close()
