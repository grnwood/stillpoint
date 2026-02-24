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
    Accepted = QDialog.DialogCode.Accepted
    Rejected = QDialog.DialogCode.Rejected

    def __init__(
        self,
        parent=None,
        selected_text: str = "",
        filter_prefix: str | None = None,
        filter_label: str | None = None,
        clear_filter_cb=None,
        current_page_path: str | None = None,
        editing: bool = False,
        initial_link_target: str | None = None,
        initial_link_label: str | None = None,
        as_popup: bool = False,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit Link" if editing else "Insert Link")
        self._as_popup = as_popup
        if as_popup:
            self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
            self.setModal(False)
        else:
            self.setModal(True)
            self.setWindowModality(Qt.ApplicationModal)
        self._filter_prefix = filter_prefix
        self._filter_label = filter_label
        self._clear_filter_cb = clear_filter_cb
        self._current_page_path = current_page_path
        self._launched_with_selection = False
        self._seeded_text = ""
        self._create_new_selected = False
        self._create_new_target: str | None = None
        self._accepted_target: str | None = None
        
        # Set up geometry save timer (debounced)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)
        
        # Focus timer for popup mode
        if as_popup:
            self._focus_timer = QTimer(self)
            self._focus_timer.setInterval(150)
            self._focus_timer.timeout.connect(self._ensure_search_focus)
        else:
            self._focus_timer = None
        
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
        
        if selected_text:
            self._refresh()
        else:
            self.list_widget.clear()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        # Ensure the search field takes focus when the dialog appears
        if self._as_popup:
            self._ensure_search_focus()
            if self._focus_timer:
                self._focus_timer.start()
        else:
            QTimer.singleShot(0, self.search.setFocus)

    def hideEvent(self, event):  # type: ignore[override]
        if self._focus_timer:
            self._focus_timer.stop()
        super().hideEvent(event)

    def _ensure_search_focus(self) -> None:
        if not self.isVisible():
            return
        if self.search.hasFocus():
            return
        try:
            self.raise_()
            self.activateWindow()
            self.search.setFocus()
        except Exception:
            pass

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
        if self._create_new_selected and self._create_new_target:
            return self._create_new_target
        if self._accepted_target:
            return self._accepted_target
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

    def should_create_new_page(self) -> bool:
        """Return True when the selected action is explicit 'create new page'."""
        return self._create_new_selected and bool(self._create_new_target)

    def _accept_from_list(self):
        """Accept dialog when item in list is double-clicked."""
        item = self.list_widget.currentItem()
        if item:
            payload = item.data(Qt.UserRole)
            if isinstance(payload, dict) and payload.get("create"):
                target = str(payload.get("target") or "").strip()
                self._create_new_selected = bool(target)
                self._create_new_target = target or None
                self._accepted_target = None
                self.accept()
                return
            colon_path = str(payload or "")
            if colon_path:
                self._accepted_target = self._apply_current_anchor(colon_path)
            self._create_new_selected = False
            self._create_new_target = None
            self.accept()

    def _on_search_changed(self):
        """Called when user types in the search field."""
        if self._ignore_search_change:
            return
        self._accepted_target = None
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
            payload = current.data(Qt.UserRole)
            if isinstance(payload, dict) and payload.get("create"):
                target = str(payload.get("target") or "").strip()
                self._create_new_selected = bool(target)
                self._create_new_target = target or None
                self._accepted_target = None
                return
            self._create_new_selected = False
            self._create_new_target = None
            colon_path = str(payload or "")
            if colon_path:
                self._accepted_target = self._apply_current_anchor(colon_path)
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
        # If list has focus, allow type-ahead into the search field
        previous_focus = self.focusWidget()
        if previous_focus is self.list_widget:
            key = event.key()
            text = event.text()
            if text and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
                self.search.setFocus()
                self.search.insert(text)
                event.accept()
                return
            if key in (Qt.Key_Backspace, Qt.Key_Delete):
                self.search.setFocus()
                QApplication.sendEvent(self.search, event)
                event.accept()
                return
        # Handle arrow keys and vi-mode shortcuts (Shift+J/K)
        if event.key() in (Qt.Key_Up, Qt.Key_Down):
            # Only pass arrow keys to list if search field has focus
            # Don't interfere with arrow keys in link_name field
            if previous_focus is self.search or previous_focus is self.list_widget:
                QApplication.sendEvent(self.list_widget, event)
                if previous_focus is not self.list_widget:
                    previous_focus.setFocus()
                event.accept()
                return
        # Handle Ctrl+Shift+J/K for vi insert mode
        mods = event.modifiers() & ~Qt.KeypadModifier
        if event.key() == Qt.Key_J and (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
            current_row = self.list_widget.currentRow()
            if current_row < self.list_widget.count() - 1:
                self.list_widget.setCurrentRow(current_row + 1)
            elif self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(0)  # Wrap to top
            event.accept()
            return
        elif event.key() == Qt.Key_K and (mods & Qt.ControlModifier) and (mods & Qt.ShiftModifier):
            current_row = self.list_widget.currentRow()
            if current_row > 0:
                self.list_widget.setCurrentRow(current_row - 1)
            elif self.list_widget.count() > 0:
                self.list_widget.setCurrentRow(self.list_widget.count() - 1)  # Wrap to bottom
            event.accept()
            return
        # Handle Shift+J (down) and Shift+K (up) as arrow key equivalents
        elif event.key() == Qt.Key_J and (mods & Qt.ShiftModifier) and not (mods & Qt.ControlModifier):
            # Directly manipulate list selection instead of sending synthetic events
            current_row = self.list_widget.currentRow()
            if current_row < self.list_widget.count() - 1:
                self.list_widget.setCurrentRow(current_row + 1)
            event.accept()
            return
        elif event.key() == Qt.Key_K and (mods & Qt.ShiftModifier) and not (mods & Qt.ControlModifier):
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

        # Prefer current list selection if present.
        if self._activate_current():
            return
        self.accept()

    def _activate_current(self) -> bool:
        """Accept dialog if an item is selected, or use what's typed in the search field."""
        item = self.list_widget.currentItem()
        if item:
            payload = item.data(Qt.UserRole)
            if isinstance(payload, dict) and payload.get("create"):
                target = str(payload.get("target") or "").strip()
                self._create_new_selected = bool(target)
                self._create_new_target = target or None
                self._accepted_target = None
                self.accept()
                return True
            colon_path = str(payload or "")
            if colon_path:
                self._accepted_target = self._apply_current_anchor(colon_path)
            self._create_new_selected = False
            self._create_new_target = None
            self.accept()
            return True
        elif self.search.text().strip():
            self._accepted_target = None
            self._create_new_selected = False
            self._create_new_target = None
            self.accept()
            return True
        return False

    def _refresh(self) -> None:
        """Refresh the list of pages based on search term."""
        term = self.search.text().strip()
        if not term:
            self.list_widget.clear()
            self._accepted_target = None
            self._create_new_selected = False
            self._create_new_target = None
            return

        search_term, anchor = self._split_anchor(term)
        normalized_term = search_term.lstrip(":")
        if ":" in normalized_term:
            normalized_term = normalized_term.replace(":", "/")
        query = normalized_term or search_term
        pages = config.search_pages(query)
        self.list_widget.clear()
        existing_exact = False
        create_target = ""
        create_target_base = ""
        if term and not term.startswith(("http://", "https://")):
            create_target = self._generate_create_target(search_term)
            if anchor and create_target and "#" not in create_target:
                create_target = f"{create_target}{anchor}"
            create_target_base, _anchor = self._split_anchor(create_target)
            create_target_base = normalize_link_target(create_target_base)

        for page in pages:
            if self._filter_prefix and not page["path"].startswith(self._filter_prefix):
                continue
            # Convert filesystem path to colon notation
            colon_path = path_to_colon(page["path"])
            if not colon_path:
                continue
            rooted_colon = normalize_link_target(":" + colon_path.lstrip(":"))
            if create_target_base and rooted_colon.lower() == create_target_base.lower():
                existing_exact = True
            display_text = self._display_label(page, colon_path)

            item = QListWidgetItem(display_text)
            item.setData(Qt.UserRole, rooted_colon)
            item.setToolTip(display_text)
            self.list_widget.addItem(item)

        if term and not term.startswith(("http://", "https://")) and not existing_exact:
            current_location = self._current_page_display()
            create_text = (
                f"<i>Create new page '{html.escape(term)}' at '{html.escape(current_location)}'</i>"
            )
            create_item = QListWidgetItem(create_text)
            create_item.setData(
                Qt.UserRole,
                {"create": True, "target": create_target},
            )
            create_item.setToolTip(create_text)
            self.list_widget.insertItem(0, create_item)

        # Keep a deterministic default selection for Enter.
        if self.list_widget.count() > 0:
            self.list_widget.setCurrentRow(0)
        else:
            self._accepted_target = None
            self._create_new_selected = False
            self._create_new_target = None
        if self.list_widget.count() == 0:
            self.list_widget.clearSelection()

    def _current_page_display(self) -> str:
        if not self._current_page_path:
            return "/"
        try:
            return path_to_colon(self._current_page_path) or self._current_page_path
        except Exception:
            return self._current_page_path

    def _generate_create_target(self, raw_name: str) -> str:
        clean = normalize_link_target(raw_name).lstrip(":")
        if not clean:
            return ""
        # If user typed a hierarchy explicitly, treat it as absolute colon path.
        if ":" in clean:
            return normalize_link_target(f":{clean}")
        if self._current_page_path:
            rel_current = self._current_page_path.strip("/")
            parts = rel_current.split("/") if rel_current else []
            parent_parts = parts[:-1] if parts else []
            parent_path = ":".join(p for p in parent_parts if p)
            if parent_path:
                return normalize_link_target(f":{parent_path}:{clean}")
        return normalize_link_target(f":{clean}")

    @staticmethod
    def _split_anchor(target: str) -> tuple[str, str]:
        text = (target or "").strip()
        if "#" not in text:
            return text, ""
        base, anchor = text.split("#", 1)
        anchor = anchor.strip()
        return base.strip(), f"#{anchor}" if anchor else ""

    def _current_anchor(self) -> str:
        _base, anchor = self._split_anchor(self.search.text())
        return anchor

    def _apply_current_anchor(self, target: str) -> str:
        normalized = normalize_link_target(target)
        if "#" in normalized:
            return normalized
        anchor = self._current_anchor()
        return f"{normalized}{anchor}" if anchor else normalized
    
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
