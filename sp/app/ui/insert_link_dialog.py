"""Dialog for inserting links to other pages in colon notation."""
from __future__ import annotations

from PySide6.QtCore import Qt, QByteArray, QTimer, QRectF, QSize
from PySide6.QtGui import QKeyEvent, QPainter, QTextDocument, QAbstractTextDocumentLayout
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QVBoxLayout,
    QStyledItemDelegate,
    QStyle,
)

from sp.app import config
from .path_utils import path_to_colon, normalize_link_target
import html
import re


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


class InsertLinkDialog(QDialog):
    """Dialog for searching and inserting page links in colon notation (PageA:PageB:PageC)."""

    def __init__(
        self,
        parent=None,
        selected_text: str = "",
        filter_prefix: str | None = None,
        filter_label: str | None = None,
        clear_filter_cb=None,
        editing: bool = False,
        initial_link_target: str | None = None,
        initial_link_label: str | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Link" if editing else "Insert Link")
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        self._filter_prefix = filter_prefix
        self._filter_label = filter_label
        self._clear_filter_cb = clear_filter_cb
        self._launched_with_selection = False
        self._seeded_text = ""
        
        # Set up geometry save timer (debounced)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)
        
        # Make dialog wider than tall (~80 chars wide)
        self.resize(640, 360)
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

        # Track whether user has manually edited the link name
        self._link_name_manually_edited = False
        self._ignore_search_change = False
        normalized_initial_target: str | None = None
        initial_label_clean: str | None = None

        # Form layout for Link to and Link Name fields
        form = QFormLayout()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Type to search pages…")
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._on_search_return)
        # Disable autocomplete to prevent Qt from suggesting completions
        self.search.setCompleter(None)
        form.addRow("Link to:", self.search)

        self.link_name = QLineEdit()
        self.link_name.setPlaceholderText("Display name (optional)")
        self.link_name.textChanged.connect(self._on_link_name_changed)
        self.link_name.returnPressed.connect(self._activate_current)
        self.link_name.installEventFilter(self)
        # Completely disable autocomplete
        self.link_name.setCompleter(None)
        # Also clear any auto-completion behavior
        try:
            from PySide6.QtWidgets import QCompleter
            empty_completer = QCompleter([])
            empty_completer.setCompletionMode(QCompleter.NoCompletion)
            self.link_name.setCompleter(empty_completer)
        except Exception:
            pass
        form.addRow("Link Name:", self.link_name)
        
        # Initialize link name with selected editor text when available
        seeded_text = self._prepare_selected_text(selected_text) or self._prepare_selected_text(
            self._pull_selected_text_from_parent(parent)
        )
        if seeded_text:
            self._seed_link_name(seeded_text, mark_as_selection=True)

        # Editing existing link: pre-fill target and label
        if initial_link_target:
            normalized_initial_target = (
                initial_link_target.strip()
                if initial_link_target.strip().startswith(("http://", "https://", "HTTP://", "HTTPS://"))
                else normalize_link_target(initial_link_target)
            )
        if initial_link_label:
            initial_label_clean = self._prepare_selected_text(initial_link_label) or None

        layout.addLayout(form)

        self.list_widget = QListWidget()
        self.list_widget.setItemDelegate(HTMLDelegate(self.list_widget))
        self.list_widget.itemDoubleClicked.connect(self._accept_from_list)
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        # Apply editing seed data now that widgets are ready
        if initial_label_clean:
            self._seed_link_name(initial_label_clean, mark_as_selection=False)
        if normalized_initial_target is not None:
            self.search.blockSignals(True)
            self.search.setText(normalized_initial_target)
            self.search.blockSignals(False)
            self._on_search_changed()

        self.setLayout(layout)
        
        # Restore saved geometry after layout is set up
        self._restore_geometry()
        
        self.search.setFocus()
        if selected_text:
            self._refresh()
        else:
            self.list_widget.clear()

    def _prepare_selected_text(self, text: str | None) -> str:
        """Normalize selected text for seeding the link name."""
        if not text:
            return ""
        return text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()

    def _pull_selected_text_from_parent(self, parent) -> str:
        """Best-effort grab of selected text from the parent editor if not explicitly provided."""
        try:
            editor = getattr(parent, "editor", None)
            if editor and hasattr(editor, "textCursor"):
                cursor = editor.textCursor()
                if cursor and cursor.hasSelection():
                    return cursor.selectedText()
        except Exception:
            pass
        return ""

    def _seed_link_name(self, clean_text: str, *, mark_as_selection: bool) -> None:
        """Apply selected text into the link name field and mark it as user-provided."""
        if mark_as_selection:
            self._launched_with_selection = True
            self._seeded_text = clean_text
        self.link_name.blockSignals(True)
        self.link_name.setText(clean_text)
        self.link_name.blockSignals(False)
        self._link_name_manually_edited = True

    def selected_colon_path(self) -> str | None:
        """Return the selected page in colon notation or HTTP URL."""
        text = self.search.text().strip()
        # Don't normalize HTTP URLs
        if text.startswith(("http://", "https://", "HTTP://", "HTTPS://")):
            return text or None
        if self._launched_with_selection and text == self._seeded_text:
            return text or None
        normalized = normalize_link_target(text)
        return normalized or None

    def selected_link_name(self) -> str | None:
        """Return the display name for the link, or None if empty."""
        name = self.link_name.text().strip()
        # Clean any line breaks or paragraph separators that might have been pasted
        name = name.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()
        return name or None

    def _accept_from_list(self):
        """Accept dialog when item in list is double-clicked."""
        item = self.list_widget.currentItem()
        if item:
            colon_path = item.data(Qt.UserRole)
            if colon_path:
                normalized = normalize_link_target(colon_path)
                self.search.setText(normalized)
            self.accept()

    def _on_search_changed(self):
        """Called when user types in the search field."""
        if self._ignore_search_change:
            return
        # If typing an HTTP URL, skip page search
        text = self.search.text().strip()
        if text.startswith(("http://", "https://")):
            self.list_widget.clear()
            # Auto-populate link name with URL if not manually edited
            if not self._link_name_manually_edited:
                self.link_name.blockSignals(True)
                self.link_name.setText(text)
                self.link_name.blockSignals(False)
            return
        # Auto-populate link name with typed text if not manually edited
        if not self._link_name_manually_edited and text:
            self.link_name.blockSignals(True)
            self.link_name.setText(text)
            self.link_name.blockSignals(False)
        self._refresh()

    def _on_link_name_changed(self):
        """Track that user has manually edited the link name."""
        self._link_name_manually_edited = True

    def _on_selection_changed(self, current, previous):
        """Called when user navigates through the list with arrow keys or Shift+J/K."""
        if current:
            colon_path = current.data(Qt.UserRole)
            if colon_path:
                # Update the search field with the selected item
                self._ignore_search_change = True
                self.search.blockSignals(True)
                self.search.setText(colon_path)
                self.search.blockSignals(False)
                self._ignore_search_change = False
                # Update link name if not manually edited
                if not self._link_name_manually_edited:
                    self.link_name.blockSignals(True)
                    self.link_name.setText(colon_path)
                    self.link_name.blockSignals(False)

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Event filter for link name field."""
        # Don't auto-select text to prevent interference with user input
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # type: ignore[override]
        # Handle arrow keys and vi-mode shortcuts (Shift+J/K)
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            # Only pass arrow keys to list if search field has focus
            # Don't interfere with arrow keys in link_name field
            previous_focus = self.focusWidget()
            if previous_focus is self.search or previous_focus is self.list_widget:
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

    def _on_search_return(self) -> None:
        """Handle Enter key in search field - create new page if needed."""
        # Check if current text matches an existing page
        current_text = self.search.text().strip()
        if not current_text:
            return
            
        # Check if exact match exists in list
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.UserRole) == current_text:
                # Exact match found, just accept
                self.list_widget.setCurrentItem(item)
                self.accept()
                return
        
        # No exact match - this will create a new page
        self.accept()

    def _activate_current(self) -> bool:
        """Accept dialog if an item is selected, or use what's typed in the search field."""
        item = self.list_widget.currentItem()
        if item:
            colon_path = item.data(Qt.UserRole)
            if colon_path:
                self.search.setText(colon_path)
            self.accept()
            return True
        elif self.search.text().strip():
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        """Refresh the list of pages based on search term."""
        term = self.search.text().strip()
        if not term:
            self.list_widget.clear()
            return

        search_term = term
        if "#" in search_term:
            search_term = search_term.split("#", 1)[0].strip()
        normalized_term = search_term.lstrip(":")
        if ":" in normalized_term:
            normalized_term = normalized_term.replace(":", "/")
        query = normalized_term or search_term
        pages = config.search_pages(query)
        self.list_widget.clear()

        for page in pages:
            if self._filter_prefix and not page["path"].startswith(self._filter_prefix):
                continue
            # Convert filesystem path to colon notation
            colon_path = path_to_colon(page["path"])
            if not colon_path:
                continue
            display_text = self._display_label(page, colon_path)

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, normalize_link_target(colon_path))
            item.setToolTip(display_text)
            self.list_widget.addItem(item)

        # Do not auto-select results; user controls selection via keyboard/mouse
        if self.list_widget.count() == 0:
            self.list_widget.clearSelection()
    
    def _restore_geometry(self) -> None:
        """Restore saved dialog geometry."""
        saved_geometry = config.load_dialog_geometry("insert_link_dialog")
        if saved_geometry:
            try:
                print(f"[Dialog] Restoring insert link dialog geometry: {len(saved_geometry)} chars")
                geometry_bytes = QByteArray.fromBase64(saved_geometry.encode('ascii'))
                result = self.restoreGeometry(geometry_bytes)
                print(f"[Dialog] Insert link dialog geometry restore result: {result}")
            except Exception as e:
                print(f"[Dialog] Failed to restore insert link dialog geometry: {e}")
        else:
            print("[Dialog] No saved insert link dialog geometry found")
    
    def _save_geometry(self) -> None:
        """Save current dialog geometry."""
        try:
            geometry_bytes = self.saveGeometry()
            geometry_b64 = geometry_bytes.toBase64().data().decode('ascii')
            config.save_dialog_geometry("insert_link_dialog", geometry_b64)
            print(f"[Dialog] Saved insert link dialog geometry: {len(geometry_b64)} chars")
        except Exception as e:
            print(f"[Dialog] Failed to save insert link dialog geometry: {e}")
    
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

    def _display_label(self, page: dict, colon_path: str) -> str:
        """Format display label with title first, then path (like jump dialog), with search highlighting."""
        title = page.get("title", "")
        if self._filter_prefix and page.get("path", "").startswith(self._filter_prefix):
            rel = page["path"][len(self._filter_prefix) :].lstrip("/")
            rel_colon = normalize_link_target(path_to_colon("/" + rel)) if rel else colon_path
            display_text = rel_colon or title or colon_path
        else:
            normalized_colon = normalize_link_target(colon_path)
            # Format like jump dialog: "Title — Path" or just "Path"
            display_text = f"{title} — {normalized_colon}" if title else normalized_colon
        
        # Apply search term highlighting
        return self._highlight_search_term(display_text)
    
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
        
        # Case-insensitive highlighting with subtle styling
        pattern = re.compile(f"({escaped_search})", re.IGNORECASE)
        highlighted = pattern.sub(r'<span style="font-weight: bold; font-size: 105%;">\1</span>', escaped_text)
        
        return highlighted
    
    def closeEvent(self, event) -> None:  # type: ignore[override]
        """Save dialog geometry when closing."""
        self.geometry_save_timer.stop()  # Cancel any pending save
        self._save_geometry()  # Immediate save on close
        super().closeEvent(event)
