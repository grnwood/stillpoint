"""Tests for page navigation (history and hierarchy).

NOTE: These tests require a running backend server and are marked as integration tests.
They test the full navigation stack including history and hierarchy navigation.
Run with: pytest tests/test_navigation.py --runintegration
"""
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QTimer


pytestmark = pytest.mark.skip(reason="Navigation tests require running backend server - run integration tests separately")


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
        
        assert main_window.current_path == "/test_vault.md"
    
    def test_navigate_up_at_root(self, main_window):
        """Test that navigating up at root stays at root."""
        main_window._open_file("/test_vault.md")
        QApplication.processEvents()
        
        # Try to navigate up - should stay at root
        main_window._navigate_hierarchy_up()
        QApplication.processEvents()
        
        assert main_window.current_path == "/test_vault.md"
    
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
