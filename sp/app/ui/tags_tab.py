"""Tags tab widget for filtering pages by tags."""

from __future__ import annotations

from typing import TYPE_CHECKING
from PySide6.QtCore import Qt, Signal, QRect, QSize
from PySide6.QtGui import QCursor
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
)

from .path_utils import path_to_colon
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
    
    def __init__(self, tag: str, parent=None):
        super().__init__(f"@{tag}", parent)
        self.tag = tag
        self.selected = False
        self.setCheckable(True)
        self.setStyleSheet(self._get_style())
        self.toggled.connect(self._on_toggled)
    
    def _get_style(self):
        """Get stylesheet for chicklet based on selection state."""
        if self.selected:
            return """
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: 2px solid #45a049;
                    border-radius: 12px;
                    padding: 4px 12px;
                    margin: 2px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
            """
        else:
            return """
                QPushButton {
                    background-color: palette(button);
                    color: palette(buttonText);
                    border: 2px solid palette(dark);
                    border-radius: 12px;
                    padding: 4px 12px;
                    margin: 2px;
                }
                QPushButton:hover {
                    background-color: palette(midlight);
                    border: 2px solid palette(dark);
                }
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
        self.include_task_tags = False  # Whether to include task tags (default false)
        
        self._init_ui()
        # Don't load tags immediately - wait for vault to be opened
    
    def _init_ui(self):
        """Initialize the UI layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(0)
        
        # Create scrollable area for tag chicklets with flow layout
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setMinimumHeight(60)
        scroll_area.setFrameShape(QFrame.StyledPanel)
        scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        
        self.tags_container = QWidget()
        self.tags_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Minimum)
        self.tags_layout = FlowLayout(self.tags_container, margin=4, spacing=4)
        self.tags_container.setLayout(self.tags_layout)
        scroll_area.setWidget(self.tags_container)
        
        # Tags header with checkbox
        from PySide6.QtWidgets import QCheckBox, QToolButton
        from PySide6.QtWidgets import QStyle
        from PySide6.QtGui import QPalette
        header_layout = QHBoxLayout()
        header_layout.setSpacing(8)

        tags_label = QLabel("Tags:")
        tags_label.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(tags_label)

        self.task_tags_checkbox = QCheckBox("Tasks?")
        self.task_tags_checkbox.setChecked(False)
        self.task_tags_checkbox.setToolTip("Include tags from tasks (reduces clutter when off)")
        self.task_tags_checkbox.toggled.connect(self._on_task_tags_toggled)
        header_layout.addWidget(self.task_tags_checkbox)

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

        tags_panel = QWidget()
        tags_panel_layout = QVBoxLayout()
        tags_panel_layout.setContentsMargins(0, 0, 0, 0)
        tags_panel_layout.setSpacing(0)
        tags_panel_layout.addLayout(header_layout)
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
        self.results_tree.keyPressEvent = self._on_results_key_press
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
        layout.addWidget(splitter, 1)
        
        self.setLayout(layout)
    
    def keyPressEvent(self, event):
        """Handle key press events for the tags tab."""
        from PySide6.QtCore import Qt
        
        # Esc key clears all tag filters
        if event.key() == Qt.Key_Escape:
            self._clear_all_tags()
            event.accept()
            return
        
        # Call parent implementation for other keys
        super().keyPressEvent(event)
    
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
    
    def _load_tags(self):
        """Load all tags from the database and create chicklets."""
        try:
            from sp.app import config
            conn = config._get_conn()
            should_close = False
            if not conn:
                db_path = config._vault_db_path()
                if not db_path:
                    print("[TagsTab] No vault database path available")
                    return
                import sqlite3
                conn = sqlite3.connect(str(db_path), check_same_thread=False)
                should_close = True
            rows = config.fetch_tag_summary()
            if self.include_task_tags:
                task_rows = conn.execute("SELECT DISTINCT tag FROM task_tags").fetchall()
                task_tags = {row[0] for row in task_rows}
                existing = {tag for tag, _ in rows}
                for tag in sorted(task_tags - existing):
                    rows.append((tag, 0))
            if should_close:
                conn.close()
            
            print(f"[TagsTab] Query returned {len(rows)} tags from database")
            
            # Clear existing chicklets
            for chicklet in self.tag_chicklets.values():
                chicklet.deleteLater()
            self.tag_chicklets.clear()
            
            # Create chicklets for each tag
            for tag, count in rows:
                chicklet = TagChicklet(tag, self.tags_container)
                chicklet.setToolTip(f"@{tag} ({count} pages)")
                chicklet.clicked.connect(lambda checked, t=tag: self._on_tag_clicked(t, checked))
                self.tags_layout.addWidget(chicklet)
                self.tag_chicklets[tag] = chicklet

            self.tags_layout.invalidate()
            self.tags_container.adjustSize()
            self.tags_container.updateGeometry()
            
            print(f"[TagsTab] Loaded {len(rows)} tags")
            
        except Exception as e:
            import traceback
            print(f"[TagsTab] Error loading tags: {str(e)}")
            traceback.print_exc()
    
    def _on_task_tags_toggled(self, checked: bool):
        """Handle task tags checkbox toggle."""
        self.include_task_tags = checked
        print(f"[TagsTab] Task tags checkbox toggled: {checked}")
        # Clear selection since tags will change
        self.selected_tags.clear()
        # Reload tags with new filter
        self._load_tags()
        # Clear results
        self.results_tree.clear()
        self.status_label.setText("Select tags to filter pages")
    
    def _on_tag_clicked(self, tag: str, checked: bool):
        """Handle tag chicklet click."""
        if checked:
            self.selected_tags.add(tag)
        else:
            self.selected_tags.discard(tag)
        
        self._refresh_results()
    
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
                SELECT p.path
                FROM pages p
                WHERE (
                    SELECT COUNT(DISTINCT pt.tag)
                    FROM page_tags pt
                    WHERE pt.page = p.path AND pt.tag IN ({placeholders})
                ) = ?
                ORDER BY p.path
            """
            
            params = list(self.selected_tags) + [len(self.selected_tags)]
            rows = conn.execute(query, params).fetchall()
            conn.close()
            
            # Display results
            self._display_results([row[0] for row in rows])
            
            tag_list = ", ".join(f"@{t}" for t in sorted(self.selected_tags))
            self.status_label.setText(f"Found {len(rows)} page(s) with tags: {tag_list}")
            
        except Exception as e:
            import traceback
            print(f"[TagsTab] Error refreshing results: {str(e)}")
            traceback.print_exc()
            self.status_label.setText(f"Error: {str(e)}")
    
    def _display_results(self, paths: list[str]):
        """Display page results in the tree widget."""
        try:
            self.results_tree.clear()
            
            for idx, path in enumerate(paths):
                # Extract leaf node from path
                leaf_name = path.rstrip("/").split("/")[-1] if "/" in path else path
                leaf_name = strip_page_suffix(leaf_name)
                
                # Create item for the page path
                path_item = QTreeWidgetItem(self.results_tree)
                path_item.setText(0, leaf_name)
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
            print(f"[TagsTab] Error displaying results: {str(e)}")
            traceback.print_exc()
            self.status_label.setText(f"Error displaying results: {str(e)}")
    
    def _on_results_key_press(self, event):
        """Handle key press events in results tree."""
        # Handle Ctrl+Enter to load page and focus editor
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
            current_item = self.results_tree.currentItem()
            if current_item:
                path = current_item.data(0, Qt.UserRole)
                line = current_item.data(0, Qt.UserRole + 1) or 0
                if path:
                    self.pageNavigationWithEditorFocusRequested.emit(path, line)
                event.accept()
                return
        
        # Handle regular Enter to load page but keep focus on results
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current_item = self.results_tree.currentItem()
            if current_item:
                self._on_result_double_clicked(current_item, 0)
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
        """Load tags when tab becomes visible for the first time."""
        super().showEvent(event)
        if not self._tags_loaded:
            print("[TagsTab] Tab shown for first time, loading tags...")
            self._load_tags()
            self._tags_loaded = True
    
    def refresh_tags(self):
        """Reload tags from database (call when vault changes)."""
        print("[TagsTab] refresh_tags() called")
        self.selected_tags.clear()
        self._load_tags()
        self.results_tree.clear()
        self.status_label.setText("Select tags to filter pages")
        self._tags_loaded = True
