from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QImage, QKeySequence, QPalette, QTextCursor
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

    ruled = render_markdown_html("---\n\n# Heading", source_anchors=True)
    assert '<hr id="sp-source-1" />' in ruled
    assert "<hr /><span" not in ruled

    italic_rows = render_markdown_html(
        "    *- A new virtual location will be configured*\n"
        "    *- The EGC items will be configured*",
        source_anchors=True,
    )
    assert "<pre>" not in italic_rows
    assert "<em>- A new virtual location will be configured</em>" in italic_rows


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


def test_reading_view_tracks_hard_break_source_lines(qtbot, qapp) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(500, 260)
    view.show()
    view.set_markdown("[ ] First\n[ ] Second\n[ ] Third", source_line=3)
    qapp.processEvents()
    qapp.processEvents()

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

    initial_position = view.browser._reading_cursor.position()
    initial_marker_top = marker.top()
    QTest.keyClick(view.browser, Qt.Key_J)
    assert view.browser._reading_cursor.position() > initial_position
    assert view.browser.verticalScrollBar().value() == scrollbar.minimum()
    assert view.browser.vi_rail_rect().top() > initial_marker_top

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


def test_reading_view_vi_slash_opens_and_focuses_search(qtbot, qapp) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown("Alpha\n\nBeta")
    view.set_vi_enabled(True)
    view.show()
    view.focus_reader()
    qapp.processEvents()

    assert view.find_bar.isHidden()
    QTest.keyClick(view.browser, Qt.Key_Slash)
    qapp.processEvents()

    assert not view.find_bar.isHidden()
    assert view.find_query.hasFocus()


def test_reading_vi_cursor_crosses_a_tall_image_smoothly(
    qtbot, qapp, tmp_path: Path
) -> None:
    image_path = tmp_path / "tall.png"
    image = QImage(493, 364, QImage.Format_RGB32)
    image.fill(QColor("#4488aa"))
    assert image.save(str(image_path))

    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(420, 220)
    view.set_markdown(
        "Before\n\n![image](tall.png)\n\nAfter",
        base_directory=tmp_path,
    )
    view.set_vi_enabled(True)
    view.show()
    qapp.processEvents()
    qapp.processEvents()
    QTest.qWait(view.ZOOM_DEBOUNCE_MS + 30)
    assert view.browser.horizontalScrollBar().maximum() == 0

    QTest.keyClick(view.browser, Qt.Key_J)
    assert "\ufffc" in view.browser._reading_cursor.block().text()
    QTest.keyClick(view.browser, Qt.Key_J)
    QTest.qWait(240)
    assert "After" in view.browser._reading_cursor.block().text()
    assert view.browser.verticalScrollBar().value() > 0


def test_reading_images_reflow_with_the_content_column(
    qtbot, qapp, tmp_path: Path
) -> None:
    image_path = tmp_path / "responsive.svg"
    image_path.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="600" '
        'viewBox="0 0 1200 600"><rect width="1200" height="600" '
        'fill="#4488aa"/></svg>',
        encoding="utf-8",
    )
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(420, 300)
    view.show()
    view.set_markdown(
        "Before\n\n![diagram](responsive.svg)\n\nAfter",
        base_directory=tmp_path,
    )
    qapp.processEvents()
    qapp.processEvents()

    def image_dimensions() -> tuple[float, float]:
        block = view.browser.document().begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid() and fragment.charFormat().isImageFormat():
                    image_format = fragment.charFormat().toImageFormat()
                    return image_format.width(), image_format.height()
                iterator += 1
            block = block.next()
        raise AssertionError("Rendered image was not found")

    narrow_width, narrow_height = image_dimensions()
    assert narrow_width == pytest.approx(view.browser.viewport().width() - 80)
    assert narrow_height == pytest.approx(narrow_width / 2)

    view.resize(700, 300)
    QTest.qWait(view.ZOOM_DEBOUNCE_MS + 30)
    qapp.processEvents()
    wide_width, wide_height = image_dimensions()
    assert wide_width > narrow_width
    assert wide_width == pytest.approx(view.browser.viewport().width() - 80)
    assert wide_height == pytest.approx(wide_width / 2)


def test_reading_view_defaults_white_and_can_persist_theme_colors(
    qtbot, monkeypatch
) -> None:
    import sp.app.ui.markdown_preview as preview_module

    state = {"enabled": False}
    values = {
        "markdown_preview.paper_bg": "#fdfaf2",
        "markdown_preview.text": "#201a16",
        "markdown_preview.table_header_bg": "#ddd2bf",
    }
    monkeypatch.setattr(
        preview_module.config,
        "load_reading_view_use_theme_colors",
        lambda: state["enabled"],
    )
    monkeypatch.setattr(
        preview_module.config,
        "save_reading_view_use_theme_colors",
        lambda enabled: state.__setitem__("enabled", enabled),
    )
    monkeypatch.setattr(
        preview_module,
        "theme_value",
        lambda path, default=None: values.get(path, default),
    )
    view = MarkdownReadingView()
    qtbot.addWidget(view)

    assert not view.theme_toggle.isChecked()
    assert "background: #ffffff" in view._css
    assert "background: #fdfaf2" not in view._css

    view.theme_toggle.click()

    assert state["enabled"] is True
    assert view.theme_toggle.isChecked()
    assert "background: #fdfaf2" in view._css
    assert "font-size: 24.00pt" in view._css
    assert "border: 2px solid" in view._css
    assert "background: #ddd2bf" in view._css
    assert "margin-top: 0.2em" in view._css
    assert "table.reading-rule" in view._css

    restored = MarkdownReadingView()
    qtbot.addWidget(restored)
    assert restored.theme_toggle.isChecked()
    assert "background: #fdfaf2" in restored._css


def test_reading_view_theme_mode_uses_live_palette_when_preview_is_stock(
    qtbot, monkeypatch
) -> None:
    import sp.app.ui.markdown_preview as preview_module

    monkeypatch.setattr(
        preview_module.config,
        "load_reading_view_use_theme_colors",
        lambda: False,
    )
    monkeypatch.setattr(
        preview_module.config,
        "save_reading_view_use_theme_colors",
        lambda _enabled: None,
    )
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    dark = QPalette(view.palette())
    dark.setColor(QPalette.Base, QColor("#101826"))
    dark.setColor(QPalette.Text, QColor("#e8edf5"))
    dark.setColor(QPalette.AlternateBase, QColor("#172235"))
    dark.setColor(QPalette.Mid, QColor("#52627a"))
    dark.setColor(QPalette.Link, QColor("#77b7ff"))
    view.setPalette(dark)

    view.theme_toggle.click()

    assert "background: #101826" in view._css
    assert "color: #e8edf5" in view._css
    assert "background: #172235" in view._css


def test_reading_view_materializes_checkbox_glyphs(qtbot) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.set_markdown("[ ] Open item\n[x] Completed item")

    rendered = view.browser.toPlainText()
    assert "☐ Open item" in rendered
    assert "☑ Completed item" in rendered


def test_reading_view_zoom_scales_all_document_typography(qtbot, qapp) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(640, 480)
    view.show()
    view.set_markdown(
        "# Heading\n\nParagraph\n\n| Column |\n| --- |\n| Cell |\n\n`Code`"
    )

    def sizes() -> dict[str, float]:
        wanted = {"Heading", "Paragraph", "Column", "Cell", "Code"}
        result: dict[str, float] = {}
        block = view.browser.document().begin()
        while block.isValid():
            text = block.text().replace("\u200b", "").strip()
            if text in wanted:
                result[text] = block.charFormat().fontPointSize()
            block = block.next()
        return result

    before = sizes()
    assert before["Column"] == pytest.approx(before["Paragraph"])
    assert before["Cell"] == pytest.approx(before["Paragraph"])
    assert before["Code"] == pytest.approx(before["Paragraph"])
    view.zoom_in(2)
    QTest.qWait(view.ZOOM_DEBOUNCE_MS + 30)
    qapp.processEvents()
    after = sizes()

    assert before.keys() == after.keys()
    for label in before:
        assert after[label] > before[label]
        assert after[label] / before[label] == pytest.approx(1.21, rel=0.03)


def test_reading_view_zoom_debounces_expensive_renders(
    qtbot, qapp, monkeypatch
) -> None:
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.resize(640, 480)
    view.set_markdown("# Heading\n\nParagraph")
    view.show()
    qapp.processEvents()
    original_set_markdown = view.set_markdown
    renders: list[int] = []

    def record_render(*args, **kwargs):
        renders.append(view._zoom_steps)
        return original_set_markdown(*args, **kwargs)

    monkeypatch.setattr(view, "set_markdown", record_render)
    view.zoom_in()
    view.zoom_in()
    view.zoom_in()

    assert renders == []
    QTest.qWait(view.ZOOM_DEBOUNCE_MS + 30)
    qapp.processEvents()
    assert renders == [3]


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
    initial_reading_zoom = main_window.reading_view._zoom_steps
    initial_editor_size = main_window.font_size
    main_window._adjust_markdown_font_size(1)
    assert main_window.reading_view._zoom_steps == initial_reading_zoom
    main_window._adjust_markdown_font_size(-1)
    assert main_window.font_size == initial_editor_size

    main_window._action_reading_view.trigger()

    assert QKeySequence("Ctrl+Space") in {
        shortcut.key() for shortcut in main_window._reading_view_shortcuts
    }
    assert main_window._action_reading_view in main_window.toolbar.actions()
    assert main_window._reading_view_enabled
    assert main_window._editor_view_stack.currentWidget() is main_window.reading_view
    assert "<table>" in main_window.reading_view.last_html
    editor_size_in_reading_view = main_window.font_size
    main_window._adjust_reading_view_zoom(1)
    assert main_window.font_size == editor_size_in_reading_view
    assert main_window.reading_view._zoom_steps == initial_reading_zoom + 1
    main_window._adjust_reading_view_zoom(-1)

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
        assert window.active_tab().editor._primary_wheel_zoom_filters
        assert window.active_tab().reading_view.browser._primary_wheel_zoom_filters
        assert QKeySequence("Ctrl+Space") in {
            shortcut.key() for shortcut in window.active_tab().reading_view_shortcuts
        }
        initial_editor_zoom = window.editor_zoom_steps
        initial_reading_zoom = window.reading_zoom_steps
        window._zoom_active_view(1)
        assert window.editor_zoom_steps == initial_editor_zoom + 1
        assert window.reading_zoom_steps == initial_reading_zoom
        assert window.active_tab().reading_view._zoom_steps == initial_reading_zoom
        window.reading_view_action.trigger()

        assert window.active_tab().reading_view_enabled
        assert window.reading_view_action.isChecked()
        assert "Alpha" in window.active_tab().reading_view.browser.toPlainText()
        editor_zoom_in_reading_view = window.editor_zoom_steps
        window._zoom_active_view(1)
        assert window.editor_zoom_steps == editor_zoom_in_reading_view
        assert window.reading_zoom_steps == initial_reading_zoom + 1
        assert window.active_tab().reading_view._zoom_steps == initial_reading_zoom + 1
        window.active_tab().reading_view.edit_button.click()
        assert not window.reading_view_action.isChecked()
    finally:
        window.close()
