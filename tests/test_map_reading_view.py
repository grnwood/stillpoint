import pytest
from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QTextTable
from PySide6.QtTest import QTest

from sp.app import config
from sp.app.ui.map_panel import MapPanel


@pytest.fixture
def panel(qtbot, monkeypatch):
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: False)
    widget = MapPanel()
    widget.set_window_mode_control(visible=True)
    widget.resize(1000, 700)
    qtbot.addWidget(widget)
    widget.set_content("/Page.md", "# Root\n\n## Details\n\n| Name | Value |\n| --- | --- |\n| A | 42 |\n\n### Child\nKeep child content.\n")
    node = next(node for node in widget._collect_nodes(widget._latest_root) if node.label == "Details")
    widget._set_selected_node(node)
    return widget


def has_table(frame):
    return isinstance(frame, QTextTable) or any(has_table(child) for child in frame.childFrames())


def test_enter_opens_rendered_table_and_second_enter_edits(panel):
    original = panel._current_markdown
    QTest.keyClick(panel.preview_label, Qt.Key_Return)
    tooltip = panel._content_tooltip
    QTest.qWait(20)
    assert tooltip.isVisible()
    assert has_table(tooltip._editor.document().rootFrame())
    assert panel._body_editor is None
    assert panel._current_markdown == original
    QTest.keyClick(tooltip._editor, Qt.Key_Return)
    popup = panel._body_editor
    assert popup is not None and popup.isVisible()
    assert not tooltip.isVisible()
    assert "| A | 42 |" in popup.editor.to_markdown()
    assert "Child" not in popup.editor.to_markdown()


def test_plain_node_click_opens_reader_without_activating_main_editor(panel):
    activations = []
    panel.headingActivated.connect(lambda *args: activations.append(args))
    node = panel._selected_node()
    x, y, width, height = panel._node_hitboxes[node.node_id]
    ox, oy = panel._preview_pixmap_offset()
    point = QPoint(round(ox + (x + width / 2) * panel._zoom_factor),
                   round(oy + (y + height / 2) * panel._zoom_factor))
    QTest.mouseClick(panel.preview_label, Qt.LeftButton, pos=point)
    assert panel._content_tooltip.isVisible()
    assert activations == []
    assert panel._body_editor is None


def test_double_click_reading_popup_enters_edit_mode(panel):
    panel._show_selected_node_note_popup()
    browser = panel._content_tooltip._editor
    QTest.mouseDClick(browser.viewport(), Qt.LeftButton, pos=QPoint(30, 30))
    assert panel._body_editor is not None
    assert panel._body_editor.editor.isVisible()


@pytest.mark.parametrize("modifier", [Qt.ControlModifier, Qt.MetaModifier])
def test_ctrl_cmd_space_toggles_reading_editing_without_losing_draft(panel, modifier):
    panel._show_selected_node_note_popup()
    QTest.keyClick(panel._content_tooltip._editor, Qt.Key_Space, modifier)
    popup = panel._body_editor
    assert popup is not None
    draft = "Draft words\n\n| Name | Value |\n| --- | --- |\n| B | 99 |\n"
    popup.editor.set_markdown(draft)
    before = popup.editor.to_markdown()
    original = panel._current_markdown
    QTest.keyClick(popup.editor, Qt.Key_Space, modifier)
    QTest.qWait(20)
    assert popup._reading_view_enabled
    assert has_table(popup.reading_view.browser.document().rootFrame())
    assert "Draft words" in popup.reading_view.browser.toPlainText()
    QTest.keyClick(popup.reading_view.browser, Qt.Key_Space, modifier)
    assert not popup._reading_view_enabled
    assert popup.editor.to_markdown() == before
    assert panel._current_markdown == original
    QTest.keyClick(popup.editor, Qt.Key_Return, Qt.ControlModifier)
    assert panel._body_editor is None
    assert "Draft words" in panel._current_markdown
    assert "Keep child content." in panel._current_markdown


@pytest.mark.parametrize("trigger", ["enter", "double_click"])
def test_draft_reading_view_can_return_to_editing(panel, trigger):
    panel._edit_selected_body()
    popup = panel._body_editor
    popup.set_reading_view(True)
    if trigger == "enter":
        QTest.keyClick(popup.reading_view.browser, Qt.Key_Return)
    else:
        QTest.mouseDClick(popup.reading_view.browser.viewport(), Qt.LeftButton, pos=QPoint(30, 30))
    assert not popup._reading_view_enabled
    assert popup.editor.isVisible()


def test_enter_from_reader_starts_vi_insert_mode(panel, monkeypatch):
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    panel._show_selected_node_note_popup()
    QTest.keyClick(panel._content_tooltip._editor, Qt.Key_Return)
    popup = panel._body_editor
    assert popup.editor._vi_insert_mode
    QTest.keyClicks(popup.editor, "Added ")
    assert "Added " in popup.editor.toPlainText()


@pytest.mark.parametrize("bounds", [QRect(0, 0, 1200, 800), QRect(0, 0, 480, 260)])
def test_read_and_edit_popups_keep_same_geometry_and_border(panel, monkeypatch, bounds):
    monkeypatch.setattr("sp.app.ui.map_panel.popup_available_geometry", lambda **kwargs: bounds)
    panel._show_selected_node_note_popup()
    reader = panel._content_tooltip
    QTest.qWait(20)
    geometry = reader.geometry()
    assert bounds.contains(reader.frameGeometry())
    QTest.keyClick(reader._editor, Qt.Key_Return)
    popup = panel._body_editor
    assert popup.geometry() == geometry
    assert popup.windowFlags() & Qt.FramelessWindowHint
    assert popup.layout().contentsMargins() == reader.layout().contentsMargins()
    assert popup.styleSheet() == reader.styleSheet().replace("mapContentTooltip", "mapBodyEditor")
    assert popup.font_smaller_button.isVisible()
    assert popup.font_larger_button.isVisible()


def test_read_and_edit_font_buttons_persist_separate_sizes(qtbot, monkeypatch):
    state = {"map_read_font_size": 12, "map_edit_font_size": 15}
    monkeypatch.setattr(config, "_read_global_config", lambda: state)
    monkeypatch.setattr(config, "_update_global_config", state.update)
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: False)

    def create():
        widget = MapPanel()
        widget.set_window_mode_control(visible=True)
        qtbot.addWidget(widget)
        widget.set_content("/Page.md", "# Root\n\n## Details\nBody content.\n")
        node = next(node for node in widget._collect_nodes(widget._latest_root) if node.label == "Details")
        widget._set_selected_node(node)
        return widget

    widget = create()
    widget._show_selected_node_note_popup()
    reader = widget._content_tooltip
    original = widget._current_markdown
    canvas_zoom = widget._zoom_factor
    reader.font_larger_button.click()
    assert state["map_read_font_size"] == 13
    assert state["map_edit_font_size"] == 15
    assert reader.reading_view._base_font_point_size == 13
    reader.font_smaller_button.click()
    assert state["map_read_font_size"] == 12
    widget._edit_selected_body()
    popup = widget._body_editor
    assert popup.editor.font().pointSize() == 15
    draft = popup.editor.to_markdown()
    popup.font_larger_button.click()
    assert popup.editor.font().pointSize() == 16
    assert state["map_edit_font_size"] == 16
    popup.set_reading_view(True)
    assert popup.reading_view._base_font_point_size == 12
    popup.font_larger_button.click()
    assert state["map_read_font_size"] == 13
    assert popup.reading_view._base_font_point_size == 13
    assert popup.editor.font().pointSize() == 16
    popup.set_reading_view(False)
    assert popup.editor.to_markdown() == draft
    assert widget._current_markdown == original
    assert widget._zoom_factor == canvas_zoom
    popup.reject()
    widget.close()
    restored = create()
    restored._show_selected_node_note_popup()
    assert restored._content_tooltip.reading_view._base_font_point_size == 13
    restored._edit_selected_body()
    assert restored._body_editor.editor.font().pointSize() == 16


def test_map_content_font_size_preferences_validate_and_clamp(monkeypatch):
    state = {"map_read_font_size": "invalid", "map_edit_font_size": 100}
    monkeypatch.setattr(config, "_read_global_config", lambda: state)
    monkeypatch.setattr(config, "_update_global_config", state.update)
    assert config.load_map_content_font_size("read", 12) == 12
    assert config.load_map_content_font_size("edit") == 36
    config.save_map_content_font_size("edit", -1)
    assert state["map_edit_font_size"] == 8
    with pytest.raises(ValueError):
        config.save_map_content_font_size("other", 12)


def test_hover_preview_and_clicked_preview_reuse_content_controls_and_position(panel, monkeypatch):
    node = panel._selected_node()
    panel._content_preview_enabled = True
    panel._schedule_hover_tooltip(node, panel._selected_node_popup_anchor())
    panel._show_hover_tooltip()
    reader = panel._content_tooltip
    geometry = reader.geometry()
    html = reader.reading_view.last_html
    assert reader._close_btn.isVisible()
    assert reader._edit_hint.isVisible()
    assert reader.header.isVisible()
    assert reader.header.mode.text() == "Reading"
    assert reader.header.title.toolTip() == "Map note · Details"
    assert reader._edit_hint.y() > reader.reading_view.y()
    monkeypatch.setattr(reader, "show_markdown", lambda *args, **kwargs: pytest.fail("reopened hover preview"))
    x, y, width, height = panel._node_hitboxes[node.node_id]
    ox, oy = panel._preview_pixmap_offset()
    point = QPoint(round(ox + (x + width / 2) * panel._zoom_factor),
                   round(oy + (y + height / 2) * panel._zoom_factor))
    QTest.mouseClick(panel.preview_label, Qt.LeftButton, pos=point)
    assert panel._content_tooltip is reader
    assert reader.isVisible()
    assert reader._is_pinned
    assert reader.geometry() == geometry
    assert reader.reading_view.last_html == html
    assert panel._selected_note_popup_active


def test_popup_header_stays_compact_with_long_node_title(panel):
    panel._show_selected_node_note_popup()
    reader = panel._content_tooltip
    title = "A very long heading with enough words to exceed the popup title bar " * 3
    reader.header.title.set_title(title)
    QTest.qWait(20)
    assert reader.header.title.toolTip() == "Map note · " + title
    assert reader.header.title.text() != reader.header.title.toolTip()
    assert reader.header.height() < 60
    panel._edit_selected_body()
    popup = panel._body_editor
    assert popup.header.mode.text() == "Editing"
    assert popup.header.title.toolTip() == "Map note · Details"
    popup.set_reading_view(True)
    assert popup.header.mode.text() == "Reading"


@pytest.mark.parametrize("trigger", ["enter", "click"])
@pytest.mark.parametrize("hover_enabled", [False, True])
def test_sidebar_navigates_without_hover_or_click_popups(panel, trigger, hover_enabled):
    panel.set_window_mode_control(visible=False)
    panel._content_preview_enabled = hover_enabled
    node = panel._selected_node()
    activations = []
    panel.headingActivated.connect(lambda *args: activations.append(args))
    panel._schedule_hover_tooltip(node, panel._selected_node_popup_anchor())
    assert not panel._tooltip_timer.isActive()
    panel._hovered_node_id = node.node_id
    panel._show_hover_tooltip()
    assert not panel._content_tooltip.isVisible()
    if trigger == "enter":
        QTest.keyClick(panel.preview_label, Qt.Key_Return)
    else:
        x, y, width, height = panel._node_hitboxes[node.node_id]
        ox, oy = panel._preview_pixmap_offset()
        point = QPoint(round(ox + (x + width / 2) * panel._zoom_factor),
                       round(oy + (y + height / 2) * panel._zoom_factor))
        QTest.mouseClick(panel.preview_label, Qt.LeftButton, pos=point)
    assert activations == [(panel._current_page_path, node.line_number)]
    assert not panel._content_tooltip.isVisible()
    assert not panel._edit_selected_body()
    assert panel._body_editor is None
    for button in (panel.note_toggle_btn, panel.note_font_smaller_btn, panel.note_font_larger_btn):
        assert not button.isVisible()


def test_returning_to_sidebar_closes_detached_popups(panel):
    original = panel._current_markdown
    panel._show_selected_node_note_popup()
    assert panel._content_tooltip.isVisible()
    panel.set_window_mode_control(visible=False)
    assert not panel._content_tooltip.isVisible()
    panel.set_window_mode_control(visible=True)
    panel._edit_selected_body()
    panel._body_editor.editor.insertPlainText("Unsaved draft")
    panel.set_window_mode_control(visible=False)
    assert panel._body_editor is None
    assert panel._current_markdown == original


@pytest.mark.parametrize("trigger", ["enter", "click"])
def test_sidebar_click_and_enter_scroll_main_editor(main_window, monkeypatch, trigger):
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    main_window.editor.setPlainText("# Root\n\n## Section\nBody text\n")
    right = main_window.right_panel
    if right.map_panel is None:
        right._add_map_tab()
    panel = right.map_panel
    right.tabs.setCurrentWidget(panel)
    panel.set_content(main_window.current_path, main_window.editor.toPlainText())
    node = next(node for node in panel._collect_nodes(panel._latest_root) if node.label == "Section")
    panel._set_selected_node(node)
    panel.preview_label.setFocus()
    if trigger == "enter":
        QTest.keyClick(panel.preview_label, Qt.Key_Return)
    else:
        x, y, width, height = panel._node_hitboxes[node.node_id]
        ox, oy = panel._preview_pixmap_offset()
        point = QPoint(round(ox + (x + width / 2) * panel._zoom_factor),
                       round(oy + (y + height / 2) * panel._zoom_factor))
        QTest.mouseClick(panel.preview_label, Qt.LeftButton, pos=point)
    QTest.qWait(50)
    assert main_window.editor.textCursor().blockNumber() == 2
    assert panel.preview_label.hasFocus()
    assert not panel._content_tooltip.isVisible()
    assert panel._body_editor is None
