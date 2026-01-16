from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFormLayout,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QDialogButtonBox,
)

from sp.app import config
from .path_utils import path_to_colon, normalize_link_target
from .insert_link_dialog import HTMLDelegate
import html
import re


class EditLinkDialog(QDialog):
    """Dialog to edit a link with separate display text and target.

    - Link to: colon-notation path selected via type-ahead list (like Jump/Insert link)
    - Link text: free text to display in the editor
    """

    def _activate_current(self) -> bool:
        """Accept the dialog as if OK was pressed, for Enter key handling."""
        # Optionally, add validation here if needed
        self.accept()
        return True

    def __init__(self, link_to: str = "", link_text: str = "", parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Link")
        self.resize(520, 420)
        self.setModal(True)
        self.setWindowModality(Qt.ApplicationModal)
        
        # Track whether user has manually edited the link name
        self._link_name_manually_edited = bool(link_text)  # True if editing existing link with text

        layout = QVBoxLayout(self)
        form = QFormLayout()

        # Check if it's an HTTP URL - if so, don't normalize it
        if link_to.startswith(("http://", "https://")):
            normalized_link = link_to
        else:
            normalized_link = normalize_link_target(link_to)
        self.search_edit = QLineEdit(normalized_link)
        self.search_edit.setPlaceholderText("Type to filter pages or paste HTTP URL…")
        self.search_edit.textChanged.connect(self._on_search_changed)
        form.addRow("Link to:", self.search_edit)

        self.text_edit = QLineEdit(link_text or normalized_link)
        self.text_edit.setPlaceholderText("Display name (defaults to link target)")
        self.text_edit.textChanged.connect(self._on_link_name_changed)
        self.text_edit.installEventFilter(self)
        form.addRow("Link Name:", self.text_edit)

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

        self._force_list_sync = False
        self._refresh()

    def eventFilter(self, obj, event):  # type: ignore[override]
        # Let default behavior apply; we avoid resetting manual-edit tracking so free typing is preserved
        return super().eventFilter(obj, event)
    
    def _accept_from_list(self):
        item = self.list_widget.currentItem()
        if item:
            normalized = item.data(Qt.UserRole)
            self.search_edit.setText(normalized)
            self.accept()
    
    def _on_search_changed(self):
        """Called when user types in the search field."""
        text = self.search_edit.text().strip()
        # If typing an HTTP URL, skip page search
        if text.startswith(("http://", "https://")):
            self.list_widget.clear()
            # Update link name if not manually edited
            if not self._link_name_manually_edited:
                self.text_edit.setText(text)
            return
        # Refresh suggestions without forcing a selection
        self._refresh()
        # Update link name if not manually edited
        if not self._link_name_manually_edited:
            self.text_edit.setText(self.search_edit.text())
    
    def _on_link_name_changed(self):
        """Track that user has manually edited the link name."""
        self._link_name_manually_edited = True
    
    def _on_selection_changed(self, current, previous):
        """Called when user navigates through the list with arrow keys."""
        if current:
            self._sync_to_current_list_item(force=self._force_list_sync or self.list_widget.hasFocus())
        self._force_list_sync = False

    def _refresh(self) -> None:
        orig_term = self.search_edit.text().strip()
        search_term = orig_term
        if "#" in search_term:
            search_term = search_term.split("#", 1)[0].strip()
        normalized_term = search_term.lstrip(":")
        pages = config.search_pages(normalized_term)
        self.list_widget.clear()
        for page in pages:
            colon = path_to_colon(page["path"]) or ""
            if not colon:
                continue
            normalized_colon = normalize_link_target(colon)
            display_text = self._display_label(page, normalized_colon)
            item = QListWidgetItem(display_text)
            item.setToolTip(display_text)
            item.setData(Qt.UserRole, normalized_colon)
            self.list_widget.addItem(item)
        # Do not auto-select an item; user can choose via arrows/double-click

    def keyPressEvent(self, event):  # type: ignore[override]
        # Handle arrow keys and vi-mode shortcuts (Shift+J/K)
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            previous_focus = self.focusWidget()
            if previous_focus in (self.search_edit, self.list_widget, self.text_edit):
                self._force_list_sync = True
                QApplication.sendEvent(self.list_widget, event)
                if previous_focus is not self.list_widget:
                    previous_focus.setFocus()
                self._sync_to_current_list_item(force=True)
                return
        elif event.key() == Qt.Key_J and (event.modifiers() & Qt.ShiftModifier):
            previous_focus = self.focusWidget()
            down_event = QKeyEvent(event.type(), Qt.Key_Down, Qt.NoModifier)
            self._force_list_sync = True
            QApplication.sendEvent(self.list_widget, down_event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            self._sync_to_current_list_item(force=True)
            return
        elif event.key() == Qt.Key_K and (event.modifiers() & Qt.ShiftModifier):
            previous_focus = self.focusWidget()
            up_event = QKeyEvent(event.type(), Qt.Key_Up, Qt.NoModifier)
            self._force_list_sync = True
            QApplication.sendEvent(self.list_widget, up_event)
            if previous_focus is not self.list_widget:
                previous_focus.setFocus()
            self._sync_to_current_list_item(force=True)
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self._activate_current():
                return
        super().keyPressEvent(event)

    def _sync_to_current_list_item(self, force: bool = False) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        colon_path = item.data(Qt.UserRole)
        if not colon_path:
            return
        if force or self.list_widget.hasFocus():
            self.search_edit.blockSignals(True)
            self.search_edit.setText(colon_path)
            self.search_edit.blockSignals(False)
            self.text_edit.blockSignals(True)
            self.text_edit.setText(colon_path)
            self.text_edit.blockSignals(False)
            self._link_name_manually_edited = False

    def link_to(self) -> str:
        text = self.search_edit.text().strip()
        # Clean any line breaks or paragraph separators
        text = text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()
        # Don't normalize HTTP URLs
        if text.startswith(("http://", "https://")):
            return text
        return normalize_link_target(text)

    def link_text(self) -> str:
        text = self.text_edit.text().strip()
        # Clean any line breaks or paragraph separators
        return text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()

    def _display_label(self, page: dict, colon_path: str) -> str:
        """Format like insert link dialog: title then path, with search highlighting."""
        title = page.get("title", "")
        normalized_colon = normalize_link_target(colon_path)
        display_text = f"{title} — {normalized_colon}" if title else normalized_colon
        return self._highlight_search_term(display_text)

    def _highlight_search_term(self, text: str) -> str:
        """Highlight the current search term using the same styling as insert link."""
        search_term = self.search_edit.text().strip()
        if not search_term or len(search_term) < 2:
            return html.escape(text)
        escaped_text = html.escape(text)
        escaped_search = re.escape(search_term)
        pattern = re.compile(f"({escaped_search})", re.IGNORECASE)
        return pattern.sub(r'<span style="font-weight: bold; font-size: 105%;">\\1</span>', escaped_text)
