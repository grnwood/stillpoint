"""Search tab widget for full-text search across the vault."""

from __future__ import annotations

from typing import TYPE_CHECKING
from PySide6.QtCore import Qt, QTimer, Signal, QAbstractItemModel, QModelIndex, QRect, QSize
from PySide6.QtGui import QIcon, QCursor, QPalette, QTextDocument, QAbstractTextDocumentLayout
from PySide6.QtWidgets import (
    QApplication,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QTreeView,
    QLabel,
    QToolButton,
    QStyle,
    QMenu,
    QStyledItemDelegate,
    QStyleOptionViewItem,
)
from sp.server.adapters.files import strip_page_suffix

from .jump_dialog import JumpToPageDialog
from .path_utils import path_to_colon

if TYPE_CHECKING:
    import httpx


class SearchResultItem:
    """Data holder for a search result node."""
    def __init__(
        self,
        path: str = "",
        snippet: str = "",
        is_page: bool = True,
        line: int = 0,
        position: int = -1,
    ):
        self.path = path
        self.snippet = snippet
        self.is_page = is_page  # True for page nodes, False for snippet nodes
        self.line = line
        self.position = position
        self.children = []
        self.parent = None
    
    def add_child(self, child: "SearchResultItem"):
        child.parent = self
        self.children.append(child)


class SearchResultModel(QAbstractItemModel):
    """Model for search results with parent pages and child snippets."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.root_items = []
    
    def set_results(self, results: list[dict]):
        """Populate model from search results."""
        self.beginResetModel()
        self.root_items = []
        
        for idx, result in enumerate(results):
            path = result.get("path", "")
            snippet = result.get("snippet", "")
            line = result.get("line", 0)
            position = result.get("pos", -1)
            
            leaf_name = path.rstrip("/").split("/")[-1] if "/" in path else path
            leaf_name = strip_page_suffix(leaf_name)
            
            # Create page item (both page and snippet use the same line number)
            page_item = SearchResultItem(
                path=path,
                snippet=leaf_name,
                is_page=True,
                line=line,
                position=position,
            )
            
            # Create snippet child
            snippet_item = SearchResultItem(
                path=path,
                snippet=snippet,
                is_page=False,
                line=line,
                position=position,
            )
            page_item.add_child(snippet_item)
            
            self.root_items.append(page_item)
        
        self.endResetModel()
    
    def clear(self):
        """Clear all results."""
        self.beginResetModel()
        self.root_items = []
        self.endResetModel()
    
    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        
        if not parent.isValid():
            # Root level
            if 0 <= row < len(self.root_items):
                return self.createIndex(row, column, self.root_items[row])
        else:
            # Child level
            parent_item = parent.internalPointer()
            if parent_item and 0 <= row < len(parent_item.children):
                return self.createIndex(row, column, parent_item.children[row])
        
        return QModelIndex()
    
    def parent(self, index: QModelIndex) -> QModelIndex:
        if not index.isValid():
            return QModelIndex()
        
        item = index.internalPointer()
        if not item or not item.parent:
            return QModelIndex()
        
        parent_item = item.parent
        # Find parent's row in root items
        try:
            row = self.root_items.index(parent_item)
            return self.createIndex(row, 0, parent_item)
        except ValueError:
            return QModelIndex()
    
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        if parent.column() > 0:
            return 0
        
        if not parent.isValid():
            return len(self.root_items)
        
        parent_item = parent.internalPointer()
        return len(parent_item.children) if parent_item else 0
    
    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 1
    
    def data(self, index: QModelIndex, role: int = Qt.DisplayRole):
        if not index.isValid():
            return None
        
        item = index.internalPointer()
        if not item:
            return None
        
        if role == Qt.DisplayRole:
            return item.snippet
        elif role == Qt.UserRole:
            return item.path
        elif role == Qt.UserRole + 1:
            return item.line
        elif role == Qt.UserRole + 2:
            return item.is_page
        elif role == Qt.UserRole + 3:
            return item.position
        
        return None
    
    def flags(self, index: QModelIndex) -> Qt.ItemFlags:
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable


class HtmlDelegate(QStyledItemDelegate):
    """Delegate to render HTML in tree view items."""
    
    def paint(self, painter, option: QStyleOptionViewItem, index: QModelIndex):
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        
        painter.save()
        
        # Get parent index to determine alternating color
        parent_index = index.parent()
        is_page = index.data(Qt.UserRole + 2)
        
        # Determine background color with alternating rows
        if options.state & QStyle.State_Selected:
            painter.fillRect(options.rect, options.palette.highlight())
        else:
            # Alternating colors based on top-level (page) row
            if parent_index.isValid():
                # This is a child (snippet), use parent's row for alternating color
                row = parent_index.row()
            else:
                # This is a top-level item (page)
                row = index.row()
            
            if row % 2 == 1:
                # Alternating background with increased contrast
                from PySide6.QtGui import QColor
                palette = QApplication.palette()
                window_color = palette.color(QPalette.Window)
                bg_color = QColor(220, 220, 220) if window_color.lightness() > 128 else QColor(70, 70, 70)
                painter.fillRect(options.rect, bg_color)
            else:
                painter.fillRect(options.rect, options.palette.base())
        
        # Get text
        text = index.data(Qt.DisplayRole)
        
        # Create text document for HTML rendering
        doc = QTextDocument()
        if is_page:
            # Page titles in bold
            doc.setHtml(f"<b>{text}</b>")
        else:
            # Snippets with HTML formatting (matches highlighted)
            doc.setHtml(text)
        
        doc.setDefaultFont(options.font)
        doc.setTextWidth(options.rect.width())
        
        # Draw the HTML
        painter.translate(options.rect.left(), options.rect.top())
        clip = QRect(0, 0, options.rect.width(), options.rect.height())
        doc.drawContents(painter, clip)
        
        painter.restore()
    
    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        options = QStyleOptionViewItem(option)
        self.initStyleOption(options, index)
        
        text = index.data(Qt.DisplayRole)
        is_page = index.data(Qt.UserRole + 2)
        
        doc = QTextDocument()
        if is_page:
            doc.setHtml(f"<b>{text}</b>")
        else:
            doc.setHtml(text)
        
        doc.setDefaultFont(options.font)
        doc.setTextWidth(options.rect.width() if options.rect.width() > 0 else 500)
        
        return QSize(int(doc.idealWidth()), int(doc.size().height()))


class SearchTab(QWidget):
    """Widget for full-text search with FTS5."""
    
    # Signal emitted when user clicks a search result to navigate to that page
    pageNavigationRequested = Signal(str, int, int)  # path, line_number, position
    # Signal emitted when user wants to navigate and focus editor (Ctrl+Enter)
    pageNavigationWithEditorFocusRequested = Signal(str, int, int)  # path, line_number, position
    
    def __init__(self, parent=None, http_client: "httpx.Client" = None):
        super().__init__(parent)
        self.http = http_client
        self.current_subtree = None  # Optional path filter
        
        self._init_ui()

    def set_http_client(self, http_client: "httpx.Client") -> None:
        self.http = http_client
    
    def _init_ui(self):
        """Initialize the UI layout."""
        layout = QVBoxLayout()
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        
        # Search input row
        search_row = QHBoxLayout()
        search_row.setSpacing(4)
        
        # Search entry field
        self.search_entry = QLineEdit()
        self.search_entry.setPlaceholderText("Enter search query...")
        self.search_entry.returnPressed.connect(self._perform_search)
        search_row.addWidget(self.search_entry, 1)
        
        # Go button
        self.search_button = QPushButton("Go")
        self.search_button.setMaximumWidth(50)
        self.search_button.clicked.connect(self._perform_search)
        search_row.addWidget(self.search_button)
        
        # Help icon
        self.help_button = QToolButton()
        self.help_button.setIcon(self.style().standardIcon(QStyle.SP_MessageBoxQuestion))
        self.help_button.setAutoRaise(True)
        self.help_button.setToolTip(self._get_help_text())
        search_row.addWidget(self.help_button)
        
        layout.addLayout(search_row)
        
        # Subtree filter row
        filter_row = QHBoxLayout()
        filter_row.setSpacing(4)
        
        filter_label = QLabel("Filter:")
        filter_label.setMaximumWidth(45)
        filter_label.setToolTip("Filter results by parent page path")
        filter_row.addWidget(filter_label)
        
        self.subtree_entry = QLineEdit()
        self.subtree_entry.setPlaceholderText("(optional - click to select)")
        self.subtree_entry.setReadOnly(True)
        self.subtree_entry.mousePressEvent = lambda e: self._select_subtree()
        self.subtree_entry.setCursor(QCursor(Qt.PointingHandCursor))
        filter_row.addWidget(self.subtree_entry, 1)
        
        self.clear_subtree_button = QPushButton("Clear")
        self.clear_subtree_button.setMaximumWidth(60)
        self.clear_subtree_button.clicked.connect(self._clear_subtree)
        self.clear_subtree_button.setEnabled(False)
        filter_row.addWidget(self.clear_subtree_button)
        
        layout.addLayout(filter_row)
        
        # Status label
        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray; font-style: italic;")
        layout.addWidget(self.status_label)
        
        # Results tree with custom model and delegate
        self.results_model = SearchResultModel(self)
        self.results_tree = QTreeView()
        self.results_tree.setModel(self.results_model)
        self.results_tree.setHeaderHidden(True)
        self.results_tree.setRootIsDecorated(True)
        self.results_tree.setItemDelegate(HtmlDelegate(self))
        self.results_tree.clicked.connect(self._on_result_clicked)
        self.results_tree.doubleClicked.connect(self._on_result_double_clicked)
        self.results_tree.keyPressEvent = self._on_results_key_press
        layout.addWidget(self.results_tree, 1)
        
        self.setLayout(layout)
    
    def _get_help_text(self) -> str:
        """Generate help tooltip with FTS5 syntax examples."""
        return """<html><body style='padding: 8px;'>
<b>Full-Text Search Syntax</b><br><br>
<b>Basic Search:</b><br>
• <code>search term</code> - Find pages containing these words<br><br>
<b>Boolean Operators:</b><br>
• <code>term1 AND term2</code> - Both terms must appear<br>
• <code>term1 OR term2</code> - Either term can appear<br>
• <code>NOT term</code> - Exclude pages with this term<br><br>
<b>Phrases & Proximity:</b><br>
• <code>"exact phrase"</code> - Match exact phrase<br>
• <code>term1 NEAR term2</code> - Terms close to each other<br><br>
<b>Tag Filtering:</b><br>
• <code>search @tag1</code> - Pages with content AND tag<br>
• <code>@tag1 @tag2</code> - Pages with these tags<br><br>
<b>Examples:</b><br>
• <code>python AND (function OR method)</code><br>
• <code>"design pattern" @architecture</code><br>
• <code>bug NOT fixed @priority</code>
</body></html>"""
    
    def _select_subtree(self):
        """Open dialog to select a subtree path filter."""
        dialog = JumpToPageDialog(self, compact=True)
        if dialog.exec():
            path = dialog.selected_path()
            if path:
                self.current_subtree = path
                self.subtree_entry.setText(path_to_colon(path))
                self.clear_subtree_button.setEnabled(True)
    
    def _clear_subtree(self):
        """Clear the subtree filter."""
        self.current_subtree = None
        self.subtree_entry.clear()
        self.clear_subtree_button.setEnabled(False)
    
    def set_search_query(self, query: str, subtree: str = None):
        """Set the search query and optionally subtree from external source (e.g., Ctrl+Shift+F)."""
        self.search_entry.setText(query)
        if subtree:
            self.current_subtree = subtree
            self.subtree_entry.setText(path_to_colon(subtree))
            self.clear_subtree_button.setEnabled(True)
        self._perform_search()
    
    def _perform_search(self):
        """Execute the search query via the API."""
        query = self.search_entry.text().strip()
        
        if not query:
            self.status_label.setText("Enter a search query to begin.")
            self.results_model.clear()
            return
        
        self.status_label.setText("Searching...")
        self.results_model.clear()
        QApplication.processEvents()  # Update UI
        
        # Build API request
        params = {"q": query, "limit": 100}
        if self.current_subtree:
            params["subtree"] = self.current_subtree
        
        if not self.http:
            self.status_label.setText("Error: HTTP client not initialized")
            return
        
        try:
            response = self.http.get("/api/search", params=params)
            response.raise_for_status()
            data = response.json()
            results = data.get("results", [])
            
            if not results:
                self.status_label.setText(f"No results found for '{query}'")
                return
            
            # Debug: Print first result
            if results:
                print(f"[SearchTab] First result: {results[0]}")
            
            # Display results grouped by path
            self._display_results(results)
            self.status_label.setText(f"Found {len(results)} result(s)")
            
            # Set focus to first result
            if self.results_model.rowCount() > 0:
                first_index = self.results_model.index(0, 0)
                self.results_tree.setCurrentIndex(first_index)
                self.results_tree.setFocus()
            
        except Exception as e:
            import traceback
            error_msg = f"Search error: {str(e)}"
            self.status_label.setText(error_msg)
            print(f"[SearchTab] {error_msg}")
            traceback.print_exc()
    
    def _display_results(self, results: list[dict]):
        """Display search results using the model."""
        try:
            # Format snippets to HTML
            for result in results:
                snippet = result.get("snippet", "")
                result["snippet"] = self._format_snippet_html(snippet)
            
            # Update model with results
            self.results_model.set_results(results)
            
            # Expand all top-level items
            for i in range(self.results_model.rowCount()):
                index = self.results_model.index(i, 0)
                self.results_tree.setExpanded(index, True)
            
            # Set focus to first result
            if self.results_model.rowCount() > 0:
                first_index = self.results_model.index(0, 0)
                self.results_tree.setCurrentIndex(first_index)
                self.results_tree.setFocus()
                
        except Exception as e:
            import traceback
            print(f"[SearchTab] Error displaying results: {str(e)}")
            traceback.print_exc()
            self.status_label.setText(f"Error displaying results: {str(e)}")
    
    def _format_snippet_html(self, text: str) -> str:
        """Format snippet text to HTML with highlighted FTS5 matches."""
        import re
        import html
        
        # Escape HTML first
        text = html.escape(text)
        
        # Convert FTS5 markers [term] to bold colored highlights
        text = re.sub(r'\[([^\]]+)\]', r'<b style="color: #D2691E; background-color: rgba(255, 215, 0, 0.2);">\1</b>', text)
        
        # Remove heading hashtags but keep text
        text = re.sub(r'^#{1,6}\s+', '', text)
        text = re.sub(r'\n#{1,6}\s+', '\n', text)
        
        # Convert markdown bold **text** or __text__ to HTML bold
        text = re.sub(r'\*\*([^*]+)\*\*', r'<b>\1</b>', text)
        text = re.sub(r'__([^_]+)__', r'<b>\1</b>', text)
        
        # Convert markdown italic *text* or _text_ to HTML italic
        text = re.sub(r'(?<!\*)\*(?!\*)([^*]+)\*(?!\*)', r'<i>\1</i>', text)
        text = re.sub(r'(?<!_)_(?!_)([^_]+)_(?!_)', r'<i>\1</i>', text)
        
        # Convert inline code `code` to monospace
        text = re.sub(r'`([^`]+)`', r'<code>\1</code>', text)
        
        # Remove markdown links but keep text [text](url) -> text
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        
        return text
    
    def _on_results_key_press(self, event):
        """Handle key press events in results tree."""
        # Handle Ctrl+Enter to load page and focus editor
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and (event.modifiers() & Qt.ControlModifier):
            current_index = self.results_tree.currentIndex()
            if current_index.isValid():
                path = current_index.data(Qt.UserRole)
                line = current_index.data(Qt.UserRole + 1) or 0
                position = current_index.data(Qt.UserRole + 3) or -1
                if path:
                    self.pageNavigationWithEditorFocusRequested.emit(path, line, position)
                event.accept()
                return
        
        # Handle regular Enter to load page but keep focus on search results
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current_index = self.results_tree.currentIndex()
            if current_index.isValid():
                self._on_result_double_clicked(current_index)
                event.accept()
                return
        
        # Handle j/k navigation in vi mode
        if self._is_vi_mode():
            if event.key() == Qt.Key_J and not (event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)):
                # Move down
                current_index = self.results_tree.currentIndex()
                if current_index.isValid():
                    next_index = self.results_tree.indexBelow(current_index)
                    if next_index.isValid():
                        self.results_tree.setCurrentIndex(next_index)
                event.accept()
                return
            elif event.key() == Qt.Key_K and not (event.modifiers() & (Qt.ShiftModifier | Qt.ControlModifier | Qt.AltModifier)):
                # Move up
                current_index = self.results_tree.currentIndex()
                if current_index.isValid():
                    prev_index = self.results_tree.indexAbove(current_index)
                    if prev_index.isValid():
                        self.results_tree.setCurrentIndex(prev_index)
                event.accept()
                return
        
        # Call the original keyPressEvent for other keys
        QTreeView.keyPressEvent(self.results_tree, event)
    
    def _on_result_clicked(self, index: QModelIndex):
        """Handle single click on result item."""
        pass  # Could show preview here
    
    def _on_result_double_clicked(self, index: QModelIndex):
        """Handle double click on result item - navigate to page."""
        if not index.isValid():
            return
        path = index.data(Qt.UserRole)
        line = index.data(Qt.UserRole + 1) or 0
        position = index.data(Qt.UserRole + 3) or -1
        if path:
            self.pageNavigationRequested.emit(path, line, position)
    
    def _is_vi_mode(self) -> bool:
        """Check if vi mode is enabled in parent main window."""
        parent = self.parent()
        while parent:
            if hasattr(parent, '_vi_enabled'):
                return parent._vi_enabled
            parent = parent.parent()
        return False
    
    def focus_search(self):
        """Set focus to the search entry field."""
        self.search_entry.setFocus()
        self.search_entry.selectAll()
