import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QPalette, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QScrollArea

from sp.app.ui.map_panel import MapPanel, ZoomablePreviewLabel


def _node_by_label(panel: MapPanel, label: str):
    assert panel._latest_root is not None
    return next(node for node in panel._collect_nodes(panel._latest_root) if node.label == label)


def _wheel_event(*, pixel_x: int = 0, pixel_y: int = 0, angle_y: int = 0, modifiers=Qt.NoModifier) -> QWheelEvent:
    return QWheelEvent(
        QPointF(10, 10),
        QPointF(10, 10),
        QPoint(pixel_x, pixel_y),
        QPoint(0, angle_y),
        Qt.NoButton,
        modifiers,
        Qt.NoScrollPhase,
        False,
    )


def test_map_trackpad_pans_and_mouse_wheel_zooms(qtbot) -> None:
    area = QScrollArea()
    label = ZoomablePreviewLabel()
    label.resize(1000, 1000)
    area.setWidget(label)
    area.resize(200, 200)
    qtbot.addWidget(area)
    area.show()
    area.horizontalScrollBar().setValue(200)
    area.verticalScrollBar().setValue(200)
    zooms: list[float] = []
    label.zoomRequested.connect(lambda steps, _anchor: zooms.append(steps))

    label.wheelEvent(_wheel_event(pixel_x=-12, pixel_y=-30))

    assert area.horizontalScrollBar().value() == 212
    assert area.verticalScrollBar().value() == 230
    assert zooms == []

    label.wheelEvent(_wheel_event(angle_y=120))

    assert zooms == [1.0]


def test_map_scroll_area_yields_arrow_keys_to_preview(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    one = _node_by_label(panel, "One")
    two = _node_by_label(panel, "Two")
    panel._set_selected_node(one)

    assert panel.scroll_area.focusPolicy() == Qt.NoFocus
    assert panel.scroll_area.viewport().focusPolicy() == Qt.NoFocus
    assert panel.scroll_area.focusProxy() is panel.preview_label

    qapp.sendEvent(
        panel.preview_label,
        QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.KeypadModifier),
    )

    assert panel._selected_node_id == two.node_id


def test_detached_reorder_rebuilds_same_parent_siblings(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## One\nOne body\n\n## Two\nTwo body\n\n## Three\nThree body\n",
    )

    one = _node_by_label(panel, "One")
    two = _node_by_label(panel, "Two")

    panel._set_selected_node(two)
    assert panel._select_range(one, two)
    assert panel._move_selected_block(1)

    rebuilt_text, line_map = panel._rebuild_markdown_from_tree(
        panel._detached_session.root,  # type: ignore[union-attr]
        panel._detached_session.base_text,  # type: ignore[union-attr]
    )

    assert "## Three\nThree body\n\n## One\nOne body\n\n## Two\nTwo body\n" in rebuilt_text
    assert line_map[two.node_id] > line_map[one.node_id]
    assert not panel.accept_btn.isHidden()
    assert not panel.cancel_btn.isHidden()


def test_cancel_detached_changes_hides_action_buttons(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    two = _node_by_label(panel, "Two")
    panel._set_selected_node(two)

    assert panel._move_selected_block(-1)
    assert panel._detached_session is not None
    assert panel.cancel_detached_changes()
    assert panel._detached_session is None
    assert panel.accept_btn.isHidden()
    assert panel.cancel_btn.isHidden()


def test_indent_rewrites_heading_levels_for_subtree(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## One\n\n## Two\n\n### Two Child\nbody\n",
    )
    two = _node_by_label(panel, "Two")
    panel._set_selected_node(two)

    assert panel._indent_selected_block()
    rebuilt_text, _line_map = panel._rebuild_markdown_from_tree(
        panel._detached_session.root,  # type: ignore[union-attr]
        panel._detached_session.base_text,  # type: ignore[union-attr]
    )

    assert "## One\n\n### Two\n\n#### Two Child\nbody\n" in rebuilt_text


def test_drop_selected_block_onto_target_reparents_as_child(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## One\n\n## Two\n\n## Three\n",
    )
    three = _node_by_label(panel, "Three")
    one = _node_by_label(panel, "One")
    panel._set_selected_node(three)

    assert panel._drop_selected_block_onto(one)
    rebuilt_text, _line_map = panel._rebuild_markdown_from_tree(
        panel._detached_session.root,  # type: ignore[union-attr]
        panel._detached_session.base_text,  # type: ignore[union-attr]
    )

    assert "## One\n\n### Three\n" in rebuilt_text


def test_outdent_rewrites_heading_level_and_keeps_node(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## Parent\n\n### Child\nbody\n\n## Sibling\n",
    )
    child = _node_by_label(panel, "Child")
    panel._set_selected_node(child)

    assert panel._outdent_selected_block()
    rebuilt_text, _line_map = panel._rebuild_markdown_from_tree(
        panel._detached_session.root,  # type: ignore[union-attr]
        panel._detached_session.base_text,  # type: ignore[union-attr]
    )

    assert "## Parent\n\n## Child\nbody\n\n## Sibling\n" in rebuilt_text


def test_live_text_refresh_clears_detached_session(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    two = _node_by_label(panel, "Two")
    panel._set_selected_node(two)

    assert panel._move_selected_block(-1)
    assert panel._detached_session is not None

    panel.set_content("/Test.md", "# Root\n\n## Two\n\n## One\n\n## Three\n")

    assert panel._detached_session is None


def test_tooltip_section_uses_current_heading_section(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## Parent\nintro\n\n### Child\nbody\n",
    )
    parent = _node_by_label(panel, "Parent")
    section = panel._node_tooltip_markdown(parent)

    assert section == "## Parent\nintro\n\n### Child\nbody\n"


def test_enter_commits_detached_changes(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    two = _node_by_label(panel, "Two")
    panel._set_selected_node(two)

    assert panel._move_selected_block(-1)
    assert panel._detached_session is not None

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))

    assert panel._detached_session is None


def test_shift_u_and_shift_n_extend_selection(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n\n## Three\n")
    two = _node_by_label(panel, "Two")
    three = _node_by_label(panel, "Three")
    panel._set_selected_node(two)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_N, Qt.ShiftModifier))

    assert panel._selected_node_id == three.node_id
    assert panel._selected_node_ids == {two.node_id, three.node_id}

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_U, Qt.ShiftModifier))

    assert panel._selected_node_id == two.node_id
    assert panel._selected_node_ids == {two.node_id}


def test_page_without_headings_has_no_placeholder_child(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "plain text\nmore text\n")

    assert panel._latest_root is not None
    assert panel._latest_root.children == []


def test_collapsed_branches_do_not_reserve_vertical_space(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## One\n\n### One A\n\n### One B\n\n## Two\n",
    )

    assert panel._latest_root is not None
    root = panel._latest_root
    one = _node_by_label(panel, "One")
    two = _node_by_label(panel, "Two")

    assert [child.label for child in panel._visible_children(root)] == ["One", "Two"]
    assert panel._visible_children(one) == []
    collapsed_gap = two.y - one.y
    collapsed_height = one.subtree_height

    assert collapsed_height == pytest.approx(one.height)

    panel._toggle_node(one)

    one_expanded = _node_by_label(panel, "One")
    two_expanded = _node_by_label(panel, "Two")
    expanded_children = panel._visible_children(one_expanded)
    expanded_gap = two_expanded.y - one_expanded.y

    assert [child.label for child in expanded_children] == ["One A", "One B"]
    assert one_expanded.subtree_height > one_expanded.height
    assert expanded_gap > collapsed_gap


def test_alt_enter_starts_inline_rename(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))

    assert panel._inline_rename_node_id == parent.node_id
    assert panel._inline_rename_edit.text() == "Parent"


def test_left_key_closes_selected_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\nbody\n\n### Child\n")
    one = _node_by_label(panel, "One")
    panel._set_selected_node(one)

    panel._show_selected_node_note_popup()
    assert panel._selected_note_popup_active is True

    qapp.sendEvent(panel._content_tooltip._editor, QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))
    qapp.processEvents()

    assert panel._selected_note_popup_active is False
    assert panel._content_tooltip.isVisible() is False


def test_vi_l_closes_selected_note_popup(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\nbody\n\n### Child\n")
    one = _node_by_label(panel, "One")
    panel._set_selected_node(one)

    panel._show_selected_node_note_popup()
    assert panel._selected_note_popup_active is True

    qapp.sendEvent(panel._content_tooltip._editor, QKeyEvent(QEvent.KeyPress, Qt.Key_L, Qt.NoModifier))
    qapp.processEvents()

    assert panel._selected_note_popup_active is False
    assert panel._content_tooltip.isVisible() is False


def test_note_popup_escape_closes_selected_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel._show_selected_node_note_popup()
    assert panel._selected_note_popup_active is True

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))

    assert panel._selected_note_popup_active is False
    assert panel._content_tooltip.isVisible() is False


def test_down_arrow_scrolls_selected_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    long_body = "\n".join(f"line {idx}" for idx in range(40))
    panel.set_content("/Test.md", f"# Root\n\n## Parent\n{long_body}\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel._show_selected_node_note_popup()
    scrollbar = panel._content_tooltip._editor.verticalScrollBar()
    start = scrollbar.value()

    qapp.sendEvent(panel._content_tooltip._editor, QKeyEvent(QEvent.KeyPress, Qt.Key_Down, Qt.NoModifier))

    assert panel._selected_note_popup_active is True
    assert scrollbar.value() > start


def test_vi_j_scrolls_selected_note_popup_by_line(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    long_body = "\n".join(f"line {idx}" for idx in range(40))
    panel.set_content("/Test.md", f"# Root\n\n## Parent\n{long_body}\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel._show_selected_node_note_popup()
    scrollbar = panel._content_tooltip._editor.verticalScrollBar()
    start = scrollbar.value()
    step = max(1, scrollbar.singleStep())

    qapp.sendEvent(
        panel._content_tooltip._editor,
        QKeyEvent(QEvent.KeyPress, Qt.Key_J, Qt.NoModifier),
    )

    assert panel._selected_note_popup_active is True
    assert scrollbar.value() >= start + step


def test_ctrl_shift_j_scrolls_selected_note_popup_by_page_in_vi_mode(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Ctrl+Shift+J/K is the Windows/Linux vi-navigation chord; force that
    # platform so this test is deterministic regardless of the host OS
    # (macOS uses Meta+J/K instead - see test_keyboard_shortcuts.py).
    monkeypatch.setattr("sp.app.ui.keyboard_shortcuts.platform.system", lambda: "Linux")
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    long_body = "\n".join(f"line {idx}" for idx in range(40))
    panel.set_content("/Test.md", f"# Root\n\n## Parent\n{long_body}\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel._show_selected_node_note_popup()
    scrollbar = panel._content_tooltip._editor.verticalScrollBar()
    start = scrollbar.value()
    step = max(1, scrollbar.pageStep())

    qapp.sendEvent(
        panel._content_tooltip._editor,
        QKeyEvent(QEvent.KeyPress, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier),
    )

    assert panel._selected_note_popup_active is True
    assert scrollbar.value() >= start + step


def test_ctrl_enter_reveals_selected_heading_while_keeping_map_focus(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)
    activations: list[tuple[str, int]] = []
    panel.headingActivated.connect(lambda path, line: activations.append((path, line)))

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ControlModifier))

    assert activations == [("/Test.md", parent.line_number)]
    assert panel.consume_activation_source() == "keyboard_keep_panel"


def test_ctrl_space_toggles_selected_node_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nbody\n\n### Child\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier))

    assert panel._selected_note_popup_active is True
    assert panel._content_tooltip.isVisible() is True

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.ControlModifier))

    assert panel._selected_note_popup_active is False
    assert panel._content_tooltip.isVisible() is False


def test_space_toggles_selected_node(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n\n### Child\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    assert panel._scope_depth(parent) == 0

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))

    assert panel._scope_depth(parent) == 1

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))

    assert panel._scope_depth(parent) == 0


def test_space_on_root_toggles_h1_visibility(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    assert panel._latest_root is not None
    root = panel._latest_root
    panel._set_selected_node(root)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))

    assert root.node_id in panel._collapsed_node_ids
    assert panel._visible_children(root) == []

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))

    assert root.node_id not in panel._collapsed_node_ids
    assert [child.label for child in panel._visible_children(root)] == ["One", "Two"]


def test_note_popup_uses_map_theme_palette(qapp: QApplication) -> None:
    panel = MapPanel()
    expected = panel._editor_theme_palette().color(QPalette.Base).name()

    assert panel._content_tooltip.palette().color(QPalette.Base).name() == expected
    assert panel._content_tooltip._editor.palette().color(QPalette.Base).name() == expected


def test_f_key_calls_fit_map(qapp: QApplication, monkeypatch: pytest.MonkeyPatch) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n")
    called: list[bool] = []
    monkeypatch.setattr(panel, "fit_map", lambda: called.append(True))

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_F, Qt.NoModifier))

    assert called == [True]


def test_right_from_expanded_root_moves_into_h1_instead_of_recelling_root(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    assert panel._latest_root is not None
    root = panel._latest_root
    panel._set_selected_node(root)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Space, Qt.NoModifier))
    assert panel._visible_children(root)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.NoModifier))

    visible_h1_ids = {child.node_id for child in panel._visible_children(root)}
    assert panel._selected_node_id in visible_h1_ids
    assert root.node_id not in panel._collapsed_node_ids


def test_fit_current_canvas_recenters_after_zoomed_in_view(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.resize(900, 700)
    panel.show()
    qapp.processEvents()
    panel.set_content("/Test.md", "# Root\n\n## One\nbody\n\n### Child\nbody\n\n## Two\nbody\n")
    qapp.processEvents()

    hbar = panel.scroll_area.horizontalScrollBar()
    vbar = panel.scroll_area.verticalScrollBar()
    hbar.setValue(hbar.maximum())
    vbar.setValue(vbar.maximum())
    panel._zoom_factor = 2.0

    panel.fit_map()
    qapp.processEvents()

    expected_x = max(hbar.minimum(), min(int(round((panel.preview_label.width() - panel.scroll_area.viewport().width()) / 2)), hbar.maximum()))
    expected_y = max(vbar.minimum(), min(int(round((panel.preview_label.height() - panel.scroll_area.viewport().height()) / 2)), vbar.maximum()))
    assert hbar.value() == expected_x
    assert vbar.value() == expected_y


def test_escape_collapses_entire_map_to_root(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n\n### Child\n")
    parent = _node_by_label(panel, "Parent")
    assert panel._latest_root is not None
    root = panel._latest_root

    panel._set_selected_node(parent)
    panel._toggle_node(parent)
    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))

    assert panel._selected_node_id == root.node_id
    assert panel._filter_node_id is None
    assert panel._scope_expansion_depths == {}
    assert root.node_id in panel._collapsed_node_ids
    assert panel._visible_children(root) == []


def test_filter_shortcuts_use_alt_brackets(qapp: QApplication) -> None:
    panel = MapPanel()

    assert panel._filter_on_shortcut.key().toString() == "Alt+["
    assert panel._filter_off_shortcut.key().toString() == "Alt+]"


def test_ctrl_i_starts_child_draft_heading(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_I, Qt.ControlModifier))

    assert panel._draft_heading is not None
    assert panel._draft_heading.as_child is True


def test_selected_outline_uses_filter_red_when_filter_active(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n\n### Child\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)
    assert panel.apply_selected_filter() is True
    assert panel._latest_root is not None

    svg = panel._build_map_svg(panel._latest_root, reset_canvas=True)

    assert ".selected { stroke: #dc2626;" in svg
    assert ".multi-selected { stroke: #dc2626;" in svg


def test_content_preview_toggle_state_persists(
    qapp: QApplication,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    saved: list[bool] = []
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_map_note_panel_visible", lambda: True)
    monkeypatch.setattr("sp.app.ui.map_panel.config.save_map_note_panel_visible", lambda value: saved.append(bool(value)))

    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")

    assert panel._content_preview_enabled is True
    assert panel.note_toggle_btn.isChecked() is True

    panel._toggle_content_previews(False)

    assert saved
    assert saved[-1] is False


def test_tooltip_toggle_off_prevents_hover_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")

    panel._toggle_content_previews(False)
    panel._schedule_hover_tooltip(parent, panel.mapToGlobal(panel.rect().center()))

    assert not panel._tooltip_timer.isActive()
    assert not panel._content_tooltip.isVisible()


def test_visible_hover_tooltip_can_be_pinned_for_scrolling(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")

    panel._toggle_content_previews(True)
    panel._schedule_hover_tooltip(parent, panel.mapToGlobal(panel.rect().center()))
    panel._show_hover_tooltip()

    assert panel._content_tooltip.isVisible()

    panel._pin_hover_tooltip()

    assert panel._tooltip_pinned is True
    assert panel._content_tooltip.focusPolicy() == Qt.StrongFocus
    assert panel._content_tooltip._close_btn.isVisible() is True


def test_visible_hover_tooltip_remains_after_pointer_leaves_node(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")

    panel._toggle_content_previews(True)
    panel._schedule_hover_tooltip(parent, panel.mapToGlobal(panel.rect().center()))
    panel._show_hover_tooltip()
    assert panel._content_tooltip.isVisible()

    panel._schedule_hover_tooltip(None, panel.mapToGlobal(panel.rect().bottomRight()))
    panel._on_tooltip_hover_changed(False)

    assert panel._content_tooltip.isVisible()
    assert not panel._tooltip_hide_timer.isActive()


def test_existing_hover_tooltip_stays_visible_while_next_node_is_pending(
    qapp: QApplication,
) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## First\none\n\n## Second\ntwo\n")
    first = _node_by_label(panel, "First")
    second = _node_by_label(panel, "Second")

    panel._toggle_content_previews(True)
    panel._schedule_hover_tooltip(first, panel.mapToGlobal(panel.rect().center()))
    panel._show_hover_tooltip()

    panel._schedule_hover_tooltip(second, panel.mapToGlobal(panel.rect().topRight()))

    assert panel._content_tooltip.isVisible()
    assert panel._tooltip_timer.isActive()
    assert panel._hovered_node_id == second.node_id


def test_transient_tooltip_click_pins_and_close_button_dismisses(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")

    panel._toggle_content_previews(True)
    panel._schedule_hover_tooltip(parent, panel.mapToGlobal(panel.rect().center()))
    panel._show_hover_tooltip()

    assert panel._content_tooltip.isVisible()
    assert panel._tooltip_pinned is False

    panel._pin_hover_tooltip()

    assert panel._tooltip_pinned is True

    panel._content_tooltip.closeRequested.emit()

    assert panel._content_tooltip.isVisible() is False
    assert panel._tooltip_pinned is False


def test_detached_map_window_control_toggles_fullscreen_state(qtbot) -> None:
    from sp.app.ui.main_window import MainWindow

    panel = MapPanel()
    qtbot.addWidget(panel)
    requested = []
    panel.windowModeToggleRequested.connect(lambda: requested.append(True))

    panel.set_window_mode_control(visible=True, windowed=False)
    assert panel.window_mode_btn.isHidden() is False
    assert panel.window_mode_btn.toolTip() == "Switch to windowed mode"

    panel.window_mode_btn.click()
    assert requested == [True]

    class FakeWindow:
        def __init__(self) -> None:
            self.full_screen = True

        def isFullScreen(self) -> bool:
            return self.full_screen

        def showNormal(self) -> None:
            self.full_screen = False

        def showFullScreen(self) -> None:
            self.full_screen = True

    window = FakeWindow()
    MainWindow._toggle_map_window_mode(window, panel)
    assert window.full_screen is False
    assert panel.window_mode_btn.toolTip() == "Switch to full screen"

    MainWindow._toggle_map_window_mode(window, panel)
    assert window.full_screen is True
    assert panel.window_mode_btn.toolTip() == "Switch to windowed mode"


def _press(panel, key, mods=Qt.NoModifier, text=""):
    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, mods, text))


def test_enter_reviews_and_shift_enter_adds_sibling_with_full_body(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## One\nbody\nlast body line\n\n## Two\n")
    panel._set_selected_node(_node_by_label(panel, "One"))
    _press(panel, Qt.Key_Return)
    assert panel._selected_note_popup_active
    assert panel._draft_heading is None
    _press(panel, Qt.Key_Escape)
    _press(panel, Qt.Key_Return, Qt.ShiftModifier)
    assert panel._draft_heading is not None
    # Map command letters and punctuation must remain ordinary text here.
    for char in "first?":
        _press(panel, ord(char.upper()), text=char)
    _press(panel, Qt.Key_Return)
    assert "last body line\n\n## first?\n## Two" in panel._current_markdown
    assert panel._selected_node().label == "first?"
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert "first?" not in panel._current_markdown
    _press(panel, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert "first?" in panel._current_markdown


def test_add_child_to_collapsed_root_reveals_draft_and_cancel_restores_fold(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## One\n")
    panel._set_selected_node(panel._latest_root)
    _press(panel, Qt.Key_Space)
    assert panel._latest_root.node_id in panel._collapsed_node_ids
    _press(panel, Qt.Key_Insert)
    assert panel._selected_node_id == "draft:new-heading"
    assert "draft:new-heading" in panel._node_hitboxes
    _press(panel, Qt.Key_Escape)
    assert panel._latest_root.node_id in panel._collapsed_node_ids
    _press(panel, Qt.Key_Insert)
    _press(panel, Qt.Key_N, text="New")
    _press(panel, Qt.Key_Return)
    assert panel._selected_node().label == "New"
    assert panel._selected_node_id in panel._node_hitboxes


def test_keyboard_move_applies_immediately_and_undo_restores_content(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## One\nOne body\n\n## Two\nTwo body\n\n### Child\nChild body\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Two"))
    changes = []
    panel.headingReorderRequested.connect(lambda *args: changes.append(args))
    _press(panel, Qt.Key_Up, Qt.ControlModifier)
    assert panel._detached_session is None
    assert panel._current_markdown.index("## Two") < panel._current_markdown.index("## One")
    assert "### Child\nChild body" in panel._current_markdown
    assert len(changes) == 1
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == original
    assert len(changes) == 2


def test_cut_navigate_paste_reparents_entire_branch_and_can_cancel(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## One\nOne body\n\n## Two\nTwo body\n\n### Child\nChild body\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Two"))
    _press(panel, Qt.Key_X, Qt.ControlModifier)
    assert panel._cut_node_ids
    assert "pending-move" in panel._svg_content
    _press(panel, Qt.Key_Escape)
    assert not panel._cut_node_ids
    assert panel._current_markdown == original
    _press(panel, Qt.Key_X, Qt.ControlModifier)
    panel._set_selected_node(_node_by_label(panel, "One"))
    _press(panel, Qt.Key_V, Qt.ControlModifier)
    assert panel._detached_session is None
    assert "### Two\nTwo body\n\n#### Child\nChild body" in panel._current_markdown
    assert not panel._cut_node_ids
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == original


def test_cut_cannot_move_into_descendant(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Parent\n\n### Child\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_X, Qt.ControlModifier)
    panel._set_selected_node(_node_by_label(panel, "Child"))
    _press(panel, Qt.Key_V, Qt.ControlModifier)
    assert panel._current_markdown == original
    assert panel._cut_node_ids


def test_move_past_maximum_heading_level_keeps_page_and_pending_branch(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Source\nbody\n\n## Target\n### Three\n#### Four\n##### Five\n###### Six\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Source"))
    _press(panel, Qt.Key_X, Qt.ControlModifier)
    panel._set_selected_node(_node_by_label(panel, "Five"))
    _press(panel, Qt.Key_V, Qt.ControlModifier)
    assert panel._current_markdown == original
    assert panel._cut_node_ids
    assert not panel._undo_stack
    assert panel._detached_session is None


def test_external_page_changes_clear_map_undo_and_pending_move(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    panel._set_selected_node(_node_by_label(panel, "Two"))
    _press(panel, Qt.Key_Up, Qt.ControlModifier)
    assert panel._undo_stack
    _press(panel, Qt.Key_X, Qt.ControlModifier)
    panel.set_content("/Test.md", "# Root\nExternally edited\n")
    assert not panel._undo_stack
    assert not panel._cut_node_ids


def test_map_commands_override_parent_window_shortcuts(qtbot, monkeypatch):
    from sp.app import config
    from PySide6.QtGui import QKeySequence, QShortcut
    from PySide6.QtWidgets import QMainWindow
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    window = QMainWindow()
    qtbot.addWidget(window)
    panel = MapPanel()
    window.setCentralWidget(panel)
    window.show()
    panel.preview_label.setFocus()
    parent_calls, map_calls = [], []
    reload_shortcut = QShortcut(QKeySequence("Ctrl+R"), window)
    reload_shortcut.activated.connect(lambda: parent_calls.append("reload"))
    nav_shortcut = QShortcut(QKeySequence("Alt+Up"), window)
    nav_shortcut.activated.connect(lambda: parent_calls.append("navigate"))
    monkeypatch.setattr(panel, "_undo_map_edit", lambda **kwargs: map_calls.append(kwargs))
    monkeypatch.setattr(panel, "zoom_selected_node", lambda delta: map_calls.append(delta) or True)
    QApplication.processEvents()
    QTest.keyClick(panel.preview_label, Qt.Key_R, Qt.ControlModifier)
    QTest.keyClick(panel.preview_label, Qt.Key_Up, Qt.AltModifier)
    assert parent_calls == []
    assert map_calls == [{"redo": True}, 1]


@pytest.mark.parametrize("vi", [False, True])
def test_vi_aliases_are_conditional_and_zoom_matches_arrows(qtbot, monkeypatch, vi):
    from sp.app import config
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: vi)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## One\n\n## Two\n")
    panel._set_selected_node(_node_by_label(panel, "One"))
    calls = []
    monkeypatch.setattr(panel, "zoom_selected_node", lambda delta: calls.append(delta) or True)
    _press(panel, Qt.Key_J, Qt.AltModifier)
    _press(panel, Qt.Key_K, Qt.AltModifier)
    assert calls == ([-1, 1] if vi else [])
    _press(panel, Qt.Key_J)
    assert panel._selected_node().label == ("Two" if vi else "One")
    _press(panel, Qt.Key_O)
    assert (panel._draft_heading is not None) == vi


def test_vi_add_above_rename_and_undo(qtbot, monkeypatch):
    from sp.app import config
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## One\nOne body\n\n## Two\nTwo body\n")
    panel._set_selected_node(_node_by_label(panel, "Two"))
    _press(panel, Qt.Key_O, Qt.ShiftModifier)
    _press(panel, Qt.Key_N, text="New")
    _press(panel, Qt.Key_Return)
    assert "One body\n\n## New\n## Two" in panel._current_markdown
    _press(panel, Qt.Key_I)
    assert panel._inline_rename_edit.isVisible()
    panel._inline_rename_edit.setText("Renamed")
    panel._commit_inline_rename()
    assert "## Renamed" in panel._current_markdown
    _press(panel, Qt.Key_U)
    assert "## New" in panel._current_markdown
    _press(panel, Qt.Key_R, Qt.ControlModifier)
    assert "## Renamed" in panel._current_markdown


def test_review_vi_paging_reaches_focused_reader(qtbot, monkeypatch):
    from sp.app import config
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## One\n" + "body\n\n" * 150)
    panel._set_selected_node(_node_by_label(panel, "One"))
    _press(panel, Qt.Key_Return)
    calls = []
    monkeypatch.setattr(panel._content_tooltip, "page_forward", lambda: calls.append("down"))
    monkeypatch.setattr(panel._content_tooltip, "page_backward", lambda: calls.append("up"))
    QTest.keyClick(panel._content_tooltip._editor, Qt.Key_D, Qt.ControlModifier)
    QTest.keyClick(panel._content_tooltip._editor, Qt.Key_U, Qt.ControlModifier)
    assert calls == ["down", "up"]
    QTest.keyClick(panel._content_tooltip._editor, Qt.Key_Escape)
    assert not panel._content_tooltip.isVisible()


def test_keyboard_help_popup_updates_vi_column(qtbot, monkeypatch):
    from sp.app import config
    from PySide6.QtWidgets import QTextBrowser
    vi = [False]
    monkeypatch.setattr(config, "load_vi_mode_enabled", lambda: vi[0])
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.help_btn.click()
    assert panel._keyboard_help.isVisible()
    browser = panel._keyboard_help.findChild(QTextBrowser, "mapKeyboardHelp")
    assert "Vi keys" not in browser.toPlainText()
    assert "Shift+Enter" in browser.toPlainText()
    panel._keyboard_help.close()
    vi[0] = True
    _press(panel, Qt.Key_Question, Qt.ShiftModifier)
    assert "Vi keys" in browser.toPlainText()
    assert "Alt+K / Alt+J" in browser.toPlainText()
    QTest.keyClick(panel._keyboard_help, Qt.Key_Escape)
    assert not panel._keyboard_help.isVisible()


def test_map_move_add_rename_and_undo_share_pending_editor_content(main_window, qtbot, monkeypatch):
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    main_window.editor.setPlainText("# Root\n\n## One\nbody\n\n## Two\nbody\n")
    monkeypatch.setattr(main_window, "_editor_has_focus", lambda: False)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content(main_window.current_path, main_window.editor.toPlainText())
    panel.headingReorderRequested.connect(main_window._reorder_headings_from_map_request)
    panel._set_selected_node(_node_by_label(panel, "Two"))
    _press(panel, Qt.Key_Up, Qt.ControlModifier)
    QTest.qWait(10)
    _press(panel, Qt.Key_Return, Qt.ShiftModifier)
    _press(panel, Qt.Key_N, text="New")
    _press(panel, Qt.Key_Return)
    assert panel._selected_node().label == "New"
    QTest.qWait(10)
    assert panel._selected_node().label == "New"
    _press(panel, Qt.Key_F2)
    panel._inline_rename_edit.setText("Renamed")
    panel._commit_inline_rename()
    QTest.qWait(10)
    pending = main_window._pending_map_sync_entry(main_window.current_path)
    assert pending["content"] == panel._current_markdown
    assert pending["content"].index("## Two") < pending["content"].index("## Renamed") < pending["content"].index("## One")
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    QTest.qWait(10)
    assert main_window._pending_map_sync_entry(main_window.current_path)["content"] == panel._current_markdown
    assert "## New" in panel._current_markdown


@pytest.mark.parametrize("heading,body", [("Root", "\nroot body\n\n"), ("Parent", "parent body\n\n")])
def test_detached_ctrl_enter_edits_body_without_adding_map_undo(qtbot, monkeypatch, heading, body):
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: False)
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\nroot body\n\n## Parent\nparent body\n\n### Child\nchild body\n\n## Sibling\nsibling body\n"
    panel.set_content("/Test.md", original)
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, heading))
    activations, edits = [], []
    panel.headingActivated.connect(lambda *args: activations.append(args))
    panel.headingReorderRequested.connect(lambda *args: edits.append(args))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    assert popup.isVisible()
    assert popup.editor.toPlainText() == body
    popup.editor.setPlainText("Updated **Markdown** body")
    QTest.keyClick(popup.editor, Qt.Key_End, Qt.ControlModifier)
    QTest.keyClick(popup.editor, Qt.Key_Return)
    assert popup.editor.toPlainText().endswith("\n")
    QTest.keyClick(popup.editor, Qt.Key_Return, Qt.ControlModifier)
    assert panel._body_editor is None
    assert not activations
    assert len(edits) == 1
    assert panel._current_markdown == original.replace(body, "Updated **Markdown** body\n", 1)
    assert "### Child\nchild body\n\n## Sibling\nsibling body\n" in panel._current_markdown
    assert panel._selected_node().label == heading
    saved = panel._current_markdown
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == saved
    assert not panel._undo_stack
    assert not panel._redo_stack


def test_body_editor_cancel_and_unchanged_save_leave_page_untouched(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Parent\nbody\n"
    panel.set_content("/Test.md", original)
    panel.set_window_mode_control(visible=True, windowed=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    popup.editor.setPlainText("Discard this")
    QTest.keyClick(popup.editor, Qt.Key_Escape)
    assert panel._body_editor is None
    assert panel._current_markdown == original
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    QTest.keyClick(panel._body_editor.editor, Qt.Key_Enter, Qt.ControlModifier)
    assert panel._body_editor is None
    assert panel._current_markdown == original
    assert not panel._undo_stack


def test_body_save_starts_new_map_history_without_losing_saved_text(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\nOld body\n")
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    assert panel._start_inline_rename()
    panel._inline_rename_edit.setText("Renamed")
    panel._commit_inline_rename()
    assert panel._undo_stack
    panel._edit_selected_body()
    panel._body_editor.editor.setPlainText("Saved body")
    QTest.keyClick(panel._body_editor.editor, Qt.Key_Return, Qt.ControlModifier)
    saved = panel._current_markdown
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == saved
    assert not panel._undo_stack
    assert not panel._redo_stack
    assert panel._start_inline_rename()
    panel._inline_rename_edit.setText("Latest name")
    panel._commit_inline_rename()
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == saved
    assert panel._selected_node().label == "Renamed"
    assert "Saved body" in panel._current_markdown


@pytest.mark.parametrize("detached", [False, True])
def test_ctrl_enter_from_review_uses_the_current_map_mode(qtbot, detached):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\nbody\n")
    panel.set_window_mode_control(visible=detached)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    activations = []
    panel.headingActivated.connect(lambda *args: activations.append(args))
    _press(panel, Qt.Key_Return)
    QTest.keyClick(panel._content_tooltip._editor, Qt.Key_Return, Qt.ControlModifier)
    assert not panel._content_tooltip.isVisible()
    assert (panel._body_editor is not None) == detached
    assert bool(activations) == (not detached)


@pytest.mark.parametrize("new_path", ["/Test.md", "/Other.md"])
def test_body_editor_detects_external_changes_without_losing_draft(qtbot, new_path):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\nbody\n")
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    popup.editor.setPlainText("My draft")
    external = "# Root\n\n## Parent\nExternally updated\n"
    panel.set_content(new_path, external)
    QTest.keyClick(popup.editor, Qt.Key_Return, Qt.ControlModifier)
    assert panel._body_editor is popup
    assert popup.editor.toPlainText() == "My draft"
    assert "page changed" in popup.error_label.text()
    assert panel._current_markdown == external


def test_body_editor_keeps_headings_in_map_controls(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Parent"
    panel.set_content("/Test.md", original)
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    assert popup.editor.toPlainText() == ""
    popup.editor.setPlainText("### A child heading")
    QTest.keyClick(popup.editor, Qt.Key_Return, Qt.ControlModifier)
    assert panel._current_markdown == original
    assert not popup.error_label.isHidden()
    popup.editor.setPlainText("New body")
    QTest.keyClick(popup.editor, Qt.Key_Return, Qt.ControlModifier)
    assert panel._current_markdown == original + "\nNew body\n"


def test_tabbed_ctrl_enter_reveals_heading_without_editor_focus(main_window, monkeypatch):
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    main_window.editor.setPlainText("# Root\n\n## Parent\nbody\n")
    right = main_window.right_panel
    if right.map_panel is None:
        right._add_map_tab()
    panel = right.map_panel
    right.tabs.setCurrentWidget(panel)
    panel.set_content(main_window.current_path, main_window.editor.toPlainText())
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    panel.preview_label.setFocus()
    QTest.keyClick(panel.preview_label, Qt.Key_Return, Qt.ControlModifier)
    QTest.qWait(20)
    assert main_window.editor.textCursor().blockNumber() == 2
    assert not main_window.editor.hasFocus()
    assert panel.preview_label.hasFocus()
    assert panel._body_editor is None


def test_body_popup_save_queues_content_before_focus_refresh(main_window, qtbot, monkeypatch):
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window, "_editor_has_focus", lambda: False)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    main_window.editor.setPlainText("# Root\n\n## Parent\nbody\n\n### Child\nchild body\n")
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_window_mode_control(visible=True)
    panel.set_content(main_window.current_path, main_window.editor.toPlainText())
    panel.headingReorderRequested.connect(main_window._reorder_headings_from_map_request)
    panel.focusSyncRequested.connect(lambda: panel.set_content(main_window.current_path, main_window._get_editor_text_for_path(main_window.current_path)))
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    panel._body_editor.editor.setPlainText("Updated body\n\n")
    QTest.keyClick(panel._body_editor.editor, Qt.Key_Return, Qt.ControlModifier)
    QTest.qWait(20)
    pending = main_window._pending_map_sync_entry(main_window.current_path)
    assert pending["content"] == panel._current_markdown
    assert "Updated body\n\n### Child\nchild body" in pending["content"]
    assert panel._selected_node().label == "Parent"
    assert panel._body_editor is None


def test_body_popup_fits_small_screen_with_buttons_visible(qtbot, monkeypatch):
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QPushButton
    bounds = QRect(0, 0, 480, 260)
    monkeypatch.setattr("sp.app.ui.map_panel.popup_available_geometry", lambda **kwargs: bounds)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\n" + "body\n" * 100)
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    QApplication.processEvents()
    assert bounds.contains(popup.frameGeometry())
    assert popup.width() <= 560
    assert popup.height() <= 320
    for button in popup.findChildren(QPushButton):
        assert popup.rect().contains(button.geometry())
        assert button.isVisible()
    assert popup.layout().contentsMargins().left() == 6
    assert popup.editor.document().documentMargin() == 4


def test_body_popup_supports_vi_editing_and_escape_mode_transition(qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\nbody\n")
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    popup.editor.repaint()
    QTest.qWait(60)
    assert popup.editor._vi_mode_active
    QTest.keyClick(popup.editor, Qt.Key_I)
    QTest.keyClicks(popup.editor, "New ")
    assert popup.editor._vi_insert_mode
    QTest.keyClick(popup.editor, Qt.Key_Escape)
    assert panel._body_editor is popup
    assert popup.editor._vi_mode_active
    assert not popup.editor._vi_insert_mode
    QTest.keyClick(popup.editor, Qt.Key_H)
    position = popup.editor.textCursor().position()
    QTest.keyClick(popup.editor, Qt.Key_H)
    assert popup.editor.textCursor().position() < position
    before = popup.editor.toPlainText()
    QTest.keyClick(popup.editor, Qt.Key_X)
    assert popup.editor.toPlainText() != before
    QTest.keyClick(popup.editor, Qt.Key_U)
    assert popup.editor.toPlainText() == before
    QTest.keyClick(popup.editor, Qt.Key_Return, Qt.ControlModifier)
    assert panel._body_editor is None
    assert "New body" in panel._current_markdown


def test_body_popup_renders_stillpoint_links_without_navigation_and_round_trips(qtbot, monkeypatch):
    from sp.app.ui.markdown_editor import LINK_SENTINEL
    from PySide6.QtGui import QTextCursor
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: False)
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Parent\nSee [:Page:Target|Friendly label] here.\n\n### Child\nKeep child.\n"
    panel.set_content("/Test.md", original)
    panel.set_window_mode_control(visible=True)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    _press(panel, Qt.Key_Return, Qt.ControlModifier)
    popup = panel._body_editor
    editor = popup.editor
    QApplication.processEvents()
    assert LINK_SENTINEL in editor.toPlainText()
    editor.highlighter.rehighlight()
    label_position = editor.toPlainText().index("Friendly label")
    assert any(r.start <= label_position < r.start + r.length and r.format.fontUnderline() for r in editor.document().firstBlock().layout().formats())
    activated = []
    editor.linkActivated.connect(activated.append)
    cursor = editor.textCursor()
    cursor.setPosition(label_position + 2)
    editor.setTextCursor(cursor)
    QTest.mouseClick(editor.viewport(), Qt.LeftButton, pos=editor.cursorRect().center())
    assert editor._activate_link(":Page:Target") is False
    assert not activated
    editor.moveCursor(QTextCursor.End)
    editor.insertPlainText("More content.\n")
    QTest.keyClick(editor, Qt.Key_Return, Qt.ControlModifier)
    assert "[:Page:Target|Friendly label]" in panel._current_markdown
    assert LINK_SENTINEL not in panel._current_markdown
    assert "### Child\nKeep child.\n" in panel._current_markdown
    assert "More content." in panel._current_markdown


@pytest.mark.parametrize("line,heading", [(1, "Root"), (4, "Parent"), (6, "Child"), (8, "Grandchild"), (10, "Other"), (100, "Other")])
def test_focus_editor_line_reveals_expands_and_centers_containing_heading(qtbot, monkeypatch, line, heading):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\nbody\n### Child\nchild body\n#### Grandchild\nbody\n## Other\nbody\n")
    panel._filter_node_id = _node_by_label(panel, "Other").node_id
    panel._collapsed_node_ids.add(panel._latest_root.node_id)
    centered = []
    monkeypatch.setattr(panel, "center_selected_node", lambda: centered.append(panel._selected_node().label))
    assert panel.focus_heading_at_line(line)
    node = panel._selected_node()
    assert node.label == heading
    assert node.node_id in panel._node_hitboxes
    assert panel._filter_node_id is None
    if node.children:
        assert panel._scope_depth(node) >= 1
        assert all(child.node_id in panel._node_hitboxes for child in node.children)
    assert centered == [heading]


def test_open_map_reuses_window_and_follows_editor_cursor_and_page(main_window, qtbot, monkeypatch):
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    monkeypatch.setattr(main_window, "_save_before_page_transition", lambda **kwargs: True)
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: True)
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window, "_apply_geometry_persistence", lambda *args: None)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    main_window.editor.setPlainText("# Root\n\n## Parent\nbody\n### Child\nchild body\n#### Grandchild\nbody\n## Other\nbody\n")

    def cursor_to(line):
        cursor = main_window.editor.textCursor()
        cursor.setPosition(main_window.editor.document().findBlockByLineNumber(line - 1).position())
        main_window.editor.setTextCursor(cursor)

    cursor_to(6)
    main_window._open_map_panel_window()
    panel = main_window._detached_map_panels[0]
    window = panel.window()
    qtbot.addWidget(window)
    from sp.app.ui.diagram_editor_icons import get_diagram_editor_icon
    assert window.windowIcon().cacheKey() == get_diagram_editor_icon("mindmap").cacheKey()
    QTest.qWait(30)
    assert len(main_window._detached_map_panels) == 1
    assert panel._selected_node().label == "Child"
    assert _node_by_label(panel, "Grandchild").node_id in panel._node_hitboxes
    assert panel.preview_label.hasFocus()

    # Reopening preserves windowed mode while changing the selected heading.
    main_window._toggle_map_window_mode(window, panel)
    panel.help_btn.setFocus()
    assert panel.help_btn.hasFocus()
    cursor_to(10)
    main_window._open_map_panel_window()
    QTest.qWait(30)
    assert main_window._detached_map_panels == [panel]
    assert panel.window() is window
    assert not window.isFullScreen()
    assert panel._selected_node().label == "Other"
    assert panel.preview_label.hasFocus()

    # Returning from the main editor restores canvas focus, not the last
    # toolbar control, even when native window activation arrives later.
    panel.help_btn.setFocus()
    main_window.activateWindow()
    main_window.editor.setFocus()
    QApplication.processEvents()
    main_window._open_map_panel_window()
    # Simulate late native focus restoration after the first queued request.
    QApplication.processEvents()
    panel.help_btn.setFocus()
    QTest.qWait(80)
    assert panel.preview_label.hasFocus()
    assert QApplication.focusWidget() is panel.preview_label
    assert QApplication.activeWindow() is window
    selected = panel._selected_node()
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_K)
    assert panel._selected_node().label != selected.label

    # A minimized or closed window is restored rather than duplicated.
    window.showMinimized()
    main_window._open_map_panel_window()
    QTest.qWait(30)
    assert not window.isMinimized()
    window.close()
    main_window._open_file("/PageB/PageB.md")
    QApplication.processEvents()
    main_window.editor.setPlainText("# Page B\n\n## Destination\nbody\n")
    cursor_to(4)
    main_window._open_map_panel_window()
    QTest.qWait(30)
    assert main_window._detached_map_panels == [panel]
    assert window.isVisible()
    assert panel._current_page_path == "/PageB/PageB.md"
    assert panel._selected_node().label == "Destination"


@pytest.mark.parametrize("undo_key,mods", [(Qt.Key_U, Qt.NoModifier), (Qt.Key_Z, Qt.ControlModifier)])
def test_map_undo_survives_rendered_editor_round_trip(main_window, qtbot, monkeypatch, undo_key, mods):
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    original = "# Root\n\n## One\nSee [:Page:Target|Link label].\n\n## Two\nbody\n"
    main_window.editor.set_markdown(original)
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content(main_window.current_path, main_window.editor.toPlainText())
    panel.headingReorderRequested.connect(main_window._reorder_headings_from_map_request)
    # Queue map changes, then apply them to the main editor as happens when
    # returning to StillPoint; this encodes headings and links for display.
    monkeypatch.setattr(main_window, "_editor_has_focus", lambda: False)
    panel._set_selected_node(_node_by_label(panel, "Two"))
    _press(panel, Qt.Key_Up, Qt.ControlModifier)
    main_window._apply_pending_editor_sync_if_needed(main_window.current_path)
    panel.set_content(main_window.current_path, main_window.editor.toPlainText())
    assert panel._undo_stack
    _press(panel, undo_key, mods)
    pending = main_window._pending_map_sync_entry(main_window.current_path)
    assert pending is not None
    assert main_window.editor._from_display(pending["content"]) == original
    assert "Link label" in pending["content"]
    _press(panel, Qt.Key_R, Qt.ControlModifier)
    assert panel._current_markdown.index("## Two") < panel._current_markdown.index("## One")


@pytest.mark.parametrize("undo_key,mods", [(Qt.Key_U, Qt.NoModifier), (Qt.Key_Z, Qt.ControlModifier)])
def test_inline_rename_undo_after_canvas_focus_refresh(main_window, qtbot, monkeypatch, undo_key, mods):
    monkeypatch.setattr(main_window, "_save_current_file", lambda *args, **kwargs: None)
    monkeypatch.setattr("sp.app.ui.main_window.config.has_active_vault", lambda: True)
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    monkeypatch.setattr(main_window, "_apply_geometry_persistence", lambda *args: None)
    main_window._open_file("/PageA/PageA.md")
    QApplication.processEvents()
    original = "# Root\n\n## One\nbody\n\n## Two\nbody\n"
    main_window.editor.set_markdown(original)
    cursor = main_window.editor.textCursor()
    cursor.setPosition(main_window.editor.document().findBlockByLineNumber(2).position())
    main_window.editor.setTextCursor(cursor)
    main_window._open_map_panel_window()
    panel = main_window._detached_map_panels[0]
    qtbot.addWidget(panel.window())
    QTest.qWait(80)
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_F2)
    assert panel._inline_rename_edit.hasFocus()
    QTest.keyClicks(panel._inline_rename_edit, "Renamed")
    QTest.keyClick(panel._inline_rename_edit, Qt.Key_Return)
    QTest.qWait(80)
    assert panel._selected_node().label == "Renamed"

    assert panel._undo_stack
    assert QApplication.focusWidget() is panel.preview_label
    QTest.keyClick(QApplication.focusWidget(), undo_key, mods)
    QTest.qWait(30)
    assert panel._selected_node().label == "One"
    assert panel._current_markdown == original
    assert main_window.editor._from_display(main_window._get_editor_text_for_path(main_window.current_path)) == original
    QTest.keyClick(QApplication.focusWidget(), Qt.Key_R, Qt.ControlModifier)
    QTest.qWait(30)
    assert panel._selected_node().label == "Renamed"


@pytest.mark.parametrize("vi_delete", [False, True])
def test_delete_empty_node_is_undoable_and_help_lists_keys(qtbot, monkeypatch, vi_delete):
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: vi_delete)
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Empty\n  \n\n## Next\nbody\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Empty"))
    edits = []
    panel.headingReorderRequested.connect(lambda *args: edits.append(args))
    if vi_delete:
        _press(panel, Qt.Key_D)
        assert panel._current_markdown == original
        _press(panel, Qt.Key_D)
    else:
        _press(panel, Qt.Key_Delete)
    assert panel._current_markdown == "# Root\n\n## Next\nbody\n"
    assert len(edits) == 1
    assert panel._selected_node().label == "Next"
    assert "Delete empty node" in panel._keyboard_help_html()
    assert (">dd<" in panel._keyboard_help_html()) == vi_delete
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == original
    _press(panel, Qt.Key_Z, Qt.ControlModifier | Qt.ShiftModifier)
    assert "## Empty" not in panel._current_markdown


def test_delete_empty_parent_promotes_children_and_keeps_their_bodies(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Empty\n\n### Child\nChild body\n\n#### Grandchild\nKeep me\n\n### Second\nSecond body\n\n## Next\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Empty"))
    _press(panel, Qt.Key_Delete)
    assert panel._current_markdown == "# Root\n\n## Child\nChild body\n\n### Grandchild\nKeep me\n\n## Second\nSecond body\n\n## Next\n"
    assert panel._selected_node().label == "Child"
    _press(panel, Qt.Key_Z, Qt.ControlModifier)
    assert panel._current_markdown == original


@pytest.mark.parametrize("body", ["Body text", "![Image](image.png)", "- [ ] Task", "---"])
def test_delete_refuses_body_with_red_white_border_and_message(qtbot, body):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.show()
    original = f"# Root\n\n## Protected\n{body}\n\n### Child\n"
    panel.set_content("/Test.md", original)
    node = _node_by_label(panel, "Protected")
    panel._set_selected_node(node)
    _press(panel, Qt.Key_Delete)
    assert panel._current_markdown == original
    assert not panel._undo_stack
    assert panel._delete_flash_node_id == node.node_id
    assert not panel._delete_message.isHidden()
    assert panel._delete_message.text() == "node has body content, not deleting"
    assert 'style="stroke: #ef4444; stroke-width: 4' in panel._svg_content
    panel._advance_delete_feedback()
    assert 'style="stroke: #ffffff; stroke-width: 4' in panel._svg_content
    for _ in range(5):
        panel._advance_delete_feedback()
    assert panel._delete_flash_ticks == 6
    assert panel._delete_flash_timer.interval() == 90
    assert not panel._delete_message.isHidden()
    assert panel._delete_message.parentWidget() is panel
    panel.layout().activate()
    assert panel._delete_message.geometry().bottom() == panel.rect().bottom()
    assert panel._delete_flash_node_id is None
    assert not panel._delete_flash_timer.isActive()
    assert panel._delete_message_timer.isActive()
    panel._delete_message_timer.timeout.emit()
    assert panel._delete_message.isHidden()
    assert panel._current_markdown == original


def test_content_dots_show_own_body_separately_from_child_content(qtbot):
    import xml.etree.ElementTree as ET
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Empty parent\n\n### Child with body\nA paragraph\n\n## Whitespace\n  \n\n## Image\n![Image](image.png)\n")
    panel.expand_all()
    for label, expected in [("Root", False), ("Empty parent", False), ("Child with body", True), ("Whitespace", False), ("Image", True)]:
        node = _node_by_label(panel, label)
        assert node.has_body is expected
        svg = ET.fromstring("<svg>" + panel._render_node(node, 0, 0) + "</svg>")
        dot = next(item for item in svg.findall("circle") if "content-dot" in item.attrib.get("class", ""))
        assert (dot.attrib["fill"] != "none") is expected
        assert float(dot.attrib["cx"]) < node.x
        assert float(dot.attrib["cy"]) > node.y
        assert float(dot.attrib["r"]) <= 3


def test_body_popup_click_outside_cancels_without_changing_page_or_undo(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.show()
    original = "# Root\n\n## Parent\nBody\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    panel._start_inline_rename()
    panel._inline_rename_edit.setText("Renamed")
    panel._commit_inline_rename()
    before = panel._current_markdown
    history = list(panel._undo_stack)
    panel._edit_selected_body()
    popup = panel._body_editor
    popup.editor.setPlainText("Unsaved draft")
    edits = []
    panel.headingReorderRequested.connect(lambda *args: edits.append(args))
    QTest.mouseClick(panel.preview_label, Qt.LeftButton, pos=QPoint(5, 5))
    assert panel._body_editor is None
    assert not popup.isVisible()
    assert panel._current_markdown == before
    assert panel._undo_stack == history
    assert not edits


def test_body_popup_internal_clicks_do_not_cancel_and_save_button_still_works(qtbot):
    from PySide6.QtWidgets import QPushButton
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.show()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nBody\n")
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    panel._edit_selected_body()
    popup = panel._body_editor
    popup.editor.setPlainText("Saved text")
    QTest.mouseClick(popup.editor.viewport(), Qt.LeftButton)
    assert panel._body_editor is popup
    save = next(button for button in popup.findChildren(QPushButton) if button.text().startswith("Save"))
    QTest.mouseClick(save, Qt.LeftButton)
    assert panel._body_editor is None
    assert "Saved text" in panel._current_markdown


def test_body_popup_application_deactivation_cancels_draft(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    panel.set_content("/Test.md", "# Root\n\n## Parent\nBody\n")
    panel._set_selected_node(_node_by_label(panel, "Parent"))
    original = panel._current_markdown
    panel._edit_selected_body()
    panel._body_editor.editor.setPlainText("Discard on leaving")
    QApplication.sendEvent(QApplication.instance(), QEvent(QEvent.ApplicationDeactivate))
    assert panel._body_editor is None
    assert panel._current_markdown == original


def test_vi_delete_prefix_can_cancel_and_does_not_apply_with_vi_disabled(qtbot, monkeypatch):
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "# Root\n\n## Empty\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(_node_by_label(panel, "Empty"))
    _press(panel, Qt.Key_D)
    _press(panel, Qt.Key_Escape)
    assert panel._vi_delete_pending is None
    assert panel._current_markdown == original
    assert panel._selected_node().label == "Empty"
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: False)
    _press(panel, Qt.Key_D)
    _press(panel, Qt.Key_D)
    assert panel._current_markdown == original


def test_delete_does_not_remove_synthetic_page_root(qtbot):
    panel = MapPanel()
    qtbot.addWidget(panel)
    original = "## Heading\n"
    panel.set_content("/Test.md", original)
    panel._set_selected_node(panel._latest_root)
    _press(panel, Qt.Key_Delete)
    assert panel._current_markdown == original
    assert not panel._undo_stack
