"""Tests for tree navigation and expansion state preservation.

Tests verify:
- Folders stay open when navigating with Ctrl+Up/Down
- Expanded state is preserved after tree refresh
- Collapsed folders are tracked correctly
"""
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt, QModelIndex
from PySide6.QtTest import QTest


class TestTreeExpansionNavigation:
    """Test tree expansion behavior during navigation."""
    
    def test_ctrl_navigation_preserves_expansion(self, qtbot):
        """Test that _walk_tree navigation doesn't collapse folders."""
        from sp.app.ui.main_window import VaultTreeView, PATH_ROLE, TYPE_ROLE
        from PySide6.QtGui import QStandardItemModel, QStandardItem
        
        # Create tree view with model
        tree = VaultTreeView()
        model = QStandardItemModel()
        tree.setModel(model)
        
        # Create tree structure:
        # Root
        #   ├─ Folder1 (has children)
        #   ├─ Folder2 (has children)
        #   └─ Folder3 (has children)
        root = model.invisibleRootItem()
        folder1 = QStandardItem("Folder1")
        folder1.setData("/folder1", PATH_ROLE)
        folder1.setData(True, TYPE_ROLE)
        folder1.appendRow(QStandardItem("Child1"))
        
        folder2 = QStandardItem("Folder2")
        folder2.setData("/folder2", PATH_ROLE)
        folder2.setData(True, TYPE_ROLE)
        folder2.appendRow(QStandardItem("Child2"))
        
        folder3 = QStandardItem("Folder3")
        folder3.setData("/folder3", PATH_ROLE)
        folder3.setData(True, TYPE_ROLE)
        folder3.appendRow(QStandardItem("Child3"))
        
        root.appendRow(folder1)
        root.appendRow(folder2)
        root.appendRow(folder3)
        
        # Expand all folders
        idx1 = model.indexFromItem(folder1)
        idx2 = model.indexFromItem(folder2)
        idx3 = model.indexFromItem(folder3)
        
        tree.expand(idx1)
        tree.expand(idx2)
        tree.expand(idx3)
        
        # Verify all expanded
        assert tree.isExpanded(idx1)
        assert tree.isExpanded(idx2)
        assert tree.isExpanded(idx3)
        
        # Set current to folder1
        tree.setCurrentIndex(idx1)
        
        # Call _walk_tree directly (simulates Ctrl+Down)
        tree._walk_tree(direction=1)
        QApplication.processEvents()
        
        # Verify folder1 is still expanded (not auto-collapsed by old code)
        assert tree.isExpanded(idx1), "Folder1 should remain expanded after navigating away"
        
        # Call _walk_tree again (move to folder3)
        tree._walk_tree(direction=1)
        QApplication.processEvents()
        
        # Verify all folders remain expanded
        assert tree.isExpanded(idx1), "Folder1 should still be expanded"
        assert tree.isExpanded(idx2), "Folder2 should still be expanded"
        assert tree.isExpanded(idx3), "Folder3 should still be expanded"
    
    def test_expanded_paths_tracking(self, qtbot):
        """Test that _expanded_paths set correctly tracks expanded folders."""
        from sp.app.ui.main_window import MainWindow
        
        # Create main window instance
        window = MainWindow(api_base="http://localhost:5050")
        qtbot.addWidget(window)
        
        # Verify _expanded_paths exists
        assert hasattr(window, '_expanded_paths')
        assert isinstance(window._expanded_paths, set)
        assert len(window._expanded_paths) == 0
    
    def test_expanded_state_added_on_expand(self, qtbot):
        """Test that expanding a folder adds it to _expanded_paths."""
        from sp.app.ui.main_window import MainWindow, PATH_ROLE
        from PySide6.QtGui import QStandardItem
        
        window = MainWindow(api_base="http://localhost:5050")
        qtbot.addWidget(window)
        
        # Create a test item
        item = QStandardItem("TestFolder")
        item.setData("/test/folder", PATH_ROLE)
        window.tree_model.invisibleRootItem().appendRow(item)
        
        # Get index and simulate expand
        idx = window.tree_model.indexFromItem(item)
        
        # Manually call the expand handler
        window._on_tree_expanded(idx)
        QApplication.processEvents()
        
        # Verify the path was added to expanded set
        assert "/test/folder" in window._expanded_paths
    
    def test_expanded_state_removed_on_collapse(self, qtbot):
        """Test that collapsing a folder removes it from _expanded_paths."""
        from sp.app.ui.main_window import MainWindow, PATH_ROLE
        from PySide6.QtGui import QStandardItem
        
        window = MainWindow(api_base="http://localhost:5050")
        qtbot.addWidget(window)
        
        # Create a test item
        item = QStandardItem("TestFolder")
        item.setData("/test/folder", PATH_ROLE)
        window.tree_model.invisibleRootItem().appendRow(item)
        
        # Add to expanded paths
        window._expanded_paths.add("/test/folder")
        assert "/test/folder" in window._expanded_paths
        
        # Get index and simulate collapse
        idx = window.tree_model.indexFromItem(item)
        window._on_tree_collapsed(idx)
        QApplication.processEvents()
        
        # Verify the path was removed
        assert "/test/folder" not in window._expanded_paths
    
    def test_save_expanded_state(self, qtbot):
        """Test that _save_expanded_state correctly captures expanded folders."""
        from sp.app.ui.main_window import MainWindow, PATH_ROLE
        from PySide6.QtGui import QStandardItem
        
        window = MainWindow(api_base="http://localhost:5050")
        qtbot.addWidget(window)
        
        # Create test tree structure
        root = window.tree_model.invisibleRootItem()
        folder1 = QStandardItem("Folder1")
        folder1.setData("/folder1", PATH_ROLE)
        folder1.appendRow(QStandardItem("Child1"))
        
        folder2 = QStandardItem("Folder2")
        folder2.setData("/folder2", PATH_ROLE)
        folder2.appendRow(QStandardItem("Child2"))
        
        root.appendRow(folder1)
        root.appendRow(folder2)
        
        # Expand only folder1
        idx1 = window.tree_model.indexFromItem(folder1)
        window.tree_view.expand(idx1)
        QApplication.processEvents()
        
        # Clear the expanded paths set and call save
        window._expanded_paths.clear()
        window._save_expanded_state()
        
        # Verify folder1 was saved, folder2 was not
        assert "/folder1" in window._expanded_paths
        assert "/folder2" not in window._expanded_paths
    
    def test_restore_expanded_state(self, qtbot):
        """Test that _restore_expanded_state re-expands the correct folders."""
        from sp.app.ui.main_window import MainWindow, PATH_ROLE
        from PySide6.QtGui import QStandardItem
        
        window = MainWindow(api_base="http://localhost:5050")
        qtbot.addWidget(window)
        
        # Create test tree structure
        root = window.tree_model.invisibleRootItem()
        folder1 = QStandardItem("Folder1")
        folder1.setData("/folder1", PATH_ROLE)
        folder1.appendRow(QStandardItem("Child1"))
        
        folder2 = QStandardItem("Folder2")
        folder2.setData("/folder2", PATH_ROLE)
        folder2.appendRow(QStandardItem("Child2"))
        
        root.appendRow(folder1)
        root.appendRow(folder2)
        
        # Mark folder1 as expanded in the set
        window._expanded_paths.add("/folder1")
        
        # Initially both should be collapsed
        idx1 = window.tree_model.indexFromItem(folder1)
        idx2 = window.tree_model.indexFromItem(folder2)
        window.tree_view.collapse(idx1)
        window.tree_view.collapse(idx2)
        QApplication.processEvents()
        
        # Restore expanded state
        window._restore_expanded_state()
        QApplication.processEvents()
        
        # Verify only folder1 is expanded
        assert window.tree_view.isExpanded(idx1), "Folder1 should be expanded"
        assert not window.tree_view.isExpanded(idx2), "Folder2 should remain collapsed"


class TestTreeExpansionPreservation:
    """Test that tree expansion state is preserved during refresh operations."""
    
    @pytest.mark.skip(reason="Requires backend server")
    def test_expansion_preserved_after_tree_refresh(self, main_window):
        """Test that expanded folders stay open after _populate_vault_tree."""
        # Expand some folders
        root = main_window.tree_model.invisibleRootItem()
        if root.rowCount() > 0:
            first_folder = root.child(0)
            idx = main_window.tree_model.indexFromItem(first_folder)
            main_window.tree_view.expand(idx)
            QApplication.processEvents()
            
            # Verify it's expanded
            assert main_window.tree_view.isExpanded(idx)
            
            # Trigger tree refresh
            main_window._populate_vault_tree()
            QApplication.processEvents()
            
            # Find the same folder in the new tree (by path)
            path = first_folder.data(Qt.UserRole)
            for row in range(root.rowCount()):
                item = root.child(row)
                if item.data(Qt.UserRole) == path:
                    new_idx = main_window.tree_model.indexFromItem(item)
                    assert main_window.tree_view.isExpanded(new_idx), \
                        "Folder should remain expanded after tree refresh"
                    break
    
    @pytest.mark.skip(reason="Requires backend server")
    def test_expansion_preserved_after_file_move(self, main_window):
        """Test that expanded folders stay open after moving a file."""
        # This would test the full integration with move operations
        # Requires a running backend and actual file operations
        pass


class TestTreeCollapseRestore:
    """Test collapse/expand interactions."""
    
    def test_manual_collapse_removes_from_tracking(self, qtbot):
        """Test that manually collapsing a folder removes it from tracked state."""
        from sp.app.ui.main_window import MainWindow, PATH_ROLE
        from PySide6.QtGui import QStandardItem
        
        window = MainWindow(api_base="http://localhost:5050")
        qtbot.addWidget(window)
        
        # Create test item
        item = QStandardItem("TestFolder")
        item.setData("/test/folder", PATH_ROLE)
        item.appendRow(QStandardItem("Child"))
        window.tree_model.invisibleRootItem().appendRow(item)
        
        # Expand the folder
        idx = window.tree_model.indexFromItem(item)
        window._on_tree_expanded(idx)
        assert "/test/folder" in window._expanded_paths
        
        # Collapse the folder
        window._on_tree_collapsed(idx)
        assert "/test/folder" not in window._expanded_paths
        
        # If we restore now, it should not be expanded
        window.tree_view.collapse(idx)
        window._restore_expanded_state()
        QApplication.processEvents()
        
        assert not window.tree_view.isExpanded(idx), \
            "Folder should not be re-expanded after manual collapse"
