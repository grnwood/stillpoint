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


def test_note_section_ends_before_next_h3(qapp: QApplication) -> None:
    panel = MapPanel()
    panel.set_content(
        "/Test.md",
        "# Root\n\n## Parent\nintro\n\n### Child\nbody\n",
    )
    parent = _node_by_label(panel, "Parent")
    panel._set_selected_node(parent)

    section = panel._selected_note_section()

    assert section is not None
    assert section.text == "## Parent\nintro\n"
