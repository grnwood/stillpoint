from __future__ import annotations

from typing import TYPE_CHECKING
from PySide6.QtCore import Qt, QByteArray, QTimer, QRectF, QSize
from PySide6.QtGui import QKeyEvent, QPainter, QTextDocument, QAbstractTextDocumentLayout
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QStyledItemDelegate,
    QStyle,
)

from sp.app import config
from .path_utils import path_to_colon
import html
import re

if TYPE_CHECKING:
    import httpx


class HTMLDelegate(QStyledItemDelegate):
    """Custom delegate to render HTML in list items."""
    
    def paint(self, painter: QPainter, option, index):
        painter.save()
        
        # Get the HTML text from the item
        text = index.data(Qt.DisplayRole)
        
        # Create a QTextDocument to render HTML
        doc = QTextDocument()
        doc.setHtml(text)
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(2)
        
        # Set the width to match the item width
        doc.setTextWidth(option.rect.width())
        
        # Draw background if selected
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            # Adjust text color for selection
            doc.setDefaultStyleSheet("body { color: white; }")
            doc.setHtml(text)  # Re-parse with new stylesheet
        
        # Translate painter to item position
        painter.translate(option.rect.topLeft())
        
        # Render the document
        doc.drawContents(painter)
        
        painter.restore()
    
    def sizeHint(self, option, index):
        text = index.data(Qt.DisplayRole)
        doc = QTextDocument()
        doc.setHtml(text)
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(2)
        doc.setTextWidth(option.rect.width() if option.rect.width() > 0 else 400)
        size = doc.size()
        return QSize(int(size.width()), int(size.height()))


class JumpToPageDialog(QDialog):
    def __init__(
        self,
        parent=None,
        filter_prefix: str | None = None,
        filter_label: str | None = None,
        clear_filter_cb=None,
        *,
        compact: bool = False,
        geometry_key: str | None = "jump_dialog",
        anchor_global_pos=None,
        show_rewrite_links_checkbox: bool = False,
        http_client: "httpx.Client" = None,
        remote_mode: bool = False,
        launch_mode: str = "jump",
        current_page_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._launch_mode = launch_mode  # 'jump', 'insert_link', or 'create_new'
        self._current_page_path = current_page_path
        self._has_matching_pages = True
        self.setWindowTitle(self._get_title())
        self.setModal(True)
        self._filter_prefix = filter_prefix
        self._filter_label = filter_label
        self._clear_filter_cb = clear_filter_cb
        self._compact = bool(compact)
        self._geometry_key = geometry_key
        self._anchor_global_pos = anchor_global_pos
        self._show_rewrite_links_checkbox = show_rewrite_links_checkbox
        self.rewrite_links_checkbox = None
        self.http = http_client
        self._remote_mode = remote_mode
        
        # Set up geometry save timer (debounced)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)
        
        # Make dialog same size as insert link dialog
        self.resize(520, 280) if self._compact else self.resize(640, 360)
        layout = QVBoxLayout()

        if self._filter_prefix:
            self.filter_banner = QLabel()
            self.filter_banner.setTextFormat(Qt.RichText)
            self.filter_banner.setTextInteractionFlags(Qt.TextBrowserInteraction)
            self.filter_banner.setOpenExternalLinks(False)
            label = self._filter_label or self._filter_prefix
            self.filter_banner.setText(
                f"<div style='background:#c62828; color:#ffffff; padding:6px; font-weight:bold;'>"
                f"Filtered by {label} "
                f"(<a href='remove' style='color:#ffffff; text-decoration:underline;'>Remove</a>)"
                f"</div>"
            )
            self.filter_banner.linkActivated.connect(self._on_remove_filter)
            layout.addWidget(self.filter_banner)
        else:
            self.filter_banner = None

        # Add checkbox for link rewriting option (for move operations)
        if self._show_rewrite_links_checkbox:
            from PySide6.QtWidgets import QCheckBox
            self.rewrite_links_checkbox = QCheckBox("Rewrite target page links")
            self.rewrite_links_checkbox.setChecked(True)  # Pre-selected
            layout.addWidget(self.rewrite_links_checkbox)

        self.search = QLineEdit()
        self.search.setPlaceholderText("Start typing to filter pages…")
        self.search.textChanged.connect(self._refresh)
        self.search.returnPressed.connect(self._activate_current)
        layout.addWidget(self.search)

        self.list_widget = QListWidget()
        self.list_widget.setItemDelegate(HTMLDelegate(self.list_widget))
        self.list_widget.itemDoubleClicked.connect(self.accept)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.setLayout(layout)
        
        # Restore saved geometry after layout is set up
        if self._geometry_key:
            self._restore_geometry()
        elif self._anchor_global_pos is not None:
            self._position_near_anchor()
        
        self.search.setFocus()
        self._refresh()

    def selected_path(self) -> str | None:
        item = self.list_widget.currentItem()
        if item:
            return item.data(Qt.UserRole)
        # If no selection but in insert_link/create_new mode with search text, return new page path
        if self._launch_mode in ("insert_link", "create_new") and not self._has_matching_pages:
            search_term = self.search.text().strip()
            if search_term:
                return self._generate_new_page_path(search_term)
        return None
    
    def should_rewrite_links(self) -> bool:
        """Return whether link rewriting is enabled (only relevant for move operations)."""
        if self.rewrite_links_checkbox:
            return self.rewrite_links_checkbox.isChecked()
        return True  # Default to true if checkbox not shown

    def keyPressEvent(self, event):  # type: ignore[override]
        # Handle arrow keys and vi-mode shortcuts (Shift+J/K)
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            previous_focus = self.focusWidget()
            QApplication.sendEvent(self.list_widget, event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            event.accept()
            return
        # Handle Shift+J (down) and Shift+K (up) as arrow key equivalents
        elif event.key() == Qt.Key_J and (event.modifiers() & Qt.ShiftModifier):
            # Directly manipulate list selection instead of sending synthetic events
            current_row = self.list_widget.currentRow()
            if current_row < self.list_widget.count() - 1:
                self.list_widget.setCurrentRow(current_row + 1)
            event.accept()
            return
        elif event.key() == Qt.Key_K and (event.modifiers() & Qt.ShiftModifier):
            # Directly manipulate list selection instead of sending synthetic events
            current_row = self.list_widget.currentRow()
            if current_row > 0:
                self.list_widget.setCurrentRow(current_row - 1)
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._activate_current():
                return
        super().keyPressEvent(event)

    def _activate_current(self) -> bool:
        if self.list_widget.currentItem():
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        term = self.search.text().strip()
        if term.startswith(":"):
            term = term.lstrip(":")
        
        # Get pages from remote API or local config
        if self._remote_mode and self.http:
            try:
                resp = self.http.get("/api/pages/search", params={"q": term, "limit": 100})
                resp.raise_for_status()
                pages = resp.json().get("pages", [])
            except Exception as exc:
                print(f"[JumpDialog] Failed to fetch pages from API: {exc}")
                pages = []
        else:
            pages = config.search_pages(term)
        
        self.list_widget.clear()
        for page in pages:
            if self._filter_prefix and not page["path"].startswith(self._filter_prefix):
                continue
            item = QListWidgetItem(self._display_label(page))
            item.setData(Qt.UserRole, page["path"])
            self.list_widget.addItem(item)
        
        # Track if we have matching pages and update title accordingly
        self._has_matching_pages = self.list_widget.count() > 0
        
        # If no matches in insert_link mode, show create option
        if not self._has_matching_pages and self._launch_mode == "insert_link" and term:
            new_path = self._generate_new_page_path(term)
            item = QListWidgetItem(f"<i>Create new page: {html.escape(term)}</i>")
            item.setData(Qt.UserRole, new_path)
            self.list_widget.addItem(item)
        
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        
        # Update title based on whether we have matches
        self.setWindowTitle(self._get_title())

    def _display_label(self, page: dict) -> str:
        """Format display label with search term highlighting."""
        full_path = page.get("path") or ""
        title = page.get("title") or ""
        if self._filter_prefix and full_path.startswith(self._filter_prefix):
            rel = full_path[len(self._filter_prefix) :].lstrip("/")
            rel_colon = path_to_colon("/" + rel) if rel else path_to_colon(full_path)
            display_text = rel_colon or rel or title or full_path
        else:
            pretty_path = path_to_colon(full_path)
            display_text = f"{title} — {pretty_path}" if title else pretty_path
        
        # Apply search term highlighting
        return self._highlight_search_term(display_text)
    
    def _restore_geometry(self) -> None:
        """Restore saved dialog geometry."""
        if not self._geometry_key:
            return
        saved_geometry = config.load_dialog_geometry(self._geometry_key)
        if saved_geometry:
            try:
                print(f"[Dialog] Restoring jump dialog geometry: {len(saved_geometry)} chars")
                geometry_bytes = QByteArray.fromBase64(saved_geometry.encode('ascii'))
                result = self.restoreGeometry(geometry_bytes)
                print(f"[Dialog] Jump dialog geometry restore result: {result}")
            except Exception as e:
                print(f"[Dialog] Failed to restore jump dialog geometry: {e}")
        else:
            print("[Dialog] No saved jump dialog geometry found")

    def _position_near_anchor(self) -> None:
        """Position dialog near a global cursor point, clamped to the visible screen."""
        try:
            from PySide6.QtGui import QGuiApplication
        except Exception:
            return
        anchor = self._anchor_global_pos
        if anchor is None:
            return
        try:
            screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
            if not screen:
                return
            avail = screen.availableGeometry()
            # Prefer below-right of the cursor, with a small offset.
            x = anchor.x() + 12
            y = anchor.y() + 12
            w = self.width()
            h = self.height()
            x = max(avail.left(), min(x, avail.right() - w))
            y = max(avail.top(), min(y, avail.bottom() - h))
            self.move(x, y)
        except Exception:
            return
    
    def _save_geometry(self) -> None:
        """Save current dialog geometry."""
        if not self._geometry_key:
            return
        try:
            geometry_bytes = self.saveGeometry()
            geometry_b64 = geometry_bytes.toBase64().data().decode('ascii')
            config.save_dialog_geometry(self._geometry_key, geometry_b64)
            print(f"[Dialog] Saved jump dialog geometry: {len(geometry_b64)} chars")
        except Exception as e:
            print(f"[Dialog] Failed to save jump dialog geometry: {e}")
    
    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Handle dialog resize: save geometry with debounce."""
        super().resizeEvent(event)
        self.geometry_save_timer.start()

    def _on_remove_filter(self, link: str) -> None:
        if self._clear_filter_cb:
            try:
                self._clear_filter_cb()
            except Exception:
                pass
        self._filter_prefix = None
        if self.filter_banner:
            self.filter_banner.hide()
        self._refresh()
    
    def _get_title(self) -> str:
        """Generate dialog title based on launch mode and search state."""
        if self._launch_mode == "insert_link":
            if not self._has_matching_pages and self.search.text().strip():
                vault_level = self._get_current_vault_level()
                return f"Create New Page in {vault_level}"
            return "Insert Link to Page"
        return "Jump to Page"
    
    def _get_current_vault_level(self) -> str:
        """Get the current vault level (folder hierarchy) from current page path."""
        if not self._current_page_path:
            return "Vault Root"
        # Extract folder path from current page
        # Path format: /Folder1/Folder2/Page/Page.md
        parts = self._current_page_path.strip("/").split("/")
        if len(parts) <= 1:
            return "Vault Root"
        # Return parent folder (where current page lives)
        return parts[-2] if len(parts) >= 2 else "Vault Root"
    
    def _generate_new_page_path(self, page_name: str) -> str:
        """Generate path for a new page at the current vault level."""
        # Clean up the page name
        clean_name = page_name.strip().replace("/", "_")
        if not self._current_page_path:
            # Create at root
            return f"/{clean_name}/{clean_name}.md"
        # Get parent folder from current page path
        parts = self._current_page_path.strip("/").split("/")
        if len(parts) <= 1:
            return f"/{clean_name}/{clean_name}.md"
        # Create in same folder as current page
        parent = parts[-2] if len(parts) >= 2 else ""
        if parent:
            return f"/{parent}/{clean_name}/{clean_name}.md"
        return f"/{clean_name}/{clean_name}.md"
    
    def _highlight_search_term(self, text: str) -> str:
        """Highlight search term in text using HTML."""
        search_term = self.search.text().strip()
        if not search_term or len(search_term) < 2:
            # Escape HTML but don't highlight
            return html.escape(text)
        
        # Escape the text first
        escaped_text = html.escape(text)
        
        # Escape the search term for regex
        escaped_search = re.escape(search_term)
        
        # Case-insensitive highlighting with bold styling
        pattern = re.compile(f"({escaped_search})", re.IGNORECASE)
        highlighted = pattern.sub(r'<span style="font-weight: bold; font-size: 105%;">\1</span>', escaped_text)
        
        return highlighted
    
    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Save dialog geometry when closing."""
        self.geometry_save_timer.stop()  # Cancel any pending save
        self._save_geometry()  # Immediate save on close
        super().closeEvent(event)
