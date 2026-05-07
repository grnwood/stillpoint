import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication

from sp.app.ui.map_panel import MapPanel


def _node_by_label(panel: MapPanel, label: str):
    assert panel._latest_root is not None
    return next(node for node in panel._collect_nodes(panel._latest_root) if node.label == label)


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


def test_alt_enter_opens_selected_node_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))

    assert panel._selected_note_popup_active is True
    assert panel._content_tooltip.isVisible() is True


def test_left_key_closes_selected_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## One\nbody\n\n### Child\n")
    one = _node_by_label(panel, "One")
    panel._set_selected_node(one)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))
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

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))
    assert panel._selected_note_popup_active is True

    qapp.sendEvent(panel._content_tooltip._editor, QKeyEvent(QEvent.KeyPress, Qt.Key_L, Qt.NoModifier))
    qapp.processEvents()

    assert panel._selected_note_popup_active is False
    assert panel._content_tooltip.isVisible() is False


def test_alt_enter_toggles_selected_note_popup_closed(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\nintro\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))
    assert panel._selected_note_popup_active is True

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))

    assert panel._selected_note_popup_active is False
    assert panel._content_tooltip.isVisible() is False


def test_down_arrow_scrolls_selected_note_popup(qapp: QApplication) -> None:
    panel = MapPanel()
    long_body = "\n".join(f"line {idx}" for idx in range(40))
    panel.set_content("/Test.md", f"# Root\n\n## Parent\n{long_body}\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))
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

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))
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
    monkeypatch.setattr("sp.app.ui.map_panel.config.load_vi_mode_enabled", lambda: True)
    panel = MapPanel()
    long_body = "\n".join(f"line {idx}" for idx in range(40))
    panel.set_content("/Test.md", f"# Root\n\n## Parent\n{long_body}\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.AltModifier))
    scrollbar = panel._content_tooltip._editor.verticalScrollBar()
    start = scrollbar.value()
    step = max(1, scrollbar.pageStep())

    qapp.sendEvent(
        panel._content_tooltip._editor,
        QKeyEvent(QEvent.KeyPress, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier),
    )

    assert panel._selected_note_popup_active is True
    assert scrollbar.value() >= start + step


def test_ctrl_enter_starts_inline_rename(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content("/Test.md", "# Root\n\n## Parent\n")
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    panel.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.ControlModifier))

    assert panel._inline_rename_node_id == parent.node_id
    assert panel._inline_rename_edit.text() == "Parent"


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
