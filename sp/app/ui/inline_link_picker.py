"""Compact inline link picker overlays for quick-link and create-page triggers."""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Callable
from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QKeyEvent, QFont, QColor, QPainter, QTextDocument
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QWidget,
    QStyledItemDelegate,
    QStyle,
    QGraphicsDropShadowEffect,
)

from sp.app import config
from .path_utils import path_to_colon, normalize_link_target
from .screen_positioning import popup_available_geometry, clamp_popup_top_left
import html

if TYPE_CHECKING:
    from PySide6.QtCore import QPoint


class HTMLDelegate(QStyledItemDelegate):
    """Custom delegate to render HTML in list items."""
    
    def paint(self, painter: QPainter, option, index):
        painter.save()
        
        text = index.data(Qt.DisplayRole)
        doc = QTextDocument()
        doc.setHtml(text)
        doc.setDefaultFont(option.font)
        doc.setDocumentMargin(2)
        doc.setTextWidth(option.rect.width())
        
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, option.palette.highlight())
            doc.setDefaultStyleSheet("body { color: white; }")
            doc.setHtml(text)
        
        painter.translate(option.rect.topLeft())
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


class InlineLinkPickerOverlay(QDialog):
    """Compact popup overlay for quick-link lookup or create-page insertion."""
    
    def __init__(
        self,
        *,
        parent: QWidget,
        anchor: Optional[QPoint] = None,
        vi_mode_enabled: bool = False,
        filter_prefix: str | None = None,
        filter_label: str | None = None,
        clear_filter_cb: Callable[[], None] | None = None,
        current_page_path: str | None = None,
    ) -> None:
        super().__init__(parent)
        self._mode = "quick_link"
        self.setWindowTitle("Quick Link")
        self.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setModal(False)
        self._anchor = anchor
        self._vi_mode_enabled = vi_mode_enabled
        self._filter_prefix = filter_prefix
        self._filter_label = filter_label
        self._clear_filter_cb = clear_filter_cb
        self._current_page_path = current_page_path
        self._selected_path: str | None = None
        self._has_matching_pages = True
        self._is_new_page = False
        
        self._focus_timer = QTimer(self)
        self._focus_timer.setInterval(150)
        self._focus_timer.timeout.connect(self._ensure_search_focus)
        
        self._build_ui()
        
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)
        
        card = QFrame(self)
        card.setObjectName("InlineLinkCard")
        card.setStyleSheet(
            "QFrame#InlineLinkCard {"
            "  background: #0b0b0b;"
            "  border: 1px solid #1f1f1f;"
            "  border-radius: 8px;"
            "}"
        )
        try:
            shadow = QGraphicsDropShadowEffect(card)
            shadow.setBlurRadius(20)
            shadow.setOffset(0, 4)
            shadow.setColor(QColor(0, 0, 0, 100))
            card.setGraphicsEffect(shadow)
        except Exception:
            pass
        outer.addWidget(card, 1)
        
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)
        
        # Add filter banner if filtered
        if self._filter_prefix:
            self.filter_banner = QLabel(card)
            self.filter_banner.setTextFormat(Qt.RichText)
            self.filter_banner.setTextInteractionFlags(Qt.TextBrowserInteraction)
            self.filter_banner.setOpenExternalLinks(False)
            from .path_utils import path_to_colon
            label = self._filter_label or path_to_colon(self._filter_prefix) or self._filter_prefix
            self.filter_banner.setText(
                f"<div style='background:#c62828; color:#ffffff; padding:4px; font-size:10px; font-weight:bold;'>"
                f"Filtered by {html.escape(label)} "
                f"(<a href='remove' style='color:#ffffff; text-decoration:underline;'>Remove</a>)"
                f"</div>"
            )
            self.filter_banner.linkActivated.connect(self._on_remove_filter)
            layout.addWidget(self.filter_banner)
        else:
            self.filter_banner = None
        
        title = QLabel("Quick Link", card)
        title.setStyleSheet("font-weight: 600; font-size: 11px; color: #9fb7a9;")
        layout.addWidget(title)

        self.search = QLineEdit(card)
        self.search.setPlaceholderText("Type to search pages...")
        self.search.setFocusPolicy(Qt.StrongFocus)
        self.search.setStyleSheet(
            "padding: 6px;"
            "background: #111;"
            "color: #d6f5d6;"
            "border: 1px solid #1f1f1f;"
            "border-radius: 6px;"
            "font-size: 12px;"
        )
        self.search.textChanged.connect(self._on_search_changed)
        self.search.returnPressed.connect(self._on_search_return)
        self.search.installEventFilter(self)
        layout.addWidget(self.search)

        self.list_widget = QListWidget(card)
        self.list_widget.setFocusPolicy(Qt.NoFocus)
        self.list_widget.setStyleSheet(
            "QListWidget {"
            "  background: #0d0d0d;"
            "  color: #d6f5d6;"
            "  border: 1px solid #1a1a1a;"
            "  border-radius: 6px;"
            "  padding: 4px;"
            "  font-size: 11px;"
            "}"
            "QListWidget::item {"
            "  padding: 4px;"
            "  border-radius: 4px;"
            "}"
            "QListWidget::item:selected {"
            "  background: #1a4d2e;"
            "}"
        )
        self.list_widget.setItemDelegate(HTMLDelegate(self.list_widget))
        self.list_widget.currentItemChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self._accept_current)
        layout.addWidget(self.list_widget)
        hint_text = "↑↓ to navigate, Enter to select, Esc to cancel"

        hint = QLabel(hint_text, card)
        hint.setStyleSheet("color: #7b8f84; font-size: 10px;")
        layout.addWidget(hint)

        self.resize(420, 300)
        self._refresh()
    
    def _on_search_changed(self) -> None:
        self._refresh()
    
    def _on_search_return(self) -> None:
        if self.list_widget.currentItem():
            self._accept_current()
        elif not self._has_matching_pages:
            self._accept_current()
    
    def _on_selection_changed(self, current, previous) -> None:
        if current:
            self._selected_path = current.data(Qt.UserRole)
    
    def _accept_current(self) -> None:
        current = self.list_widget.currentItem()
        if current:
            payload = current.data(Qt.UserRole)
            if isinstance(payload, dict) and payload.get("create"):
                self._selected_path = str(payload.get("target") or "")
                self._is_new_page = True
            else:
                self._selected_path = str(payload or "")
                self._is_new_page = False
        elif not self._has_matching_pages:
            # Create new page from search term
            search_term = self.search.text().strip()
            if search_term:
                self._selected_path = self._generate_new_page_path(search_term)
                self._is_new_page = True
        self.accept()
    
    def _generate_new_page_path(self, term: str) -> str:
        """Generate a colon-separated path for a new page based on search term."""
        cleaned = normalize_link_target(term.strip().replace('/', ':')).lstrip(":")
        if not cleaned:
            return ""

        # If we have a current page, create in that page location (child path).
        if self._current_page_path:
            try:
                current_colon = path_to_colon(self._current_page_path)
                if current_colon:
                    return normalize_link_target(f":{current_colon}:{cleaned}")
                return normalize_link_target(f":{cleaned}")
            except Exception:
                pass

        # Fallback: create at root level
        return normalize_link_target(f":{cleaned}")

    def _refresh(self) -> None:
        """Refresh the list of matching pages."""
        term = self.search.text().strip()
        self.list_widget.clear()
        
        if not term:
            self._has_matching_pages = False
            return
        
        # Get matching pages from config
        try:
            # Use search_pages API which returns list of page dicts
            pages = config.search_pages(term)
            term_lower = term.lower()
            matches = []
            
            for page in pages:
                page_path = page.get("path", "")
                # Filter by prefix if active
                if self._filter_prefix and not page_path.startswith(self._filter_prefix):
                    continue
                colon_path = path_to_colon(page_path)
                if colon_path:
                    matches.append((page_path, colon_path))
            
            # Sort by relevance (exact match, starts with, contains)
            def sort_key(item):
                _, colon_path = item
                cp_lower = colon_path.lower()
                if cp_lower == term_lower:
                    return (0, colon_path)
                if cp_lower.startswith(term_lower):
                    return (1, colon_path)
                return (2, colon_path)
            
            matches.sort(key=sort_key)
            
            # Limit results
            matches = matches[:50]
            
            self._has_matching_pages = len(matches) > 0
            
            for page_path, colon_path in matches:
                # Highlight matching text
                display_text = html.escape(colon_path)
                if term_lower in display_text.lower():
                    import re
                    pattern = re.compile(f'({re.escape(term)})', re.IGNORECASE)
                    display_text = pattern.sub(r'<b>\1</b>', display_text)
                
                item = QListWidgetItem(display_text)
                item.setData(Qt.UserRole, colon_path)
                self.list_widget.addItem(item)

            if term:
                create_text = f"<i>Create '<b>{html.escape(term)}</b>' here...</i>"
                item = QListWidgetItem(create_text)
                item.setData(
                    Qt.UserRole,
                    {"create": True, "target": self._generate_new_page_path(term)},
                )
                self.list_widget.insertItem(0, item)
                self.list_widget.setCurrentRow(0)
            elif self._has_matching_pages:
                self.list_widget.setCurrentRow(0)
        
        except Exception as e:
            print(f"Error refreshing link picker: {e}")
            self._has_matching_pages = False
    
    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.search and event.type() == event.Type.KeyPress:
            key = event.key()
            mods = event.modifiers() & ~Qt.KeypadModifier  # Strip keypad modifier

            # Handle up/down arrows
            if key == Qt.Key_Up:
                self._move_selection(-1)
                return True
            elif key == Qt.Key_Down:
                self._move_selection(1)
                return True
            
            # Handle Ctrl+Shift+J/K for navigation (always available, not just vi mode)
            has_ctrl = bool(mods & Qt.ControlModifier)
            has_shift = bool(mods & Qt.ShiftModifier)
            has_alt = bool(mods & Qt.AltModifier)
            has_meta = bool(mods & Qt.MetaModifier)
            
            # Check for Ctrl+Shift+J (down) - only these two modifiers
            if key == Qt.Key_J and has_ctrl and has_shift and not has_alt and not has_meta:
                self._move_selection(1)
                return True
            # Check for Ctrl+Shift+K (up) - only these two modifiers
            elif key == Qt.Key_K and has_ctrl and has_shift and not has_alt and not has_meta:
                self._move_selection(-1)
                return True
            
            # Escape to cancel
            if key == Qt.Key_Escape:
                self.reject()
                return True
        
        return super().eventFilter(obj, event)
    
    def _move_selection(self, delta: int) -> None:
        """Move selection up or down by delta."""
        current_row = self.list_widget.currentRow()
        count = self.list_widget.count()
        if count == 0:
            return
        new_row = (current_row + delta) % count
        self.list_widget.setCurrentRow(new_row)
    
    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if self._anchor is not None:
            try:
                geo = popup_available_geometry(anchor=self._anchor, parent=self.parentWidget() or self)
                self.move(clamp_popup_top_left(self._anchor, self.size(), geo, margin=8))
            except Exception:
                pass
        self._ensure_search_focus()
        self._focus_timer.start()
    
    def hideEvent(self, event) -> None:  # type: ignore[override]
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
    
    def selected_path(self) -> str | None:
        """Return the selected colon path."""
        return self._selected_path
    
    def is_new_page(self) -> bool:
        """Return whether the selected page is a new page creation."""
        return self._is_new_page
    
    def _on_remove_filter(self, link: str) -> None:
        """Handle click on filter banner remove link."""
        if self._clear_filter_cb:
            try:
                self._clear_filter_cb()
            except Exception:
                pass
        self._filter_prefix = None
        if self.filter_banner:
            self.filter_banner.hide()
        self._refresh()
