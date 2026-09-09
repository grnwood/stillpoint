"""Tags tab widget for filtering pages by tags."""

from __future__ import annotations

from typing import TYPE_CHECKING
from PySide6.QtCore import QEvent, Qt, Signal, QRect, QSize, QTimer
from PySide6.QtGui import QColor, QCursor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QLabel,
    QScrollArea,
    QFrame,
    QLayout,
    QLayoutItem,
    QSizePolicy,
    QSplitter,
    QLineEdit,
)

from .path_utils import format_journal_day_label, path_to_colon
from .page_load_logger import measure_performance
from .keyboard_shortcuts import is_vi_navigation_chord
from sp.logging_flags import log_enabled
from sp.server.adapters.files import strip_page_suffix

if TYPE_CHECKING:
    import httpx


class FlowLayout(QLayout):
    """A layout that arranges widgets in a flow (wrapping like text)."""
    
    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)
        self.setSpacing(spacing)
        self.setContentsMargins(margin, margin, margin, margin)
        self.item_list = []
    
    def __del__(self):
        item = self.takeAt(0)
        while item:
            item = self.takeAt(0)
    
    def addItem(self, item):
        self.item_list.append(item)
    
    def count(self):
        return len(self.item_list)
    
    def itemAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list[index]
        return None
    
    def takeAt(self, index):
        if 0 <= index < len(self.item_list):
            return self.item_list.pop(index)
        return None
    
    def expandingDirections(self):
        return Qt.Orientations(0)
    
    def hasHeightForWidth(self):
        return True
    
    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)
    
    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)
    
    def sizeHint(self):
        return self.minimumSize()
    
    def minimumSize(self):
        size = QSize()
        for item in self.item_list:
            size = size.expandedTo(item.minimumSize())
        margin = self.contentsMargins().left()
        size += QSize(2 * margin, 2 * margin)
        return size
    
    def _do_layout(self, rect, test_only):
        x = rect.x()
        y = rect.y()
        line_height = 0
        
        for item in self.item_list:
            widget = item.widget()
            space_x = self.spacing()
            space_y = self.spacing()
            
            next_x = x + item.sizeHint().width() + space_x
            if next_x - space_x > rect.right() and line_height > 0:
                x = rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0
            
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))
            
            x = next_x
            line_height = max(line_height, item.sizeHint().height())
        
        return y + line_height - rect.y()


from PySide6.QtCore import QPoint


class TagChicklet(QPushButton):
    """A clickable tag button."""
    
    def __init__(self, tag: str, parent=None, accent_color: str | None = None):
        super().__init__(f"#{tag}", parent)
        self.tag = tag
        self.selected = False
        self._accent_color = accent_color
        self.setCheckable(True)
        self.setStyleSheet(self._get_style())
        self.toggled.connect(self._on_toggled)

    def set_accent_color(self, accent_color: str | None) -> None:
        """Update the selected-state color to the active vault accent."""
        self._accent_color = accent_color
        self.setStyleSheet(self._get_style())

    @staticmethod
    def _contrast_text(color: QColor) -> str:
        """Choose whichever of black or white has the stronger WCAG contrast."""
        channels = []
        for value in (color.redF(), color.greenF(), color.blueF()):
            channels.append(
                value / 12.92
                if value <= 0.04045
                else ((value + 0.055) / 1.055) ** 2.4
            )
        luminance = 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
        black_contrast = (luminance + 0.05) / 0.05
        white_contrast = 1.05 / (luminance + 0.05)
        return "#111111" if black_contrast >= white_contrast else "#ffffff"

    def _selected_colors(self) -> tuple[str, str, str]:
        color = QColor(self._accent_color) if self._accent_color else self.palette().color(QPalette.Highlight)
        if not color.isValid():
            color = self.palette().color(QPalette.Highlight)
        background = color.name()
        foreground = self._contrast_text(color)
        hover = (
            color.lighter(112).name()
            if color.lightness() < 150
            else color.darker(108).name()
        )
        return background, foreground, hover

    def _selected_focus_colors(self) -> tuple[str, str]:
        """Give a selected chip an unmistakable filled keyboard-focus state."""
        color = QColor(self._accent_color) if self._accent_color else self.palette().color(QPalette.Highlight)
        if not color.isValid():
            color = self.palette().color(QPalette.Highlight)
        focused = color.lighter(140) if color.lightness() < 150 else color.darker(135)
        return focused.name(), self._contrast_text(focused)

    def _focus_ring_color(self) -> str:
        background, _foreground, _hover = self._selected_colors()
        return background

    def _unselected_hover_colors(self) -> tuple[str, str]:
        background = self.palette().color(QPalette.Button)
        hover = (
            background.lighter(125)
            if background.lightness() < 150
            else background.darker(112)
        )
        foreground = self._contrast_text(hover)
        return hover.name(), foreground
    
    def _get_style(self):
        """Get stylesheet for chicklet based on selection state."""
        if self.selected:
            background, foreground, hover = self._selected_colors()
            hover_text = self._contrast_text(QColor(hover))
            focus_background, focus_text = self._selected_focus_colors()
            return f"""
                QPushButton {{
                    background-color: {background};
                    color: {foreground};
                    border: 2px solid {background};
                    border-radius: 12px;
                    padding: 4px 12px;
                    margin: 2px;
                    font-weight: bold;
                }}
                QPushButton:hover {{
                    background-color: {hover};
                    color: {hover_text};
                }}
                QPushButton:focus {{
                    background-color: {focus_background};
                    color: {focus_text};
                    border: 2px solid {focus_background};
                }}
            """
        else:
            focus_ring = self._focus_ring_color()
            hover, hover_text = self._unselected_hover_colors()
            return f"""
                QPushButton {{
                    background-color: palette(button);
                    color: palette(buttonText);
                    border: 2px solid palette(dark);
                    border-radius: 12px;
                    padding: 4px 12px;
                    margin: 2px;
                }}
                QPushButton:hover {{
                    background-color: {hover};
                    color: {hover_text};
                    border: 2px solid palette(dark);
                }}
                QPushButton:focus {{
                    border: 3px solid {focus_ring};
                    padding: 3px 11px;
                    font-weight: bold;
                }}
            """
    
    def _on_toggled(self, checked: bool):
        """Handle toggle state change."""
        self.selected = checked
        self.setStyleSheet(self._get_style())


class TagsTab(QWidget):
    """Widget for filtering pages by tags."""
    
    # Signal emitted when user clicks a page to navigate
    pageNavigationRequested = Signal(str, int)  # path, line_number
    pageNavigationWithEditorFocusRequested = Signal(str, int)  # path, line_number
    
    def __init__(self, parent=None, http_client: "httpx.Client" = None):
        super().__init__(parent)
        self.http = http_client
        self.tag_chicklets = {}  # tag -> TagChicklet widget
        self.selected_tags = set()  # Currently selected tags
        self._tags_loaded = False  # Track if tags have been loaded
        self._tags_stale = False
        self._pending_new_tags: set[str] = set()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(120)
        self._refresh_timer.timeout.connect(self._refresh_stale_tags)
        self._vault_accent_color: str | None = None
        self._nav_filter_prefix = None
        self._filter_label = None
        self._clear_filter_cb = None
        
        self._init_ui()
        # Don't load tags immediately - wait for vault to be opened
    
    def _init_ui(self):
        """Initialize the UI layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        
        # Create scrollable area for tag chicklets with flow layout
        scroll_area = QScrollArea()
        self.tags_scroll_area = scroll_area
        scroll_area.setObjectName("tagsChipArea")
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(60)
        scroll_area.setFrameShape(QFrame.StyledPanel)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        self.tags_container = QWidget()
        self.tags_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.tags_layout = FlowLayout(self.tags_container, margin=4, spacing=4)
        self.tags_container.setLayout(self.tags_layout)
        scroll_area.setWidget(self.tags_container)
        
        # Tags header
        from PySide6.QtWidgets import QToolButton
        from PySide6.QtWidgets import QStyle
        from PySide6.QtGui import QPalette
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        tags_label = QLabel("Tags:")
        tags_label.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(tags_label)

        self.focus_indicator = QLabel()
        self.focus_indicator.setObjectName("tagsFocusIndicator")
        self.focus_indicator.hide()
        header_layout.addWidget(self.focus_indicator)

        header_layout.addStretch()

        # Refresh button
        pal = QApplication.instance().palette()
        tooltip_fg = pal.color(QPalette.ToolTipText).name()
        tooltip_bg = pal.color(QPalette.ToolTipBase).name()
        self.refresh_button = QToolButton()
        self.refresh_button.setIcon(self.style().standardIcon(QStyle.SP_BrowserReload))
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.refresh_button.setAutoRaise(True)
        self.refresh_button.setToolTip(
            f"<div style='color:{tooltip_fg}; background:{tooltip_bg}; padding:2px 4px;'>Refresh tags</div>"
        )
        self.refresh_button.clicked.connect(self.refresh_tags)
        header_layout.addWidget(self.refresh_button)

        # Search bar for filtering tags
        self.tag_search = QLineEdit()
        self.tag_search.setPlaceholderText("Search tags...")
        self.tag_search.setClearButtonEnabled(True)
        self.tag_search.textChanged.connect(self._filter_tags)
        self.tag_search.installEventFilter(self)
        
        tags_panel = QWidget()
        tags_panel_layout = QVBoxLayout()
        tags_panel_layout.setContentsMargins(0, 0, 0, 0)
        tags_panel_layout.setSpacing(4)
        tags_panel_layout.addLayout(header_layout)
        tags_panel_layout.addWidget(self.tag_search)
        tags_panel_layout.addWidget(scroll_area, 1)
        tags_panel.setLayout(tags_panel_layout)

        results_panel = QWidget()
        results_layout = QVBoxLayout()
        results_layout.setContentsMargins(0, 0, 0, 0)
        results_layout.setSpacing(0)
        # Results tree
        self.results_tree = QTreeWidget()
        self.results_tree.setHeaderLabels(["Pages"])
        self.results_tree.setHeaderHidden(True)
        self.results_tree.setRootIsDecorated(True)
        self.results_tree.itemDoubleClicked.connect(self._on_result_double_clicked)
        self.results_tree.installEventFilter(self)
        results_layout.addWidget(self.results_tree, 1)

        # Status label
        self.status_label = QLabel("Select tags to filter pages")
        self.status_label.setStyleSheet("color: gray; font-style: italic;")
        self.status_label.setMargin(0)
        results_layout.addWidget(self.status_label)
        results_panel.setLayout(results_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(tags_panel)
        splitter.addWidget(results_panel)
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 3)
        splitter.setSizes([260, 300])
        # Navigation filter banner (shown when nav filter is active)
        self.filter_banner = QLabel()
        self.filter_banner.setTextFormat(Qt.RichText)
        self.filter_banner.setTextInteractionFlags(Qt.TextBrowserInteraction)
        self.filter_banner.setOpenExternalLinks(False)
        self.filter_banner.linkActivated.connect(self._on_remove_filter)
        self.filter_banner.hide()
        layout.addWidget(self.filter_banner)
        layout.addWidget(splitter, 1)
        
        self.setLayout(layout)
        self._apply_focus_visuals()
    
    def focus_search(self):
        """Public method to focus the search bar."""
        self.tag_search.setFocus(Qt.ShortcutFocusReason)
        self.tag_search.selectAll()
    
    def focusInEvent(self, event):
        """Handle focus in event - auto-focus the search bar."""
        super().focusInEvent(event)
        # Use QTimer to defer focus until focus change completes
        QTimer.singleShot(0, self.focus_search)
    
    def keyPressEvent(self, event):
        """Handle key press events for the tags tab."""
        from PySide6.QtCore import Qt
        
        # Esc key clears search box and all tag filters, then focuses search
        if event.key() == Qt.Key_Escape:
            self.tag_search.clear()
            self._clear_all_tags()
            self.tag_search.setFocus(Qt.ShortcutFocusReason)
            event.accept()
            return
        
        # Handle the platform vi navigation chord or arrow keys.
        if ((event.key() in (Qt.Key_J, Qt.Key_K) and is_vi_navigation_chord(event.modifiers())) or
            event.key() == Qt.Key_Down or event.key() == Qt.Key_Up):
            if self.results_tree.topLevelItemCount() > 0:
                # Focus the results tree and select first item if nothing selected
                if not self.results_tree.currentItem():
                    self.results_tree.setCurrentItem(self.results_tree.topLevelItem(0))
                self.results_tree.setFocus(Qt.ShortcutFocusReason)
                # Let the results tree handle the actual navigation
                QTreeWidget.keyPressEvent(self.results_tree, event)
                event.accept()
                return
        
        # Call parent implementation for other keys
        super().keyPressEvent(event)

    def eventFilter(self, watched, event):
        """Provide a predictable search -> tags -> pages keyboard path."""
        if event.type() in (QEvent.FocusIn, QEvent.FocusOut):
            QTimer.singleShot(0, self._apply_focus_visuals)
            return super().eventFilter(watched, event)
        if event.type() != QEvent.KeyPress:
            return super().eventFilter(watched, event)
        key = event.key()
        mods = event.modifiers() & ~Qt.KeypadModifier
        shift = bool(mods & Qt.ShiftModifier)
        other_mods = mods & ~Qt.ShiftModifier

        if watched is self.results_tree:
            self._on_results_key_press(event)
            return True

        if watched is self.tag_search and key in (Qt.Key_Tab, Qt.Key_Backtab) and not other_mods:
            if not shift and key != Qt.Key_Backtab:
                chicklets = self._visible_chicklets()
                if chicklets:
                    chicklets[0].setFocus(Qt.TabFocusReason)
                    return True
                if self._focus_first_result():
                    return True

        if isinstance(watched, TagChicklet):
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Space) and not mods:
                checked = not watched.isChecked()
                watched.setChecked(checked)
                self._on_tag_clicked(watched.tag, checked)
                event.accept()
                return True
            if (
                self._is_vi_mode()
                and not mods
                and key in (Qt.Key_H, Qt.Key_J, Qt.Key_K, Qt.Key_L)
            ):
                self._move_chicklet_focus(
                    watched,
                    backwards=key in (Qt.Key_H, Qt.Key_K),
                )
                event.accept()
                return True
            if key in (Qt.Key_Tab, Qt.Key_Backtab) and not other_mods:
                backwards = shift or key == Qt.Key_Backtab
                self._move_chicklet_focus(watched, backwards=backwards)
                return True

        return super().eventFilter(watched, event)

    def _visible_chicklets(self) -> list[TagChicklet]:
        return [
            chicklet
            for chicklet in self.tag_chicklets.values()
            if not chicklet.isHidden()
        ]

    def _focus_first_result(self) -> bool:
        if self.results_tree.topLevelItemCount() <= 0:
            return False
        item = self.results_tree.currentItem() or self.results_tree.topLevelItem(0)
        self.results_tree.setCurrentItem(item)
        self.results_tree.setFocus(Qt.TabFocusReason)
        return True

    def _move_chicklet_focus(self, current: TagChicklet, *, backwards: bool) -> None:
        chicklets = self._visible_chicklets()
        try:
            index = chicklets.index(current)
        except ValueError:
            index = -1
        if backwards:
            if index > 0:
                chicklets[index - 1].setFocus(Qt.BacktabFocusReason)
            else:
                self.tag_search.setFocus(Qt.BacktabFocusReason)
            return
        if 0 <= index < len(chicklets) - 1:
            chicklets[index + 1].setFocus(Qt.TabFocusReason)
            return
        if not self._focus_first_result():
            self.tag_search.setFocus(Qt.TabFocusReason)

    def _focus_colors(self) -> tuple[str, str]:
        accent = QColor(self._vault_accent_color) if self._vault_accent_color else self.palette().color(QPalette.Highlight)
        if not accent.isValid():
            accent = self.palette().color(QPalette.Highlight)
        text = "#111111" if accent.lightness() >= 150 else "#ffffff"
        return accent.name(), text

    def _apply_focus_visuals(self) -> None:
        """Highlight the focused Tags section and name its keyboard target."""
        accent, accent_text = self._focus_colors()
        inactive = self.palette().color(QPalette.Mid).name()
        focused = QApplication.focusWidget()
        search_has_focus = focused is self.tag_search
        tag_has_focus = isinstance(focused, TagChicklet) and focused in self.tag_chicklets.values()
        pages_have_focus = bool(
            focused is self.results_tree or self.results_tree.isAncestorOf(focused)
        )

        self.tag_search.setStyleSheet(
            "QLineEdit {"
            " border: 2px solid "
            f"{accent if search_has_focus else inactive}; border-radius: 3px; padding: 2px;"
            "}"
        )
        self.tags_scroll_area.setStyleSheet(
            "QScrollArea#tagsChipArea {"
            " border: 2px solid "
            f"{accent if tag_has_focus else inactive}; border-radius: 3px;"
            "}"
        )
        self.results_tree.setStyleSheet(
            "QTreeWidget {"
            " border: 2px solid "
            f"{accent if pages_have_focus else inactive}; border-radius: 3px;"
            "}"
        )

        if search_has_focus:
            label = "FOCUS · SEARCH"
        elif tag_has_focus:
            label = f"FOCUS · #{focused.tag}"
        elif pages_have_focus:
            label = "FOCUS · PAGES"
        else:
            self.focus_indicator.hide()
            return
        self.focus_indicator.setText(label)
        self.focus_indicator.setStyleSheet(
            f"background: {accent}; color: {accent_text}; border-radius: 3px; "
            "padding: 1px 5px; font-size: 10px; font-weight: bold;"
        )
        self.focus_indicator.show()
    
    def _filter_tags(self, search_text: str):
        """Filter visible tags based on search text and auto-select exact matches."""
        search_lower = search_text.lower().strip()
        
        # Parse out potential tag names (words starting with #)
        potential_tags = []
        # For filtering visibility, we'll collect all filter terms (with # stripped)
        filter_terms = []
        
        if search_lower:
            # Split by whitespace and look for #-prefixed words
            words = search_lower.split()
            for word in words:
                if word.startswith('#'):
                    # Remove the # prefix for matching
                    tag_name = word[1:]
                    if tag_name:
                        potential_tags.append(tag_name)
                        filter_terms.append(tag_name)
                    else:
                        # Just '#' with nothing after - show all tags
                        filter_terms.append('')
                else:
                    # Non-# prefixed text - use as-is for filtering
                    filter_terms.append(word)
        
        # First pass: check for exact matches and auto-select them
        results_changed = False
        for tag_name in potential_tags:
            if tag_name in self.tag_chicklets:
                chicklet = self.tag_chicklets[tag_name]
                # Auto-select if not already selected
                if not chicklet.selected:
                    chicklet.setChecked(True)
                    # Manually add to selected_tags since setChecked doesn't trigger clicked signal
                    self.selected_tags.add(tag_name)
                    results_changed = True
        
        # Refresh results if any tags were auto-selected
        if results_changed:
            self._refresh_results()
        
        # Second pass: filter visibility based on search text
        # Show tag if any filter term matches or if filter is empty
        for tag, chicklet in self.tag_chicklets.items():
            tag_lower = tag.lower()
            if not filter_terms or any(not term or term in tag_lower for term in filter_terms):
                chicklet.show()
            else:
                chicklet.hide()
    
    def _clear_all_tags(self):
        """Clear all selected tag filters."""
        # Deselect all chicklets
        for chicklet in self.tag_chicklets.values():
            if chicklet.selected:
                chicklet.setChecked(False)  # This will trigger _on_toggled
        
        # Clear selected tags set
        self.selected_tags.clear()
        
        # Clear results
        self.results_tree.clear()
        self.status_label.setText("Select tags to filter pages")
    
    @measure_performance("panel.tags.load")
    def _load_tags(self):
        """Load all tags from the database and create chicklets."""
        try:
            from sp.app import config
            if not self._vault_accent_color:
                try:
                    self.set_vault_accent_color(config.load_vault_accent_color())
                except Exception:
                    pass
            conn = config._get_conn()
            should_close = False
            if not conn:
                db_path = config._vault_db_path()
                if not db_path:
                    if log_enabled("ui_state"):
                        print("[TagsTab] No vault database path available")
                    return
                import sqlite3
                conn = sqlite3.connect(str(db_path), check_same_thread=False)
                should_close = True
            rows = self._fetch_tag_summary(conn)
            if should_close:
                conn.close()
            
            if log_enabled("ui_state"):
                print(f"[TagsTab] Query returned {len(rows)} tags from database")
            
            # Remove both widgets and their layout items so repeated live refreshes
            # do not leave dead entries accumulating in the flow layout.
            while self.tags_layout.count():
                item = self.tags_layout.takeAt(0)
                chicklet = item.widget() if item is not None else None
                if chicklet is not None:
                    chicklet.deleteLater()
            self.tag_chicklets.clear()
            
            # Create chicklets for each tag
            for tag, count in rows:
                self._add_tag_chicklet(tag, count_label=f"{count} pages")

            # Apply any pending tags that arrived before the tab loaded
            if self._pending_new_tags:
                for tag in sorted(self._pending_new_tags):
                    if tag not in self.tag_chicklets:
                        self._add_tag_chicklet(tag, count_label="new")
                self._pending_new_tags.clear()

            # Restore selected tags after reload
            for tag in list(self.selected_tags):
                chicklet = self.tag_chicklets.get(tag)
                if chicklet:
                    chicklet.setChecked(True)

            self.tags_layout.invalidate()
            self.tags_container.adjustSize()
            self.tags_container.updateGeometry()

            if log_enabled("ui_state"):
                print(f"[TagsTab] Loaded {len(rows)} tags")

        except Exception as e:
            import traceback
            if log_enabled("ui_state"):
                print(f"[TagsTab] Error loading tags: {str(e)}")
            traceback.print_exc()
    
    def _on_tag_clicked(self, tag: str, checked: bool):
        """Handle tag chicklet click."""
        if checked:
            self.selected_tags.add(tag)
        else:
            self.selected_tags.discard(tag)
        
        self._refresh_results()
    
    @measure_performance("panel.tags.results_refresh")
    def _refresh_results(self):
        """Refresh the results list based on selected tags."""
        if not self.selected_tags:
            self.results_tree.clear()
            self.status_label.setText("Select tags to filter pages")
            return
        
        try:
            from sp.app import config
            db_path = config._vault_db_path()
            if not db_path:
                return
            
            import sqlite3
            conn = sqlite3.connect(str(db_path), check_same_thread=False)
            
            # Build query to find pages with ALL selected tags (AND logic)
            placeholders = ','.join('?' * len(self.selected_tags))
            query = f"""
                SELECT DISTINCT p.path
                FROM pages p
                WHERE (
                    SELECT COUNT(DISTINCT pt.tag)
                    FROM page_tags pt
                    WHERE pt.page = p.path AND pt.tag IN ({placeholders})
                ) = ?
                {{filter_clause}}
                ORDER BY p.path
            """
            filter_clause = ""
            params = list(self.selected_tags) + [len(self.selected_tags)]
            if self._nav_filter_prefix and self._nav_filter_prefix != "/":
                prefix = self._nav_filter_prefix.rstrip("/") or "/"
                like_prefix = prefix.rstrip("/") + "/%"
                filter_clause = "AND (p.path = ? OR p.path LIKE ?)"
                params.extend([prefix, like_prefix])
            query = query.format(filter_clause=filter_clause)

            rows = conn.execute(query, params).fetchall()
            conn.close()
            
            # Display results
            self._display_results([row[0] for row in rows])
            
            tag_list = ", ".join(f"#{t}" for t in sorted(self.selected_tags))
            self.status_label.setText(f"Found {len(rows)} page(s) with tags: {tag_list}")
            
        except Exception as e:
            import traceback
            if log_enabled("ui_state"):
                print(f"[TagsTab] Error refreshing results: {str(e)}")
            traceback.print_exc()
            self.status_label.setText(f"Error: {str(e)}")
    
    def _display_results(self, paths: list[str]):
        """Display page results in the tree widget."""
        try:
            self.results_tree.clear()
            
            # Deduplicate paths by canonical name (same folder/page name with different extensions)
            seen_canonical = {}
            unique_paths = []
            for path in paths:
                # Get the path without extension as canonical identifier
                canonical = strip_page_suffix(path)
                if canonical not in seen_canonical:
                    seen_canonical[canonical] = path
                    unique_paths.append(path)
            
            for idx, path in enumerate(unique_paths):
                # Extract leaf node from path
                leaf_name = path.rstrip("/").split("/")[-1] if "/" in path else path
                leaf_name = strip_page_suffix(leaf_name)
                display_name = format_journal_day_label(path) or leaf_name
                
                # Create item for the page path
                path_item = QTreeWidgetItem(self.results_tree)
                path_item.setText(0, display_name)
                path_item.setToolTip(0, path_to_colon(path))  # Full path in tooltip
                path_item.setData(0, Qt.UserRole, path)
                path_item.setData(0, Qt.UserRole + 1, 0)  # line number
                
                # Style the path item
                font = path_item.font(0)
                font.setBold(True)
                path_item.setFont(0, font)
                
                # Alternating background colors with increased contrast
                if idx % 2 == 1:
                    from PySide6.QtGui import QBrush, QColor, QPalette
                    palette = QApplication.palette()
                    window_color = palette.color(QPalette.Window)
                    # Use stronger contrast colors
                    bg_color = QColor(220, 220, 220) if window_color.lightness() > 128 else QColor(70, 70, 70)
                    path_item.setBackground(0, QBrush(bg_color))
            
            # Set focus to first result if any
            if self.results_tree.topLevelItemCount() > 0:
                first_item = self.results_tree.topLevelItem(0)
                self.results_tree.setCurrentItem(first_item)
                
        except Exception as e:
            import traceback
            if log_enabled("ui_state"):
                print(f"[TagsTab] Error displaying results: {str(e)}")
            traceback.print_exc()
            self.status_label.setText(f"Error displaying results: {str(e)}")
    
    def _on_results_key_press(self, event):
        """Handle key press events in results tree."""
        # Handle Escape - clear all filters and focus search bar
        if event.key() == Qt.Key_Escape:
            self.tag_search.clear()
            self._clear_all_tags()
            self.tag_search.setFocus(Qt.ShortcutFocusReason)
            event.accept()
            return
        
        # Enter loads and focuses the editor; Shift+Enter previews while keeping
        # keyboard focus in this result list.
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current_item = self.results_tree.currentItem()
            if current_item:
                path = current_item.data(0, Qt.UserRole)
                line = current_item.data(0, Qt.UserRole + 1) or 0
                if path:
                    if event.modifiers() & Qt.ShiftModifier:
                        self.pageNavigationRequested.emit(path, line)
                    else:
                        self.pageNavigationWithEditorFocusRequested.emit(path, line)
                event.accept()
                return

        if event.key() in (Qt.Key_Tab, Qt.Key_Backtab):
            current_row = self.results_tree.indexOfTopLevelItem(self.results_tree.currentItem())
            backwards = event.key() == Qt.Key_Backtab or bool(event.modifiers() & Qt.ShiftModifier)
            if backwards:
                if current_row > 0:
                    self.results_tree.setCurrentItem(self.results_tree.topLevelItem(current_row - 1))
                else:
                    chicklets = self._visible_chicklets()
                    if chicklets:
                        chicklets[-1].setFocus(Qt.BacktabFocusReason)
                    else:
                        self.tag_search.setFocus(Qt.BacktabFocusReason)
            elif current_row < self.results_tree.topLevelItemCount() - 1:
                self.results_tree.setCurrentItem(self.results_tree.topLevelItem(current_row + 1))
            event.accept()
            return
        
        # Handle the platform vi forward chord or Down arrow.
        if ((event.key() == Qt.Key_J and is_vi_navigation_chord(event.modifiers())) or
            event.key() == Qt.Key_Down):
            current_row = self.results_tree.indexOfTopLevelItem(self.results_tree.currentItem())
            if current_row < self.results_tree.topLevelItemCount() - 1:
                next_item = self.results_tree.topLevelItem(current_row + 1)
                self.results_tree.setCurrentItem(next_item)
            event.accept()
            return
        
        # Handle the platform vi backward chord or Up arrow.
        if ((event.key() == Qt.Key_K and is_vi_navigation_chord(event.modifiers())) or
            event.key() == Qt.Key_Up):
            current_row = self.results_tree.indexOfTopLevelItem(self.results_tree.currentItem())
            if current_row > 0:
                prev_item = self.results_tree.topLevelItem(current_row - 1)
                self.results_tree.setCurrentItem(prev_item)
            event.accept()
            return
        
        # Handle j/k navigation in vi mode
        if self._is_vi_mode():
            if event.key() == Qt.Key_J and not (event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)):
                # Move down
                current_row = self.results_tree.indexOfTopLevelItem(self.results_tree.currentItem())
                if current_row < self.results_tree.topLevelItemCount() - 1:
                    next_item = self.results_tree.topLevelItem(current_row + 1)
                    self.results_tree.setCurrentItem(next_item)
                event.accept()
                return
            elif event.key() == Qt.Key_K and not (event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)):
                # Move up
                current_row = self.results_tree.indexOfTopLevelItem(self.results_tree.currentItem())
                if current_row > 0:
                    prev_item = self.results_tree.topLevelItem(current_row - 1)
                    self.results_tree.setCurrentItem(prev_item)
                event.accept()
                return
        
        # Call the original keyPressEvent for other keys
        QTreeWidget.keyPressEvent(self.results_tree, event)
    
    def _on_result_double_clicked(self, item: QTreeWidgetItem, column: int):
        """Handle double click on result item - navigate to page."""
        path = item.data(0, Qt.UserRole)
        line = item.data(0, Qt.UserRole + 1) or 0
        if path:
            self.pageNavigationRequested.emit(path, line)
    
    def _is_vi_mode(self) -> bool:
        """Check if vi mode is enabled in parent main window."""
        parent = self.parent()
        while parent:
            if hasattr(parent, '_vi_enabled'):
                return parent._vi_enabled
            parent = parent.parent()
        return False
    
    def showEvent(self, event):
        """Load tags when tab becomes visible for the first time and focus search bar."""
        super().showEvent(event)
        if not self._tags_loaded or self._tags_stale:
            if log_enabled("ui_state"):
                print("[TagsTab] Tab shown or stale, loading tags...")
            self._load_tags()
            self._tags_loaded = True
            self._tags_stale = False
            self.selected_tags.intersection_update(self.tag_chicklets)
            if self.selected_tags:
                self._refresh_results()
        # Auto-focus the search bar
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, self.focus_search)
    
    def refresh_tags(self):
        """Reload tags from database (call when vault changes)."""
        if log_enabled("ui_state"):
            print("[TagsTab] refresh_tags() called")
        self.selected_tags.clear()
        self._load_tags()
        self.results_tree.clear()
        self.status_label.setText("Select tags to filter pages")
        self._tags_loaded = True
        self._tags_stale = False
        self._refresh_timer.stop()

    def mark_tags_stale(self) -> None:
        """Refresh tag summaries soon when visible, or upon the next reveal."""
        self._tags_stale = True
        if self.isVisible():
            self._refresh_timer.start()

    def _refresh_stale_tags(self) -> None:
        if not self._tags_stale or not self.isVisible():
            return
        self._load_tags()
        self._tags_loaded = True
        self._tags_stale = False
        self.selected_tags.intersection_update(self.tag_chicklets)
        if self.selected_tags:
            self._refresh_results()

    def set_vault_accent_color(self, accent_color: str | None) -> None:
        """Apply the active vault accent to selected tag chicklets."""
        self._vault_accent_color = accent_color
        for chicklet in self.tag_chicklets.values():
            chicklet.set_accent_color(accent_color)
        self._apply_focus_visuals()

    def set_navigation_filter(
        self,
        filter_prefix: str | None,
        filter_label: str | None = None,
        clear_filter_cb=None,
    ) -> None:
        """Apply navigation filter state to the tags tab."""
        self._nav_filter_prefix = filter_prefix if filter_prefix else None
        self._filter_label = filter_label
        self._clear_filter_cb = clear_filter_cb
        self._update_filter_banner()
        if self._tags_loaded:
            self._load_tags()
            if self.selected_tags:
                self._refresh_results()

    def _update_filter_banner(self) -> None:
        if not self._nav_filter_prefix:
            self.filter_banner.hide()
            return
        label = self._filter_label or path_to_colon(self._nav_filter_prefix) or self._nav_filter_prefix
        self.filter_banner.setToolTip(label)
        self.filter_banner.setText(
            f"<div style='background:#c62828; color:#ffffff; padding:8px; font-weight:bold;'>"
            f"Filtered: <a href='remove' style='color:#ffffff; text-decoration:underline;'>click to clear</a>"
            f"</div>"
        )
        self.filter_banner.show()

    def _on_remove_filter(self, link: str) -> None:
        if self._clear_filter_cb:
            try:
                self._clear_filter_cb()
                return
            except Exception:
                pass
        self.set_navigation_filter(None, None, None)

    def _fetch_tag_summary(self, conn) -> list[tuple[str, int]]:
        from sp.app import config
        if self._nav_filter_prefix and self._nav_filter_prefix != "/":
            prefix = self._nav_filter_prefix.rstrip("/") or "/"
            like_prefix = prefix.rstrip("/") + "/%"
            try:
                cur = conn.execute(
                    "SELECT tag, COUNT(DISTINCT page) FROM page_tags "
                    "WHERE page = ? OR page LIKE ? GROUP BY tag ORDER BY tag",
                    (prefix, like_prefix),
                )
                return [(row[0], row[1]) for row in cur.fetchall()]
            except Exception:
                return []
        try:
            if not conn:
                return config.fetch_tag_summary()
            cur = conn.execute("SELECT tag, COUNT(DISTINCT page) FROM page_tags GROUP BY tag ORDER BY tag")
            return [(row[0], row[1]) for row in cur.fetchall()]
        except Exception:
            return []

    def add_tag(self, tag: str) -> None:
        """Insert a new tag into the chicklet list without refreshing from DB."""
        cleaned = (tag or "").lstrip("#").strip()
        if not cleaned:
            return
        if cleaned in self.tag_chicklets:
            return
        if not self._tags_loaded:
            self._pending_new_tags.add(cleaned)
            return
        self._add_tag_chicklet(cleaned, count_label="new")
        self._filter_tags(self.tag_search.text())

    def _add_tag_chicklet(self, tag: str, count_label: str | int = 0) -> None:
        chicklet = TagChicklet(
            tag,
            self.tags_container,
            accent_color=self._vault_accent_color,
        )
        suffix = f"{count_label}" if isinstance(count_label, int) else count_label
        chicklet.setToolTip(f"#{tag} ({suffix})")
        chicklet.clicked.connect(lambda checked, t=tag: self._on_tag_clicked(t, checked))
        chicklet.installEventFilter(self)
        self.tags_layout.addWidget(chicklet)
        self.tag_chicklets[tag] = chicklet
