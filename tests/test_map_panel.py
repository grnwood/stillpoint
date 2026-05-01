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
