from __future__ import annotations

import pytest
from PySide6.QtGui import QColor, QTextCursor, QTextFormat
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


def test_markdown_editor_vi_line_cursor_outlines_without_filling(monkeypatch, qapp) -> None:
    monkeypatch.setattr("sp.app.ui.markdown_editor.config.load_vault_accent_color", lambda: "#88cc22")
    editor = MarkdownEditor()
    editor.setPlainText("one\ntwo\n")
    editor.set_vi_cursor_style("line")
    editor.setStyleSheet("QTextEdit { background: #14202b; color: #e5eaf2; }")
    default_width = editor.cursorWidth()
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)

    assert _vi_line_selections(editor) == []
    outline = editor._vi_line_outline_rect()
    assert outline.width() == editor.viewport().width() - 2
    image = editor.viewport().grab().toImage().scaled(editor.viewport().size())
    x = outline.center().x()
    assert image.pixelColor(x, outline.top()) == QColor("#88cc22")
    assert image.pixelColor(x, outline.center().y()) == QColor("#14202b")
    assert editor.cursorWidth() == default_width
    editor.setTextCursor(QTextCursor(editor.document().findBlockByNumber(1)))
    qapp.processEvents()
    assert editor._vi_line_outline_rect().top() != outline.top()
    image = editor.viewport().grab().toImage().scaled(editor.viewport().size())
    assert image.pixelColor(x, outline.top()) == QColor("#14202b")
    editor._enter_vi_insert_mode()
    assert editor._vi_line_outline_rect().isEmpty()
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


def test_main_window_markdown_vi_row_uses_shared_outline(main_window, qapp):
    editor = main_window.editor
    editor.set_markdown('one\ntwo\nthree')
    editor.set_vi_cursor_style('line')
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)
    assert not editor._vi_line_outline_rect().isEmpty()
    assert not _vi_line_selections(editor)
    cursor = editor.textCursor()
    cursor.select(QTextCursor.WordUnderCursor)
    editor.setTextCursor(cursor)
    assert editor._vi_line_outline_rect().isEmpty()


def test_folder_markdown_vi_outline_follows_visual_rows_and_local_accent(qtbot, qapp, tmp_path):
    from sp.app.folder_navigator.core import read_text
    from sp.app.folder_navigator.window import Tab

    path = tmp_path / 'outline.md'
    path.write_text('Words ' * 100 + '\n\nSecond paragraph.')
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(500, 400)
    tab.show()
    editor = tab.editor
    editor.set_vi_cursor_style('line')
    editor.set_folder_navigator_vi_cursor_color('#db2244')
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)
    before = editor.to_markdown()
    first = editor._vi_line_outline_rect()
    assert first.height() == editor.cursorRect().height()
    assert first.height() < editor.document().documentLayout().blockBoundingRect(editor.textCursor().block()).height()
    assert editor._vi_cursor_colors()[0] == QColor('#db2244')
    cursor = editor.textCursor()
    cursor.movePosition(QTextCursor.Down)
    editor.setTextCursor(cursor)
    assert editor._vi_line_outline_rect().top() > first.top()
    assert not _vi_line_selections(editor)
    assert editor.to_markdown() == before


@pytest.mark.parametrize('mode', ['focus', 'audience'])
def test_mode_vi_outline_suppresses_paragraph_fill_and_restores_insert_preference(
    qtbot, qapp, monkeypatch, mode
):
    from sp.app import config
    from sp.app.ui.mode_window import ModeWindow

    monkeypatch.setattr(config, 'load_vi_mode_enabled', lambda: True)
    monkeypatch.setattr(config, 'load_vi_cursor_style', lambda: 'line')
    monkeypatch.setattr(config, 'save_focus_mode_settings', lambda value: None)
    monkeypatch.setattr(config, 'save_audience_mode_settings', lambda value: None)
    base = MarkdownEditor()
    qtbot.addWidget(base)
    base.set_markdown('First paragraph.\n\nSecond paragraph.')
    window = ModeWindow(mode, base, vault_root=None, page_path='/Page.md',
                        read_only=False, heading_provider=lambda: [],
                        settings={'reading_view': False, 'paragraph_focus': True,
                                  'paragraph_highlight': True, 'cursor_spotlight': False})
    qtbot.addWidget(window)
    window.show()
    QTest.qWait(100)
    editor = window.editor
    _force_initial_paint(editor, qapp)
    _force_vi_navigation_mode(editor, qapp)
    window._update_paragraph_highlight()
    assert not editor._vi_line_outline_rect().isEmpty()
    assert not _vi_line_selections(editor)
    assert not [selection for selection in editor.extraSelections()
                if selection.format.property(window._HIGHLIGHT_KEY) is True]
    editor._enter_vi_insert_mode()
    assert editor._vi_line_outline_rect().isEmpty()
    assert [selection for selection in editor.extraSelections()
            if selection.format.property(window._HIGHLIGHT_KEY) is True]
    editor._enter_vi_navigation_mode(force_emit=True)
    assert not editor._vi_line_outline_rect().isEmpty()
    assert not [selection for selection in editor.extraSelections()
                if selection.format.property(window._HIGHLIGHT_KEY) is True]
    assert not window._highlight_guard
    window.close()
