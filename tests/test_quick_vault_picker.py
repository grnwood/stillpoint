from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from sp.app.ui.main_window import QuickVaultPicker


def test_quick_vault_picker_highlights_current_page(main_window, qapp) -> None:
    main_window._open_file("/PageA/Child1/Child1.md")
    qapp.processEvents()

    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    current = picker.tree.currentIndex()
    assert current.isValid()
    assert current.data(Qt.UserRole + 2) == "/PageA/Child1/Child1.md"


def test_quick_vault_picker_highlights_current_page_in_filtered_mode(main_window, qapp) -> None:
    main_window._nav_filter_path = "/PageA"
    main_window._populate_vault_tree()
    main_window._open_file("/PageA/Child1/Child1.md")
    qapp.processEvents()

    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    current = picker.tree.currentIndex()
    assert current.isValid()
    assert current.data(Qt.UserRole + 2) == "/PageA/Child1/Child1.md"


def test_quick_vault_picker_centers_current_page_on_open(main_window, qapp, monkeypatch) -> None:
    tree_state = [
        {
            "name": f"Page{index:02d}",
            "path": f"/Page{index:02d}",
            "open_path": f"/Page{index:02d}/Page{index:02d}.md",
            "children": [],
        }
        for index in range(1, 21)
    ]

    original_get = main_window.http.get

    def fake_get(path, params=None):
        if path == "/api/vault/tree":
            from tests.conftest import _TestHttpResponse

            return _TestHttpResponse(
                payload={"tree": [{"path": "/", "children": [dict(child) for child in tree_state]}], "version": 1},
                url=f"http://localhost{path}",
            )
        if path == "/api/vault/tree/expand-path":
            from tests.conftest import _TestHttpResponse

            return _TestHttpResponse(
                payload={"segments": {"/": [dict(child) for child in tree_state]}},
                url=f"http://localhost{path}",
            )
        return original_get(path, params=params)

    monkeypatch.setattr(main_window.http, "get", fake_get)
    main_window.current_path = "/Page15/Page15.md"

    main_window._show_quick_vault_picker()
    qapp.processEvents()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    current = picker.tree.currentIndex()
    assert current.isValid()
    rect = picker.tree.visualRect(current)
    viewport_center_y = picker.tree.viewport().rect().center().y()
    assert abs(rect.center().y() - viewport_center_y) <= max(24, rect.height())


def test_quick_vault_picker_shows_pretty_labels_for_underscored_pages(main_window, qapp, monkeypatch) -> None:
    tree_state = [
        {
            "name": "Roles_And_Stuff",
            "path": "/Roles_And_Stuff",
            "open_path": "/Roles_And_Stuff/Roles_And_Stuff.md",
            "children": [],
        }
    ]

    original_get = main_window.http.get

    def fake_get(path, params=None):
        if path == "/api/vault/tree":
            from tests.conftest import _TestHttpResponse

            return _TestHttpResponse(
                payload={"tree": [{"path": "/", "children": [dict(child) for child in tree_state]}], "version": 1},
                url=f"http://localhost{path}",
            )
        return original_get(path, params=params)

    monkeypatch.setattr(main_window.http, "get", fake_get)

    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    index = picker.tree.model().index(0, 0)
    assert index.isValid()
    assert index.data(Qt.DisplayRole) == "Roles And Stuff"


def test_quick_vault_picker_activation_uses_normal_open_path(main_window, monkeypatch, qapp) -> None:
    main_window._open_file("/PageA/PageA.md")
    qapp.processEvents()
    main_window.editor.insertPlainText("\nDirty change")
    qapp.processEvents()

    saved: list[tuple[bool, str]] = []

    def fake_save_current_file(auto: bool = False, reason: str = "") -> None:
        saved.append((auto, reason))
        main_window._dirty_flag = False

    monkeypatch.setattr(main_window, "_save_current_file", fake_save_current_file)

    main_window._activate_quick_vault_picker_target("/PageB/PageB.md")
    qapp.processEvents()

    assert saved == [(True, "page switch")]
    assert main_window.current_path == "/PageB/PageB.md"
    assert main_window.page_history[-1] == "/PageB/PageB.md"


def test_quick_vault_picker_navigation_includes_expanded_children(main_window, qapp) -> None:
    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None

    root_index = picker.tree.currentIndex()
    if not root_index.isValid():
        root_index = picker.tree.model().index(0, 0)
        picker.tree.setCurrentIndex(root_index)

    picker._move_right()
    qapp.processEvents()
    picker._move_selection(1)
    qapp.processEvents()

    current = picker.tree.currentIndex()
    assert current.isValid()
    assert current.data(Qt.UserRole + 2) == "/PageA/Child1/Child1.md"


def test_quick_vault_picker_parent_page_is_openable(main_window, qapp) -> None:
    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None

    index = picker.tree.model().index(0, 0)
    assert index.isValid()
    assert bool(index.data(Qt.UserRole + 1)) is True
    assert index.data(Qt.UserRole + 2) == "/PageA/PageA.md"

    picker._activate_index(index)
    qapp.processEvents()

    assert main_window.current_path == "/PageA/PageA.md"


def test_quick_vault_picker_backslash_collapses_all(main_window, qapp) -> None:
    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None

    index = picker.tree.model().index(0, 0)
    picker.tree.setCurrentIndex(index)
    picker._move_right()
    qapp.processEvents()
    picker._move_selection(1)
    qapp.processEvents()

    assert picker.tree.isExpanded(index)

    picker._collapse_all()
    qapp.processEvents()

    assert not picker.tree.isExpanded(index)
    assert picker.tree.currentIndex() == index


def test_quick_vault_picker_ignores_reopen_while_visible(main_window, qapp, monkeypatch) -> None:
    main_window.editor.set_vi_mode_enabled(True)
    main_window.editor.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()

    QTest.keyClick(main_window.editor, Qt.Key_V)
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    assert picker.isVisible()

    reopen_attempts: list[tuple[object, bool]] = []

    def fail_if_reopened(global_pos=None, prefer_above: bool = False) -> None:
        reopen_attempts.append((global_pos, prefer_above))

    monkeypatch.setattr(picker, "open_at", fail_if_reopened)

    main_window._show_quick_vault_picker()
    qapp.processEvents()

    assert reopen_attempts == []


def test_quick_vault_picker_recreates_hidden_popup_after_vi_insert_cycle(main_window, qapp) -> None:
    main_window.editor.set_vi_mode_enabled(True)
    main_window.editor.setFocus(Qt.OtherFocusReason)
    qapp.processEvents()

    QTest.keyClick(main_window.editor, Qt.Key_V)
    qapp.processEvents()

    first_picker = main_window._quick_vault_picker
    assert first_picker is not None
    assert first_picker.isVisible()

    first_picker.hide()
    qapp.processEvents()

    QTest.keyClick(main_window.editor, Qt.Key_I)
    qapp.processEvents()
    QTest.keyClick(main_window.editor, Qt.Key_Escape)
    qapp.processEvents()

    QTest.keyClick(main_window.editor, Qt.Key_V)
    qapp.processEvents()

    second_picker = main_window._quick_vault_picker
    assert second_picker is not None
    assert second_picker is not first_picker
    assert second_picker.isVisible()


def test_quick_vault_picker_recomputes_index_after_tree_reset(main_window, qapp, monkeypatch) -> None:
    main_window._open_file("/PageA/Child1/Child1.md")
    qapp.processEvents()

    picker = QuickVaultPicker(main_window, main_window)

    original_expand = picker._expand_ancestors

    def reset_tree_during_expand(index) -> None:
        original_expand(index)
        main_window._populate_vault_tree()

    monkeypatch.setattr(picker, "_expand_ancestors", reset_tree_during_expand)

    original_set_current_index = picker.tree.setCurrentIndex
    selected_validity: list[bool] = []

    def record_selected_index(index) -> None:
        selected_validity.append(index.isValid())
        original_set_current_index(index)

    monkeypatch.setattr(picker.tree, "setCurrentIndex", record_selected_index)

    picker.open_at()
    qapp.processEvents()

    assert selected_validity
    assert all(selected_validity)
    assert picker.tree.currentIndex().isValid()
    picker.close()


def test_quick_vault_picker_uses_snapshot_model(main_window, qapp) -> None:
    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    assert picker.tree.model() is not main_window.tree_model


def test_quick_vault_picker_scopes_to_journal_when_hidden(main_window, qapp, monkeypatch) -> None:
    journal_tree = [
        {
            "path": "/",
            "children": [
                {
                    "name": "Journal",
                    "path": "/Journal",
                    "open_path": "/Journal/Journal.md",
                    "children": [
                        {
                            "name": "2026",
                            "path": "/Journal/2026",
                            "children": [
                                {
                                    "name": "04",
                                    "path": "/Journal/2026/04",
                                    "children": [
                                        {
                                            "name": "29",
                                            "path": "/Journal/2026/04/29",
                                            "open_path": "/Journal/2026/04/29/29.md",
                                            "children": [],
                                        }
                                    ],
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    ]

    original_get = main_window.http.get

    def fake_get(path, params=None):
        if path == "/api/vault/tree":
            include_journal = str((params or {}).get("include_journal") or "").lower() == "true"
            tree_path = (params or {}).get("path")
            if include_journal and tree_path == "/Journal":
                from tests.conftest import _TestHttpResponse

                return _TestHttpResponse(payload={"tree": journal_tree, "version": 1}, url=f"http://localhost{path}")
        return original_get(path, params=params)

    monkeypatch.setattr(main_window.http, "get", fake_get)
    main_window._show_journal_in_nav = False
    main_window.current_path = "/Journal/2026/04/29/29.md"

    main_window._show_quick_vault_picker()
    qapp.processEvents()

    picker = main_window._quick_vault_picker
    assert picker is not None
    assert picker._root_path == "/Journal"
    assert picker._label.text() == "Journal index"
    current = picker.tree.currentIndex()
    assert current.isValid()
    assert current.data(Qt.UserRole + 2) == "/Journal/2026/04/29/29.md"
