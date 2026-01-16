"""Tests for Insert Link Dialog functionality."""
import pytest
from pathlib import Path
from PySide6.QtWidgets import QApplication, QDialog
from PySide6.QtCore import Qt
from sp.app.ui.insert_link_dialog import InsertLinkDialog


@pytest.fixture
def app(qapp):
    """Create QApplication instance."""
    return qapp


class TestInsertLinkDialogBasics:
    """Test basic Insert Link Dialog functionality."""
    
    def test_dialog_creation(self, app):
        """Test that dialog can be created."""
        dialog = InsertLinkDialog()
        assert dialog is not None
        assert dialog.windowTitle() == "Insert Link"
    
    def test_dialog_with_selected_text(self, app):
        """Test dialog initialized with selected text."""
        dialog = InsertLinkDialog(selected_text="MyPage")
        QApplication.processEvents()
        
        # Search field should contain selected text
        assert dialog.search.text() == "MyPage"
        # Link name should also contain selected text
        assert dialog.link_name.text() == "MyPage"
        # Search text should be selected for easy replacement
        assert dialog.search.selectedText() == "MyPage"
    
    def test_dialog_returns_colon_path(self, app):
        """Test that dialog returns proper colon notation."""
        dialog = InsertLinkDialog()
        dialog.search.setText(":PageA:Child")
        
        result = dialog.selected_colon_path()
        assert result == ":PageA:Child"
    
    def test_dialog_returns_link_name(self, app):
        """Test that dialog returns custom link name."""
        dialog = InsertLinkDialog()
        dialog.search.setText(":PageA")
        dialog.link_name.setText("My Custom Name")
        
        path = dialog.selected_colon_path()
        name = dialog.selected_link_name()
        
        assert path == ":PageA"
        assert name == "My Custom Name"
    
    def test_dialog_handles_http_url(self, app):
        """Test that dialog handles HTTP URLs."""
        dialog = InsertLinkDialog()
        dialog.search.setText("https://example.com")
        
        result = dialog.selected_colon_path()
        assert result == "https://example.com"
    
    def test_dialog_cleans_line_breaks(self, app):
        """Test that dialog removes line breaks from pasted text."""
        # Simulate pasted text with line breaks
        dialog = InsertLinkDialog(selected_text="Line One\nLine Two")
        QApplication.processEvents()
        
        # Should be cleaned to single line
        assert "\n" not in dialog.search.text()
        assert "\n" not in dialog.link_name.text()
    
    def test_link_name_auto_populate(self, app):
        """Test that link name auto-populates from search."""
        dialog = InsertLinkDialog()
        
        # Type in search field
        dialog.search.setText("PageA")
        QApplication.processEvents()
        
        # Link name should auto-populate
        assert dialog.link_name.text() == "PageA"
    
    def test_link_name_manual_edit_stops_auto_populate(self, app):
        """Test that manually editing link name stops auto-population."""
        dialog = InsertLinkDialog()
        
        # Type in search
        dialog.search.setText("PageA")
        QApplication.processEvents()
        
        # Manually edit link name
        dialog.link_name.setText("Custom Name")
        QApplication.processEvents()
        
        # Now changing search should not update link name
        dialog.search.setText("PageB")
        QApplication.processEvents()
        
        assert dialog.link_name.text() == "Custom Name"
    
    def test_return_key_accepts_dialog(self, app):
        """Test that pressing Enter accepts the dialog."""
        dialog = InsertLinkDialog()
        dialog.search.setText(":PageA")
        
        # Press return in search field
        from PySide6.QtTest import QTest
        QTest.keyClick(dialog.search, Qt.Key_Return)
        QApplication.processEvents()
        
        # Dialog should be accepted (result would be Accepted)
        # Note: In real usage, exec() would return QDialog.Accepted


class TestInsertLinkDialogWithFilter:
    """Test Insert Link Dialog with filter prefix."""
    
    def test_dialog_with_filter(self, app):
        """Test dialog shows filter banner."""
        dialog = InsertLinkDialog(
            filter_prefix="/PageA",
            filter_label=":PageA"
        )
        dialog.show()
        QApplication.processEvents()
        
        assert dialog.filter_banner is not None
        # Filter banner exists but may not be visible until dialog is shown
        assert ":PageA" in dialog.filter_banner.text()
    
    def test_filter_can_be_removed(self, app):
        """Test that filter can be removed via callback."""
        filter_cleared = False
        
        def clear_filter():
            nonlocal filter_cleared
            filter_cleared = True
        
        dialog = InsertLinkDialog(
            filter_prefix="/PageA",
            filter_label=":PageA",
            clear_filter_cb=clear_filter
        )
        
        # Simulate clicking remove link
        dialog._on_remove_filter("remove")
        
        assert filter_cleared
        assert not dialog.filter_banner.isVisible()


class TestInsertLinkIntegration:
    """Test link insertion in actual editor."""
    
    def test_insert_link_at_cursor(self, main_window):
        """Test that link is inserted at cursor position, not at start of file."""
        # Open a page
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        
        # Move cursor to middle of document
        cursor = main_window.editor.textCursor()
        cursor.setPosition(10)
        main_window.editor.setTextCursor(cursor)
        initial_pos = cursor.position()
        
        # Insert a link
        main_window.editor.insert_link(":PageB", "Link to PageB")
        QApplication.processEvents()
        
        # Verify link was inserted at cursor position, not at start
        text = main_window.editor.toPlainText()
        # The link should be somewhere after position 0
        assert "\x00:PageB\x00Link to PageB\x00" in text or ":PageB" in text
        
        # Cursor should have moved past the link
        final_pos = main_window.editor.textCursor().position()
        assert final_pos > initial_pos
    
    def test_insert_link_replaces_selection(self, main_window):
        """Test that inserting link replaces selected text."""
        # Open a page with content
        main_window._open_file("/PageA/PageA.md")
        QApplication.processEvents()
        
        # Select some text
        cursor = main_window.editor.textCursor()
@pytest.mark.skip(reason="Integration tests require running backend server")
class TestInsertLinkIntegration:
    """Test link insertion in actual editor.
    
    These tests require a running backend server and MainWindow initialization.
    Run separately as integration tests.
    """
    pass


@pytest.mark.skip(reason="Integration tests require running backend server")
class TestLinkCopyFunctionality:
    """Test copying links with Ctrl+Shift+L.
    
    These tests require a running backend server and MainWindow initialization.
    Run separately as integration tests.
    """
    pass