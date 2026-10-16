from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QKeySequence, QTextCursor
from PySide6.QtTest import QTest

from sp.app.folder_navigator.core import read_text
from sp.app.folder_navigator.window import Tab
from sp.app.ui.markdown_preview import MarkdownReadingView
from sp.markdown_html import nearest_source_anchor, render_markdown_html


TABLE_MARKDOWN = """# Report

| Name | Status |
| --- | --- |
| Alpha | Ready |

![diagram](diagram.png){width=300}
"""


def test_shared_markdown_renderer_keeps_print_quality_and_source_anchors() -> None:
    rendered = render_markdown_html(TABLE_MARKDOWN, source_anchors=True)

    assert "<table>" in rendered
    assert "Name</th>" in rendered
    assert 'style="width: 300px;"' in rendered
    assert 'id="sp-source-3"' in rendered
    assert "&#8203;</span></p>" not in rendered
    assert nearest_source_anchor(TABLE_MARKDOWN, 5) == "sp-source-3"


def test_reading_view_renders_live_markdown_without_a_print_url(
    qtbot, tmp_path: Path
) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)

    view.set_markdown(TABLE_MARKDOWN, base_directory=tmp_path, source_line=4)

    assert "<table>" in view.last_html
    assert "print-token" not in view.last_html
    assert view.browser.document().baseUrl() == QUrl.fromLocalFile(str(tmp_path) + "/")
    assert "Alpha" in view.browser.toPlainText()
    view.find_query.setText("Alpha")
    view.find_next()
    assert view.browser.textCursor().selectedText() == "Alpha"


def test_reading_view_keeps_source_positions_for_image_only_blocks(qtbot) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown("First\n\n![image](missing.png)\n\nLast", source_line=3)

    assert [line for _position, line in view._source_positions] == [1, 3, 5]
    assert view.current_source_line() == 3


def test_folder_markdown_tab_toggles_preview_and_restores_selection(
    qtbot, tmp_path: Path
) -> None:
    page = tmp_path / "report.md"
    page.write_text(TABLE_MARKDOWN, encoding="utf-8")
    tab = Tab(page, read_text(page), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    cursor = tab.editor.textCursor()
    cursor.setPosition(2)
    cursor.setPosition(8, QTextCursor.KeepAnchor)
    tab.editor.setTextCursor(cursor)

    tab.set_reading_view(True)

    assert tab.reading_view_enabled
    assert tab.reading_toggle.text() == "Edit"
    assert tab.editor_view_stack.currentWidget() is tab.reading_view
    assert "<table>" in tab.reading_view.last_html

    tab.set_reading_view(False)

    assert tab.editor_view_stack.currentWidget() is tab.editor
    assert tab.reading_toggle.text() == "Read"
    assert tab.editor.textCursor().anchor() == 2
    assert tab.editor.textCursor().position() == 8


def test_folder_markdown_tab_hands_cursor_position_both_directions(
    qtbot, qapp, tmp_path: Path
) -> None:
    markdown = "\n\n".join(f"Paragraph {index}" for index in range(40))
    page = tmp_path / "handoff.md"
    page.write_text(markdown, encoding="utf-8")
    tab = Tab(page, read_text(page), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(640, 320)
    tab.show()

    edit_line = 21
    edit_block = tab.editor.document().findBlockByNumber(edit_line - 1)
    cursor = QTextCursor(edit_block)
    cursor.movePosition(QTextCursor.Right, QTextCursor.MoveAnchor, 4)
    tab.editor.setTextCursor(cursor)
    tab.set_reading_view(True)
    qapp.processEvents()
    qapp.processEvents()

    assert tab.reading_view.current_source_line() == edit_line

    reading_line = 61
    reading_cursor = tab.reading_view._cursor_for_source_anchor(
        f"sp-source-{reading_line}"
    )
    assert reading_cursor is not None
    tab.reading_view.browser._reading_cursor = reading_cursor
    tab.reading_view.browser.refresh_reading_cursor()
    tab.set_reading_view(False)

    assert tab.editor.textCursor().blockNumber() + 1 == reading_line


def test_reading_view_supports_vi_document_navigation(qtbot, qapp) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(420, 240)
    view.set_markdown("\n\n".join(f"Paragraph {index}" for index in range(80)))
    view.set_vi_enabled(True)
    view.set_vi_cursor_style("line")
    view.show()
    qapp.processEvents()
    scrollbar = view.browser.verticalScrollBar()
    assert scrollbar.maximum() > 0
    assert not view.browser.extraSelections()
    marker = view.browser.vi_rail_rect()
    assert 18 <= marker.width() <= view.browser.fontMetrics().horizontalAdvance("MMMM")
    assert marker.height() <= view.browser.fontMetrics().height() + 4
    assert marker.right() < 40

    QTest.keyClick(view.browser, Qt.Key_G, Qt.ShiftModifier)
    assert scrollbar.value() == scrollbar.maximum()
    QTest.keyClick(view.browser, Qt.Key_G)
    QTest.keyClick(view.browser, Qt.Key_G)
    assert scrollbar.value() == scrollbar.minimum()

    QTest.keyClick(
        view.browser,
        Qt.Key_J,
        Qt.ControlModifier | Qt.ShiftModifier,
    )
    QTest.qWait(160)
    assert scrollbar.value() > scrollbar.minimum()
    assert not view.browser.extraSelections()
    assert not view.browser.vi_rail_rect().isEmpty()


def test_reading_view_uses_print_like_theme_override_colors(qtbot, monkeypatch) -> None:
    import sp.app.ui.markdown_preview as preview_module

    values = {
        "markdown_preview.paper_bg": "#fdfaf2",
        "markdown_preview.text": "#201a16",
        "markdown_preview.table_header_bg": "#ddd2bf",
    }
    monkeypatch.setattr(
        preview_module,
        "theme_value",
        lambda path, default=None: values.get(path, default),
    )
    view = MarkdownReadingView()
    qtbot.addWidget(view)

    assert "background: #fdfaf2" in view._css
    assert "font-size: 28pt" in view._css
    assert "border: 2px solid" in view._css
    assert "background: #ddd2bf" in view._css


def test_folder_markdown_ctrl_space_toggles_reading_view(
    qtbot, qapp, tmp_path: Path
) -> None:
    page = tmp_path / "keyboard.md"
    page.write_text(TABLE_MARKDOWN, encoding="utf-8")
    tab = Tab(page, read_text(page), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(640, 480)
    tab.show()
    tab.editor.setFocus()
    qapp.processEvents()

    QTest.keyClick(tab.editor, Qt.Key_Space, Qt.ControlModifier)
    assert tab.reading_view_enabled
    QTest.keyClick(tab.reading_view.browser, Qt.Key_Space, Qt.ControlModifier)
    assert not tab.reading_view_enabled


def test_stillpoint_reading_view_action_renders_unsaved_buffer_and_restores_cursor(
    main_window, qtbot, qapp, monkeypatch, tmp_path: Path
) -> None:
    main_window.vault_root = str(tmp_path)
    main_window.current_path = "/report.md"
    main_window.editor.set_markdown(TABLE_MARKDOWN)
    cursor = main_window.editor.textCursor()
    cursor.setPosition(4)
    main_window.editor.setTextCursor(cursor)

    main_window._action_reading_view.trigger()

    assert QKeySequence("Ctrl+Space") in {
        shortcut.key() for shortcut in main_window._reading_view_shortcuts
    }
    assert main_window._action_reading_view in main_window.toolbar.actions()
    assert main_window._reading_view_enabled
    assert main_window._editor_view_stack.currentWidget() is main_window.reading_view
    assert "<table>" in main_window.reading_view.last_html

    main_window._action_reading_view.trigger()

    assert not main_window._reading_view_enabled
    assert main_window._editor_view_stack.currentWidget() is main_window.editor
    assert main_window.editor.textCursor().position() == 4

    main_window._action_reading_view.trigger()
    main_window.current_path = None
    main_window.editor.document().setModified(False)
    main_window._dirty_flag = False
    main_window._open_file("/PageB/PageB.md", force=True)
    qapp.processEvents()

    assert "PageB" in main_window.reading_view.browser.toPlainText()
    main_window._action_reading_view.trigger()
    assert main_window.current_path == "/PageB/PageB.md"

    linked = tmp_path / "linked.md"
    linked.write_text("# Linked", encoding="utf-8")
    opened: list[str] = []
    monkeypatch.setattr(main_window, "_open_file", opened.append)
    main_window._open_reading_view_link(QUrl.fromLocalFile(str(linked)).toString())
    assert opened == ["/linked.md"]


def test_folder_window_reading_action_tracks_active_markdown_tab(
    qapp, monkeypatch, tmp_path: Path
) -> None:
    from sp.app.folder_navigator.window import Window

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    root = tmp_path / "root"
    root.mkdir()
    page = root / "report.md"
    page.write_text(TABLE_MARKDOWN, encoding="utf-8")
    window = Window(root)
    try:
        window.open_file(page, pinned=True)
        qapp.processEvents()

        assert window.reading_view_action.isEnabled()
        assert QKeySequence("Ctrl+Space") in {
            shortcut.key() for shortcut in window.active_tab().reading_view_shortcuts
        }
        window.reading_view_action.trigger()

        assert window.active_tab().reading_view_enabled
        assert window.reading_view_action.isChecked()
        assert "Alpha" in window.active_tab().reading_view.browser.toPlainText()
        window.active_tab().reading_view.edit_button.click()
        assert not window.reading_view_action.isChecked()
    finally:
        window.close()
