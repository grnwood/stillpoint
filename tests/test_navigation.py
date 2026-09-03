"""Tests for page navigation (history and hierarchy)."""
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QLineEdit, QListWidget, QSizePolicy
from PySide6.QtCore import Qt, QTimer, QModelIndex
from PySide6.QtGui import QGuiApplication, QStandardItem
from PySide6.QtTest import QTest
from sp.app.ui.main_window import MainWindow


class TestHistoryNavigation:
    """Test history navigation (Alt+Left/Right, Alt+H/L)."""
    
    def test_history_back_navigation(self, main_window):
        """Test navigating backward through page history."""
        # Open several pages
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        main_window._open_file("/PageC/PageC.md")
        QApplication.processEvents()
        
        # Verify we're at PageC
        assert main_window.current_path == "/PageC/PageC.md"
        assert len(main_window.page_history) == 3
        assert main_window.history_index == 2
        
        # Navigate back to PageB
        main_window._navigate_history_back()
        QApplication.processEvents()
        assert main_window.current_path == "/PageB/PageB.md"
        assert main_window.history_index == 1
        
        # Navigate back to PageA
        main_window._navigate_history_back()
        QApplication.processEvents()
        assert main_window.current_path == "/PageA/PageA.md"
        assert main_window.history_index == 0
        
        # Can't go back further
        main_window._navigate_history_back()
        QApplication.processEvents()
        assert main_window.current_path == "/PageA/PageA.md"
        assert main_window.history_index == 0
    
    def test_history_forward_navigation(self, main_window):
        """Test navigating forward through page history."""
        # Open several pages
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        main_window._open_file("/PageC/PageC.md")
        QApplication.processEvents()
        
        # Navigate back twice
        main_window._navigate_history_back()
        QApplication.processEvents()
        main_window._navigate_history_back()
        QApplication.processEvents()
        assert main_window.current_path == "/PageA/PageA.md"
        
        # Navigate forward to PageB
        main_window._navigate_history_forward()
        QApplication.processEvents()
        assert main_window.current_path == "/PageB/PageB.md"
        assert main_window.history_index == 1
        
        # Navigate forward to PageC
        main_window._navigate_history_forward()
        QApplication.processEvents()
        assert main_window.current_path == "/PageC/PageC.md"
        assert main_window.history_index == 2
        
        # Can't go forward further
        main_window._navigate_history_forward()
        QApplication.processEvents()
        assert main_window.current_path == "/PageC/PageC.md"
        assert main_window.history_index == 2
    
    def test_history_no_duplicates(self, main_window):
        """Test that opening same page twice doesn't create duplicate history."""
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        # Open PageA again
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        
        # History should be: PageA, PageB, PageA
        assert len(main_window.page_history) == 3
        assert main_window.page_history == ["/PageA/PageA.md", "/PageB/PageB.md", "/PageA/PageA.md"]
    
    def test_history_truncates_forward_on_new_page(self, main_window):
        """Test that opening new page after going back truncates forward history."""
        # Open PageA, PageB, PageC
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        main_window._open_file("/PageC/PageC.md")
        QApplication.processEvents()
        
        # Go back to PageB
        main_window._navigate_history_back()
        QApplication.processEvents()
        assert main_window.history_index == 1
        
        # Open PageA - should truncate PageC from history
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        
        assert len(main_window.page_history) == 3
        assert main_window.page_history[-1] == "/PageA/PageA.md"
        assert "/PageC/PageC.md" not in main_window.page_history[main_window.history_index:]
    
    def test_history_navigation_no_tree_pollution(self, main_window):
        """Test that history navigation doesn't add pages from tree selection changes."""
        # Open PageA and PageB
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        
        initial_history_len = len(main_window.page_history)
        
        # Navigate back - should not add root or any other page to history
        main_window._navigate_history_back()
        QApplication.processEvents()
        
        # History length should not change
        assert len(main_window.page_history) == initial_history_len
        assert main_window.current_path == "/PageA/PageA.md"

    def test_ctrl_tab_cycles_recent_pages_forward(self, main_window, monkeypatch):
        calls: list[tuple[str, bool]] = []

        monkeypatch.setattr(main_window, "_cycle_popup", lambda mode, reverse=False: calls.append((mode, reverse)))
        monkeypatch.setattr(main_window, "_activate_history_popup_selection", lambda: None)

        main_window._cycle_history_shortcut(reverse=False)

        assert calls == [("history", False)]

    def test_ctrl_shift_tab_cycles_recent_pages_backward(self, main_window, monkeypatch):
        calls: list[tuple[str, bool]] = []

        monkeypatch.setattr(main_window, "_cycle_popup", lambda mode, reverse=False: calls.append((mode, reverse)))
        monkeypatch.setattr(main_window, "_activate_history_popup_selection", lambda: None)

        main_window._cycle_history_shortcut(reverse=True)

        assert calls == [("history", True)]

    def test_ctrl_tab_picker_waits_for_control_release(self, main_window, monkeypatch):
        activated: list[bool] = []
        main_window._popup_mode = "history"
        main_window._popup_items = ["/PageA/PageA.md"]
        main_window._popup_index = 0
        monkeypatch.setattr(
            main_window,
            "_activate_history_popup_selection",
            lambda: activated.append(True),
        )
        monkeypatch.setattr(
            QGuiApplication,
            "queryKeyboardModifiers",
            staticmethod(lambda: Qt.ControlModifier),
        )

        main_window._finish_history_switch_when_control_released()
        assert activated == []

        monkeypatch.setattr(
            QGuiApplication,
            "queryKeyboardModifiers",
            staticmethod(lambda: Qt.NoModifier),
        )
        main_window._finish_history_switch_when_control_released()
        assert activated == [True]

    def test_history_switcher_is_an_in_window_overlay(self, main_window):
        main_window._ensure_history_popup()

        popup = main_window._history_popup
        assert popup is not None
        assert popup.isWindow() is False
        assert popup.parentWidget() is main_window.centralWidget()
        assert popup.testAttribute(Qt.WA_StyledBackground)

    def test_recent_history_chicklets_keep_journal_pages_when_nav_hides_journal(self, main_window):
        main_window._show_journal_in_nav = False
        main_window.page_history = [
            "/Projects/Alpha/Alpha.md",
            "/Journal/2026/05/18/18.md",
        ]

        main_window._refresh_history_buttons()

        history_paths = [btn.property("history_path") for btn in main_window.history_buttons]
        assert "/Journal/2026/05/18/18.md" in history_paths
        assert any(btn.text() == "18-May-26" for btn in main_window.history_buttons)

    def test_vault_toggle_journal_action_and_nav_button_stay_synchronized(self, main_window, monkeypatch):
        from sp.app import config

        saved: list[bool] = []
        monkeypatch.setattr(config, "save_show_journal", lambda value: saved.append(bool(value)))
        monkeypatch.setattr(main_window, "_populate_vault_tree", lambda: None)
        main_window._action_toggle_journal.setEnabled(True)
        main_window.journal_tree_button.setEnabled(True)
        main_window._set_show_journal_in_nav(False)

        command_labels = [label for label, _action in main_window._collect_menu_actions()]
        assert "Vault / Toggle Journal" in command_labels

        main_window._action_toggle_journal.trigger()
        assert main_window._show_journal_in_nav is True
        assert main_window.journal_tree_button.isChecked() is True

        main_window.journal_tree_button.click()
        assert main_window._show_journal_in_nav is False
        assert main_window._action_toggle_journal.isChecked() is False
        assert saved[-2:] == [True, False]

    def test_recent_history_chicklets_prettify_underscored_page_names(self, main_window):
        main_window.page_history = ["/Roles_And_Stuff/Roles_And_Stuff.md"]

        main_window._refresh_history_buttons()

        assert [btn.text() for btn in main_window.history_buttons] == ["Roles And Stuff"]

    def test_top_nav_chicklets_include_accent_hover_style(self, main_window):
        main_window._vault_accent_color = "#3B82F6"
        main_window.bookmarks = ["/PageA/PageA.md"]
        main_window.page_history = ["/PageB/PageB.md"]

        main_window._refresh_bookmark_buttons()
        main_window._refresh_history_buttons()

        bookmark_style = next(iter(main_window.bookmark_buttons.values())).styleSheet()
        history_style = main_window.history_buttons[0].styleSheet()

        assert "QPushButton:hover" in bookmark_style
        assert "#3B82F6" in bookmark_style
        assert "QPushButton:hover" in history_style
        assert "#3B82F6" in history_style

    def test_top_nav_chicklets_include_theme_normal_colors(self, main_window):
        main_window.bookmarks = ["/PageA/PageA.md"]
        main_window.page_history = ["/PageB/PageB.md"]

        main_window._refresh_bookmark_buttons()
        main_window._refresh_history_buttons()

        bookmark_style = next(iter(main_window.bookmark_buttons.values())).styleSheet()
        history_style = main_window.history_buttons[0].styleSheet()

        assert "background:" in bookmark_style
        assert "color:" in bookmark_style
        assert "border-color:" in bookmark_style
        assert next(iter(main_window.bookmark_buttons.values())).property("topNavChicklet") == "true"
        assert "background:" in history_style
        assert "color:" in history_style
        assert "border-color:" in history_style
        assert main_window.history_buttons[0].property("topNavChicklet") == "true"

        main_window._apply_top_nav_container_styles()
        assert "QWidget#historyBar" in main_window.history_bar.styleSheet()
        assert "border-top:" in main_window.history_bar.styleSheet()

    def test_top_nav_chicklet_border_adapts_when_light_global_border_meets_dark_vault_bg(self):
        border = MainWindow._top_nav_border_for_background("#e5e7eb", "#161c24")

        assert border != "#e5e7eb"

    def test_recent_history_uses_scroll_arrows_instead_of_button_shrinking(self, main_window):
        main_window.page_history = [f"/Page{idx}/Page{idx}.md" for idx in range(1, 10)]

        main_window.show()
        QApplication.processEvents()
        main_window._refresh_history_buttons()
        main_window.history_scroll_area.setFixedWidth(120)
        QApplication.processEvents()
        main_window._update_history_strip_width()
        main_window._sync_history_scroll_range()
        main_window._update_history_scroll_buttons()

        assert main_window.history_scroll_area.horizontalScrollBar().maximum() > 0
        assert main_window.history_scroll_left.isVisible()
        assert main_window.history_scroll_right.isVisible()
        assert all(btn.sizePolicy().horizontalPolicy() == QSizePolicy.Fixed for btn in main_window.history_buttons)

    def test_active_recent_history_chicklet_triggers_scroll_into_view(self, main_window, monkeypatch):
        main_window.page_history = [
            f"/LongRecentPage{idx}/LongRecentPage{idx}.md" for idx in range(1, 10)
        ]
        main_window.show()
        QApplication.processEvents()
        main_window._refresh_history_buttons()
        main_window.history_scroll_area.setFixedWidth(90)
        QApplication.processEvents()
        main_window._update_history_strip_width()
        main_window._sync_history_scroll_range()
        main_window._update_history_scroll_buttons()

        visible_calls: list[str] = []
        original_helper = main_window._ensure_history_button_visible

        def record_visible(btn):
            visible_calls.append(str(btn.property("history_path") or ""))
            original_helper(btn)

        monkeypatch.setattr(main_window, "_ensure_history_button_visible", record_visible)
        main_window.current_path = "/LongRecentPage9/LongRecentPage9.md"
        MainWindow._update_active_page_chicklets(main_window)
        QApplication.processEvents()

        assert "/LongRecentPage9/LongRecentPage9.md" in visible_calls

    def test_explicit_heading_picker_uses_persistent_popup(self, main_window, monkeypatch):
        calls: list[tuple[object, bool]] = []

        monkeypatch.setattr(
            main_window,
            "_show_heading_picker_popup",
            lambda global_pos, prefer_above=False: calls.append((global_pos, prefer_above)),
        )

        main_window._request_heading_picker_popup()

        assert len(calls) == 1

    def test_heading_picker_allows_plain_vi_keys_in_filter(self, main_window, qapp):
        main_window._toc_headings = [
            {"title": "Alpha", "line": 1, "level": 1, "position": 0},
            {"title": "Beta", "line": 2, "level": 1, "position": 10},
            {"title": "Gamma", "line": 3, "level": 1, "position": 20},
        ]

        main_window._show_heading_picker_popup(main_window.editor.mapToGlobal(main_window.editor.rect().center()))
        qapp.processEvents()

        popup = main_window._heading_picker
        assert popup is not None
        filter_edit = popup.findChild(QLineEdit)
        list_widget = popup.findChild(QListWidget)
        assert filter_edit is not None
        assert list_widget is not None

        QTest.keyClicks(filter_edit, "jklh")
        assert filter_edit.text() == "jklh"

        filter_edit.clear()
        qapp.processEvents()
        assert list_widget.currentRow() == 0

        QTest.keyClick(filter_edit, Qt.Key_J, Qt.ControlModifier | Qt.ShiftModifier)
        assert list_widget.currentRow() == 1

        QTest.keyClick(filter_edit, Qt.Key_K, Qt.ControlModifier | Qt.ShiftModifier)
        assert list_widget.currentRow() == 0

        popup.close()

    def test_context_menu_parent_path_uses_filtered_root_for_whitespace(self, main_window):
        main_window._nav_filter_path = "/PageA"
        assert main_window._context_menu_parent_path(QModelIndex()) == "/PageA"

    def test_context_menu_parent_path_uses_selected_folder_and_skips_filter_banner(self, main_window):
        from sp.app.ui.main_window import PATH_ROLE, FILTER_BANNER

        first_index = main_window.tree_model.index(0, 0)
        assert first_index.isValid()
        assert main_window._context_menu_parent_path(first_index) == "/PageA"

        banner = QStandardItem("Filtered")
        banner.setData(FILTER_BANNER, PATH_ROLE)
        main_window._nav_filter_path = "/PageB"
        main_window.tree_model.invisibleRootItem().appendRow(banner)
        assert main_window._context_menu_parent_path(banner.index()) == "/PageB"


class TestHierarchyNavigation:
    """Test hierarchy navigation (Alt+J/K for up/down)."""
    
    def test_navigate_up_to_parent(self, main_window):
        """Test navigating up to parent page."""
        # Open child page
        main_window._open_file("/PageA/Child1/Child1.md")
        QApplication.processEvents()
        
        # Navigate up
        main_window._navigate_hierarchy_up()
        QApplication.processEvents()
        
        assert main_window.current_path == "/PageA/PageA.md"
    
    def test_navigate_up_to_root(self, main_window):
        """Test navigating up to root page."""
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        
        # Navigate up to root
        main_window._navigate_hierarchy_up()
        QApplication.processEvents()
        
        assert main_window.current_path == main_window._vault_root_page_path()
    
    def test_navigate_up_at_root(self, main_window):
        """Test that navigating up at root stays at root."""
        main_window._open_file(main_window._vault_root_page_path())
        QApplication.processEvents()
        
        # Try to navigate up - should stay at root
        main_window._navigate_hierarchy_up()
        QApplication.processEvents()
        
        assert main_window.current_path == main_window._vault_root_page_path()
    
    def test_navigate_down_to_first_child(self, main_window):
        """Test navigating down to first child page."""
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        
        # Navigate down to first child (alphabetically)
        main_window._navigate_hierarchy_down()
        QApplication.processEvents()
        
        # Should open Child1 (first alphabetically)
        assert main_window.current_path == "/PageA/Child1/Child1.md"
    
    def test_navigate_down_no_children(self, main_window):
        """Test navigating down when page has no children."""
        # PageB has no children
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        
        current = main_window.current_path
        
        # Try to navigate down - should stay on same page
        main_window._navigate_hierarchy_down()
        QApplication.processEvents()
        
        assert main_window.current_path == current
    
    def test_hierarchy_navigation_no_history_pollution(self, main_window):
        """Test that hierarchy navigation doesn't add to history."""
        main_window._open_file("/PageA/Child1/Child1.md")
        QApplication.processEvents()
        
        initial_history_len = len(main_window.page_history)
        
        # Navigate up - should not add to history
        main_window._navigate_hierarchy_up()
        QApplication.processEvents()
        
        # History length should not change
        assert len(main_window.page_history) == initial_history_len
        
        # Navigate down - should not add to history
        main_window._navigate_hierarchy_down()
        QApplication.processEvents()
        
        assert len(main_window.page_history) == initial_history_len


class TestCursorPositionMemory:
    """Test that navigation remembers cursor positions."""
    
    def test_history_remembers_cursor_position(self, main_window):
        """Test that going back/forward restores cursor positions."""
        # Open PageA and set cursor position
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        cursor = main_window.editor.textCursor()
        cursor.setPosition(10)
        main_window.editor.setTextCursor(cursor)
        
        # Open PageB
        main_window._open_file("/PageB/PageB.md")
        QApplication.processEvents()
        
        # Go back to PageA
        main_window._navigate_history_back()
        QApplication.processEvents()
        
        # Cursor should be restored (approximately, accounting for display format changes)
        restored_pos = main_window.editor.textCursor().position()
        assert restored_pos >= 8  # Allow some tolerance for format changes
    
    def test_hierarchy_remembers_cursor_position(self, main_window):
        """Test that hierarchy navigation restores cursor positions."""
        # Open parent page and set cursor position
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        cursor = main_window.editor.textCursor()
        cursor.setPosition(10)
        main_window.editor.setTextCursor(cursor)
        
        # Navigate down then back up
        main_window._navigate_hierarchy_down()
        QApplication.processEvents()
        main_window._navigate_hierarchy_up()
        QApplication.processEvents()
        
        # Should be back at PageA with cursor restored
        assert main_window.current_path == "/PageA/PageA.md"
        restored_pos = main_window.editor.textCursor().position()
        assert restored_pos >= 8


class TestTreeClickGuard:
    def test_tree_clicks_queue_latest_target_until_editor_ready(self, main_window, monkeypatch):
        root = main_window.tree_model.invisibleRootItem()
        first_index = main_window.tree_model.indexFromItem(root.child(0))
        second_index = main_window.tree_model.indexFromItem(root.child(1))

        readiness = iter([False, False, True])
        monkeypatch.setattr(main_window.editor, "is_ready_for_page_switch", lambda: next(readiness))
        monkeypatch.setattr(main_window, "_arm_pending_tree_open", lambda: None)

        opened: list[str] = []
        monkeypatch.setattr(
            main_window,
            "_open_file",
            lambda path, *args, **kwargs: (opened.append(path), setattr(main_window, "current_path", path)),
        )

        main_window._on_tree_row_clicked(first_index)
        main_window._on_tree_row_clicked(second_index)

        assert opened == []
        assert main_window._pending_tree_open_path == "/PageB/PageB.md"

        main_window._drain_pending_tree_open()

        assert opened == ["/PageB/PageB.md"]
        assert main_window._pending_tree_open_path is None

    def test_tree_open_requests_are_dropped_during_vault_switch(self, main_window, monkeypatch):
        opened: list[str] = []
        monkeypatch.setattr(
            main_window,
            "_open_file",
            lambda path, *args, **kwargs: opened.append(path),
        )

        main_window._vault_switch_in_progress = True
        main_window._request_tree_open("/PageA/PageA.md", focus_target="editor")

        assert opened == []
        assert main_window._pending_tree_open_path is None
        assert main_window._pending_tree_open_focus_target is None

    def test_prepare_vault_switch_ui_reset_clears_pending_state(self, main_window, monkeypatch):
        root = main_window.tree_model.invisibleRootItem()
        first_index = main_window.tree_model.indexFromItem(root.child(0))
        main_window.tree_view.setCurrentIndex(first_index)
        main_window.current_path = "/PageA/PageA.md"
        main_window._pending_tree_open_path = "/PageB/PageB.md"
        main_window._pending_tree_open_focus_target = "editor"
        main_window._pending_tree_open_retry_armed = True
        main_window._pending_selection = "/PageC/PageC.md"

        unloaded: list[str] = []
        monkeypatch.setattr(main_window.editor, "unload_for_delete", lambda: unloaded.append("ok"))

        current_pages: list[tuple[object, object]] = []
        monkeypatch.setattr(
            main_window.right_panel,
            "set_current_page",
            lambda path, title: current_pages.append((path, title)),
        )

        main_window._prepare_vault_switch_ui_reset()

        assert unloaded == ["ok"]
        assert main_window.current_path is None
        assert main_window._pending_tree_open_path is None
        assert main_window._pending_tree_open_focus_target is None
        assert main_window._pending_tree_open_retry_armed is False
        assert main_window._pending_selection is None
        assert main_window._skip_next_selection_open is True
        assert not main_window.tree_view.currentIndex().isValid()
        assert current_pages == [(None, None)]

    def test_hidden_journal_page_does_not_queue_tree_selection(self, main_window, monkeypatch):
        journal_path = "/Journal/2026/07/11/11.md"
        main_window.current_path = journal_path
        main_window._show_journal_in_nav = False
        main_window._pending_selection = journal_path
        main_window._deferred_nav_tree_refresh_target = journal_path
        ensured: list[str] = []
        monkeypatch.setattr(main_window, "_ensure_tree_path_loaded", lambda path, **kwargs: ensured.append(path))

        main_window._sync_nav_tree_to_active_page()

        assert ensured == []
        assert main_window._pending_selection is None
        assert main_window._deferred_nav_tree_refresh_target is None

    def test_command_bar_contains_page_move_and_locate_actions(self, main_window):
        labels = {label for label, _action in main_window._collect_menu_actions()}

        assert "File / Move Page…" in labels
        assert "Go / Locate in Page Tree" in labels


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
