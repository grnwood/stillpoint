from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPalette, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from sp.app import config
from sp.app.folder_navigator.core import read_text
from sp.app.folder_navigator.window import Tab, Window
from sp.app.ui.markdown_preview import MarkdownReadingView
from sp.app.ui.reading_options import reading_source_blocks


@pytest.fixture(autouse=True)
def isolated_reading_options(monkeypatch, request):
    if request.node.name == "test_reading_options_validation_and_persistence":
        return
    monkeypatch.setattr(config, "load_reading_options", lambda: config.normalize_reading_options({}))
    monkeypatch.setattr(config, "save_reading_options", lambda options: None)


def test_reading_options_validation_and_persistence(monkeypatch):
    monkeypatch.setattr(config, "_read_global_config", lambda: {
        "reading_options": {"width": "unknown", "size": "not a size", "spacing": None}
    })
    assert config.load_reading_options() == {"width": "comfortable", "size": 100, "spacing": "normal", "stripe_tables": True}
    updates = []
    monkeypatch.setattr(config, "_update_global_config", updates.append)
    config.save_reading_options({"width": "full", "size": 999, "spacing": "relaxed", "stripe_tables": False})
    assert updates == [{"reading_options": {"width": "full", "size": 200, "spacing": "relaxed", "stripe_tables": False}}]


def test_copy_source_ranges_preserve_fences_spacing_and_escaped_pipes():
    code = '````python\ntext = "```"\n\nprint(text)  \n````\n'
    table = '| Name | Value |\n| :--- | ---: |\n| A \\| B | `x` |\n'
    blocks = reading_source_blocks('# Heading\n\n' + code + '\n' + table)
    assert [(block.kind, block.markdown) for block in blocks] == [("code", code), ("table", table)]
    single_column = '| Only |\n| --- |\n| One |\n'
    assert reading_source_blocks(single_column)[0].markdown == single_column


def test_reading_options_resize_without_touching_editor_and_sync(qtbot, qapp, tmp_path):
    path = tmp_path / "notes.md"
    path.write_text("# Notes\n\n" + "Some text. " * 100)
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(1600, 550)
    tab.show()
    tab.set_reading_view(True)
    QTest.qWait(40)
    view = tab.reading_view
    before = tab.editor.font().pointSizeF()
    other = MarkdownReadingView()
    qtbot.addWidget(other)
    view.set_reading_options({"width": "narrow", "size": 170, "spacing": "relaxed"})
    QTest.qWait(40)
    narrow = view.browser.width()
    assert view.browser.x() > 0
    assert other.reading_options == view.reading_options
    assert tab.editor.font().pointSizeF() == before
    assert f"font-size: {before * 1.7:.2f}pt" in view._css
    view.set_reading_options({"width": "full", "size": 170, "spacing": "compact"})
    QTest.qWait(40)
    assert view.browser.width() > narrow
    assert view.browser.width() >= view.width() - 10
    assert tab.editor.font().pointSizeF() == before


def test_reading_options_popup_controls_and_reset(qtbot, qapp):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    view.show()
    view.show_reading_options()
    popup = view.options_popup
    popup.width_choice.setCurrentIndex(popup.width_choice.findData("wide"))
    popup.larger.click()
    popup.spacing_choice.setCurrentIndex(popup.spacing_choice.findData("relaxed"))
    popup.stripes.click()
    assert view.reading_options == {"width": "wide", "size": 110, "spacing": "relaxed", "stripe_tables": False}
    QTest.keyClick(popup.width_choice, Qt.Key_Escape)
    qapp.processEvents()
    assert not popup.isVisible()


def test_copy_buttons_copy_exact_markdown_and_acknowledge(qtbot, qapp):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    code = '```python\nprint("hello")\n\nprint("goodbye")\n```\n'
    table = '| Name | Value |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n'
    view.set_markdown('# Example\n\n' + code + '\n' + table)
    view.resize(850, 700)
    view.show()
    QTest.qWait(40)
    assert len(view._copy_buttons) == 2
    for (button, _position, _kind), expected in zip(view._copy_buttons, (code, table)):
        before = view.browser.verticalScrollBar().value()
        button.click()
        assert QApplication.clipboard().text() == expected
        assert button.text() == "Copied"
        assert view.browser.verticalScrollBar().value() == before
    QTest.qWait(1550)
    assert all(button.text() == "" for button, _, _ in view._copy_buttons)
    assert "sp-source" not in view.browser.toPlainText()


def _icon_ink(icon, state=QIcon.Off):
    image = icon.pixmap(QSize(16, 16), QIcon.Normal, state).toImage()
    return [image.pixelColor(x, y).lightness() for x in range(image.width())
            for y in range(image.height()) if image.pixelColor(x, y).alpha() > 100]


def test_focus_checked_icon_is_dark_on_light_checked_background(qtbot, monkeypatch):
    monkeypatch.setattr(config, "load_reading_view_use_theme_colors", lambda: False)
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    palette = QPalette(view.palette())
    palette.setColor(QPalette.Text, QColor("#f4f7fb"))
    palette.setColor(QPalette.Base, QColor("#320a18"))
    view.set_theme_palette(palette)
    target = QWidget(view)
    view.configure_focus_mode(lambda: [target])
    view.focus_button.click()
    assert view.focus_button.isChecked()
    assert view._focus_checked_background.lightness() > 128
    assert _icon_ink(view.focus_button.icon(), QIcon.On)
    assert max(_icon_ink(view.focus_button.icon(), QIcon.On)) < 128
    assert min(_icon_ink(view.focus_button.icon(), QIcon.Off)) > 128


@pytest.mark.parametrize("kind", ["table", "code"])
@pytest.mark.parametrize("dark", [False, True])
def test_copy_control_contrasts_with_paper_and_stays_visible_mid_block(
    qtbot, qapp, monkeypatch, kind, dark
):
    monkeypatch.setattr(config, "load_reading_view_use_theme_colors", lambda: dark)
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    palette = QPalette(view.palette())
    palette.setColor(QPalette.Text, QColor("#f4f7fb"))
    palette.setColor(QPalette.Base, QColor("#320a18"))
    view.set_theme_palette(palette)
    source = (
        '| Name | Value |\n| --- | --- |\n' + ''.join(f'| Row {i} | Value {i} |\n' for i in range(100))
        if kind == "table" else '```python\n' + ''.join(f'print({i})\n' for i in range(100)) + '```\n'
    )
    view.resize(850, 400)
    view.show()
    view.set_markdown(source)
    QTest.qWait(40)
    assert len(view._copy_buttons) == 1
    button = view._copy_buttons[0][0]
    ink = _icon_ink(button.icon())
    assert ink
    assert min(ink) > 128 if dark else max(ink) < 128
    bar = view.browser.verticalScrollBar()
    bar.setValue(bar.maximum() // 2)
    QTest.qWait(40)
    assert button.isVisible()
    assert view.browser.viewport().rect().contains(button.geometry())
    button.click()
    assert QApplication.clipboard().text() == source
    assert button.text() == "Copied"
    assert button.isVisible()


def test_alternate_table_rows_are_subtle_and_toggle_off(qtbot):
    view = MarkdownReadingView()
    qtbot.addWidget(view)
    source = '| Name | Value |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n'
    view.set_markdown(source)
    table = view.browser.document().rootFrame().childFrames()[0]
    stripe = table.cellAt(2, 0).format().background().color()
    assert stripe != view._paper_color
    assert abs(stripe.lightness() - view._paper_color.lightness()) < 25
    view._apply_reading_options(config.normalize_reading_options({"stripe_tables": False}))
    table = view.browser.document().rootFrame().childFrames()[0]
    assert table.cellAt(2, 0).format().background().style() == Qt.NoBrush


@pytest.mark.parametrize("width,size,spacing", [("narrow", 160, "relaxed"), ("full", 80, "compact")])
@pytest.mark.parametrize("kind", ["table", "code"])
def test_folder_handoff_maps_visible_table_rows_and_code_lines_with_independent_sizing(
    qtbot, qapp, tmp_path, width, size, spacing, kind
):
    prefix = '\n\n'.join(f'Paragraph {i}: ' + 'words ' * 20 for i in range(30))
    content = (
        '| Name | Value |\n| --- | --- |\n' + ''.join(f'| Row {i} | Value {i} |\n' for i in range(20))
        if kind == "table" else '```python\n' + ''.join(f'print("line {i}")\n' for i in range(20)) + '```\n'
    )
    markdown = prefix + '\n\n' + content + '\n' + prefix
    path = tmp_path / "position.md"
    path.write_text(markdown)
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(1000, 600)
    tab.show()
    tab.editor.set_font_point_size(14)
    tab.reading_view._apply_reading_options(config.normalize_reading_options({"width": width, "size": size, "spacing": spacing}))
    QTest.qWait(40)
    view = tab.reading_view
    tab.set_reading_view(True)
    QTest.qWait(40)
    source = next(block for block in view._source_blocks if block.kind == kind)
    target_line = source.line + 10
    view.scroll_to_source_line(target_line)
    QTest.qWait(40)
    reading_line, screen_y = view.reading_view_position()
    assert reading_line == target_line
    tab.set_reading_view(False)
    qapp.processEvents()
    assert tab.editor.textCursor().blockNumber() + 1 == target_line
    editor_y = tab.editor.viewport().mapToGlobal(tab.editor.cursorRect().topLeft()).y()
    assert abs(editor_y - screen_y) <= 2
    # The reverse trip must find the same source row with a different layout.
    tab.set_reading_view(True)
    QTest.qWait(40)
    line, returned_y = view.reading_view_position()
    assert line == target_line
    assert abs(returned_y - editor_y) <= 2
    assert tab.editor.font().pointSizeF() == 14


def test_folder_focus_escape_restores_layout_and_toc_navigates(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: True)
    path = tmp_path / "outline.md"
    path.write_text('\n\n'.join(f'## Heading {i}\n\nText ' + 'words ' * 25 for i in range(25)))
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.resize(1200, 700)
    window.show()
    window.open_file(path, pinned=True)
    tab = window.active_tab()
    tab.set_reading_view(True)
    QTest.qWait(50)
    view = tab.reading_view
    assert view.toc_widget.tree.topLevelItemCount() == 25
    view.toc_widget.set_collapsed(False)
    item = view.toc_widget.tree.topLevelItem(10)
    line = item.data(0, Qt.UserRole)["line"]
    view.toc_widget._on_item_activated(item)
    QTest.qWait(40)
    assert view.current_source_line() == line
    sizes = window.splitter.sizes()
    visible = [not widget.isHidden() for widget in (window.folder_rail_container, window.bookmarks_container, window.tabs.tabBar())]
    view.focus_button.click()
    QTest.qWait(40)
    assert window.folder_rail_container.isHidden()
    assert window.bookmarks_container.isHidden()
    assert window.tabs.tabBar().isHidden()
    assert view.focus_button.isChecked()
    view.show_find()
    QTest.keyClick(view.find_query, Qt.Key_Escape)
    assert not view.find_bar.isVisible()
    assert view.focus_button.isChecked()
    QTest.keyClick(view.browser, Qt.Key_Escape)
    QTest.qWait(40)
    assert not view.focus_button.isChecked()
    assert [not widget.isHidden() for widget in (window.folder_rail_container, window.bookmarks_container, window.tabs.tabBar())] == visible
    assert window.splitter.sizes() == sizes


def test_mouse_scrolling_table_without_vi_preserves_source_on_return(qtbot, qapp, tmp_path):
    prefix = '\n\n'.join(f'Paragraph {i}' for i in range(30))
    table = '| Name | Value |\n| --- | --- |\n' + ''.join(f'| Row {i} | Value {i} |\n' for i in range(50))
    path = tmp_path / "scroll.md"
    path.write_text(prefix + '\n\n' + table + '\n' + prefix)
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(1000, 500)
    tab.show()
    view = tab.reading_view
    view._apply_reading_options(config.normalize_reading_options({"width": "wide", "size": 180, "spacing": "relaxed"}))
    tab.set_reading_view(True)
    view.set_vi_enabled(False)
    QTest.qWait(40)
    source = next(block for block in view._source_blocks if block.kind == "table")
    cursor = QTextCursor(view.browser.document())
    cursor.setPosition(view._source_line_positions[source.line + 25])
    bar = view.browser.verticalScrollBar()
    bar.setValue(bar.value() + view.browser.cursorRect(cursor).top() - 80)
    QTest.qWait(40)
    line, screen_y = view.reading_view_position()
    assert source.line < line < source.end_line
    tab.set_reading_view(False)
    assert tab.editor.textCursor().blockNumber() + 1 == line
    assert abs(tab.editor.viewport().mapToGlobal(tab.editor.cursorRect().topLeft()).y() - screen_y) <= 2


def test_main_reader_options_focus_and_handoff(main_window, qapp):
    window = main_window
    window.current_path = "/reading.md"
    window.editor.set_markdown('\n\n'.join(f'## Heading {i}\n\nParagraph ' + 'words ' * 30 for i in range(50)))
    window._set_reading_view_enabled(True)
    QTest.qWait(40)
    view = window.reading_view
    assert view.options_button.parent() is window._editor_status
    view._apply_reading_options(config.normalize_reading_options({"width": "narrow", "size": 160, "spacing": "relaxed"}))
    QTest.qWait(40)
    view.scroll_to_source_line(81)
    QTest.qWait(40)
    line, screen_y = view.reading_view_position()
    window._set_reading_view_enabled(False)
    assert window.editor.textCursor().blockNumber() + 1 == line
    assert abs(window.editor.viewport().mapToGlobal(window.editor.cursorRect().topLeft()).y() - screen_y) <= 2
    window._set_reading_view_enabled(True)
    QTest.qWait(40)
    window._toggle_mode_overlay("focus")
    assert window.left_panel_container.isHidden()
    assert window.toolbar.isHidden()
    QTest.keyClick(view.browser, Qt.Key_Escape)
    assert not view.focus_button.isChecked()
    assert not window.toolbar.isHidden()


def test_folder_editor_toc_navigates_copies_updates_and_follows_reading_mode(
    qtbot, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(config, "GLOBAL_CONFIG", tmp_path / "preferences.json")
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: True)
    path = tmp_path / "outline.md"
    path.write_text('\n\n'.join(f'## Heading {i}\n\nText ' + 'words ' * 25 for i in range(25)))
    window = Window(tmp_path)
    qtbot.addWidget(window)
    window.resize(1200, 700)
    window.show()
    window.open_file(path, pinned=True)
    QTest.qWait(60)
    tab = window.active_tab()
    toc = tab.toc_widget
    assert not tab.reading_view_enabled
    assert toc.isVisible()
    assert toc.tree.topLevelItemCount() == 25
    toc.set_collapsed(False)
    item = toc.tree.topLevelItem(10)
    data = item.data(0, Qt.UserRole)
    toc._on_item_activated(item)
    assert tab.editor.textCursor().position() == data["position"]
    assert abs(tab.editor.cursorRect().top() - tab.editor.viewport().height() // 4) <= 2
    toc._copy_link(data)
    assert QApplication.clipboard().text() == path.as_uri() + "#heading-10"

    window._set_folder_identity_accent("#b51b42")
    assert window.palette().color(QPalette.AlternateBase).name() in toc.styleSheet()
    toc.set_collapsed(True)
    tab.set_reading_view(True)
    QTest.qWait(40)
    assert not toc.isVisible()
    assert tab.reading_view.toc_widget.collapsed()
    tab.reading_view.toc_widget.set_collapsed(False)
    tab.set_reading_view(False)
    QTest.qWait(40)
    assert toc.isVisible()
    assert not toc.collapsed()

    tab.editor.set_markdown(path.read_text().replace("Heading 0", "Renamed heading"))
    tab.editor.refresh_heading_outline()
    assert toc.tree.topLevelItem(0).text(0) == "Renamed heading"
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: False)
    tab._position_editor_toc()
    assert not toc.isVisible()
    tab.editor.document().setModified(False)


@pytest.mark.parametrize("source", ["No headings", "# Single heading\n\n" + "Text\n\n" * 100, "# One\n\n## Two"])
def test_folder_editor_toc_hides_without_scrollable_heading_outline(qtbot, tmp_path, monkeypatch, source):
    monkeypatch.setattr(config, "load_toc_widget_enabled", lambda: True)
    path = tmp_path / "short.md"
    path.write_text(source)
    tab = Tab(path, read_text(path), markdown=True, root=tmp_path)
    qtbot.addWidget(tab)
    tab.resize(850, 500)
    tab.show()
    QTest.qWait(40)
    tab.editor.refresh_heading_outline()
    assert not tab.toc_widget.isVisible()
