"""Independent Qt window for browsing one ordinary filesystem root."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from collections import OrderedDict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import quote

from PySide6.QtCore import (QAbstractListModel, QAbstractTableModel, QDir, QEvent, QFile, QFileInfo, QFileSystemWatcher, QMimeData,
                            QItemSelection, QItemSelectionModel, QModelIndex, QObject, QPoint,
                            QPointF, QRect, QSize, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QColor, QDesktopServices, QDrag, QFont, QIcon, QImageReader, QKeySequence, QPalette,
    QNativeGestureEvent, QPainter, QPainterPath, QPen, QPixmap, QShortcut, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QAbstractItemView, QAbstractScrollArea, QFileIconProvider, QFileSystemModel, QFrame, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListView, QListWidgetItem, QInputDialog, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QTabWidget, QToolButton,
    QStackedWidget, QStyle, QTabBar, QTableView, QTextEdit, QPlainTextEdit, QTreeView, QVBoxLayout, QWidget, QScrollArea, QSpinBox,
    QSizePolicy)
from PySide6.QtWidgets import QTextBrowser

from .core import (DEFAULT_PRUNED_DIRECTORY_GLOBS, DEFAULT_PRUNED_DIRECTORY_NAMES,
    DEFAULT_PRUNED_FILE_GLOBS, DEFAULT_PRUNED_FILE_SUFFIXES,
    MAX_CONCURRENT_WORK, MAX_DIRECTORY_ENTRIES,
    MAX_EDIT_BYTES, MAX_IMAGE_PIXELS,
    MAX_INDEX_FILES, MAX_INDEX_SECONDS, MAX_RESULTS, MAX_SEARCH_BYTES,
    ConflictError, TextFile, atomic_save, content_matches, fingerprint, fuzzy_score, inside,
    pruned_relative_path, read_text, rich_markdown_fallback_reason, walk_files)
from .catalog import CatalogError, FolderCatalog
from .editors import (MarkdownEditor, SourceEditor, configure_markdown_editor,
                      configure_source_editor, format_markdown_table)
from .icon import (
    configure_folder_navigator_application,
    configure_folder_navigator_process,
    get_folder_navigator_icon,
)
from .launch import launch
from .tabular import TablePreview, read_delimited_preview, read_workbook_preview
from sp.app.ui.keyboard_shortcuts import (
    history_cycle_modifier_release_key,
    history_cycle_sequences,
    is_vi_navigation_chord,
    vi_navigation_sequences,
)
from sp.app.ui.canvas_navigation import native_zoom_steps, wheel_action, zoom_factor
from sp.app.ui.theme import (
    chrome_colors,
    status_bar_stylesheet,
    tab_widget_stylesheet,
    theme_color,
    theme_value,
    tree_view_stylesheet,
)
from sp.app.ui.utility_header import CompactToolbarIdentity, UtilityPanelHeader

DELIMITED_SUFFIXES = {".csv", ".tsv", ".tab"}
WORKBOOK_SUFFIXES = {".xls", ".xlsx", ".xlsm", ".xlsb", ".ods"}
DIAGRAM_SUFFIXES = {".puml", ".mmd", ".excalidraw"}
DOCUMENT_SUFFIXES = {".docx", ".pptx"}
CHAT_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
DIAGRAM_PREVIEW_CACHE_ENTRIES = 128
DIAGRAM_PREVIEW_CACHE_BYTES = 128 * 1024 * 1024
IMAGE_PREVIEW_CACHE_ENTRIES = 32
IMAGE_PREVIEW_CACHE_BYTES = 128 * 1024 * 1024
IMAGE_FLYOVER_MAX_PIXELS = 2_000_000
_BACKGROUND_SUBPROCESS_OPTIONS = (
    {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
)
VALUE_FILTER_MAX_ROWS = 25_000
VALUE_FILTER_MAX_CELLS = 500_000
VALUE_FILTER_MAX_DISTINCT = 20_000


class Bridge(QObject):
    result = Signal(object)
    finished = Signal(object)


@lru_cache(maxsize=1)
def _image_suffixes() -> frozenset[str]:
    return frozenset(
        f".{bytes(fmt).decode('ascii').casefold()}"
        for fmt in QImageReader.supportedImageFormats()
    )


def _may_be_image(path: Path) -> bool:
    suffix = path.suffix.casefold()
    return not suffix or suffix in _image_suffixes()


class FolderModel(QFileSystemModel):
    def __init__(self, root: Path, parent=None):
        super().__init__(parent)
        self.root = root
        self.setIconProvider(QFileIconProvider())
        self.setFilter(QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs)
        self.setNameFilterDisables(False)
        self.setRootPath(str(root))

    def canFetchMore(self, parent):
        if parent.isValid() and not inside(self.root, Path(self.filePath(parent))):
            return False
        return super().canFetchMore(parent)

    def data(self, index, role=Qt.DisplayRole):
        if role == Qt.ToolTipRole and index.isValid():
            path = Path(self.filePath(index))
            if path.is_symlink():
                return f"Link: {path.resolve()} — outside-root links are not followed"
            return str(path)
        return super().data(index, role)


class NavigatorTree(QTreeView):
    INTERNAL_PATHS_MIME = "application/x-stillpoint-folder-navigator-paths"
    openFile = Signal(str, bool)
    openFileAndFocus = Signal(str, bool)
    escapePressed = Signal()
    bookmarkPickerRequested = Signal()
    folderPickerRequested = Signal()
    pathsDropped = Signal(object, object)
    pathsMoved = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False
        self.setHeaderHidden(False)
        self.header().setSectionsMovable(True)
        self.header().setStretchLastSection(False)
        self.header().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setUniformRowHeights(True)
        self.setAnimated(False)
        self.setMouseTracking(True)
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)
        self.setDragEnabled(True)
        self.setDragDropMode(QAbstractItemView.DragDrop)
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.AscendingOrder)

    def _drop_directory(self, position):
        index = self.indexAt(position)
        model = self.model()
        if index.isValid():
            path = Path(model.filePath(index))
            return path if model.isDir(index) else path.parent
        root = self.rootIndex()
        root_path = model.filePath(root) if root.isValid() else ""
        return Path(root_path) if root_path else None

    @staticmethod
    def _local_drop_paths(event):
        if not event.mimeData().hasUrls():
            return []
        return [Path(url.toLocalFile()) for url in event.mimeData().urls()
                if url.isLocalFile() and url.toLocalFile()]

    def _selected_drag_paths(self):
        model = self.model()
        return [Path(model.filePath(index)) for index in self.selectionModel().selectedRows(0)
                if index.isValid() and (Path(model.filePath(index)).is_file()
                                        or Path(model.filePath(index)).is_dir())]

    def startDrag(self, supported_actions):  # type: ignore[override]
        paths = self._selected_drag_paths()
        if not paths:
            return
        mime = QMimeData()
        mime.setData(self.INTERNAL_PATHS_MIME, json.dumps([str(path) for path in paths]).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.MoveAction)

    def _internal_drop_paths(self, event):
        if event.source() is not self or not event.mimeData().hasFormat(self.INTERNAL_PATHS_MIME):
            return []
        try:
            return [Path(value) for value in json.loads(
                bytes(event.mimeData().data(self.INTERNAL_PATHS_MIME)).decode("utf-8")
            ) if isinstance(value, str)]
        except (UnicodeError, ValueError, TypeError):
            return []

    def dragEnterEvent(self, event):  # type: ignore[override]
        if self._internal_drop_paths(event):
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        if self._local_drop_paths(event):
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        event.ignore()

    def dragMoveEvent(self, event):  # type: ignore[override]
        target = self._drop_directory(event.position().toPoint())
        if (self._internal_drop_paths(event) and target is not None
                and target.is_dir() and inside(self.model().root, target)):
            event.setDropAction(Qt.MoveAction)
            event.accept()
            return
        if self._local_drop_paths(event) and target is not None and target.is_dir():
            event.setDropAction(Qt.CopyAction)
            event.accept()
            return
        event.ignore()

    def dropEvent(self, event):  # type: ignore[override]
        target = self._drop_directory(event.position().toPoint())
        internal = self._internal_drop_paths(event)
        if internal and target is not None and target.is_dir():
            event.setDropAction(Qt.MoveAction)
            event.accept()
            self.pathsMoved.emit(internal, target)
            return
        sources = self._local_drop_paths(event)
        if not sources or target is None or not target.is_dir():
            event.ignore()
            return
        event.setDropAction(Qt.CopyAction)
        event.accept()
        self.pathsDropped.emit(sources, target)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape:
            self.escapePressed.emit()
            return
        if self.vi_enabled and mods == Qt.NoModifier:
            if key == Qt.Key_F:
                self.bookmarkPickerRequested.emit()
                return
            if key == Qt.Key_V:
                self.folderPickerRequested.emit()
                return
            mapping = {Qt.Key_J: Qt.Key_Down, Qt.Key_K: Qt.Key_Up,
                       Qt.Key_H: Qt.Key_Left, Qt.Key_L: Qt.Key_Right}
            if key in mapping:
                from PySide6.QtGui import QKeyEvent
                event = QKeyEvent(event.type(), mapping[key], Qt.NoModifier)
        if key in (Qt.Key_Return, Qt.Key_Enter):
            index = self.currentIndex()
            model = self.model()
            if index.isValid() and model.isDir(index):
                self.setExpanded(index, not self.isExpanded(index))
            elif index.isValid():
                signal = self.openFileAndFocus if mods == Qt.ShiftModifier else self.openFile
                signal.emit(model.filePath(index), True)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        point = event.position().toPoint() if hasattr(event, "position") else event.pos()
        index = self.indexAt(point)
        if event.button() == Qt.RightButton:
            # Context menus target indexAt(point) directly. Do not let Qt make
            # that row current first: changing currentIndex would trigger a
            # disposable flyover preview before the requested menu action.
            selection_model = self.selectionModel()
            previous_selection = selection_model.selection()
            previous_current = selection_model.currentIndex()
            was_blocked = selection_model.blockSignals(True)
            try:
                super().mousePressEvent(event)
                selection_model.select(
                    previous_selection,
                    QItemSelectionModel.ClearAndSelect,
                )
                selection_model.setCurrentIndex(
                    previous_current,
                    QItemSelectionModel.NoUpdate,
                )
            finally:
                selection_model.blockSignals(was_blocked)
            return
        super().mousePressEvent(event)


class InlineFileNameEdit(QLineEdit):
    canceled = Signal()

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            self.canceled.emit()
            return
        super().keyPressEvent(event)


class SearchResultsList(QListWidget):
    """Search results with ordinary arrow navigation plus optional Vim keys."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False

    def _move_to_result(self, delta):
        row = self.currentRow() + delta
        while 0 <= row < self.count():
            item = self.item(row)
            if item and item.data(Qt.UserRole):
                self.setCurrentRow(row)
                return
            row += delta

    def keyPressEvent(self, event):  # type: ignore[override]
        if (self.vi_enabled
                and event.key() in (Qt.Key_J, Qt.Key_K)
                and is_vi_navigation_chord(event.modifiers() & ~Qt.KeypadModifier)):
            from PySide6.QtGui import QKeyEvent
            page_key = Qt.Key_PageDown if event.key() == Qt.Key_J else Qt.Key_PageUp
            super().keyPressEvent(QKeyEvent(event.type(), page_key, Qt.NoModifier))
            return
        if self.vi_enabled and event.modifiers() == Qt.NoModifier:
            if event.key() == Qt.Key_J:
                self._move_to_result(1)
                return
            if event.key() == Qt.Key_K:
                self._move_to_result(-1)
                return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and self.currentItem():
            self.itemActivated.emit(self.currentItem())
            return
        super().keyPressEvent(event)


class CommandBar(QWidget):
    """Folder Navigator command palette matching StillPoint's command bar."""

    actionTriggered = Signal(QAction)

    def __init__(self, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.entries = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Type a command…")
        self.results = QListWidget()
        self.results.setUniformItemSizes(True)
        self.results.setMinimumHeight(220)
        layout.addWidget(self.search)
        layout.addWidget(self.results)
        self.search.textChanged.connect(self._refresh)
        self.results.itemActivated.connect(lambda *_: self._activate())
        self.results.itemClicked.connect(lambda *_: self._activate())
        self.search.installEventFilter(self)
        self._apply_theme()

    def _apply_theme(self):
        palette = QApplication.palette()
        base = palette.color(QPalette.Base).name()
        alternate = palette.color(QPalette.AlternateBase).name()
        text = palette.color(QPalette.Text).name()
        border = palette.color(QPalette.Mid).name()
        self.setStyleSheet(
            f"background: {theme_value('main_window.menu_command_bar.bg', base)}; "
            f"color: {theme_value('main_window.menu_command_bar.text', text)}; "
            f"border-radius: 10px; border: 1px solid {theme_value('main_window.menu_command_bar.border', border)};"
        )
        self.search.setStyleSheet(
            f"font-size: {theme_value('main_window.menu_command_bar.search_font_size_px', 18)}px; "
            f"color: {theme_value('main_window.menu_command_bar.search_text', text)}; "
            f"background: {theme_value('main_window.menu_command_bar.search_bg', alternate)}; "
            f"border: 1px solid {theme_value('main_window.menu_command_bar.search_border', border)}; "
            "padding: 8px; border-radius: 6px;"
        )
        self.results.setStyleSheet(
            f"font-size: {theme_value('main_window.menu_command_bar.list_font_size_px', 18)}px; "
            f"color: {theme_value('main_window.menu_command_bar.list_text', text)}; "
            "background: transparent; padding: 4px;"
        )

    def show_actions(self, actions):
        self._apply_theme()
        self.entries = [action for action in actions if action.isVisible()]
        self.search.clear()
        parent = self.parentWidget()
        if parent:
            area = parent.rect()
            width = max(420, int(area.width() * .8))
            height = min(280, max(200, area.height() - 100))
            origin = parent.mapToGlobal(area.topLeft())
            self.setGeometry(origin.x() + (area.width() - width) // 2,
                             origin.y() + int(area.height() * .2), width, height)
        self._refresh()
        self.show()
        self.raise_()
        self.search.setFocus()

    def _refresh(self):
        query = self.search.text().casefold().strip()
        self.results.clear()
        label_for = lambda action: str(action.property("commandLabel") or action.text()).replace("&", "")
        actions = [a for a in self.entries if not query or query in label_for(a).casefold()]
        if query:
            def rank(action):
                label = label_for(action).casefold()
                command = label.partition(" / ")[2]
                if command.startswith(query):
                    return (0, label)
                if label.startswith(query):
                    return (1, label)
                if any(token.startswith(query) for token in re.split(r"[\s/:\-]+", label)):
                    return (2, label)
                return (3, label)
            actions.sort(key=rank)
        for action in actions:
            item = QListWidgetItem(label_for(action))
            item.setData(Qt.UserRole, action)
            if not action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _move(self, delta):
        if self.results.count():
            self.results.setCurrentRow(max(0, min(self.results.count() - 1,
                                                   self.results.currentRow() + delta)))

    def _activate(self):
        item = self.results.currentItem()
        action = item.data(Qt.UserRole) if item else None
        if action and action.isEnabled():
            self.hide()
            self.actionTriggered.emit(action)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.search and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                self._move(1 if event.key() == Qt.Key_Down else -1)
                return True
            if is_vi_navigation_chord(event.modifiers()):
                if event.key() == Qt.Key_J:
                    self._move(1)
                    return True
                if event.key() == Qt.Key_K:
                    self._move(-1)
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._activate()
                return True
            if event.key() == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)


class HeadingPicker(QDialog):
    """Small filterable heading navigator used by Markdown tabs."""

    def __init__(self, headings, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.headings = headings
        self.selected_line = None
        self.resize(520, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        title = QLabel("Headings")
        title.setStyleSheet("font-weight: bold;")
        self.query = QLineEdit()
        self.query.setPlaceholderText("Filter headings…")
        self.setFocusProxy(self.query)
        self.results = QListWidget()
        layout.addWidget(title)
        layout.addWidget(self.query)
        layout.addWidget(self.results)
        self.query.textChanged.connect(self._refresh)
        self.query.installEventFilter(self)
        self.results.installEventFilter(self)
        self.results.itemActivated.connect(lambda *_: self._accept_current())
        self.results.itemDoubleClicked.connect(lambda *_: self._accept_current())
        self._refresh()

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        QTimer.singleShot(0, self._focus_query)

    def _focus_query(self):
        self.raise_()
        self.activateWindow()
        self.query.setFocus(Qt.PopupFocusReason)
        self.query.selectAll()

    def _refresh(self):
        needle = self.query.text().casefold().strip()
        self.results.clear()
        for level, title, line in self.headings:
            if needle and needle not in title.casefold():
                continue
            item = QListWidgetItem(f"{'    ' * (level - 1)}{title}  (line {line})")
            item.setData(Qt.UserRole, line)
            self.results.addItem(item)
        if self.results.count():
            self.results.setCurrentRow(0)

    def _move(self, delta):
        if self.results.count():
            self.results.setCurrentRow(max(0, min(self.results.count() - 1,
                                                   self.results.currentRow() + delta)))

    def _accept_current(self):
        item = self.results.currentItem()
        if item:
            self.selected_line = item.data(Qt.UserRole)
            self.accept()

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj in (self.query, self.results) and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                self._move(1 if event.key() == Qt.Key_Down else -1)
                return True
            if is_vi_navigation_chord(event.modifiers()):
                if event.key() == Qt.Key_J:
                    self._move(1)
                    return True
                if event.key() == Qt.Key_K:
                    self._move(-1)
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._accept_current()
                return True
            if event.key() == Qt.Key_Escape:
                self.reject()
                return True
        return super().eventFilter(obj, event)


def _spreadsheet_column_name(column):
    name = ""
    column += 1
    while column:
        column, remainder = divmod(column - 1, 26)
        name = chr(65 + remainder) + name
    return name


class TablePreviewModel(QAbstractTableModel):
    """Incrementally exposes an already bounded table to Qt's virtualized view."""

    CHUNK_ROWS = 500

    def __init__(self, preview: TablePreview, parent=None):
        super().__init__(parent)
        self.preview = preview
        self.header_row = bool(preview.has_header and preview.rows)
        self.columns = max((len(row) for row in preview.rows), default=0)
        self.filter_column = -1
        self.filter_text = ""
        self.value_filters = {}
        self.sort_column = -1
        self.sort_order = Qt.AscendingOrder
        self.row_order = []
        self._rebuild_row_order()
        self.visible_rows = min(self.CHUNK_ROWS, self._data_row_count())

    def _default_row_order(self):
        return list(range(1 if self.header_row else 0, len(self.preview.rows)))

    def _data_row_count(self):
        return len(self.row_order)

    def _rebuild_row_order(self):
        rows = self._default_row_order()
        rows = [row_index for row_index in rows if self._row_matches(row_index)]
        if 0 <= self.sort_column < self.columns:
            column = self.sort_column
            rows.sort(
                key=lambda row_index: self._sort_key(
                    self.preview.rows[row_index][column]
                    if column < len(self.preview.rows[row_index]) else ""
                ),
                reverse=self.sort_order == Qt.DescendingOrder,
            )
        self.row_order = rows

    @staticmethod
    def _cell_text(row, column):
        return str(row[column] if column < len(row) else "")

    def _row_matches(self, row_index, *, skip_value_column=None):
        row = self.preview.rows[row_index]
        needle = self.filter_text.casefold()
        if needle:
            if self.filter_column >= 0:
                if needle not in self._cell_text(row, self.filter_column).casefold():
                    return False
            elif not any(needle in str(value).casefold() for value in row):
                return False
        for column, selected in self.value_filters.items():
            if column == skip_value_column:
                continue
            if self._cell_text(row, column) not in selected:
                return False
        return True

    def distinct_values(self, column):
        if column < 0 or column >= self.columns:
            return []
        values = {
            self._cell_text(self.preview.rows[row_index], column)
            for row_index in self._default_row_order()
            if self._row_matches(row_index, skip_value_column=column)
        }
        return sorted(values, key=lambda value: (value != "", value.casefold(), value))

    def selected_values(self, column, available=None):
        values = list(self.distinct_values(column) if available is None else available)
        selected = self.value_filters.get(column)
        return set(values if selected is None else selected)

    def set_value_filter(self, column, selected_values, available_values=None):
        column = int(column)
        available = set(
            self.distinct_values(column)
            if available_values is None else available_values
        )
        selected = {str(value) for value in selected_values}
        self.beginResetModel()
        if selected == available:
            self.value_filters.pop(column, None)
        else:
            self.value_filters[column] = frozenset(selected)
        self._rebuild_row_order()
        self.visible_rows = min(self.CHUNK_ROWS, self._data_row_count())
        self.endResetModel()

    def clear_value_filters(self):
        if not self.value_filters:
            return
        self.beginResetModel()
        self.value_filters.clear()
        self._rebuild_row_order()
        self.visible_rows = min(self.CHUNK_ROWS, self._data_row_count())
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):  # type: ignore[override]
        return 0 if parent.isValid() else self.visible_rows

    def columnCount(self, parent=QModelIndex()):  # type: ignore[override]
        return 0 if parent.isValid() else self.columns

    def data(self, index, role=Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid():
            return None
        source_row = self.row_order[index.row()]
        row = self.preview.rows[source_row]
        if role in (Qt.DisplayRole, Qt.ToolTipRole):
            return row[index.column()] if index.column() < len(row) else ""
        if role == Qt.BackgroundRole:
            color = QColor.fromHsl((index.column() * 47) % 360, 110, 128)
            color.setAlpha(18)
            return color
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):  # type: ignore[override]
        if role == Qt.DisplayRole:
            if orientation == Qt.Vertical:
                return section + 1 + (1 if self.header_row else 0)
            if self.header_row and section < len(self.preview.rows[0]):
                return self.preview.rows[0][section]
            return _spreadsheet_column_name(section)
        if role == Qt.ForegroundRole and orientation == Qt.Horizontal:
            return QColor.fromHsl((section * 47) % 360, 180, 115)
        return None

    def canFetchMore(self, parent=QModelIndex()):  # type: ignore[override]
        return not parent.isValid() and self.visible_rows < self._data_row_count()

    def fetchMore(self, parent=QModelIndex()):  # type: ignore[override]
        if parent.isValid():
            return
        remaining = self._data_row_count() - self.visible_rows
        count = min(self.CHUNK_ROWS, remaining)
        if count <= 0:
            return
        first = self.visible_rows
        self.beginInsertRows(QModelIndex(), first, first + count - 1)
        self.visible_rows += count
        self.endInsertRows()

    def set_header_row(self, enabled):
        self.beginResetModel()
        self.header_row = bool(enabled and self.preview.rows)
        self._rebuild_row_order()
        self.visible_rows = min(self.CHUNK_ROWS, self._data_row_count())
        self.endResetModel()

    def set_filter(self, column, text):
        self.beginResetModel()
        self.filter_column = int(column)
        self.filter_text = str(text or "").strip()
        self._rebuild_row_order()
        self.visible_rows = min(self.CHUNK_ROWS, self._data_row_count())
        self.endResetModel()

    @staticmethod
    def _sort_key(value):
        text = str(value or "").strip()
        if not text:
            return 2, 0, ""
        try:
            return 0, float(text.replace(",", "")), ""
        except ValueError:
            return 1, 0, text.casefold()

    def sort(self, column, order=Qt.AscendingOrder):  # type: ignore[override]
        if column < 0 or column >= self.columns:
            return
        self.layoutAboutToBeChanged.emit()
        self.sort_column = column
        self.sort_order = order
        self._rebuild_row_order()
        self.layoutChanged.emit()


class ColumnValueListModel(QAbstractListModel):
    """Virtualized check-list for one spreadsheet column's distinct values."""

    selectionChanged = Signal()

    def __init__(self, values, selected, parent=None):
        super().__init__(parent)
        self.values = list(values)
        self.visible_values = list(self.values)
        self.selected = set(selected) & set(self.values)

    def rowCount(self, parent=QModelIndex()):  # type: ignore[override]
        return 0 if parent.isValid() else len(self.visible_values)

    def data(self, index, role=Qt.DisplayRole):  # type: ignore[override]
        if not index.isValid() or index.row() >= len(self.visible_values):
            return None
        value = self.visible_values[index.row()]
        if role == Qt.DisplayRole:
            return "(Blanks)" if value == "" else value
        if role == Qt.CheckStateRole:
            return Qt.Checked if value in self.selected else Qt.Unchecked
        if role == Qt.ToolTipRole:
            return "(Blanks)" if value == "" else value
        return None

    def flags(self, index):  # type: ignore[override]
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable

    def setData(self, index, value, role=Qt.EditRole):  # type: ignore[override]
        if role != Qt.CheckStateRole or not index.isValid():
            return False
        item = self.visible_values[index.row()]
        if value == Qt.Checked.value or value == Qt.Checked:
            self.selected.add(item)
        else:
            self.selected.discard(item)
        self.dataChanged.emit(index, index, [Qt.CheckStateRole])
        self.selectionChanged.emit()
        return True

    def set_query(self, query):
        needle = str(query or "").casefold()
        self.beginResetModel()
        self.visible_values = [
            value for value in self.values
            if not needle or needle in (("(Blanks)" if value == "" else value).casefold())
        ]
        self.endResetModel()
        self.selectionChanged.emit()

    def set_all(self, checked, *, visible_only=True):
        targets = set(self.visible_values if visible_only else self.values)
        if checked:
            self.selected.update(targets)
        else:
            self.selected.difference_update(targets)
        if self.visible_values:
            self.dataChanged.emit(
                self.index(0, 0),
                self.index(len(self.visible_values) - 1, 0),
                [Qt.CheckStateRole],
            )
        self.selectionChanged.emit()


class ColumnFilterPopup(QDialog):
    """Excel-style distinct-value filter shown below a table header."""

    def __init__(self, title, values, selected, parent=None):
        super().__init__(parent, Qt.Popup)
        self.setObjectName("spreadsheetColumnFilter")
        self.resize(280, 360)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        heading = QLabel(str(title))
        heading.setStyleSheet("font-weight: 600;")
        layout.addWidget(heading)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search values…")
        layout.addWidget(self.search)
        self.select_all = QCheckBox("Select All")
        self.select_all.setTristate(True)
        layout.addWidget(self.select_all)
        self.values_model = ColumnValueListModel(values, selected, self)
        self.values_view = QListView()
        self.values_view.setAccessibleName(f"Values for {title}")
        self.values_view.setModel(self.values_model)
        self.values_view.setUniformItemSizes(True)
        layout.addWidget(self.values_view, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.Apply | QDialogButtonBox.Cancel)
        self.apply_button = buttons.button(QDialogButtonBox.Apply)
        self.clear_button = buttons.addButton("Clear Filter", QDialogButtonBox.ResetRole)
        self.apply_button.clicked.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.clear_button.clicked.connect(self._clear_and_accept)
        layout.addWidget(buttons)
        self.search.textChanged.connect(self.values_model.set_query)
        self.select_all.clicked.connect(
            lambda checked: self.values_model.set_all(bool(checked))
        )
        self.values_model.selectionChanged.connect(self._sync_select_all)
        self._sync_select_all()

    def _sync_select_all(self):
        visible = set(self.values_model.visible_values)
        total = len(visible)
        selected = len(visible & self.values_model.selected)
        state = (
            Qt.Unchecked if selected == 0
            else Qt.Checked if selected == total
            else Qt.PartiallyChecked
        )
        self.select_all.blockSignals(True)
        self.select_all.setCheckState(state)
        self.select_all.blockSignals(False)

    def _clear_and_accept(self):
        self.values_model.set_all(True, visible_only=False)
        self.accept()

    def selected_values(self):
        return set(self.values_model.selected)


class FilterHeaderView(QHeaderView):
    """Spreadsheet header that paints clickable filter dropdowns per column."""

    filterRequested = Signal(int, QPoint)

    def __init__(self, parent=None):
        super().__init__(Qt.Horizontal, parent)
        self.filter_enabled = False
        self.active_filter_columns = set()
        self.setSectionsClickable(True)

    def set_filter_enabled(self, enabled):
        self.filter_enabled = bool(enabled)
        self.setToolTip(
            "Click a column dropdown to choose values"
            if self.filter_enabled else "Click a column heading to sort"
        )
        self.viewport().update()

    def set_active_filter_columns(self, columns):
        self.active_filter_columns = set(columns)
        self.viewport().update()

    @staticmethod
    def _filter_rect(rect):
        return QRect(rect.right() - 20, rect.top() + 2, 18, max(16, rect.height() - 4))

    def paintSection(self, painter, rect, logical_index):  # type: ignore[override]
        super().paintSection(painter, rect, logical_index)
        if not self.filter_enabled or not rect.isValid():
            return
        button = self._filter_rect(rect)
        palette = self.palette()
        painter.save()
        painter.setPen(palette.color(QPalette.Mid))
        painter.setBrush(
            palette.color(QPalette.Highlight)
            if logical_index in self.active_filter_columns
            else palette.color(QPalette.Button)
        )
        painter.drawRoundedRect(button, 3, 3)
        painter.setPen(
            palette.color(QPalette.HighlightedText)
            if logical_index in self.active_filter_columns
            else palette.color(QPalette.ButtonText)
        )
        painter.drawText(button, Qt.AlignCenter, "▾")
        painter.restore()

    def mousePressEvent(self, event):  # type: ignore[override]
        if self.filter_enabled:
            point = event.position().toPoint()
            section = self.logicalIndexAt(point)
            if section >= 0:
                rect = QRect(
                    self.sectionViewportPosition(section),
                    0,
                    self.sectionSize(section),
                    self.height(),
                )
                if self._filter_rect(rect).contains(point):
                    popup_point = self.viewport().mapToGlobal(
                        QPoint(rect.right() - 280, rect.bottom())
                    )
                    self.filterRequested.emit(section, popup_point)
                    event.accept()
                    return
        super().mousePressEvent(event)


class PreviewTableView(QTableView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False
        self.vi_page_shortcuts = []
        for sequence, page_key in zip(
                vi_navigation_sequences(), (Qt.Key_PageDown, Qt.Key_PageUp)):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda selected=page_key: self._vi_page(selected)
            )
            self.vi_page_shortcuts.append(shortcut)

    def _vi_page(self, page_key):
        if not self.vi_enabled:
            return
        from PySide6.QtGui import QKeyEvent
        super().keyPressEvent(QKeyEvent(QEvent.KeyPress, page_key, Qt.NoModifier))

    def _copy_selected_cells(self):
        indexes = self.selectionModel().selectedIndexes()
        if not indexes and self.currentIndex().isValid():
            indexes = [self.currentIndex()]
        if not indexes:
            return
        top = min(index.row() for index in indexes)
        bottom = max(index.row() for index in indexes)
        left = min(index.column() for index in indexes)
        right = max(index.column() for index in indexes)
        selected = {(index.row(), index.column()): index for index in indexes}
        lines = []
        for row in range(top, bottom + 1):
            values = []
            for column in range(left, right + 1):
                index = selected.get((row, column))
                value = index.data(Qt.DisplayRole) if index is not None else ""
                values.append(str(value or "").replace("\t", " "))
            lines.append("\t".join(values))
        QApplication.clipboard().setText("\n".join(lines))

    def _extend_cell_selection(self, row_delta, column_delta):
        model = self.model()
        if model is None or not model.rowCount() or not model.columnCount():
            return
        current = self.currentIndex()
        if not current.isValid():
            current = model.index(0, 0)
        selected = self.selectionModel().selectedIndexes()
        anchor = current if len(selected) <= 1 else getattr(
            self, "_vi_selection_anchor", current
        )
        self._vi_selection_anchor = anchor
        target_row = max(0, current.row() + row_delta)
        while target_row >= model.rowCount() and model.canFetchMore():
            model.fetchMore()
        target_row = min(model.rowCount() - 1, target_row)
        target_column = max(0, min(model.columnCount() - 1, current.column() + column_delta))
        target = model.index(target_row, target_column)
        top_left = model.index(
            min(anchor.row(), target.row()), min(anchor.column(), target.column())
        )
        bottom_right = model.index(
            max(anchor.row(), target.row()), max(anchor.column(), target.column())
        )
        selection_model = self.selectionModel()
        selection_model.select(
            QItemSelection(top_left, bottom_right), QItemSelectionModel.ClearAndSelect
        )
        selection_model.setCurrentIndex(target, QItemSelectionModel.NoUpdate)

    def _move_to_file_edge(self, *, end):
        model = self.model()
        if model is None:
            return
        if end:
            while model.canFetchMore():
                model.fetchMore()
        if not model.rowCount() or not model.columnCount():
            return
        current = self.currentIndex()
        column = current.column() if current.isValid() else 0
        target = model.index(model.rowCount() - 1 if end else 0, column)
        self._vi_selection_anchor = target
        self.setCurrentIndex(target)
        self.scrollTo(target)

    def keyPressEvent(self, event):  # type: ignore[override]
        if (self.vi_enabled
                and event.key() in (Qt.Key_J, Qt.Key_K)
                and is_vi_navigation_chord(event.modifiers() & ~Qt.KeypadModifier)):
            self._vi_page(Qt.Key_PageDown if event.key() == Qt.Key_J else Qt.Key_PageUp)
            return
        if (self.vi_enabled and event.key() == Qt.Key_G
                and event.modifiers() & ~Qt.KeypadModifier
                in (Qt.NoModifier, Qt.ShiftModifier)):
            self._move_to_file_edge(
                end=bool(event.modifiers() & Qt.ShiftModifier)
            )
            return
        if self.vi_enabled and event.modifiers() == Qt.ShiftModifier:
            select_mapping = {
                Qt.Key_H: (0, -1),
                Qt.Key_J: (1, 0),
                Qt.Key_K: (-1, 0),
                Qt.Key_L: (0, 1),
                Qt.Key_N: (1, 0),
                Qt.Key_U: (-1, 0),
                Qt.Key_Left: (0, -1),
                Qt.Key_Down: (1, 0),
                Qt.Key_Up: (-1, 0),
                Qt.Key_Right: (0, 1),
            }
            if event.key() in select_mapping:
                self._extend_cell_selection(*select_mapping[event.key()])
                return
        if self.vi_enabled and event.modifiers() == Qt.NoModifier:
            mapping = {
                Qt.Key_H: Qt.Key_Left,
                Qt.Key_J: Qt.Key_Down,
                Qt.Key_K: Qt.Key_Up,
                Qt.Key_L: Qt.Key_Right,
            }
            if event.key() in mapping:
                from PySide6.QtGui import QKeyEvent
                event = QKeyEvent(event.type(), mapping[event.key()], Qt.NoModifier)
            elif event.key() == Qt.Key_C:
                self._copy_selected_cells()
                return
        if event.matches(QKeySequence.Copy):
            self._copy_selected_cells()
            return
        super().keyPressEvent(event)


class SpreadsheetView(QWidget):
    sheetRequested = Signal(str)
    sourceRequested = Signal()
    statusRequested = Signal(str, int)

    @staticmethod
    def _filter_icon(palette, *, active=False):
        pixmap = QPixmap(16, 16)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        role = QPalette.HighlightedText if active else QPalette.ButtonText
        painter.setPen(QPen(palette.color(role), 1.5))
        path = QPainterPath()
        path.moveTo(2.5, 3)
        path.lineTo(13.5, 3)
        path.lineTo(9.5, 8)
        path.lineTo(9.5, 12.5)
        path.lineTo(6.5, 14)
        path.lineTo(6.5, 8)
        path.closeSubpath()
        painter.drawPath(path)
        painter.end()
        return QIcon(pixmap)

    def __init__(self, preview: TablePreview, *, allow_source=False, zoom_steps=0,
                 vi_enabled=False, parent=None):
        super().__init__(parent)
        self.allow_source = allow_source
        self.zoom_steps = zoom_steps
        self._base_font = QFont(QApplication.font())
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        controls = QHBoxLayout()
        self.sheet_selector = QComboBox()
        self.sheet_selector.setAccessibleName("Worksheet")
        self.sheet_selector.currentTextChanged.connect(self._sheet_changed)
        controls.addWidget(self.sheet_selector)
        self.first_row_header = QCheckBox("First row contains headers")
        self.first_row_header.toggled.connect(self._header_toggled)
        controls.addWidget(self.first_row_header)
        self.value_filter_button = QToolButton()
        self.value_filter_button.setObjectName("spreadsheetValueFilterToggle")
        self.value_filter_button.setText("Filter Off")
        self.value_filter_button.setIcon(self._filter_icon(self.palette()))
        self.value_filter_button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.value_filter_button.setCheckable(True)
        self.value_filter_button.setToolTip(
            "Show an Excel-style value dropdown on every column"
        )
        self.value_filter_button.toggled.connect(self._toggle_value_filters)
        controls.addWidget(self.value_filter_button)
        self._sync_value_filter_button_visual()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find in loaded rows")
        self.search.returnPressed.connect(self.find_next)
        controls.addWidget(self.search, 1)
        find_next = QPushButton("Next")
        find_next.clicked.connect(self.find_next)
        controls.addWidget(find_next)
        if allow_source:
            raw = QPushButton("Raw Text")
            raw.clicked.connect(self.sourceRequested)
            controls.addWidget(raw)
        layout.addLayout(controls)
        filters = QHBoxLayout()
        filters.addWidget(QLabel("Filter"))
        self.filter_column = QComboBox()
        self.filter_column.setAccessibleName("Filter column")
        self.filter_column.currentIndexChanged.connect(self._schedule_filter)
        filters.addWidget(self.filter_column)
        self.filter_text = QLineEdit()
        self.filter_text.setAccessibleName("Column filter text")
        self.filter_text.setPlaceholderText("Rows containing…")
        self.filter_text.textChanged.connect(self._schedule_filter)
        self.filter_text.returnPressed.connect(self._apply_filter)
        filters.addWidget(self.filter_text, 1)
        clear_filter = QPushButton("Clear")
        clear_filter.clicked.connect(self.filter_text.clear)
        filters.addWidget(clear_filter)
        layout.addLayout(filters)
        self.summary = QLabel()
        layout.addWidget(self.summary)
        self.table = PreviewTableView()
        self.filter_header = FilterHeaderView(self.table)
        self.filter_header.filterRequested.connect(self._show_column_filter)
        self.table.setHorizontalHeader(self.filter_header)
        self.table.vi_enabled = vi_enabled
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectItems)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setWordWrap(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self.table.horizontalHeader().setDefaultSectionSize(140)
        self.table.horizontalHeader().sectionClicked.connect(self._sort_clicked)
        layout.addWidget(self.table)
        self.setFocusProxy(self.table)
        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(lambda: (self.search.setFocus(), self.search.selectAll()))
        self.find_shortcut = find_shortcut
        self.filter_timer = QTimer(self)
        self.filter_timer.setSingleShot(True)
        self.filter_timer.setInterval(160)
        self.filter_timer.timeout.connect(self._apply_filter)
        self.set_preview(preview)
        self.set_zoom_steps(zoom_steps)

    def set_preview(self, preview):
        self.preview = preview
        self.model = TablePreviewModel(preview, self)
        self.table.setModel(self.model)
        self._sort_column = -1
        self._sort_order = Qt.AscendingOrder
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self.first_row_header.blockSignals(True)
        self.first_row_header.setChecked(preview.has_header)
        self.first_row_header.blockSignals(False)
        # This is a view preference for both delimited files and workbooks;
        # it does not modify the source file.
        self.first_row_header.setVisible(True)
        self.filter_timer.stop()
        self.filter_text.blockSignals(True)
        self.filter_text.clear()
        self.filter_text.blockSignals(False)
        self._populate_filter_columns()
        self._configure_value_filter_availability()
        self.sheet_selector.blockSignals(True)
        self.sheet_selector.clear()
        self.sheet_selector.addItems(preview.sheet_names)
        if preview.sheet_name:
            self.sheet_selector.setCurrentText(preview.sheet_name)
        self.sheet_selector.blockSignals(False)
        self.sheet_selector.setVisible(bool(preview.sheet_names))
        self._base_dimensions = f"{preview.total_rows:,} rows × {preview.total_columns:,} columns" if (
            preview.total_rows is not None and preview.total_columns is not None
        ) else f"{len(preview.rows):,} loaded rows × {self.model.columns:,} columns"
        self._update_summary()

    def _configure_value_filter_availability(self):
        rows = max(0, len(self.preview.rows) - (1 if self.model.header_row else 0))
        cells = rows * self.model.columns
        safe = rows <= VALUE_FILTER_MAX_ROWS and cells <= VALUE_FILTER_MAX_CELLS
        self.value_filter_disabled_reason = ""
        self.value_filter_button.setEnabled(safe)
        if safe:
            self.value_filter_button.setToolTip(
                "Show an Excel-style value dropdown on every column"
            )
            self._sync_value_filter_button_visual()
            return
        self.value_filter_button.blockSignals(True)
        self.value_filter_button.setChecked(False)
        self.value_filter_button.blockSignals(False)
        self.filter_header.set_filter_enabled(False)
        reason = (
            f"Column value filters disabled for this preview "
            f"({rows:,} rows × {self.model.columns:,} columns) to keep navigation responsive"
        )
        self.value_filter_disabled_reason = reason
        self.value_filter_button.setToolTip(reason)
        self._sync_value_filter_button_visual()
        self.statusRequested.emit(reason, 8000)

    def _sync_value_filter_button_visual(self):
        available = self.value_filter_button.isEnabled()
        active = available and self.value_filter_button.isChecked()
        self.value_filter_button.setText(
            "Filter On" if active else "Filter Off" if available else "Filter Unavailable"
        )
        self.value_filter_button.setIcon(self._filter_icon(self.palette(), active=active))
        palette = self.palette()
        highlight = palette.color(QPalette.Highlight).name()
        highlighted_text = palette.color(QPalette.HighlightedText).name()
        hover = palette.color(QPalette.AlternateBase).name()
        self.value_filter_button.setStyleSheet(
            "QToolButton#spreadsheetValueFilterToggle {"
            "border: 1px solid transparent; border-radius: 4px; padding: 2px 6px;"
            "}"
            "QToolButton#spreadsheetValueFilterToggle:hover {"
            f"background: {hover}; border-color: {highlight};"
            "}"
            "QToolButton#spreadsheetValueFilterToggle:checked {"
            f"background: {highlight}; color: {highlighted_text}; border-color: {highlight};"
            "font-weight: 600;"
            "}"
        )

    def _toggle_value_filters(self, enabled):
        self._sync_value_filter_button_visual()
        if enabled and not self.value_filter_button.isEnabled():
            return
        self.filter_header.set_filter_enabled(enabled)
        if enabled:
            if self.preview.truncated:
                self.statusRequested.emit(
                    "Column filters apply to the loaded preview rows only; "
                    "the source was limited for performance",
                    7000,
                )
            else:
                self.statusRequested.emit(
                    "Column filters enabled; use the dropdown on any column",
                    3500,
                )
            return
        self.model.clear_value_filters()
        self.filter_header.set_active_filter_columns(set())
        self._update_summary()

    def _show_column_filter(self, column, global_position):
        values = self.model.distinct_values(column)
        if len(values) > VALUE_FILTER_MAX_DISTINCT:
            self.statusRequested.emit(
                f"Column filter disabled: {len(values):,} distinct values exceed the "
                f"safe {VALUE_FILTER_MAX_DISTINCT:,}-value limit",
                8000,
            )
            return
        title = str(self.model.headerData(column, Qt.Horizontal) or "").strip()
        popup = ColumnFilterPopup(
            title or _spreadsheet_column_name(column),
            values,
            self.model.selected_values(column, values),
            self,
        )
        screen = QApplication.screenAt(global_position)
        if screen is not None:
            available = screen.availableGeometry()
            global_position.setX(max(available.left(), min(global_position.x(), available.right() - popup.width())))
            global_position.setY(max(available.top(), min(global_position.y(), available.bottom() - popup.height())))
        popup.move(global_position)
        if popup.exec() == QDialog.Accepted:
            self._set_column_value_filter(column, popup.selected_values(), values)

    def _set_column_value_filter(self, column, selected, available):
        self.model.set_value_filter(column, selected, available)
        self.filter_header.set_active_filter_columns(self.model.value_filters)
        self._update_summary()
        if self.model.rowCount() and self.model.columnCount():
            self.table.setCurrentIndex(self.model.index(0, 0))

    def _sheet_changed(self, name):
        if name and name != self.preview.sheet_name:
            self.summary.setText(f"Loading {name}…")
            self.sheetRequested.emit(name)

    def _header_toggled(self, enabled):
        self.model.set_header_row(enabled)
        self._sort_column = -1
        self.table.horizontalHeader().setSortIndicatorShown(False)
        self._populate_filter_columns()
        self._apply_filter()
        self._configure_value_filter_availability()

    def _populate_filter_columns(self):
        selected = self.filter_column.currentData()
        self.filter_column.blockSignals(True)
        self.filter_column.clear()
        self.filter_column.addItem("All columns", -1)
        for column in range(self.model.columns):
            label = str(self.model.headerData(column, Qt.Horizontal) or "").strip()
            self.filter_column.addItem(label or _spreadsheet_column_name(column), column)
        index = self.filter_column.findData(selected)
        self.filter_column.setCurrentIndex(max(0, index))
        self.filter_column.blockSignals(False)

    def _schedule_filter(self, *_args):
        self.filter_timer.start()

    def _apply_filter(self):
        self.filter_timer.stop()
        column = self.filter_column.currentData()
        self.model.set_filter(-1 if column is None else column, self.filter_text.text())
        self._update_summary()
        if self.model.rowCount() and self.model.columnCount():
            self.table.setCurrentIndex(self.model.index(0, 0))

    def _update_summary(self):
        delimiter = f" · delimiter {self.preview.delimiter!r}" if self.preview.delimiter else ""
        limited = " · preview limited for performance" if self.preview.truncated else ""
        filtered = (
            f" · {self.model._data_row_count():,} matching rows"
            if self.model.filter_text or self.model.value_filters else ""
        )
        self.summary.setText(self._base_dimensions + filtered + delimiter + limited)

    def _sort_clicked(self, column):
        if column == self._sort_column:
            self._sort_order = (
                Qt.DescendingOrder
                if self._sort_order == Qt.AscendingOrder else Qt.AscendingOrder
            )
        else:
            self._sort_column = column
            self._sort_order = Qt.AscendingOrder
        self.model.sort(column, self._sort_order)
        self.table.horizontalHeader().setSortIndicator(column, self._sort_order)
        self.table.horizontalHeader().setSortIndicatorShown(True)

    def find_next(self):
        needle = self.search.text().casefold()
        if not needle or not self.model.rowCount() or not self.model.columnCount():
            return
        current = self.table.currentIndex()
        start = current.row() * self.model.columnCount() + current.column() + 1 if current.isValid() else 0
        total = self.model._data_row_count() * self.model.columnCount()
        for offset in range(total):
            flat = (start + offset) % total
            row, column = divmod(flat, self.model.columnCount())
            while row >= self.model.rowCount() and self.model.canFetchMore():
                self.model.fetchMore()
            index = self.model.index(row, column)
            if needle in str(self.model.data(index, Qt.DisplayRole)).casefold():
                self.table.setCurrentIndex(index)
                self.table.scrollTo(index)
                return

    def set_zoom_steps(self, steps):
        self.zoom_steps = steps
        font = QFont(self._base_font)
        size = font.pointSizeF() if font.pointSizeF() > 0 else QApplication.font().pointSizeF()
        font.setPointSizeF(max(6.0, size + steps))
        self.table.setFont(font)
        self.table.horizontalHeader().setFont(font)
        self.table.verticalHeader().setFont(font)

    def zoom_in(self):
        self.set_zoom_steps(min(20, self.zoom_steps + 1))

    def zoom_out(self):
        self.set_zoom_steps(max(-8, self.zoom_steps - 1))


class Tab(QWidget):
    def __init__(self, path: Path, loaded: TextFile | None = None, *, markdown=False,
                 details="", root: Path | None = None, defer_enhancements=False):
        super().__init__()
        self.path = path
        self.loaded = loaded
        self.clean_text = loaded.text if loaded else ""
        self.markdown = markdown
        self.pinned = False
        self.detached = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.notice = QLabel("")
        self.notice.setAccessibleName("File status")
        self.notice.hide()
        layout.addWidget(self.notice)
        if loaded:
            if markdown:
                from sp.app.ui.markdown_editor import MarkdownEditor
                self.editor = MarkdownEditor()
                # Previewing from the folder tree must not let deferred Vi-mode
                # activation pull focus away from filesystem navigation.
                self.editor.setProperty("suppressViFocus", True)
                configure_markdown_editor(self.editor, path)
                if root is not None:
                    self.editor.set_context(str(root), str(path.relative_to(root)))
            else:
                self.editor = SourceEditor(path, highlight=not defer_enhancements)
                configure_source_editor(self.editor)
                self.setProperty("folderSourceHighlighted", not defer_enhancements)
            self.editor.setPlainText(loaded.text)
            if markdown:
                # Markdown serialization canonicalizes some source forms even
                # before rich rendering begins. Capture that representation
                # before deferred editor timers can report it as a change.
                self.clean_text = self.editor.to_markdown()
            self.editor.document().setModified(False)
            self.editor.setReadOnly(not os.access(path, os.W_OK))
            self.find_bar = QWidget()
            find_layout = QHBoxLayout(self.find_bar)
            self.find_query = QLineEdit()
            self.find_query.setPlaceholderText("Find")
            self.replace_query = QLineEdit()
            self.replace_query.setPlaceholderText("Replace")
            find_next = QPushButton("Next")
            replace = QPushButton("Replace")
            find_next.clicked.connect(self._find_next)
            self.find_query.returnPressed.connect(self._find_next)
            replace.clicked.connect(self._replace_current)
            for control in (self.find_query, find_next, self.replace_query, replace):
                find_layout.addWidget(control)
            find_close_shortcut = QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar)
            find_close_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            find_close_shortcut.activated.connect(self._hide_find)
            self.find_close_shortcut = find_close_shortcut
            self.find_bar.hide()
            layout.addWidget(self.find_bar)
            find_shortcut = QShortcut(QKeySequence.Find, self.editor)
            find_shortcut.activated.connect(self._show_find)
            self.find_shortcut = find_shortcut
            if isinstance(self.editor, SourceEditor):
                self.editor.findRequested.connect(self._show_find)
            if self.editor.isReadOnly():
                self.show_notice("Read-only file: editing and saving are disabled")
            layout.addWidget(self.editor)
            self.editor_status = QFrame()
            self.editor_status.setObjectName("folderEditorStatus")
            status_layout = QHBoxLayout(self.editor_status)
            status_layout.setContentsMargins(8, 2, 8, 2)
            status_layout.setSpacing(14)
            self.cursor_status = QLabel()
            self.cursor_status.setAccessibleName("Cursor position and selection")
            self.file_status = QLabel()
            self.file_status.setAccessibleName("File format and editor mode")
            status_layout.addWidget(self.cursor_status)
            status_layout.addStretch()
            status_layout.addWidget(self.file_status)
            palette = QApplication.palette()
            self.editor_status.setStyleSheet(
                "QFrame#folderEditorStatus {"
                f"background: {palette.color(QPalette.AlternateBase).name()};"
                f"color: {palette.color(QPalette.Text).name()};"
                f"border-top: 1px solid {palette.color(QPalette.Mid).name()};"
                "} QLabel { border: none; background: transparent; }"
            )
            layout.addWidget(self.editor_status)
            self.editor.cursorPositionChanged.connect(self._update_editor_status)
            self.editor.selectionChanged.connect(self._update_editor_status)
            self.editor.document().blockCountChanged.connect(
                lambda _count: self._update_editor_status()
            )
            self._update_editor_status()
        else:
            self.editor = None
            label = QLabel(details)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            label.setWordWrap(True)
            layout.addWidget(label)
            self.placeholder = label

    @property
    def dirty(self):
        return bool(self.editor and self.editor.document().isModified())

    def text_for_save(self):
        if isinstance(self.editor, MarkdownEditor):
            return self.editor.to_markdown()
        return self.editor.toPlainText() if self.editor else ""

    def load_text(self, text):
        """Replace a clean editor buffer after an external filesystem change."""
        if self.editor is None:
            return
        if isinstance(self.editor, MarkdownEditor):
            self.editor.set_markdown(text)
            self.clean_text = self.editor.to_markdown()
        else:
            self.editor.setPlainText(text)
            self.clean_text = text
        self.editor.document().setModified(False)
        self.detached = False
        self._update_editor_status()

    def show_notice(self, message):
        self.notice.setText(message)
        self.notice.show()

    def _update_editor_status(self):
        if self.editor is None or not hasattr(self, "cursor_status"):
            return
        cursor = self.editor.textCursor()
        position = f"Ln {cursor.blockNumber() + 1}, Col {cursor.positionInBlock() + 1}"
        if cursor.hasSelection():
            selected = cursor.selectedText().replace("\u2029", "\n")
            position += f"  ·  {len(selected):,} selected"
        position += f"  ·  {self.editor.document().blockCount():,} lines"
        self.cursor_status.setText(position)

        suffix = self.path.suffix.casefold()
        languages = {
            ".py": "Python", ".pyw": "Python", ".js": "JavaScript",
            ".ts": "TypeScript", ".tsx": "TypeScript JSX", ".jsx": "JavaScript JSX",
            ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
            ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
            ".sh": "Shell", ".bash": "Shell", ".ps1": "PowerShell",
            ".sql": "SQL", ".xml": "XML", ".md": "Markdown", ".markdown": "Markdown",
            ".c": "C", ".h": "C", ".cpp": "C++", ".hpp": "C++",
            ".rs": "Rust", ".go": "Go", ".java": "Java",
        }
        language = languages.get(suffix, "Plain Text")
        encoding = str(self.loaded.encoding if self.loaded else "utf-8").upper().replace("-SIG", "")
        newline = {"\r\n": "CRLF", "\r": "CR", "\n": "LF"}.get(
            self.loaded.newline if self.loaded else "\n", "LF"
        )
        access = "Read-only" if self.editor.isReadOnly() else "Editable"
        self.file_status.setText(f"{language}  ·  {encoding}  ·  {newline}  ·  {access}")

    def _show_find(self):
        self.find_bar.show()
        self.find_query.setFocus()
        self.find_query.selectAll()

    def _hide_find(self):
        self.find_bar.hide()
        if self.editor:
            self.editor.setFocus(Qt.OtherFocusReason)

    def _find_next(self):
        if not self.editor or not self.find_query.text():
            return
        if not self.editor.find(self.find_query.text()):
            self.editor.moveCursor(QTextCursor.Start)
            self.editor.find(self.find_query.text())

    def _replace_current(self):
        if not self.editor or self.editor.isReadOnly() or not self.find_query.text():
            return
        cursor = self.editor.textCursor()
        if cursor.selectedText() != self.find_query.text():
            if not self.editor.find(self.find_query.text()):
                return
            cursor = self.editor.textCursor()
        cursor.insertText(self.replace_query.text())


class ImageCanvasLabel(QLabel):
    """Image canvas using StillPoint's standard wheel and trackpad policy."""

    zoomRequested = Signal(float, object)

    def __init__(self):
        super().__init__()
        self._pan_start = None
        self.grabGesture(Qt.PinchGesture)

    def _scroll_area(self):
        parent = self.parent()
        while parent:
            if isinstance(parent, QScrollArea):
                return parent
            parent = parent.parent()
        return None

    def wheelEvent(self, event):  # type: ignore[override]
        action = wheel_action(event)
        if action.is_zoom:
            self.zoomRequested.emit(action.zoom_steps, QPointF(event.position()))
            event.accept()
            return
        if action.is_pan:
            area = self._scroll_area()
            if area is not None:
                area.horizontalScrollBar().setValue(
                    area.horizontalScrollBar().value() - round(action.pan.x())
                )
                area.verticalScrollBar().setValue(
                    area.verticalScrollBar().value() - round(action.pan.y())
                )
                event.accept()
                return
        super().wheelEvent(event)

    def event(self, event):  # type: ignore[override]
        if isinstance(event, QNativeGestureEvent) and event.gestureType() == Qt.ZoomNativeGesture:
            if event.value():
                self.zoomRequested.emit(
                    native_zoom_steps(event.value()), QPointF(event.position())
                )
                event.accept()
                return True
        return super().event(event)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() in (Qt.MiddleButton, Qt.RightButton) and self.pixmap():
            self._pan_start = event.globalPosition().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._pan_start is not None:
            area = self._scroll_area()
            if area is not None:
                current = event.globalPosition().toPoint()
                delta = current - self._pan_start
                area.horizontalScrollBar().setValue(
                    area.horizontalScrollBar().value() - delta.x()
                )
                area.verticalScrollBar().setValue(
                    area.verticalScrollBar().value() - delta.y()
                )
                self._pan_start = current
                event.accept()
                return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() in (Qt.MiddleButton, Qt.RightButton) and self._pan_start is not None:
            self._pan_start = None
            self.setCursor(Qt.ArrowCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)


class ImageView(QWidget):
    def __init__(self, path, pixels, *, open_label=None, open_callback=None,
                 canvas_color=None, svg_text=None, source_dimensions=None,
                 preview_limited=False, full_resolution_callback=None):
        super().__init__()
        self.original = QPixmap.fromImage(pixels)
        self.canvas_color = canvas_color
        self.svg_text = svg_text
        self.full_resolution_callback = full_resolution_callback
        self._full_resolution_requested = False
        self.zoom = 1.0
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        if open_label and open_callback:
            open_button = QPushButton(open_label)
            open_button.clicked.connect(open_callback)
            controls.addWidget(open_button)
            controls.addSpacing(10)
        if svg_text:
            copy_svg = QPushButton("Copy SVG")
            copy_svg.clicked.connect(self.copy_svg)
            controls.addWidget(copy_svg)
            copy_png = QPushButton("Copy PNG")
            copy_png.clicked.connect(self.copy_png)
            controls.addWidget(copy_png)
            controls.addSpacing(10)
        if preview_limited and full_resolution_callback:
            full_resolution = QPushButton("Load Full Resolution")
            full_resolution.clicked.connect(self.request_full_resolution)
            controls.addWidget(full_resolution)
            controls.addSpacing(10)
        self.scroll = QScrollArea()
        self.label = ImageCanvasLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.label.zoomRequested.connect(
            lambda steps, _anchor: self._zoom_requested(zoom_factor(steps))
        )
        if canvas_color:
            # Diagram SVGs commonly have a transparent root while their
            # default text and strokes assume light paper. Keep that canvas
            # readable without changing the application theme or export.
            canvas_palette = self.label.palette()
            canvas_palette.setColor(QPalette.Window, QColor(canvas_color))
            self.label.setPalette(canvas_palette)
            self.label.setAutoFillBackground(True)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        for title, callback in (("Fit to Window", self.fit), ("Actual Size", self.actual),
                                ("Zoom In", self.zoom_in), ("Zoom Out", self.zoom_out),
                                ("Reset Zoom", self.actual)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        dimensions = source_dimensions or (pixels.width(), pixels.height())
        resolution_note = " · thumbnail preview" if preview_limited else ""
        layout.addWidget(QLabel(
            f"{dimensions[0]} × {dimensions[1]} pixels · "
            f"{path.stat().st_size:,} bytes{resolution_note}"
        ))
        layout.addWidget(self.scroll)
        QTimer.singleShot(0, self.fit)

    def scale(self, factor):
        self.zoom = min(8, max(.05, self.zoom * factor))
        scaled = self.original.scaled(
            self.original.size() * self.zoom,
            Qt.KeepAspectRatio,
            Qt.SmoothTransformation,
        )
        self.label.setMinimumSize(scaled.size())
        self.label.resize(scaled.size())
        self.label.setPixmap(scaled)

    def zoom_in(self):
        self.request_full_resolution()
        self.scale(1.25)

    def zoom_out(self):
        self.request_full_resolution()
        self.scale(.8)

    def _zoom_requested(self, factor):
        self.request_full_resolution()
        self.scale(factor)

    def request_full_resolution(self):
        if self._full_resolution_requested or not self.full_resolution_callback:
            return
        self._full_resolution_requested = True
        self.full_resolution_callback()

    def copy_svg(self):
        if self.svg_text:
            QApplication.clipboard().setText(self.svg_text)

    def copy_png(self):
        pixmap = self.original
        if self.canvas_color:
            # Match what the user sees instead of copying transparent pixels
            # that can become unreadable when pasted onto a dark surface.
            composited = QPixmap(pixmap.size())
            composited.fill(QColor(self.canvas_color))
            painter = QPainter(composited)
            painter.drawPixmap(0, 0, pixmap)
            painter.end()
            pixmap = composited
        QApplication.clipboard().setPixmap(pixmap)

    def actual(self):
        self.request_full_resolution()
        self.zoom = 1.0
        self.scale(1)

    def fit(self):
        area = self.scroll.viewport().size()
        self.zoom = min(area.width() / self.original.width(), area.height() / self.original.height(), 1)
        self.scale(1)


def pdf_view(path):
    from PySide6.QtPdf import QPdfDocument, QPdfSearchModel
    from PySide6.QtPdfWidgets import QPdfView
    widget = QWidget()
    layout = QVBoxLayout(widget)
    controls = QHBoxLayout()
    document = QPdfDocument(widget)
    status = document.load(str(path))
    if status != QPdfDocument.Error.None_:
        raise ValueError(f"PDF could not be loaded: {status}")
    viewer = QPdfView()
    viewer.setDocument(document)
    viewer.setPageMode(QPdfView.PageMode.MultiPage)
    nav = viewer.pageNavigator()
    for title, callback in (("Previous", lambda: nav.jump(max(0, nav.currentPage() - 1), nav.currentLocation(), nav.currentZoom())),
                            ("Next", lambda: nav.jump(min(document.pageCount() - 1, nav.currentPage() + 1), nav.currentLocation(), nav.currentZoom())),
                            ("Fit Width", lambda: viewer.setZoomMode(QPdfView.ZoomMode.FitToWidth)),
                            ("Fit Page", lambda: viewer.setZoomMode(QPdfView.ZoomMode.FitInView)),
                            ("Zoom In", lambda: viewer.setZoomFactor(viewer.zoomFactor() * 1.2)),
                            ("Zoom Out", lambda: viewer.setZoomFactor(viewer.zoomFactor() / 1.2))):
        button = QPushButton(title)
        button.clicked.connect(callback)
        controls.addWidget(button)
    page = QLabel(f"{document.pageCount()} pages")
    controls.addWidget(page)
    page_number = QSpinBox()
    page_number.setRange(1, max(1, document.pageCount()))
    page_number.setAccessibleName("PDF page number")
    page_number.valueChanged.connect(lambda number: nav.jump(number - 1, nav.currentLocation(), nav.currentZoom()))
    nav.currentPageChanged.connect(lambda number: page_number.setValue(number + 1))
    controls.addWidget(page_number)
    search = QLineEdit()
    search.setPlaceholderText("Find in PDF")
    model = QPdfSearchModel(widget)
    model.setDocument(document)
    search.textChanged.connect(model.setSearchString)
    viewer.setSearchModel(model)
    controls.addWidget(search)
    for title, delta in (("Previous Match", -1), ("Next Match", 1)):
        button = QPushButton(title)
        button.clicked.connect(lambda _, step=delta: viewer.setCurrentSearchResultIndex(
            max(0, min(model.rowCount() - 1, viewer.currentSearchResultIndex() + step))))
        controls.addWidget(button)
    layout.addLayout(controls)
    layout.addWidget(viewer)
    widget.document = document
    widget.search_model = model
    widget.zoom_in = lambda: viewer.setZoomFactor(viewer.zoomFactor() * 1.2)
    widget.zoom_out = lambda: viewer.setZoomFactor(viewer.zoomFactor() / 1.2)
    return widget


class BookmarkPicker(QDialog):
    def __init__(self, window, folder_navigators=()):
        super().__init__(window)
        self.window = window
        self.folder_navigators = folder_navigators
        self.selected_path = None
        self.selected_folder_navigator = False
        self.setWindowTitle("Go to Bookmark")
        self.resize(650, 430)
        layout = QVBoxLayout(self)
        self.query = QLineEdit()
        self.query.setPlaceholderText("Find a bookmark")
        self.list = QListWidget()
        layout.addWidget(self.query)
        layout.addWidget(self.list)
        self.query.textChanged.connect(self.refresh)
        self.query.installEventFilter(self)
        self.list.installEventFilter(self)
        self.list.itemActivated.connect(lambda _item: self.choose())
        self.refresh()
        self.query.setFocus()

    def refresh(self):
        query = self.query.text().strip()
        ranked = []
        for name in self.window._bookmarks():
            path = Path(name)
            try:
                relative = str(path.relative_to(self.window.root))
            except ValueError:
                relative = str(path)
            score = fuzzy_score(query, relative)
            if score is not None:
                ranked.append((-score, relative.casefold(), path, False))
        for name in self.folder_navigators:
            path = Path(name)
            score = fuzzy_score(query, f"{path.name} {path}")
            if score is not None:
                ranked.append((-score, str(path).casefold(), path, True))
        ranked.sort()
        self.list.clear()
        for _, relative, path, is_navigator in ranked:
            kind = "📁 " if path.is_dir() and not is_navigator else ""
            label = (f"{path.name}    Folder Navigator — {path}"
                     if is_navigator else f"{kind}{path.name}    {Path(relative).parent}")
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, path)
            item.setData(Qt.UserRole + 1, is_navigator)
            if is_navigator:
                item.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
            item.setToolTip(str(path))
            self.list.addItem(item)
        if ranked:
            self.list.setCurrentRow(0)
        else:
            item = QListWidgetItem("No matching bookmarks")
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)

    def choose(self):
        item = self.list.currentItem()
        if item is not None and item.data(Qt.UserRole):
            self.selected_path = Path(item.data(Qt.UserRole))
            self.selected_folder_navigator = bool(item.data(Qt.UserRole + 1))
            self.accept()

    def eventFilter(self, obj, event):
        if obj in (self.query, self.list) and event.type() == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers() & ~Qt.KeypadModifier
            if key in (Qt.Key_Return, Qt.Key_Enter):
                self.choose()
                return True
            if key in (Qt.Key_Down, Qt.Key_Up) or (
                key in (Qt.Key_J, Qt.Key_K)
                and (modifiers == Qt.ControlModifier or is_vi_navigation_chord(modifiers))
            ):
                delta = 1 if key in (Qt.Key_Down, Qt.Key_J) else -1
                self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + delta)))
                return True
        return super().eventFilter(obj, event)


class Picker(QDialog):
    resultsReady = Signal(int, object)

    def __init__(self, window, *, quick=False, folder_only=False):
        super().__init__(window)
        self.window = window
        self.quick = quick
        self.folder_only = folder_only
        title = "Folder Picker" if folder_only else "Quick Open" if quick else "Folder Picker"
        self.setWindowTitle(title)
        self.resize(650, 430)
        layout = QVBoxLayout(self)
        if quick:
            self.scope = QLabel()
            layout.addWidget(self.scope)
            self.query = QLineEdit()
            self.query.setPlaceholderText(
                "Find a folder" if folder_only else "Find a readable file or folder"
            )
            layout.addWidget(self.query)
            toggles = QHBoxLayout()
            self.include = QCheckBox(
                "Include hidden folders" if folder_only
                else "Include hidden cached files"
            )
            self.full = QCheckBox("Search full root")
            toggles.addWidget(self.include)
            toggles.addWidget(self.full)
            layout.addLayout(toggles)
            self.list = QListWidget()
            layout.addWidget(self.list)
            self.query.textChanged.connect(self.refresh)
            self.query.installEventFilter(self)
            self.list.installEventFilter(self)
            self.include.toggled.connect(self.refresh)
            self.full.toggled.connect(self.refresh)
            self.list.itemActivated.connect(lambda item: self.accept_file(False))
            self._query_generation = 0
            self._query_timer = QTimer(self)
            self._query_timer.setSingleShot(True)
            self._query_timer.setInterval(90)
            self._query_timer.timeout.connect(self._start_refresh)
            self.resultsReady.connect(self._apply_results)
            self._accept_when_ready = None
            self.refresh()
            self.query.setFocus()
        else:
            self.tree = NavigatorTree()
            self.tree.setModel(window.model)
            self.tree.setRootIndex(window.model.index(str(window.scope)))
            self.tree.vi_enabled = window.tree.vi_enabled
            self.tree.openFile.connect(self._opened)
            self.tree.escapePressed.connect(self.reject)
            layout.addWidget(self.tree)
            active = window.active_tab()
            target = active.path if active else window.scope
            QTimer.singleShot(0, lambda: self._reveal(target))

    def _reveal(self, path):
        index = self.window.model.index(str(path))
        if index.isValid():
            cursor = index.parent()
            while cursor.isValid():
                self.tree.expand(cursor)
                cursor = cursor.parent()
            self.tree.setCurrentIndex(index)
            self.tree.scrollTo(index)
        self.tree.setFocus()

    def _opened(self, path, pinned):
        self.window.open_file(Path(path))
        tab = self.window.active_tab()
        self.accept()
        QTimer.singleShot(0, lambda selected=tab: self.window._focus_tab_content(selected))

    def refresh(self):
        if not self.quick:
            return
        scope = self.window.root if self.full.isChecked() else self.window.scope
        cache_state = "indexing" if self.window.catalog_running else f"{self.window.catalog_count:,} cached"
        hidden_label = "hidden folders" if self.folder_only else "hidden cached files"
        self.scope.setText(
            f"Scope: {scope}  ·  "
            f"{'Including' if self.include.isChecked() else 'Excluding'} {hidden_label}"
            f"  ·  {cache_state}"
        )
        self._query_generation += 1
        self.list.clear()
        cache_label = "folder names" if self.folder_only else "filenames"
        item = QListWidgetItem(f"Searching cached {cache_label}…")
        item.setFlags(Qt.NoItemFlags)
        self.list.addItem(item)
        self._query_timer.start()

    def _start_refresh(self):
        if not self.quick:
            return
        generation = self._query_generation
        query = self.query.text().strip()
        scope = self.window.root if self.full.isChecked() else self.window.scope
        include_excluded = self.include.isChecked()
        opened = {tab.path for tab in self.window.all_tabs()}
        recent = set(self.window.recent)
        root = self.window.root
        catalog_db_available = self.window.catalog_db is not None

        def job():
            if generation != self._query_generation:
                return
            folders = set(self.window.catalog_directory_candidates(
                query, scope, include_excluded=include_excluded
            ))
            if self.folder_only:
                candidates = folders
            else:
                candidates = set(self.window.catalog_candidates(
                    query, scope, include_excluded=include_excluded
                ))
                candidates.update(folders)
            if generation != self._query_generation:
                return
            if not self.folder_only:
                candidates.update(opened)
            ranked = []
            for path in candidates:
                # SQLite rows were validated when indexed. Avoid thousands of
                # resolve/stat calls per keystroke; stale rows are discarded
                # lazily if the user selects one.
                if not catalog_db_available:
                    valid_path = path.is_dir() if self.folder_only else path.is_file()
                    if not valid_path or not inside(root, path):
                        continue
                try:
                    relative_path = path.relative_to(root)
                    path.relative_to(scope)
                except ValueError:
                    continue
                if (not include_excluded and path in opened
                        and self.window.excluded(path)):
                    continue
                relative = str(relative_path)
                is_folder = path in folders
                score = fuzzy_score(
                    query,
                    relative,
                    recent=not is_folder and path in recent,
                    opened=not is_folder and path in opened,
                )
                if score is not None:
                    ranked.append((-score, relative.casefold(), path, is_folder))
            ranked.sort()
            selected = []
            for candidate in ranked:
                path, is_folder = candidate[2], candidate[3]
                valid = path.is_dir() if is_folder else path.is_file()
                if valid and inside(root, path):
                    selected.append(candidate)
                    if len(selected) >= 150:
                        break
            self.resultsReady.emit(generation, selected)

        try:
            self.window.executor.submit(job)
        except RuntimeError:
            pass

    def _apply_results(self, generation, ranked):
        if generation != self._query_generation or not self.quick:
            return
        self.list.clear()
        for _, relative, path, is_folder in ranked:
            label = (
                f"📁 {path.name}    {Path(relative).parent}"
                if is_folder else f"{path.name}    {Path(relative).parent}"
            )
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, path)
            item.setData(Qt.UserRole + 1, is_folder)
            item.setToolTip(str(path))
            self.list.addItem(item)
        if not ranked:
            self._accept_when_ready = None
            if self.window.catalog_running:
                message = "Indexing folder… results will appear as they are discovered"
            elif self.folder_only:
                message = "No matching folders in the current scope and exclusion settings"
            else:
                message = "No files in the current scope and exclusion settings"
            item = QListWidgetItem(message)
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        else:
            self.list.setCurrentRow(0)
            if self._accept_when_ready is not None:
                pinned = self._accept_when_ready
                self._accept_when_ready = None
                self.accept_file(pinned)

    def accept_file(self, pinned=False):
        item = self.list.currentItem()
        if item and item.data(Qt.UserRole):
            path = Path(item.data(Qt.UserRole))
            if item.data(Qt.UserRole + 1) and path.is_dir():
                self.accept()
                QTimer.singleShot(0, lambda selected=path: self.window.reveal_tree(selected))
                return
            self.window.open_file(path, pinned=pinned)
            tab = self.window.active_tab()
            self.accept()
            QTimer.singleShot(0, lambda selected=tab: self.window._focus_tab_content(selected))
        elif self.quick and self._query_timer.isActive():
            # A fast Enter can arrive during the short debounce. Run the fresh
            # query now and open its first result when it reaches the UI.
            self._accept_when_ready = pinned
            self._query_timer.stop()
            self._start_refresh()

    def keyPressEvent(self, event):
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        vi_selection = (
            event.key() in (Qt.Key_J, Qt.Key_K)
            and (
                modifiers == Qt.ControlModifier
                or is_vi_navigation_chord(modifiers)
            )
        )
        if event.key() == Qt.Key_Escape:
            self.reject()
        elif self.quick and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept_file(bool(event.modifiers() & (Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier)))
        elif self.quick and (event.key() in (Qt.Key_Down, Qt.Key_Up) or vi_selection):
            delta = 1 if event.key() in (Qt.Key_Down, Qt.Key_J) else -1
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + delta)))
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if self.quick and obj in (self.query, self.list) and event.type() == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers() & ~Qt.KeypadModifier
            vi_selection = (
                key in (Qt.Key_J, Qt.Key_K)
                and (
                    modifiers == Qt.ControlModifier
                    or is_vi_navigation_chord(modifiers)
                )
            )
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down, Qt.Key_Up) or (
                    vi_selection):
                self.keyPressEvent(event)
                return True
        return super().eventFilter(obj, event)


class Window(QMainWindow):
    def __init__(self, root: Path):
        super().__init__()
        icon = get_folder_navigator_icon()
        if not icon.isNull():
            self.setWindowIcon(icon)
        self.root = root.resolve(strict=True)
        self.window_trace = None
        self.scope = self.root
        self.catalog: set[Path] = set()
        self.ignored_paths: set[Path] = set()
        self.catalog_cancel = threading.Event()
        self.catalog_running = False
        self.catalog_warmed = False
        self.catalog_indexed_this_run = 0
        self.catalog_force_scan = False
        self.catalog_db_error = None
        try:
            self.catalog_db = FolderCatalog(self.root)
            self.catalog_count = self.catalog_db.count()
            self.catalog_ui_state = self.catalog_db.ui_states()
            self.skipped_directories = dict(self.catalog_db.skipped_directories())
            self.catalog_state = self.catalog_db.scan_state()
        except (CatalogError, OSError, sqlite3.Error) as exc:
            self.catalog_db = None
            self.catalog_count = 0
            self.catalog_ui_state = {}
            self.skipped_directories = {}
            self.catalog_state = "unavailable"
            self.catalog_db_error = str(exc)
        self.recent: list[Path] = []
        self.executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORK, thread_name_prefix="folder-navigator")
        self.preview_executor = ThreadPoolExecutor(
            max_workers=max(1, min(2, MAX_CONCURRENT_WORK)),
            thread_name_prefix="folder-preview",
        )
        self.preview_futures = set()
        self.disposable_preview_futures = set()
        self.preview_metrics = {}
        self._preview_metrics_lock = threading.Lock()
        self.preview_selection_started = {}
        self.preview_perf_enabled = os.environ.get(
            "STILLPOINT_FOLDER_PREVIEW_METRICS", ""
        ).strip().casefold() in {"1", "true", "yes", "on"}
        self.bridge = Bridge(self)
        self.search_cancel = threading.Event()
        self.search_generation = 0
        self.new_file_edit = None
        self.new_file_directory = None
        self.new_file_is_folder = False
        self.new_file_diagram_suffix = None
        self.rename_file_edit = None
        self.rename_file_path = None
        self.tree_source_open_delay_ms = 90
        self.tree_markdown_open_delay_ms = 140
        self.tree_markdown_open_timer = QTimer(self)
        self.tree_markdown_open_timer.setSingleShot(True)
        self.tree_markdown_open_timer.timeout.connect(self._open_pending_tree_markdown)
        self.pending_tree_markdown_path = None
        self.markdown_preview_delay_ms = 200
        self.markdown_preview_timer = QTimer(self)
        self.markdown_preview_timer.setSingleShot(True)
        self.markdown_preview_timer.timeout.connect(self._refresh_pending_markdown_preview)
        self.pending_markdown_preview = None
        self.preview_hydration_delay_ms = 160
        self.preview_hydration_timer = QTimer(self)
        self.preview_hydration_timer.setSingleShot(True)
        self.preview_hydration_timer.timeout.connect(self._hydrate_pending_preview)
        self.pending_preview_hydration = None
        self.diagram_preview_cache = OrderedDict()
        self.diagram_preview_cache_bytes = 0
        self.diagram_preview_inflight = set()
        self.image_preview_cache = OrderedDict()
        self.image_preview_cache_bytes = 0
        self.image_preview_inflight = set()
        self._diagram_renderers = {}
        self._diagram_renderer_lock = threading.Lock()
        self.specialized_editor_windows: list[QMainWindow] = []
        self.excalidraw_processes: list[tuple[subprocess.Popen, str | None, Path]] = []
        self.excalidraw_browser_grants: set[str] = set()
        self.excalidraw_process_timer = QTimer(self)
        self.excalidraw_process_timer.setInterval(1000)
        self.excalidraw_process_timer.timeout.connect(self._poll_excalidraw_processes)
        self.tab_switcher = None
        self.tab_switcher_list = None
        self.tab_switcher_paths = []
        self._tab_switcher_rendered_paths = []
        self.tab_switcher_index = -1
        self._tab_switcher_focus_editor_on_activate = False
        self._startup_folder_focus_pending = True
        self._startup_selected_path = None
        self.bridge.result.connect(self._search_result)
        self.bridge.finished.connect(self._search_finished)
        self.setWindowTitle(f"Folder Navigator — {self.root.name}")
        self.resize(1100, 760)
        self.settings_path = Path.home() / ".stillpoint_folder_navigator.json"
        self.settings = self._load_settings()
        self.state = self.settings.setdefault(str(self.root), {})
        stored_masks = self.state.get("file_masks", [])
        self.file_masks = (
            [str(mask) for mask in stored_masks if str(mask).strip()]
            if isinstance(stored_masks, list) else []
        )
        try:
            self.editor_zoom_steps = max(-8, min(20, int(self.state.get("editor_zoom_steps", 0))))
            self.folder_zoom_steps = max(-8, min(20, int(self.state.get("folder_zoom_steps", 0))))
        except (TypeError, ValueError):
            self.editor_zoom_steps = 0
            self.folder_zoom_steps = 0
        self._last_focus_pane = "folder"
        self.model = FolderModel(self.root, self)
        self.model.setNameFilters(self.file_masks)
        self.tree = NavigatorTree()
        self._suppress_tree_preview = False
        try:
            global_settings = json.loads((Path.home() / ".stillpoint_config.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            global_settings = {}
        self.tree.vi_enabled = bool(global_settings.get("enable_vi_mode", False))
        self.tree.setModel(self.model)
        self.tree.setRootIndex(self.model.index(str(self.root)))
        self.tree.setColumnWidth(0, 340)
        self.tree.setColumnWidth(1, 100)
        self.tree.setColumnWidth(2, 130)
        self.tree.setColumnWidth(3, 170)
        self._tree_base_font = QFont(self.tree.font())
        self._apply_folder_zoom()
        self.tree.expanded.connect(lambda index: self._remember_expansion(index, True))
        self.tree.collapsed.connect(lambda index: self._remember_expansion(index, False))
        self.tree.selectionModel().currentChanged.connect(self._tree_selected)
        self.tree.openFile.connect(self._open_tree_file_keep_focus)
        self.tree.openFileAndFocus.connect(self._open_tree_file)
        self.tree.bookmarkPickerRequested.connect(self.bookmark_picker)
        self.tree.folderPickerRequested.connect(self.folder_picker)
        self.tree.pathsDropped.connect(self._copy_dropped_paths)
        self.tree.pathsMoved.connect(self._move_dropped_paths)
        self.tree.doubleClicked.connect(lambda i: self.open_file(Path(self.model.filePath(i)), pinned=True) if not self.model.isDir(i) else None)
        self.tree.escapePressed.connect(self._escape_tree)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._tree_menu)
        self.search_input = QLineEdit()
        self.search_input.setPlaceholderText("Search filenames and text contents")
        self.search_results = QListWidget()
        self.search_results.itemActivated.connect(self._open_search_item)
        self.search_progress = QLabel("Search is on demand; no content index is kept")
        self.search_case = QCheckBox("Case")
        self.search_word = QCheckBox("Whole word")
        self.search_regex = QCheckBox("Regex")
        self.search_ignored = QCheckBox("Include ignored")
        search_page = QWidget()
        search_layout = QVBoxLayout(search_page)
        search_layout.addWidget(self.search_input)
        options = QHBoxLayout()
        for control in (self.search_case, self.search_word, self.search_regex, self.search_ignored):
            options.addWidget(control)
        search_layout.addLayout(options)
        buttons = QHBoxLayout()
        run = QPushButton("Search")
        stop = QPushButton("Cancel")
        run.clicked.connect(self.run_search)
        stop.clicked.connect(self.search_cancel.set)
        buttons.addWidget(run)
        buttons.addWidget(stop)
        search_layout.addLayout(buttons)
        search_layout.addWidget(self.search_progress)
        search_layout.addWidget(self.search_results)
        self.search_input.returnPressed.connect(self.run_search)
        self.folder_page = QWidget()
        folder_page_layout = QVBoxLayout(self.folder_page)
        folder_page_layout.setContentsMargins(0, 0, 0, 0)
        folder_page_layout.setSpacing(0)
        identity_accent = str(theme_value("folder_navigator.identity.accent", "#4f8f8b"))
        self.folder_panel_header = UtilityPanelHeader(
            "Files",
            self.root.name,
            accent_color=identity_accent,
        )
        self.folder_panel_header.detail_label.setMaximumWidth(160)
        self.folder_panel_header.set_detail(self.root.name, str(self.root))
        folder_page_layout.addWidget(self.folder_panel_header)
        folder_page_layout.addWidget(self.tree)
        self.rail = QTabWidget()
        self.rail.setObjectName("folderNavigatorRail")
        self.rail.addTab(self.folder_page, "Files")
        self.rail.addTab(search_page, "Search")
        self.tabs = QTabWidget()
        self.tabs.setObjectName("folderNavigatorEditors")
        self.tabs.setTabsClosable(True)
        self.tabs.tabBar().setElideMode(Qt.TextElideMode.ElideMiddle)
        self.tabs.tabBar().setExpanding(False)
        self.tabs.tabBar().setUsesScrollButtons(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBarClicked.connect(self._focus_clicked_tab_editor)
        self.tabs.tabBarDoubleClicked.connect(lambda i: self.keep_open(i))
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        self.welcome = QLabel(f"{self.root.name}\n\nSelect a file to preview · Enter to keep it open · Ctrl+J to find a file")
        self.welcome.setAlignment(Qt.AlignCenter)
        self.content = QSplitter(Qt.Vertical)
        self.content.addWidget(self.tabs)
        self.content.addWidget(self.welcome)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.rail)
        self.splitter.addWidget(self.content)
        self.chat_panel = None
        self.chat_container = None
        self.chat_tabs = None
        self.chat_minibar = None
        self.chat_visible = False
        self.chat_error = None
        from sp.app import config
        if config.load_enable_folder_navigator_chat():
            try:
                self._create_chat_panel()
            except (OSError, sqlite3.Error, RuntimeError) as exc:
                self.chat_error = str(exc)
                self.chat_panel = None
        splitter_sizes = self.state.get("splitter", [300, 800])
        if self.chat_panel is not None and len(splitter_sizes) == 2:
            chat_width = self._chat_last_width if self.chat_visible else 28
            splitter_sizes = [
                splitter_sizes[0],
                max(300, splitter_sizes[1] - chat_width),
                chat_width,
            ]
        elif self.chat_panel is not None and len(splitter_sizes) == 3:
            splitter_sizes = list(splitter_sizes)
            desired_chat_width = self._chat_last_width if self.chat_visible else 28
            if (self.chat_visible and splitter_sizes[2] < 100) or not self.chat_visible:
                reclaimed = splitter_sizes[2] - desired_chat_width
                splitter_sizes[1] = max(1, splitter_sizes[1] + reclaimed)
                splitter_sizes[2] = desired_chat_width
        self.splitter.setSizes(splitter_sizes)
        self._rail_last_width = max(
            160, int(self.state.get("rail_width", self.state.get("splitter", [300])[0]))
        )
        self.rail.setVisible(bool(self.state.get("rail_visible", True)))
        outer = QWidget()
        layout = QVBoxLayout(outer)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.identity_bar = CompactToolbarIdentity(
            "Folder Navigator",
            self.root.name,
            icon=icon,
            accent_color=identity_accent,
        )
        self.identity_bar.set_detail(self.root.name, str(self.root))
        self.identity_bar.breadcrumbActivatedWithModifiers.connect(
            self._open_folder_breadcrumb
        )
        self.bookmarks_bar = QHBoxLayout()
        self.bookmarks_bar.setContentsMargins(7, 4, 7, 4)
        layout.addLayout(self.bookmarks_bar)
        layout.addWidget(self.splitter)
        self.setCentralWidget(outer)
        self.statusBar().setStyleSheet(status_bar_stylesheet(self.statusBar()))
        if self.catalog_db_error:
            self.statusBar().showMessage(
                f"Quick Open cache unavailable: {self.catalog_db_error}", 12000
            )
        if self.chat_error:
            self.statusBar().showMessage(f"AI chat unavailable: {self.chat_error}", 12000)
        self.index_notice = QLabel()
        self.index_notice.setAccessibleName("Folder indexing status")
        self.statusBar().addPermanentWidget(self.index_notice, 1)
        self.filter_label = QPushButton("Filtered")
        self.filter_label.setObjectName("filterStatusLabel")
        self.filter_label.setAccessibleName("Clear folder filter")
        self.filter_label.setCursor(Qt.PointingHandCursor)
        self.filter_label.setStyleSheet(
            "QPushButton#filterStatusLabel {"
            f"border: 1px solid {theme_value('main_window.badge.border', '#666666')};"
            "padding: 2px 6px; border-radius: 3px; margin-right: 6px;"
            f"background-color: {theme_value('main_window.filter_badge.bg', '#c62828')};"
            f"color: {theme_value('main_window.filter_badge.text', '#ffffff')};"
            "}"
        )
        self.filter_label.setToolTip("Click to clear the folder filter")
        self.filter_label.clicked.connect(self.clear_filter)
        self.filter_label.hide()
        self.statusBar().addPermanentWidget(self.filter_label)
        self.index_cancel_button = QPushButton("Cancel Indexing")
        self.index_cancel_button.clicked.connect(self._cancel_catalog_indexing)
        self.index_cancel_button.hide()
        self.statusBar().addPermanentWidget(self.index_cancel_button)
        self.index_continue_button = QPushButton("Continue Full Index")
        self.index_continue_button.clicked.connect(self._continue_catalog_indexing)
        self.index_continue_button.hide()
        self.statusBar().addPermanentWidget(self.index_continue_button)
        self._update_index_notice()
        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(self.root))
        self.watcher.directoryChanged.connect(lambda _: self._schedule_refresh())
        self.watcher.fileChanged.connect(lambda _: self._schedule_refresh())
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._refresh_disk)
        self.catalog_refresh_timer = QTimer(self)
        self.catalog_refresh_timer.setSingleShot(True)
        self.catalog_refresh_timer.timeout.connect(self._refresh_quick_pickers)
        self.layout_save_timer = QTimer(self)
        self.layout_save_timer.setSingleShot(True)
        self.layout_save_timer.timeout.connect(self._persist_sqlite_layout)
        self.tree.header().sectionResized.connect(lambda *_: self.layout_save_timer.start(500))
        self.tree.header().sectionMoved.connect(lambda *_: self.layout_save_timer.start(500))
        self.tree.header().sortIndicatorChanged.connect(lambda *_: self.layout_save_timer.start(500))
        self._make_actions()
        if self.chat_error:
            self.statusBar().showMessage(f"AI chat could not open: {self.chat_error}", 20000)
        self._apply_identity_style()
        self.command_palette = CommandBar(self)
        self.command_palette.actionTriggered.connect(self._run_command_action)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            app.focusChanged.connect(self._on_focus_changed)
        self._apply_focus_borders()
        self._render_bookmarks()
        self._restore()
        if self.chat_panel is not None:
            self._warm_catalog()
        self.model.directoryLoaded.connect(lambda _: self._schedule_refresh())
        self.model.directoryLoaded.connect(self._focus_folder_tree_on_launch)
        self.model.rowsInserted.connect(lambda parent, first, last: self._catalog_rows(parent, first, last))
        from .instances import InstanceRegistration
        self.instance_registration = InstanceRegistration(self, self.root)
        if (sys.platform == "win32" and os.environ.get(
                "STILLPOINT_FOLDER_WINDOW_TRACE", ""
        ).strip().casefold() in {"1", "true", "yes", "on"}):
            from .window_trace import WindowTrace
            self.window_trace = WindowTrace(self)
            app.aboutToQuit.connect(self.window_trace.close)
            self.statusBar().showMessage(
                f"Window popup trace: {self.window_trace.path}", 12000
            )

    def _load_settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _on_focus_changed(self, _old, _current):
        try:
            if _current is self.rail or (
                    _current is not None and self.rail.isAncestorOf(_current)):
                self._last_focus_pane = "folder"
            elif _current is self.tabs or (
                    _current is not None and self.tabs.isAncestorOf(_current)):
                self._last_focus_pane = "editor"
        except RuntimeError:
            pass
        self._apply_focus_borders()

    def _apply_identity_style(self):
        """Apply restrained Folder Navigator chrome without changing content themes."""
        accent = QColor(str(theme_value(
            "folder_navigator.identity.accent", "#4f8f8b"
        )))
        if not accent.isValid():
            accent = QColor("#4f8f8b")
        self._folder_identity_accent = accent.name()
        self.identity_bar.set_accent_color(accent.name())
        self.folder_panel_header.set_accent_color(accent.name())
        self.tree.setStyleSheet(
            tree_view_stylesheet(self.tree, accent_color=accent.name())
            + "QHeaderView::section { border: 0; border-bottom: 1px solid palette(mid); "
            "padding: 4px 6px; }"
        )

    def _apply_focus_borders(self):
        """Highlight the active navigator pane like StillPoint's main window."""
        try:
            focused = self.focusWidget()
            folder_has_focus = focused is self.rail or (
                focused is not None and self.rail.isAncestorOf(focused)
            )
            editor_has_focus = focused is self.tabs or (
                focused is not None and self.tabs.isAncestorOf(focused)
            )
            chat_has_focus = self.chat_tabs is not None and (
                focused is self.chat_tabs
                or (focused is not None and self.chat_tabs.isAncestorOf(focused))
            )
        except RuntimeError:
            return
        accent = getattr(self, "_folder_identity_accent", "#4f8f8b")
        neutral = QApplication.palette().color(QPalette.Mid).name()
        self.rail.setStyleSheet(
            tab_widget_stylesheet(
                self.rail,
                object_name="folderNavigatorRail",
                accent_color=accent,
                pane_border=accent if folder_has_focus else neutral,
                readable_rail_tabs=True,
            )
        )
        self.tabs.setStyleSheet(
            tab_widget_stylesheet(
                self.tabs,
                object_name="folderNavigatorEditors",
                accent_color=accent,
                pane_border=accent if editor_has_focus else neutral,
                readable_rail_tabs=True,
            )
        )
        if self.chat_tabs is not None:
            self.chat_tabs.setStyleSheet(
                tab_widget_stylesheet(
                    self.chat_tabs,
                    object_name="folderNavigatorChatRail",
                    accent_color=accent,
                    pane_border=accent if chat_has_focus else neutral,
                    readable_rail_tabs=True,
                )
            )

    @staticmethod
    def _encode_qt_state(value):
        return bytes(value.toBase64()).decode("ascii")

    def _persist_sqlite_layout(self):
        if self.catalog_db is None:
            return
        try:
            order = self.tree.header().sortIndicatorOrder()
            self.catalog_db.set_ui_states({
                "window_geometry": self._encode_qt_state(self.saveGeometry()),
                "splitter_state": self._encode_qt_state(self.splitter.saveState()),
                "tree_header": self._encode_qt_state(self.tree.header().saveState()),
                "sort_column": str(self.tree.header().sortIndicatorSection()),
                "sort_order": str(order.value),
            })
        except (OSError, sqlite3.Error, RuntimeError):
            pass

    def _update_index_notice(self):
        if not hasattr(self, "index_notice"):
            return
        self.index_cancel_button.setVisible(self.catalog_running)
        self.index_continue_button.setVisible(
            not self.catalog_running and self.catalog_state == "partial"
        )
        if self.catalog_running:
            budget = (
                "full scan; Cancel remains available"
                if self.catalog_force_scan
                else f"safety limit {MAX_INDEX_FILES:,} files / {MAX_INDEX_SECONDS:g}s"
            )
            self.index_notice.setText(
                f"Indexing filenames… {self.catalog_indexed_this_run:,} found "
                f"({budget})"
            )
            self.index_notice.setToolTip(
                "Quick Open remains usable while the filename index is built."
            )
            self.index_notice.show()
        elif self.catalog_state == "partial":
            self.index_notice.setText(
                f"⚠ Partial Quick Open index · {self.catalog_count:,} cached files"
            )
            self.index_notice.setToolTip(
                "The safety budget stopped full-root indexing. Quick Open still uses "
                "the partial cache. Continue only if a complete root index is needed."
            )
            self.index_notice.show()
        elif self.skipped_directories:
            count = len(self.skipped_directories)
            noun = "folder" if count == 1 else "folders"
            self.index_notice.setText(
                f"⚠ {count} large {noun} not indexed · Quick Open/search incomplete"
            )
            details = [
                f"{path.relative_to(self.root)} ({entries:,} entries)"
                for path, entries in sorted(
                    self.skipped_directories.items(), key=lambda item: str(item[0]).casefold()
                )[:20]
            ]
            self.index_notice.setToolTip(
                "Folders over the indexing safety limit:\n" + "\n".join(details)
            )
            self.index_notice.show()
        else:
            self.index_notice.clear()
            self.index_notice.hide()

    def _cancel_catalog_indexing(self):
        if self.catalog_running:
            self.catalog_cancel.set()
            self.index_notice.setText(
                f"Canceling indexing… {self.catalog_indexed_this_run:,} files retained"
            )
            self.index_cancel_button.setEnabled(False)

    def _continue_catalog_indexing(self):
        if self.catalog_running:
            return
        self.catalog_warmed = False
        self._warm_catalog(force=True)

    def _persist(self):
        current = self.tree.currentIndex()
        selected = self.model.filePath(current) if current.isValid() else None
        splitter_sizes = (
            self.splitter.sizes()
            if not self.rail.isHidden()
            else self.state.get("splitter", [self._rail_last_width, 800])
        )
        self.state.update(splitter=splitter_sizes, rail=self.rail.currentIndex(),
                          chat_visible=self.chat_visible,
                          chat_width=getattr(self, "_chat_last_width", 420),
                          rail_visible=not self.rail.isHidden(),
                          rail_width=self._rail_last_width,
                          pinned=[str(t.path) for t in self.all_tabs() if t.pinned],
                          active=str(self.active_tab().path) if self.active_tab() and self.active_tab().pinned else None,
                          selected=selected,
                          geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"))
        self._persist_sqlite_layout()
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)

    def _restore(self):
        from PySide6.QtCore import QByteArray
        geometry = self.catalog_ui_state.get("window_geometry") or self.state.get("geometry")
        if geometry:
            self.restoreGeometry(QByteArray.fromBase64(geometry.encode("ascii")))
        splitter_state = self.catalog_ui_state.get("splitter_state")
        if splitter_state:
            self.splitter.restoreState(QByteArray.fromBase64(splitter_state.encode("ascii")))
        header_state = self.catalog_ui_state.get("tree_header")
        if header_state:
            self.tree.header().restoreState(
                QByteArray.fromBase64(header_state.encode("ascii"))
            )
        if "sort_column" in self.catalog_ui_state:
            try:
                column = int(self.catalog_ui_state["sort_column"])
                order = Qt.SortOrder(int(self.catalog_ui_state.get("sort_order", "0")))
                self.tree.sortByColumn(column, order)
            except (TypeError, ValueError):
                pass
        self._sync_column_actions()
        self.rail.setCurrentIndex(self.state.get("rail", 0))
        selected = self.state.get("selected")
        if selected:
            selected_path = Path(selected)
            if inside(self.root, selected_path):
                self._startup_selected_path = selected_path
        for name in self.state.get("pinned", []):
            path = Path(name)
            if path.is_file() and inside(self.root, path):
                self.open_file(path, pinned=True)
            else:
                self.statusBar().showMessage(f"Omitted stale tab: {name}", 15000)
        active = self.state.get("active")
        for index, tab in enumerate(self.all_tabs()):
            if str(tab.path) == active:
                self.tabs.setCurrentIndex(index)
        for name in self.state.get("expanded", []):
            path = Path(name)
            if path.is_dir() and inside(self.root, path):
                index = self.model.index(name)
                if index.isValid():
                    self.tree.expand(index)
            else:
                self.statusBar().showMessage(f"Omitted stale expansion: {name}", 12000)

    def showEvent(self, event):  # type: ignore[override]
        super().showEvent(event)
        if self._startup_folder_focus_pending:
            QTimer.singleShot(0, self._focus_folder_tree_on_launch)

    def event(self, event):  # type: ignore[override]
        result = super().event(event)
        if event.type() == QEvent.WindowActivate:
            QTimer.singleShot(0, self._repair_focus_after_activation)
        return result

    def _repair_focus_after_activation(self):
        """Restore keyboard focus if an OS window switch left no pane active."""
        if QApplication.activeWindow() is not self:
            return
        if QApplication.activeModalWidget() is not None or QApplication.activePopupWidget() is not None:
            return
        focused = QApplication.focusWidget()
        if focused is not None and focused.window() is self:
            pane_roots = (
                getattr(self, "rail", None),
                getattr(self, "tabs", None),
                getattr(self, "command_palette", None),
            )
            if any(root is not None and (focused is root or root.isAncestorOf(focused))
                   for root in pane_roots):
                return
        tab = self.active_tab() if hasattr(self, "tabs") else None
        if tab is not None:
            target = tab.editor or getattr(tab, "viewer", None) or self.tabs
            target.setFocus(Qt.ActiveWindowFocusReason)
        elif hasattr(self, "rail"):
            if self.rail.isHidden():
                self._set_folder_rail_visible(True)
            self.rail.setCurrentIndex(0)
            self.tree.setFocus(Qt.ActiveWindowFocusReason)
        self._apply_focus_borders()

    def _focus_folder_tree_on_launch(self, loaded_path=None):
        """Give a newly shown navigator a usable initial tree selection."""
        if not self._startup_folder_focus_pending or not self.isVisible():
            return
        if self.rail.isHidden():
            self._startup_folder_focus_pending = False
            tab = self.active_tab()
            if tab is not None:
                self._focus_tab_content(tab)
            else:
                self.content.setFocus(Qt.OtherFocusReason)
            return
        # Focus is useful even for an empty or not-yet-populated model. Keep
        # the pending flag until a row can also be selected.
        self.rail.setCurrentIndex(0)
        self.tree.setFocus(Qt.OtherFocusReason)
        root_index = self.tree.rootIndex()
        target = None
        if self._startup_selected_path is not None:
            restored = self.model.index(str(self._startup_selected_path))
            if restored.isValid():
                target = restored
        if target is None:
            row_count = self.model.rowCount(root_index)
            if row_count:
                target = self.model.index(0, 0, root_index)
            elif loaded_path is None or Path(loaded_path) != self.scope:
                return

        self._startup_folder_focus_pending = False
        if target is not None and target.isValid():
            self._suppress_tree_preview = True
            self.tree.setCurrentIndex(target)
            self.tree.scrollTo(target)
            self._suppress_tree_preview = False

    def _remember_expansion(self, index, expanded):
        name = self.model.filePath(index)
        paths = self.state.setdefault("expanded", [])
        if expanded and name not in paths:
            paths.append(name)
        elif not expanded and name in paths:
            paths.remove(name)

    def _catalog_rows(self, parent, first, last):
        discovered = []
        for row in range(first, last + 1):
            index = self.model.index(row, 0, parent)
            if not self.model.isDir(index):
                path = Path(self.model.filePath(index))
                if inside(self.root, path):
                    self.catalog.add(path)
                    discovered.append(path)
        if discovered and self.catalog_db is not None and not self.catalog_cancel.is_set():
            try:
                self.executor.submit(self.catalog_db.upsert_paths, discovered)
            except RuntimeError:
                pass

    def catalog_candidates(self, query, scope, *, include_excluded=False):
        if self.catalog_db is not None:
            try:
                return self.catalog_db.candidates(
                    query, scope, include_excluded=include_excluded
                )
            except (OSError, sqlite3.Error):
                pass
        return self.catalog

    def catalog_directory_candidates(self, query, scope, *, include_excluded=False):
        if self.catalog_db is not None:
            try:
                return self.catalog_db.directory_candidates(
                    query, scope, include_excluded=include_excluded
                )
            except (OSError, sqlite3.Error):
                pass
        directories = set()
        for path in self.catalog:
            for parent in path.parents:
                if parent == self.root:
                    break
                if parent != scope and inside(scope, parent):
                    directories.add(parent)
        return directories

    def _make_actions(self):
        file_menu = self.menuBar().addMenu("&File")
        edit_menu = self.menuBar().addMenu("&Edit")
        view_menu = self.menuBar().addMenu("&View")
        go_menu = self.menuBar().addMenu("&Go")
        self.commands = []
        def add(menu, title, callback, shortcut=None):
            action = QAction(title, self)
            action.setProperty(
                "commandLabel",
                f"{menu.title().replace('&', '')} / {title.replace('&', '')}",
            )
            if shortcut:
                action.setShortcut(QKeySequence(shortcut))
                action.setShortcutContext(Qt.WindowShortcut)
            action.triggered.connect(callback)
            menu.addAction(action)
            self.commands.append(action)
            return action
        add(file_menu, "Open Folder…", self.open_folder)
        add(file_menu, "Add Bookmark to StillPoint", self._bookmark_in_stillpoint)
        self.save_action = add(file_menu, "Save", self.save_active, QKeySequence.Save)
        add(file_menu, "Save All", self.save_all)
        add(file_menu, "Print Page", self.print_active, QKeySequence.Print)
        add(file_menu, "Close Window", self.close)
        self.format_markdown_table_action = add(
            edit_menu, "Format Markdown Table", self._format_active_markdown_table
        )
        self.hidden_action = add(view_menu, "Show Hidden Files", self.toggle_hidden)
        self.hidden_action.setCheckable(True)
        self.hidden_action.setChecked(self.state.get("hidden", False))
        self.toggle_hidden(self.hidden_action.isChecked())
        add(view_menu, "Zoom In", lambda: self._zoom_active_view(1), QKeySequence.ZoomIn)
        add(view_menu, "Zoom Out", lambda: self._zoom_active_view(-1), QKeySequence.ZoomOut)
        self.rail_visibility_action = add(
            view_menu, "Show Folder Rail", self._toggle_folder_rail, "Ctrl+Shift+B"
        )
        self.rail_visibility_action.setCheckable(True)
        self.rail_visibility_action.setChecked(not self.rail.isHidden())
        if self.chat_panel is not None:
            self.chat_visibility_action = add(
                view_menu,
                "Show AI Chat",
                self._toggle_chat_panel,
                "Ctrl+Shift+N",
            )
            self.chat_visibility_action.setCheckable(True)
            self.chat_visibility_action.setChecked(self.chat_visible)
        self.table_preview_action = add(view_menu, "Table Preview", self._reopen_active_table)
        self.raw_text_action = add(view_menu, "Raw Text", self._reopen_active_as_text)
        self.table_preview_action.setEnabled(False)
        self.raw_text_action.setEnabled(False)
        columns_menu = view_menu.addMenu("Columns")
        columns_toolbar = self.addToolBar("File columns")
        self.columns_toolbar = columns_toolbar
        columns_toolbar.setObjectName("folder_navigator_columns")
        columns_toolbar.setMovable(False)
        self.column_actions = {}
        column_specs = (
            (1, "Size", QStyle.StandardPixmap.SP_DriveHDIcon),
            (2, "Type", QStyle.StandardPixmap.SP_FileIcon),
            (3, "Modified", QStyle.StandardPixmap.SP_BrowserReload),
        )
        for column, title, standard_icon in column_specs:
            action = QAction(self.style().standardIcon(standard_icon), title, self)
            action.setCheckable(True)
            action.setChecked(not self.tree.isColumnHidden(column))
            action.setToolTip(f"Show or hide the {title.lower()} column")
            action.setStatusTip(action.toolTip())
            action.toggled.connect(
                lambda visible, selected=column: self._set_column_visible(selected, visible)
            )
            columns_menu.addAction(action)
            columns_toolbar.addAction(action)
            self.commands.append(action)
            self.column_actions[column] = action
        identity_spacer = QWidget(columns_toolbar)
        identity_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        columns_toolbar.addWidget(identity_spacer)
        columns_toolbar.addWidget(self.identity_bar)
        add(go_menu, "Folder", lambda: (self.rail.setCurrentIndex(0), self.tree.setFocus()))
        add(go_menu, "Search", lambda: (self.rail.setCurrentIndex(1), self.search_input.setFocus()))
        add(go_menu, "Editor", lambda: self._focus_tab_content(self.active_tab()))
        add(go_menu, "Markdown Headings…", self._show_active_heading_picker, "Ctrl+Alt+T")
        add(go_menu, "Quick Open", self.quick_open, "Ctrl+J")
        add(go_menu, "Folder Picker", self.folder_picker, "Ctrl+Alt+V")
        add(go_menu, "Go to Bookmark", self.bookmark_picker)
        self.reveal_active_tab_action = add(
            go_menu, "Reveal in Folder", self._reveal_active_tab_in_folder
        )
        self.reveal_active_tab_action.setEnabled(self.active_tab() is not None)
        add(go_menu, "Filter From Here", self.filter_from_here)
        add(go_menu, "Next Tab", lambda: self._cycle_tab_popup(False))
        add(go_menu, "Previous Tab", lambda: self._cycle_tab_popup(True))
        add(go_menu, "Command Bar", self.command_bar, "Alt+G")
        self.file_mask_action = add(
            view_menu, "File Mask…", self._choose_file_mask
        )
        self.clear_file_mask_action = add(
            view_menu, "Clear File Mask", self._clear_file_mask
        )
        self.clear_file_mask_action.setEnabled(bool(self.file_masks))
        self.clear_filter_action = add(go_menu, "Remove Filter", self.clear_filter)
        self.clear_filter_action.setEnabled(self.scope != self.root)
        self.command_bar_shortcuts = []
        for sequence in (["Ctrl+Shift+P", "Meta+Shift+P"] if sys.platform == "darwin"
                         else ["Ctrl+Shift+P"]):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(self.command_bar)
            self.command_bar_shortcuts.append(shortcut)
        self.native_open_shortcuts = []
        for sequence in ("Ctrl+Shift+Return", "Ctrl+Shift+Enter"):
            shortcut = QShortcut(QKeySequence(sequence), self.rail)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self._open_navigator_selection_in_system)
            self.native_open_shortcuts.append(shortcut)
        forward_sequence, backward_sequence = history_cycle_sequences()
        cycle_sequences = [(forward_sequence, False), (backward_sequence, True)]
        if sys.platform == "darwin":
            # Qt and Cocoa can report the physical Control key through either
            # modifier spelling depending on the focused native view. Keep the
            # recent-tab switcher reachable with the user's physical Control
            # chord in either case.
            cycle_sequences.extend((("Ctrl+Tab", False), ("Ctrl+Shift+Tab", True)))
        self.tab_cycle_shortcuts = []
        for sequence, reverse in cycle_sequences:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(lambda backwards=reverse: self._cycle_tab_popup(backwards))
            self.tab_cycle_shortcuts.append(shortcut)
        close_shortcut = QShortcut(QKeySequence.Close, self)
        close_shortcut.activated.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        self.mru = []
        self._cycling_tabs = False

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.layout_save_timer.start(250)

    def _toggle_folder_rail(self):
        self._set_folder_rail_visible(self.rail.isHidden())

    def _set_folder_rail_visible(self, visible):
        visible = bool(visible)
        if visible == (not self.rail.isHidden()):
            return
        focused = self.focusWidget()
        rail_had_focus = focused is self.rail or (
            focused is not None and self.rail.isAncestorOf(focused)
        )
        sizes = self.splitter.sizes()
        total = sum(sizes) or max(1, self.splitter.width())
        if not visible:
            if sizes and sizes[0] > 0:
                self._rail_last_width = sizes[0]
                self.state["splitter"] = list(sizes)
            self.rail.hide()
            if rail_had_focus:
                tab = self.active_tab()
                if tab is not None:
                    self._focus_tab_content(tab)
                else:
                    self.content.setFocus(Qt.OtherFocusReason)
        else:
            self.rail.show()
            width = min(self._rail_last_width, max(160, total - 160))
            if self.chat_container is not None:
                chat_width = self.chat_container.width()
                self.splitter.setSizes(
                    [width, max(1, total - width - chat_width), chat_width]
                )
            else:
                self.splitter.setSizes([width, max(1, total - width)])
            if self.active_tab() is None:
                self.tree.setFocus(Qt.OtherFocusReason)
        self.rail_visibility_action.blockSignals(True)
        self.rail_visibility_action.setChecked(visible)
        self.rail_visibility_action.blockSignals(False)
        self.state["rail_visible"] = visible
        self._persist()

    def _sync_column_actions(self):
        for column, action in getattr(self, "column_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(not self.tree.isColumnHidden(column))
            action.blockSignals(False)

    def _zoom_active_view(self, direction):
        """Route StillPoint's standard zoom keys to the active viewer."""
        focused = self.focusWidget()
        folder_focused = focused is self.rail or (
            focused is not None and self.rail.isAncestorOf(focused)
        )
        if folder_focused or (
                focused is not None and self.command_palette.isAncestorOf(focused)
                and self._last_focus_pane == "folder"):
            self.folder_zoom_steps = max(-8, min(20, self.folder_zoom_steps + direction))
            self.state["folder_zoom_steps"] = self.folder_zoom_steps
            self._apply_folder_zoom()
            self._persist()
            return
        tab = self.active_tab()
        if tab is None:
            return
        if tab.editor is not None:
            new_steps = max(-8, min(20, self.editor_zoom_steps + direction))
            applied = new_steps - self.editor_zoom_steps
            if not applied:
                return
            self.editor_zoom_steps = new_steps
            self.state["editor_zoom_steps"] = new_steps
            for candidate in self.all_tabs():
                if candidate.editor is not None:
                    if applied > 0:
                        candidate.editor.zoomIn(applied)
                    else:
                        candidate.editor.zoomOut(-applied)
            self._persist()
            return
        viewer = getattr(tab, "viewer", None)
        if isinstance(viewer, SpreadsheetView):
            new_steps = max(-8, min(20, self.editor_zoom_steps + direction))
            applied = new_steps - self.editor_zoom_steps
            if not applied:
                return
            self.editor_zoom_steps = new_steps
            self.state["editor_zoom_steps"] = new_steps
            for candidate in self.all_tabs():
                if candidate.editor is not None:
                    if applied > 0:
                        candidate.editor.zoomIn(applied)
                    else:
                        candidate.editor.zoomOut(-applied)
                candidate_viewer = getattr(candidate, "viewer", None)
                if isinstance(candidate_viewer, SpreadsheetView):
                    candidate_viewer.set_zoom_steps(new_steps)
            self._persist()
            return
        callback = getattr(viewer, "zoom_in" if direction > 0 else "zoom_out", None)
        if callable(callback):
            callback()
        else:
            self.statusBar().showMessage("Zoom is not available for this preview", 2500)

    def _apply_folder_zoom(self):
        font = QFont(self._tree_base_font)
        base_size = font.pointSizeF()
        if base_size <= 0:
            base_size = QApplication.font().pointSizeF()
        font.setPointSizeF(max(6.0, base_size + self.folder_zoom_steps))
        self.tree.setFont(font)

    def active_tab(self):
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, Tab) else None

    def _create_chat_panel(self):
        from .chat import FolderChatPanel

        self.chat_panel = FolderChatPanel(
            self.root,
            file_candidates=self._chat_file_candidates,
            directory_candidates=self._chat_directory_candidates,
            editor_text=self._chat_editor_text,
            image_candidates=self._chat_image_candidates,
            index_state=lambda: self.catalog_state,
            dirty_paths=lambda: {tab.path for tab in self.all_tabs() if tab.dirty},
            parent=self,
        )
        self.chat_panel.chatNavigateRequested.connect(self._chat_navigate)
        self.chat_panel.pageWritten.connect(self._chat_file_written)
        self.chat_tabs = QTabWidget()
        self.chat_tabs.setObjectName("folderNavigatorChatRail")
        self.chat_tabs.addTab(self.chat_panel, "AI Chat")

        self.chat_toggle_button = QToolButton()
        self.chat_toggle_button.setAutoRaise(True)
        self.chat_toggle_button.setFocusPolicy(Qt.NoFocus)
        self.chat_toggle_button.clicked.connect(
            lambda *_: self._toggle_chat_panel(False)
        )
        self.chat_tabs.setCornerWidget(self.chat_toggle_button, Qt.TopRightCorner)

        self.chat_minibar_tab = QTabBar()
        self.chat_minibar_tab.setObjectName("folderNavigatorChatMinibarTab")
        self.chat_minibar_tab.setDocumentMode(True)
        self.chat_minibar_tab.setExpanding(False)
        self.chat_minibar_tab.setUsesScrollButtons(False)
        self.chat_minibar_tab.setFocusPolicy(Qt.NoFocus)
        self.chat_minibar_tab.setElideMode(Qt.ElideNone)
        self.chat_minibar_tab.setShape(QTabBar.RoundedEast)
        self.chat_minibar_tab.addTab("AI Chat")
        self.chat_minibar_tab.tabBarClicked.connect(
            lambda _index: self._toggle_chat_panel(True)
        )
        chat_tab_colors = chrome_colors(
            self.chat_minibar_tab,
            theme_value("folder_navigator.identity.accent", "#4f8f8b"),
        )
        self.chat_minibar_tab.setStyleSheet(
            f"QTabBar::tab {{ background: {chat_tab_colors['rail_inactive']}; "
            f"color: {chat_tab_colors['rail_inactive_text']}; "
            f"border: 1px solid {chat_tab_colors['border']}; "
            "border-radius: 4px; padding: 6px 10px; margin: 2px 0; }"
            f"QTabBar::tab:selected {{ background: {chat_tab_colors['rail_active']}; "
            f"color: {chat_tab_colors['rail_active_text']}; "
            f"border: 1px solid {chat_tab_colors['accent']}; }}"
        )
        self.chat_minibar_toggle = QToolButton()
        self.chat_minibar_toggle.setAutoRaise(True)
        self.chat_minibar_toggle.setFocusPolicy(Qt.NoFocus)
        self.chat_minibar_toggle.clicked.connect(
            lambda *_: self._toggle_chat_panel(True)
        )
        self.chat_minibar = QWidget()
        minibar_layout = QVBoxLayout(self.chat_minibar)
        minibar_layout.setContentsMargins(0, 0, 0, 0)
        minibar_layout.setSpacing(0)
        minibar_layout.setAlignment(Qt.AlignTop)
        minibar_layout.addWidget(self.chat_minibar_toggle)
        minibar_layout.addWidget(self.chat_minibar_tab)

        self.chat_container = QStackedWidget()
        self.chat_container.setObjectName("folderNavigatorChatContainer")
        self.chat_container.addWidget(self.chat_tabs)
        self.chat_container.addWidget(self.chat_minibar)
        self.splitter.addWidget(self.chat_container)
        self._chat_last_width = max(220, int(self.state.get("chat_width", 420)))
        self.chat_visible = bool(self.state.get("chat_visible", True))
        self._set_chat_panel_visible(self.chat_visible, focus=False, persist=False)

    def _chat_file_candidates(self, query: str, scope: Path) -> list[Path]:
        if self.catalog_db is not None:
            try:
                return self.catalog_db.candidates(
                    query, scope, exclude_suffixes=CHAT_IMAGE_SUFFIXES,
                )
            except (OSError, sqlite3.Error):
                pass
        candidates = self.catalog_candidates(query, scope)
        return sorted(
            (path for path in candidates if inside(scope, path) and path.is_file()
             and path.suffix.casefold() not in CHAT_IMAGE_SUFFIXES
             and (not query or fuzzy_score(query, str(path.relative_to(self.root))) is not None)),
            key=lambda path: str(path).casefold(),
        )

    def _chat_image_candidates(self, query: str, scope: Path) -> list[Path]:
        if self.catalog_db is not None:
            try:
                return self.catalog_db.candidates(
                    query, scope, suffixes=CHAT_IMAGE_SUFFIXES,
                )
            except (OSError, sqlite3.Error):
                pass
        return sorted(
            (path for path in self.catalog_candidates(query, scope)
             if inside(scope, path) and path.is_file()
             and path.suffix.casefold() in CHAT_IMAGE_SUFFIXES
             and (not query or fuzzy_score(query, str(path.relative_to(self.root))) is not None)),
            key=lambda path: str(path).casefold(),
        )

    def _chat_directory_candidates(self, query: str, scope: Path) -> list[Path]:
        candidates = self.catalog_directory_candidates(query, scope)
        return sorted(
            (path for path in candidates if inside(scope, path) and path.is_dir()),
            key=lambda path: str(path).casefold(),
        )

    def _chat_editor_text(self, path: Path) -> str | None:
        for tab in self.all_tabs():
            if tab.path == path and tab.editor is not None:
                return tab.editor.to_markdown() if tab.markdown else tab.editor.toPlainText()
        return None

    def _chat_file_written(self, name: str) -> None:
        path = Path(name)
        if not path.is_file() or not inside(self.root, path):
            return
        self.catalog.add(path)
        if self.catalog_db is not None:
            try:
                self.catalog_db.upsert_paths([path])
                self.catalog_count = self.catalog_db.count()
            except (OSError, sqlite3.Error) as exc:
                self.statusBar().showMessage(f"Quick Open cache update failed: {exc}", 8000)
        self._refresh_quick_pickers()
        self._schedule_refresh()

    def _chat_navigate(self, path: str) -> None:
        target = Path(path)
        if target.is_file() and inside(self.root, target):
            self.open_file(target, pinned=True)
        elif target.is_dir() and inside(self.root, target):
            self.rail.setCurrentIndex(0)
            index = self.model.index(str(target))
            if index.isValid():
                self.tree.setCurrentIndex(index)
                self.tree.scrollTo(index)

    def _toggle_chat_panel(self, visible: bool) -> None:
        self._set_chat_panel_visible(visible, focus=bool(visible), persist=True)

    def _set_chat_panel_visible(
        self, visible: bool, *, focus: bool, persist: bool
    ) -> None:
        if self.chat_panel is None or self.chat_container is None:
            return
        visible = bool(visible)
        widths = self.splitter.sizes()
        total = sum(widths) or max(1, self.splitter.width())
        if visible:
            self.chat_container.setMinimumWidth(28)
            self.chat_container.setMaximumWidth(16777215)
            self.chat_container.setCurrentWidget(self.chat_tabs)
            if len(widths) == 3 and (not self.chat_visible or widths[2] < 100):
                target = min(self._chat_last_width, max(220, total - 380))
                self.splitter.setSizes(
                    [widths[0], max(1, total - widths[0] - target), target]
                )
        else:
            if len(widths) == 3 and self.chat_visible and widths[2] >= 100:
                self._chat_last_width = widths[2]
            self.chat_container.setCurrentWidget(self.chat_minibar)
            self.chat_container.setFixedWidth(28)
            if len(widths) == 3:
                self.splitter.setSizes(
                    [widths[0], max(1, total - widths[0] - 28), 28]
                )
        self.chat_visible = visible
        self._update_chat_toggle_icons()
        action = getattr(self, "chat_visibility_action", None)
        if action is not None:
            action.blockSignals(True)
            action.setChecked(visible)
            action.blockSignals(False)
        self.state["chat_visible"] = visible
        self.state["chat_width"] = self._chat_last_width
        if visible and focus:
            self.chat_panel.focus_input()
        if persist:
            self._persist()

    def _update_chat_toggle_icons(self) -> None:
        if self.chat_panel is None:
            return
        collapse_icon = self.style().standardIcon(QStyle.SP_ArrowRight)
        expand_icon = self.style().standardIcon(QStyle.SP_ArrowLeft)
        self.chat_toggle_button.setIcon(collapse_icon)
        self.chat_toggle_button.setToolTip("Hide AI chat sidebar")
        self.chat_minibar_toggle.setIcon(expand_icon)
        self.chat_minibar_toggle.setToolTip("Show AI chat sidebar")

    def all_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _index_for(self, path):
        return next((i for i, tab in enumerate(self.all_tabs()) if tab.path == path), -1)

    def _tree_selected(self, current, previous):
        if self._suppress_tree_preview:
            return
        # A real selection change supersedes the queued initial-focus choice.
        self._startup_folder_focus_pending = False
        self._cancel_pending_tree_markdown()
        self._cancel_pending_preview_hydration()
        self._cancel_disposable_preview_jobs()
        self.markdown_preview_timer.stop()
        self.pending_markdown_preview = None
        if current.isValid() and not self.model.isDir(current):
            path = Path(self.model.filePath(current))
            if self.window_trace is not None:
                self.window_trace.arm(path)
            self.preview_selection_started.clear()
            self.preview_selection_started[path] = time.perf_counter()
            suffix = path.suffix.casefold()
            rich_suffixes = (
                DELIMITED_SUFFIXES | WORKBOOK_SUFFIXES | DIAGRAM_SUFFIXES
                | DOCUMENT_SUFFIXES | {".pdf"}
            )
            defer_open = (
                suffix in {".md", ".markdown"}
                or (suffix not in rich_suffixes and not _may_be_image(path))
            )
            if defer_open:
                self.pending_tree_markdown_path = path
                delay = (
                    self.tree_markdown_open_delay_ms
                    if suffix in {".md", ".markdown"}
                    else self.tree_source_open_delay_ms
                )
                self.tree_markdown_open_timer.start(delay)
            else:
                self.open_file(path, defer_enhancements=True)

    def _cancel_pending_tree_markdown(self):
        self.tree_markdown_open_timer.stop()
        self.pending_tree_markdown_path = None

    def _cancel_pending_preview_hydration(self):
        self.preview_hydration_timer.stop()
        self.pending_preview_hydration = None

    def _cancel_disposable_preview_jobs(self):
        for future in list(self.disposable_preview_futures):
            future.cancel()

    def _record_preview_metric(self, kind, event, duration_ms=None):
        with self._preview_metrics_lock:
            metrics = self.preview_metrics.setdefault(kind, {
                "cache_hits": 0,
                "cache_misses": 0,
                "completed": 0,
                "last_ms": 0.0,
                "max_ms": 0.0,
                "visible": 0,
                "last_visible_ms": 0.0,
                "max_visible_ms": 0.0,
            })
            if event in {"cache_hits", "cache_misses"}:
                metrics[event] += 1
            elif event == "completed" and duration_ms is not None:
                duration = max(0.0, float(duration_ms))
                metrics["completed"] += 1
                metrics["last_ms"] = duration
                metrics["max_ms"] = max(metrics["max_ms"], duration)
            elif event == "visible" and duration_ms is not None:
                duration = max(0.0, float(duration_ms))
                metrics["visible"] += 1
                metrics["last_visible_ms"] = duration
                metrics["max_visible_ms"] = max(
                    metrics["max_visible_ms"], duration
                )
        if self.preview_perf_enabled:
            detail = f" {duration_ms:.1f} ms" if duration_ms is not None else ""
            print(
                f"[Folder Navigator preview] {kind} {event}{detail}",
                file=sys.stderr,
                flush=True,
            )

    def _record_preview_visible(self, path, kind):
        started = self.preview_selection_started.pop(Path(path), None)
        if started is not None:
            self._record_preview_metric(
                kind, "visible", (time.perf_counter() - started) * 1000
            )

    def _submit_preview_job(self, job, *, kind="preview", disposable=False, cleanup=None):
        started = time.perf_counter()

        def measured_job():
            try:
                job()
            finally:
                self._record_preview_metric(
                    kind, "completed", (time.perf_counter() - started) * 1000
                )

        future = self.preview_executor.submit(measured_job)
        self.preview_futures.add(future)
        if disposable:
            self.disposable_preview_futures.add(future)

        def finished(completed):
            self.preview_futures.discard(completed)
            self.disposable_preview_futures.discard(completed)
            if completed.cancelled() and cleanup is not None:
                cleanup()

        future.add_done_callback(finished)
        return future

    def _open_pending_tree_markdown(self):
        path = self.pending_tree_markdown_path
        self.pending_tree_markdown_path = None
        if path is None:
            return
        index = self.tree.currentIndex()
        if (index.isValid() and not self.model.isDir(index)
                and Path(self.model.filePath(index)) == path):
            self.open_file(path, defer_enhancements=True)
            if path.suffix.casefold() in {".md", ".markdown"}:
                # The tree-selection timer has already established that this
                # is a stable hover. Render Markdown now; source highlighting
                # retains its own short debounce after the lightweight editor
                # appears.
                self._schedule_markdown_preview(self.active_tab(), immediate=True)

    def _open_tree_file_keep_focus(self, path, pinned):
        self._cancel_pending_tree_markdown()
        self.open_file(Path(path), pinned=pinned)
        self._schedule_markdown_preview(self.active_tab(), immediate=True)

    def _open_tree_file(self, path, pinned):
        """Open a tree selection and hand keyboard control to its editor."""
        self._cancel_pending_tree_markdown()
        self.open_file(Path(path), pinned=pinned)
        tab = self.active_tab()
        if tab and tab.editor:
            self._schedule_markdown_preview(tab, immediate=True)
        self._focus_tab_content(tab)

    def open_file(self, path: Path, pinned=False, line=None, defer_enhancements=False,
                  force_text=False, force_rich_markdown=False, replace_preview=True):
        if self.window_trace is not None:
            self.window_trace.arm(path)
        # Once a visible window is explicitly opening content, do not let a
        # late QFileSystemModel load steal focus back to the startup target.
        if self.isVisible():
            self._startup_folder_focus_pending = False
        if not path.is_file() or not inside(self.root, path):
            self.statusBar().showMessage(f"Unavailable or outside root: {path}", 12000)
            return
        index = self._index_for(path)
        if index >= 0:
            if pinned:
                self.keep_open(index)
            self.tabs.setCurrentIndex(index)
            if line:
                self._reveal_editor_line(self.tabs.widget(index), line)
            tab = self.tabs.widget(index)
            if getattr(tab, "preview_kind", None) and not tab.property("folderPreviewHydrated"):
                self._schedule_preview_hydration(
                    tab, immediate=pinned or not defer_enhancements
                )
            self._schedule_markdown_preview(self.tabs.widget(index))
            return
        preview = (
            next((i for i, tab in enumerate(self.all_tabs()) if not tab.pinned and not tab.dirty), -1)
            if replace_preview else -1
        )
        if preview >= 0:
            previous_preview = self.tabs.widget(preview)
            self.tabs.removeTab(preview)
            previous_preview.deleteLater()
        try:
            suffix = path.suffix.casefold()
            if not force_text and suffix in DELIMITED_SUFFIXES:
                tab = Tab(path, details="Table preview queued…", root=self.root)
                tab.preview_kind = "table"
            elif not force_text and suffix in WORKBOOK_SUFFIXES:
                tab = Tab(path, details="Workbook preview queued…", root=self.root)
                tab.preview_kind = "workbook"
            elif not force_text and suffix in DIAGRAM_SUFFIXES:
                tab = Tab(path, details="Diagram preview queued…", root=self.root)
                tab.preview_kind = "diagram"
            elif not force_text and suffix in DOCUMENT_SUFFIXES:
                tab = Tab(path, details="Document preview queued…", root=self.root)
                tab.preview_kind = "document"
            elif suffix == ".pdf":
                tab = Tab(path, details="PDF preview queued…", root=self.root)
                tab.preview_kind = "pdf"
            elif _may_be_image(path) and QImageReader.imageFormat(str(path)):
                tab = Tab(path, details="Image preview queued…", root=self.root)
                tab.preview_kind = "image"
                tab.requested_image_quality = (
                    "thumbnail" if defer_enhancements and not pinned else "full"
                )
            else:
                loaded = read_text(path)
                is_markdown = path.suffix.casefold() in (".md", ".markdown")
                fallback_reason = (
                    rich_markdown_fallback_reason(loaded.text, path.stat().st_size)
                    if is_markdown and not force_rich_markdown else None
                )
                tab = Tab(
                    path,
                    loaded,
                    markdown=is_markdown and fallback_reason is None,
                    root=self.root,
                    defer_enhancements=defer_enhancements or fallback_reason is not None,
                )
                if fallback_reason:
                    tab.setProperty("folderLargeMarkdownSource", True)
                    tab.show_notice(
                        "Large Markdown opened in lightweight mode "
                        f"({fallback_reason}) to keep Folder Navigator responsive."
                    )
                    rich_button = QPushButton("Enable Rich Markdown Anyway")
                    rich_button.setToolTip(
                        "The rich editor may pause on files of this size or shape."
                    )
                    rich_button.clicked.connect(
                        lambda checked=False, selected=path: self._enable_rich_markdown(selected)
                    )
                    tab.layout().insertWidget(1, rich_button)
                    tab.rich_markdown_button = rich_button
        except Exception as exc:
            info = path.stat()
            details = (f"{path.name}\nType: {mimetypes.guess_type(path.name)[0] or 'Unknown'}\n"
                       f"Size: {info.st_size:,} bytes\nModified: {datetime.fromtimestamp(info.st_mtime)}\n"
                       f"Path: {path}\n\n{exc}")
            tab = Tab(path, details=details, root=self.root)
            buttons = QHBoxLayout()
            for text, callback in (("Open in Default Application", lambda: self.system_open(path)),
                                   ("Reveal in File Manager", lambda: self.reveal(path))):
                button = QPushButton(text)
                button.clicked.connect(callback)
                buttons.addWidget(button)
            tab.layout().addLayout(buttons)
        tab.pinned = pinned
        if getattr(tab, "preview_kind", None):
            tab.setProperty("folderPreviewHydrated", False)
        index = self.tabs.addTab(tab, path.name)
        if path.suffix.casefold() in DIAGRAM_SUFFIXES and not force_text:
            tab.preview_signature = self._diagram_preview_signature(path)
        elif path.suffix.casefold() in DOCUMENT_SUFFIXES and not force_text:
            from .documents import document_signature
            tab.preview_signature = document_signature(path)
        elif getattr(tab, "preview_kind", None) == "image":
            tab.preview_signature = fingerprint(path)
        self._update_tab_tooltip(tab)
        self.tabs.setCurrentIndex(index)
        if not tab.editor:
            self._install_preview_context_menu(tab, getattr(tab, "viewer", tab))
        if tab.editor:
            if self.editor_zoom_steps > 0:
                tab.editor.zoomIn(self.editor_zoom_steps)
            elif self.editor_zoom_steps < 0:
                tab.editor.zoomOut(-self.editor_zoom_steps)
            # Folder Navigator owns a deliberately small editor context menu.
            # In particular, Markdown tabs must not expose StillPoint page,
            # link-graph, AI, or vault-navigation actions here.
            tab.editor.setContextMenuPolicy(Qt.CustomContextMenu)
            tab.editor.customContextMenuRequested.connect(
                lambda point, t=tab: self._show_editor_context_menu(t, point)
            )
            tab.editor.viNavigationEscapePressed.connect(
                lambda t=tab: self._focus_tree_from_editor(t)
            )
            tab.editor.installEventFilter(self)
            if isinstance(tab.editor, MarkdownEditor):
                tab.editor.setProperty("folderNavigatorMarkdown", True)
                tab.editor.highlighter.set_folder_navigator_table_style(True)
                tab.editor.headingPickerRequested.connect(
                    lambda _point, _prefer_above, t=tab: self._show_heading_picker(t)
                )
            tab.editor.document().modificationChanged.connect(lambda dirty, t=tab: self._modified(t, dirty))
            if tab.markdown:
                # MarkdownEditor may rewrite display symbols while handling a
                # keystroke. Those rewrites can change the document's modified
                # flag without another useful modificationChanged edge, so
                # compare serialized content after the keystroke settles.
                tab.editor.textChanged.connect(
                    lambda t=tab: QTimer.singleShot(
                        0, t, lambda current=t: self._refresh_markdown_dirty(current)
                    )
                )
            # Markdown's initial display formatting can toggle Qt's modified
            # flag after the file is loaded. Clear only formatting-only changes;
            # never clear the flag once the text differs from disk.
            for delay in (0, 100):
                QTimer.singleShot(
                    delay, tab,
                    lambda t=tab: self._clear_initial_formatting_dirty(t),
                )
            if line:
                self._reveal_editor_line(tab, line)
        self.recent.insert(0, path)
        self.recent = list(dict.fromkeys(self.recent))[:100]
        self._watch_files()
        self._update_welcome()
        if tab.editor is not None:
            self._record_preview_visible(
                path, "markdown" if tab.markdown else "source"
            )
        if getattr(tab, "preview_kind", None):
            self._schedule_preview_hydration(
                tab, immediate=pinned or not defer_enhancements
            )
        self._schedule_markdown_preview(tab if defer_enhancements else None)

    def _enable_rich_markdown(self, path):
        index = self._index_for(Path(path))
        if index < 0:
            return
        tab = self.tabs.widget(index)
        if tab.dirty:
            self.statusBar().showMessage(
                "Save or undo lightweight-mode edits before enabling rich Markdown",
                7000,
            )
            return
        pinned = tab.pinned
        self.tabs.removeTab(index)
        tab.deleteLater()
        self.open_file(
            Path(path),
            pinned=pinned,
            force_rich_markdown=True,
        )
        self._schedule_markdown_preview(self.active_tab(), immediate=True)

    def _schedule_preview_hydration(self, tab, *, immediate=False):
        """Hydrate one rich preview only after disposable navigation settles."""
        self._cancel_pending_preview_hydration()
        if not isinstance(tab, Tab) or self.tabs.indexOf(tab) < 0:
            return
        if not getattr(tab, "preview_kind", None) or tab.property("folderPreviewHydrated"):
            return
        self.pending_preview_hydration = tab
        if immediate:
            self._hydrate_pending_preview()
        else:
            self.preview_hydration_timer.start(self.preview_hydration_delay_ms)

    def _hydrate_pending_preview(self):
        tab = self.pending_preview_hydration
        self.pending_preview_hydration = None
        if not isinstance(tab, Tab) or self.tabs.indexOf(tab) < 0:
            return
        # Do not spend work on a disposable preview that the user has already
        # left. Pinned tabs are allowed to hydrate in the background.
        if tab is not self.active_tab() and not tab.pinned:
            return
        if tab.property("folderPreviewHydrated"):
            return
        tab.setProperty("folderPreviewHydrated", True)
        kind = getattr(tab, "preview_kind", None)
        messages = {
            "table": "Loading table preview…",
            "workbook": "Loading workbook preview…",
            "diagram": "Loading cached diagram or rendering once…",
            "document": "Preparing document preview…",
            "pdf": "Loading PDF preview…",
            "image": "Decoding image preview…",
        }
        placeholder = getattr(tab, "placeholder", None)
        if placeholder is not None:
            placeholder.setText(messages.get(kind, "Loading preview…"))
        disposable = not tab.pinned
        if kind == "table":
            self._load_delimited_preview(tab.path, disposable=disposable)
        elif kind == "workbook":
            self._load_workbook_preview(tab.path, disposable=disposable)
        elif kind == "diagram":
            self._load_diagram_preview(tab.path, disposable=disposable)
        elif kind == "document":
            self._load_document_preview(tab.path, disposable=disposable)
        elif kind == "image":
            self._load_image_preview(
                tab.path,
                quality=getattr(tab, "requested_image_quality", "full"),
                disposable=disposable,
            )
        elif kind == "pdf":
            try:
                view = pdf_view(tab.path)
                tab.viewer = view
                tab.layout().addWidget(view)
                if placeholder is not None:
                    placeholder.hide()
                self._install_preview_context_menu(tab, view)
                self._record_preview_visible(tab.path, "pdf")
            except (ImportError, ValueError, RuntimeError) as exc:
                tab.show_notice(f"PDF preview unavailable: {exc}")

    def _load_delimited_preview(self, path, *, disposable=False):
        def job():
            try:
                preview = read_delimited_preview(path)
                self.bridge.result.emit(("table", path, preview, True, None))
            except Exception as exc:
                self.bridge.result.emit(("table", path, None, True, str(exc)))
        self._submit_preview_job(job, kind="table", disposable=disposable)

    def _load_workbook_preview(self, path, sheet_name=None, *, disposable=False):
        def job():
            try:
                preview = read_workbook_preview(path, sheet_name)
                self.bridge.result.emit(("table", path, preview, False, None))
            except Exception as exc:
                self.bridge.result.emit(("table", path, None, False, str(exc)))
        self._submit_preview_job(job, kind="workbook", disposable=disposable)

    def _load_document_preview(self, path, *, disposable=False):
        from .documents import build_docx_preview, build_pptx_preview, document_signature

        path = Path(path)
        signature = document_signature(path)
        builder = build_pptx_preview if path.suffix.casefold() == ".pptx" else build_docx_preview

        def job():
            try:
                preview = builder(path)
                self.bridge.result.emit(("document", path, signature, preview, None))
            except Exception as exc:
                self.bridge.result.emit(("document", path, signature, None, str(exc)))

        self._submit_preview_job(job, kind="document", disposable=disposable)

    @staticmethod
    def _image_cache_key(path, signature, quality):
        return str(Path(path).resolve(strict=False)), signature, quality

    def _cached_image_preview(self, path, signature, quality):
        key = self._image_cache_key(path, signature, quality)
        cached = self.image_preview_cache.get(key)
        if cached is None:
            self._record_preview_metric("image", "cache_misses")
            return None
        self._record_preview_metric("image", "cache_hits")
        self.image_preview_cache.move_to_end(key)
        image, source_dimensions, _size = cached
        return image.copy(), source_dimensions

    def _cache_image_preview(self, path, signature, quality, image, source_dimensions):
        try:
            size = int(image.sizeInBytes())
        except (AttributeError, TypeError, ValueError):
            return
        if size > IMAGE_PREVIEW_CACHE_BYTES:
            return
        key = self._image_cache_key(path, signature, quality)
        for existing_key in list(self.image_preview_cache):
            if (existing_key[0] != key[0]
                    or (existing_key[1] == signature and existing_key[2] != quality)):
                continue
            _image, _dimensions, existing_size = self.image_preview_cache.pop(existing_key)
            self.image_preview_cache_bytes -= existing_size
        self.image_preview_cache[key] = (image.copy(), source_dimensions, size)
        self.image_preview_cache_bytes += size
        while (len(self.image_preview_cache) > IMAGE_PREVIEW_CACHE_ENTRIES
               or self.image_preview_cache_bytes > IMAGE_PREVIEW_CACHE_BYTES):
            _old_key, (_image, _dimensions, old_size) = self.image_preview_cache.popitem(last=False)
            self.image_preview_cache_bytes -= old_size

    def _load_image_preview(self, path, *, quality="full", disposable=False):
        path = Path(path)
        quality = "thumbnail" if quality == "thumbnail" else "full"
        signature = fingerprint(path)
        cached = self._cached_image_preview(path, signature, quality)
        if cached is not None:
            image, source_dimensions = cached
            QTimer.singleShot(
                0,
                lambda: self._show_image_preview(
                    path, signature, quality, image, source_dimensions, None,
                    cache_result=False,
                ),
            )
            return
        cache_key = self._image_cache_key(path, signature, quality)
        if cache_key in self.image_preview_inflight:
            return
        self.image_preview_inflight.add(cache_key)

        def decode():
            try:
                reader = QImageReader(str(path))
                reader.setAutoTransform(True)
                size = reader.size()
                if size.width() * size.height() > MAX_IMAGE_PIXELS:
                    raise ValueError(
                        f"Image exceeds the configured {MAX_IMAGE_PIXELS:,} pixel preview limit"
                    )
                source_dimensions = (size.width(), size.height())
                if (quality == "thumbnail"
                        and size.width() * size.height() > IMAGE_FLYOVER_MAX_PIXELS):
                    factor = (
                        IMAGE_FLYOVER_MAX_PIXELS / (size.width() * size.height())
                    ) ** 0.5
                    reader.setScaledSize(QSize(
                        max(1, int(size.width() * factor)),
                        max(1, int(size.height() * factor)),
                    ))
                image = reader.read()
                if image.isNull():
                    raise ValueError(reader.errorString() or "Could not decode image")
                self.bridge.result.emit((
                    "image", path, signature, quality, image, source_dimensions, None,
                ))
            except Exception as exc:
                self.bridge.result.emit((
                    "image", path, signature, quality, None, None, str(exc),
                ))

        try:
            self._submit_preview_job(
                decode,
                kind="image",
                disposable=disposable,
                cleanup=lambda: self.image_preview_inflight.discard(cache_key),
            )
        except RuntimeError:
            self.image_preview_inflight.discard(cache_key)
            raise

    def _show_image_preview(self, path, signature, quality, pixels,
                            source_dimensions, error, *, cache_result=True):
        cache_key = self._image_cache_key(path, signature, quality)
        self.image_preview_inflight.discard(cache_key)
        if signature != fingerprint(path):
            return
        if cache_result and pixels is not None and not error:
            self._cache_image_preview(
                path, signature, quality, pixels, source_dimensions
            )
        index = self._index_for(path)
        if index < 0:
            return
        tab = self.tabs.widget(index)
        tab.preview_signature = signature
        if error or pixels is None or pixels.isNull():
            tab.show_notice(f"Image preview failed: {error or 'unknown error'}")
            return
        if (quality == "thumbnail"
                and getattr(tab, "requested_image_quality", "thumbnail") == "full"):
            return
        placeholder = getattr(tab, "placeholder", None)
        if placeholder is not None:
            placeholder.hide()
        current = getattr(tab, "viewer", None)
        if current is not None:
            tab.layout().removeWidget(current)
            current.deleteLater()
        tab.preview_image_quality = quality
        tab.viewer = ImageView(
            path,
            pixels,
            source_dimensions=source_dimensions,
            preview_limited=quality == "thumbnail",
            full_resolution_callback=lambda checked=False, current=tab:
                self._request_full_image_preview(current),
        )
        tab.layout().addWidget(tab.viewer)
        self._install_preview_context_menu(tab, tab.viewer)
        tab.notice.hide()
        self._record_preview_visible(path, "image")

    def _request_full_image_preview(self, tab):
        if (not isinstance(tab, Tab) or self.tabs.indexOf(tab) < 0
                or getattr(tab, "preview_kind", None) != "image"):
            return
        tab.requested_image_quality = "full"
        tab.setProperty("folderPreviewHydrated", True)
        self._cancel_disposable_preview_jobs()
        if getattr(tab, "preview_image_quality", None) == "full":
            return
        tab.show_notice("Loading full-resolution image…")
        self._load_image_preview(tab.path, quality="full", disposable=False)

    def _diagram_renderer(self, suffix):
        with self._diagram_renderer_lock:
            renderer = self._diagram_renderers.get(suffix)
            if renderer is not None:
                return renderer
            if suffix == ".puml":
                from sp.app.plantuml_renderer import PlantUMLRenderer
                renderer = PlantUMLRenderer()
            else:
                from sp.app.mermaid_renderer import MermaidRenderer
                renderer = MermaidRenderer()
            self._diagram_renderers[suffix] = renderer
            return renderer

    @staticmethod
    def _diagram_preview_signature(path):
        path = Path(path)
        sidecar = path.with_name(f"{path.name}.png")
        return fingerprint(path), fingerprint(sidecar) if path.suffix.casefold() == ".excalidraw" else None

    @staticmethod
    def _diagram_cache_key(path, signature):
        return str(Path(path).resolve(strict=False)), signature

    @staticmethod
    def _diagram_payload_size(svg, pixels, error):
        size = len((svg or "").encode("utf-8")) + len((error or "").encode("utf-8"))
        if pixels is not None:
            try:
                size += int(pixels.sizeInBytes())
            except (AttributeError, TypeError, ValueError):
                pass
        return size

    def _cached_diagram_preview(self, path, signature):
        key = self._diagram_cache_key(path, signature)
        cached = self.diagram_preview_cache.get(key)
        if cached is None:
            self._record_preview_metric("diagram", "cache_misses")
            return None
        self._record_preview_metric("diagram", "cache_hits")
        self.diagram_preview_cache.move_to_end(key)
        svg, pixels, error, _size = cached
        return svg, pixels.copy() if pixels is not None else None, error

    def _cache_diagram_preview(self, path, signature, svg, pixels, error):
        key = self._diagram_cache_key(path, signature)
        size = self._diagram_payload_size(svg, pixels, error)
        # One unusually large sidecar should not evict every useful preview.
        if size > DIAGRAM_PREVIEW_CACHE_BYTES:
            return
        for existing_key in list(self.diagram_preview_cache):
            if existing_key[0] != key[0]:
                continue
            _svg, _pixels, _error, existing_size = self.diagram_preview_cache.pop(existing_key)
            self.diagram_preview_cache_bytes -= existing_size
        stored_pixels = pixels.copy() if pixels is not None else None
        self.diagram_preview_cache[key] = (svg, stored_pixels, error, size)
        self.diagram_preview_cache_bytes += size
        while (len(self.diagram_preview_cache) > DIAGRAM_PREVIEW_CACHE_ENTRIES
               or self.diagram_preview_cache_bytes > DIAGRAM_PREVIEW_CACHE_BYTES):
            _old_key, (_svg, _pixels, _error, old_size) = self.diagram_preview_cache.popitem(last=False)
            self.diagram_preview_cache_bytes -= old_size

    def _load_diagram_preview(self, path, *, disposable=False):
        path = Path(path)
        signature = self._diagram_preview_signature(path)
        cached = self._cached_diagram_preview(path, signature)
        if cached is not None:
            svg, pixels, error = cached
            QTimer.singleShot(
                0,
                lambda: self._show_diagram_preview(
                    path, signature, svg, pixels, error, cache_result=False
                ),
            )
            return
        cache_key = self._diagram_cache_key(path, signature)
        if cache_key in self.diagram_preview_inflight:
            return
        self.diagram_preview_inflight.add(cache_key)

        def job():
            try:
                suffix = path.suffix.casefold()
                if suffix == ".excalidraw":
                    sidecar = path.with_name(f"{path.name}.png")
                    if not sidecar.is_file():
                        raise ValueError(
                            "No rendered preview exists yet. Open the Excalidraw editor and save the drawing."
                        )
                    reader = QImageReader(str(sidecar))
                    reader.setAutoTransform(True)
                    image = reader.read()
                    if image.isNull():
                        raise ValueError(reader.errorString() or "Could not decode preview image")
                    self.bridge.result.emit(("diagram", path, signature, None, image, None))
                    return
                source = read_text(path).text
                result = self._diagram_renderer(suffix).render_svg(source)
                if not result.success or not result.svg_content:
                    raise ValueError(result.error_message or result.stderr or "Diagram render failed")
                self.bridge.result.emit(("diagram", path, signature, result.svg_content, None, None))
            except Exception as exc:
                self.bridge.result.emit(("diagram", path, signature, None, None, str(exc)))

        try:
            self._submit_preview_job(
                job,
                kind="diagram",
                disposable=disposable,
                cleanup=lambda: self.diagram_preview_inflight.discard(cache_key),
            )
        except RuntimeError:
            self.diagram_preview_inflight.discard(cache_key)
            raise

    def _show_diagram_preview(self, path, signature, svg, pixels, error, *, cache_result=True):
        cache_key = self._diagram_cache_key(path, signature)
        self.diagram_preview_inflight.discard(cache_key)
        if signature != self._diagram_preview_signature(path):
            return
        # Convert SVG once before caching. Keeping ready-to-display pixels is
        # what makes repeat flyovers avoid both the external renderer and a
        # second Qt SVG rasterization on the UI path.
        if not error and pixels is None:
            pixmap = QPixmap()
            if pixmap.loadFromData(svg.encode("utf-8"), "SVG"):
                pixels = pixmap.toImage()
            else:
                error = "Rendered SVG could not be displayed"
        # Cache completed previews even if their disposable tab has already
        # been replaced. Transient renderer/tool failures intentionally retry.
        if cache_result and not error:
            self._cache_diagram_preview(path, signature, svg, pixels, error)
        index = self._index_for(path)
        if index < 0:
            return
        tab = self.tabs.widget(index)
        tab.preview_signature = signature
        current = getattr(tab, "viewer", None)
        if current is not None:
            tab.layout().removeWidget(current)
            current.deleteLater()
        if error:
            tab.show_notice(f"Diagram preview unavailable: {error}")
            fallback = QWidget()
            fallback_layout = QVBoxLayout(fallback)
            fallback_layout.addWidget(QLabel(str(error)))
            open_button = QPushButton(self._diagram_editor_label(path))
            open_button.clicked.connect(
                lambda checked=False, selected=path: self._open_specialized_editor(selected)
            )
            fallback_layout.addWidget(open_button)
            fallback_layout.addStretch()
            tab.viewer = fallback
            tab.layout().addWidget(fallback)
            self._install_preview_context_menu(tab, fallback)
            return
        placeholder = tab.layout().itemAt(1)
        if placeholder and placeholder.widget():
            placeholder.widget().hide()
        tab.viewer = ImageView(
            path,
            pixels,
            open_label=self._diagram_editor_label(path),
            open_callback=lambda checked=False, selected=path: self._open_specialized_editor(selected),
            canvas_color="#ffffff" if path.suffix.casefold() in {".puml", ".mmd"} else None,
            svg_text=svg if path.suffix.casefold() in {".puml", ".mmd"} else None,
        )
        tab.layout().addWidget(tab.viewer)
        self._install_preview_context_menu(tab, tab.viewer)
        tab.notice.hide()
        self._record_preview_visible(path, "diagram")
        if tab is self.active_tab() and self.focusWidget() is self.tabs:
            tab.viewer.setFocus(Qt.OtherFocusReason)

    @staticmethod
    def _diagram_editor_label(path):
        return {
            ".puml": "Open in PlantUML Editor",
            ".mmd": "Open in Mermaid Editor",
            ".excalidraw": "Open in Excalidraw Editor",
        }.get(Path(path).suffix.casefold(), "Open Diagram Editor")

    def _show_table_preview(self, path, preview, allow_source, error):
        index = self._index_for(path)
        if index < 0:
            return
        tab = self.tabs.widget(index)
        current_viewer = getattr(tab, "viewer", None)
        if error:
            tab.show_notice(f"Table preview failed: {error}")
            return
        if isinstance(current_viewer, SpreadsheetView):
            requested = getattr(current_viewer, "requested_sheet", None)
            if requested and preview.sheet_name != requested:
                return
            current_viewer.set_preview(preview)
            current_viewer.requested_sheet = preview.sheet_name
            return
        tab.layout().itemAt(1).widget().hide()
        viewer = SpreadsheetView(
            preview, allow_source=allow_source, zoom_steps=self.editor_zoom_steps,
            vi_enabled=self.tree.vi_enabled,
        )
        viewer.requested_sheet = preview.sheet_name
        viewer.statusRequested.connect(self.statusBar().showMessage)
        if viewer.value_filter_disabled_reason:
            self.statusBar().showMessage(viewer.value_filter_disabled_reason, 8000)
        viewer.sourceRequested.connect(lambda selected=path: self._open_table_as_text(selected))
        viewer.sheetRequested.connect(
            lambda sheet, selected=path, view=viewer: self._request_workbook_sheet(
                selected, sheet, view
            )
        )
        tab.viewer = viewer
        tab.layout().addWidget(viewer)
        self._install_preview_context_menu(tab, viewer)
        self._record_preview_visible(path, "table")
        if tab is self.active_tab():
            self.table_preview_action.setEnabled(False)
            self.raw_text_action.setEnabled(allow_source)
        if tab is self.active_tab() and self.focusWidget() is self.tabs:
            viewer.setFocus(Qt.OtherFocusReason)

    def _show_document_preview(self, path, signature, preview, error):
        from .documents import document_signature

        index = self._index_for(path)
        if index < 0:
            return
        try:
            if signature != document_signature(path):
                return
        except OSError:
            return
        tab = self.tabs.widget(index)
        tab.preview_signature = signature
        current = getattr(tab, "viewer", None)
        if current is not None:
            tab.layout().removeWidget(current)
            current.deleteLater()
        if error or preview is None:
            tab.show_notice(f"Document preview failed: {error or 'unknown error'}")
            return

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        header = QHBoxLayout()
        if preview.backend == "libreoffice":
            description = QLabel("Print-layout preview rendered by LibreOffice")
        else:
            description = QLabel(preview.notice or "Simplified document preview")
            description.setWordWrap(True)
        header.addWidget(description)
        header.addStretch()
        open_button = QPushButton("Open in Default Application")
        open_button.clicked.connect(
            lambda checked=False, selected=path: self.system_open(selected)
        )
        header.addWidget(open_button)
        layout.addLayout(header)

        try:
            if preview.pdf_path is not None:
                layout_content = pdf_view(preview.pdf_path)
                content = layout_content
                if preview.html:
                    selectable_content = QTextBrowser()
                    selectable_content.setAccessibleName("Selectable document text")
                    selectable_content.setTextInteractionFlags(
                        Qt.TextSelectableByMouse
                        | Qt.TextSelectableByKeyboard
                        | Qt.LinksAccessibleByMouse
                    )
                    selectable_content.setOpenExternalLinks(True)
                    selectable_content.setHtml(preview.html)
                    views = QStackedWidget()
                    views.addWidget(layout_content)
                    views.addWidget(selectable_content)
                    layout_button = QPushButton("Layout")
                    text_button = QPushButton("Selectable Text")
                    layout_button.setCheckable(True)
                    text_button.setCheckable(True)

                    def select_document_view(index):
                        views.setCurrentIndex(index)
                        layout_button.setChecked(index == 0)
                        text_button.setChecked(index == 1)
                        views.currentWidget().setFocus(Qt.OtherFocusReason)

                    layout_button.clicked.connect(lambda: select_document_view(0))
                    text_button.clicked.connect(lambda: select_document_view(1))
                    layout_button.setChecked(True)
                    header.insertWidget(0, layout_button)
                    header.insertWidget(1, text_button)
                    content = views
                    container.selectable_content = selectable_content
                    container.document_views = views
                    container.zoom_in = lambda: (
                        layout_content.zoom_in()
                        if views.currentIndex() == 0
                        else selectable_content.zoomIn()
                    )
                    container.zoom_out = lambda: (
                        layout_content.zoom_out()
                        if views.currentIndex() == 0
                        else selectable_content.zoomOut()
                    )
                else:
                    container.zoom_in = layout_content.zoom_in
                    container.zoom_out = layout_content.zoom_out
            else:
                content = QTextBrowser()
                content.setAccessibleName("Selectable document text")
                content.setTextInteractionFlags(
                    Qt.TextSelectableByMouse
                    | Qt.TextSelectableByKeyboard
                    | Qt.LinksAccessibleByMouse
                )
                content.setOpenExternalLinks(True)
                content.setHtml(preview.html)
                container.zoom_in = content.zoomIn
                container.zoom_out = content.zoomOut
        except (ImportError, ValueError, RuntimeError) as exc:
            tab.show_notice(f"Document preview failed: {exc}")
            container.deleteLater()
            return
        layout.addWidget(content)
        placeholder = tab.layout().itemAt(1)
        if placeholder and placeholder.widget():
            placeholder.widget().hide()
        tab.viewer = container
        tab.layout().addWidget(container)
        self._install_preview_context_menu(tab, container)
        tab.notice.hide()
        self._record_preview_visible(path, "document")
        if tab is self.active_tab() and self.focusWidget() is self.tabs:
            content.setFocus(Qt.OtherFocusReason)

    def _request_workbook_sheet(self, path, sheet_name, viewer):
        viewer.requested_sheet = sheet_name
        self._load_workbook_preview(path, sheet_name)

    def _open_table_as_text(self, path):
        index = self._index_for(path)
        if index < 0:
            return
        pinned = self.tabs.widget(index).pinned
        old = self.tabs.widget(index)
        self.tabs.removeTab(index)
        old.deleteLater()
        self.open_file(path, pinned=pinned, force_text=True)

    def _reopen_active_table(self):
        tab = self.active_tab()
        if tab is None or tab.path.suffix.casefold() not in DELIMITED_SUFFIXES:
            return
        if isinstance(getattr(tab, "viewer", None), SpreadsheetView):
            return
        pinned = tab.pinned
        index = self.tabs.indexOf(tab)
        self.tabs.removeTab(index)
        tab.deleteLater()
        self.open_file(tab.path, pinned=pinned)

    def _reopen_active_as_text(self):
        tab = self.active_tab()
        if tab is not None and tab.path.suffix.casefold() in DELIMITED_SUFFIXES:
            self._open_table_as_text(tab.path)

    def _schedule_markdown_preview(self, tab, *, immediate=False):
        self.markdown_preview_timer.stop()
        self.pending_markdown_preview = None
        if not tab or not tab.editor:
            return
        if tab.property("folderLargeMarkdownSource"):
            return
        if (tab.markdown and tab.property("folderMarkdownRendered")) or (
                isinstance(tab.editor, SourceEditor)
                and tab.property("folderSourceHighlighted")):
            return
        self.pending_markdown_preview = tab
        if immediate:
            self._refresh_pending_markdown_preview()
        else:
            self.markdown_preview_timer.start(self.markdown_preview_delay_ms)

    def _refresh_pending_markdown_preview(self):
        tab = self.pending_markdown_preview
        self.pending_markdown_preview = None
        if (not tab or self.tabs.indexOf(tab) < 0 or tab is not self.active_tab()
                or not tab.editor or not tab.loaded):
            return
        if tab.dirty:
            # Markdown's initial display formatting can briefly toggle Qt's
            # modified flag. Reconcile that formatting-only state before
            # abandoning the deferred render; real buffer edits still differ
            # from clean_text and remain protected from replacement.
            self._clear_initial_formatting_dirty(tab)
            if tab.dirty:
                return
        if isinstance(tab.editor, SourceEditor):
            tab.editor.enable_syntax_highlighting()
            tab.setProperty("folderSourceHighlighted", True)
            return
        if not tab.markdown or tab.property("folderMarkdownRendered"):
            return
        tab.setProperty("folderMarkdownRendering", True)
        try:
            tab.editor.set_markdown(tab.loaded.text)
            # Rendering intentionally canonicalizes a few Markdown forms
            # (notably +CamelCase links and relative image paths). Treat that
            # canonical representation as clean; it was not a user edit.
            tab.clean_text = tab.text_for_save()
            tab.editor.document().setModified(False)
            tab.setProperty("folderMarkdownRendered", True)
        finally:
            tab.setProperty("folderMarkdownRendering", False)

    @staticmethod
    def _clear_initial_formatting_dirty(tab):
        try:
            if (tab.editor and tab.loaded
                    and tab.text_for_save() == tab.clean_text
                    and tab.editor.document().isModified()):
                tab.editor.document().setModified(False)
        except RuntimeError:
            # A fast preview replacement may delete the tab before this queued
            # formatting-only dirty-state cleanup runs.
            pass

    def _focus_tree_from_editor(self, tab):
        """Return vi navigation to the file that owns the focused editor."""
        if tab is self.active_tab():
            current = self.tree.currentIndex()
            if (current.isValid()
                    and Path(self.model.filePath(current)) == tab.path):
                self.rail.setCurrentIndex(0)
                self.tree.setFocus(Qt.OtherFocusReason)
            else:
                self.reveal_tree(tab.path)

    def _reveal_editor_line(self, tab, line):
        """Select, flash, and scroll a search target into view."""
        if not tab or not tab.editor or not line:
            return
        block = tab.editor.document().findBlockByNumber(max(0, int(line) - 1))
        if not block.isValid():
            return
        cursor = QTextCursor(block)
        tab.editor.setTextCursor(cursor)
        tab.editor.ensureCursorVisible()
        marker = QTextEdit.ExtraSelection()
        marker.cursor = cursor
        marker.format.setBackground(theme_color("page_editor_window.highlight.selection_bg", "#ffd54f"))
        marker.format.setProperty(QTextFormat.FullWidthSelection, True)
        marker.format.setProperty(QTextFormat.UserProperty, 9911)
        current = [selection for selection in tab.editor.extraSelections()
                   if selection.format.property(QTextFormat.UserProperty) != 9911]
        tab.editor.setExtraSelections(current + [marker])

        def clear_marker():
            try:
                keep = [selection for selection in tab.editor.extraSelections()
                        if selection.format.property(QTextFormat.UserProperty) != 9911]
                tab.editor.setExtraSelections(keep)
            except RuntimeError:
                pass

        QTimer.singleShot(1400, clear_marker)
        tab.editor.setFocus(Qt.OtherFocusReason)

    def _show_active_heading_picker(self):
        tab = self.active_tab()
        if not tab or not tab.markdown or not tab.editor:
            self.statusBar().showMessage("Heading navigation is available for Markdown files", 3000)
            return
        self._show_heading_picker(tab)

    def _position_heading_picker(self, tab, picker):
        """Center the picker in the editor viewport and keep it on-screen."""
        viewport = tab.editor.viewport()
        viewport_top_left = viewport.mapToGlobal(QPoint(0, 0))
        viewport_center = viewport_top_left + viewport.rect().center()
        screen = QApplication.screenAt(viewport_center) or self.screen()
        available = screen.availableGeometry() if screen else self.frameGeometry()
        margin = 12
        width = min(
            520,
            max(280, viewport.width() - 48),
            max(1, available.width() - (margin * 2)),
        )
        height = min(
            360,
            max(200, viewport.height() - 48),
            max(1, available.height() - (margin * 2)),
        )
        picker.resize(width, height)
        x = viewport_center.x() - (width // 2)
        y = viewport_center.y() - (height // 2)
        min_x = available.left() + margin
        min_y = available.top() + margin
        max_x = max(min_x, available.right() - margin - width + 1)
        max_y = max(min_y, available.bottom() - margin - height + 1)
        picker.move(max(min_x, min(x, max_x)), max(min_y, min(y, max_y)))

    def _show_heading_picker(self, tab):
        if not tab or not tab.markdown or not tab.editor:
            return
        source = tab.text_for_save()
        headings = []
        source_lines = source.splitlines()
        for index, text in enumerate(source_lines):
            match = re.match(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$", text)
            if match:
                headings.append((len(match.group(1)), match.group(2), index + 1))
                continue
            if index and re.match(r"^\s*(?:=+|-+)\s*$", text) and source_lines[index - 1].strip():
                headings.append((1 if "=" in text else 2, source_lines[index - 1].strip(), index))
        if not headings:
            self.statusBar().showMessage("This Markdown file has no headings", 3000)
            return
        picker = HeadingPicker(headings, self)
        self._position_heading_picker(tab, picker)
        if picker.exec() == QDialog.Accepted and picker.selected_line:
            self._reveal_editor_line(tab, picker.selected_line)

    def _format_active_markdown_table(self):
        tab = self.active_tab()
        if not tab or not tab.markdown or not tab.editor:
            self.statusBar().showMessage(
                "Format Table is available in a Markdown editor", 4000
            )
            return
        source = tab.text_for_save()
        cursor_line = tab.editor.textCursor().blockNumber()
        formatted, changed = format_markdown_table(source, cursor_line)
        if not changed:
            self.statusBar().showMessage(
                "No unformatted Markdown table at the cursor", 4000
            )
            return
        old_lines = source.splitlines()
        new_lines = formatted.splitlines()
        changed_lines = [
            index for index, (old, new) in enumerate(zip(old_lines, new_lines))
            if old != new
        ]
        if not changed_lines:
            return
        first, last = min(changed_lines), max(changed_lines)
        replacement = "\n".join(new_lines[first:last + 1])
        replacement = tab.editor._to_display(replacement)
        document = tab.editor.document()
        first_block = document.findBlockByNumber(first)
        last_block = document.findBlockByNumber(last)
        if not first_block.isValid() or not last_block.isValid():
            self.statusBar().showMessage("Could not locate the table in the editor", 4000)
            return
        original_cursor = tab.editor.textCursor()
        original_line = original_cursor.blockNumber()
        original_column = original_cursor.position() - original_cursor.block().position()
        edit = QTextCursor(document)
        edit.setPosition(first_block.position())
        edit.setPosition(
            last_block.position() + max(0, last_block.length() - 1),
            QTextCursor.KeepAnchor,
        )
        tab.editor._display_guard = True
        try:
            edit.beginEditBlock()
            edit.insertText(replacement)
            edit.endEditBlock()
        finally:
            tab.editor._display_guard = False
        restored_block = document.findBlockByNumber(original_line)
        if restored_block.isValid():
            restored = QTextCursor(document)
            restored.setPosition(
                restored_block.position()
                + min(original_column, max(0, restored_block.length() - 1))
            )
            tab.editor.setTextCursor(restored)
        self._refresh_markdown_dirty(tab)
        self.statusBar().showMessage("Markdown table aligned", 3000)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if (event.type() == QEvent.KeyPress
                and event.key() in (Qt.Key_F, Qt.Key_V)
                and event.modifiers() == Qt.NoModifier):
            tab = self.active_tab()
            editor = tab.editor if tab is not None else None
            if (obj is editor
                    and getattr(editor, "_vi_feature_enabled", False)
                    and not getattr(editor, "_vi_insert_mode", True)
                    and getattr(editor, "_vi_mode_active", True)):
                if event.key() == Qt.Key_F:
                    self.bookmark_picker()
                else:
                    self.folder_picker()
                return True
        if (event.type() == QEvent.KeyPress
                and event.key() == Qt.Key_Escape
                and event.modifiers() == Qt.NoModifier
                and self.rail.isHidden()
                and isinstance(obj, QWidget)
                and obj.window() is self):
            tab = self.active_tab()
            find_bar = getattr(tab, "find_bar", None) if tab is not None else None
            if (find_bar is not None and not find_bar.isHidden()
                    and (obj is find_bar or find_bar.isAncestorOf(obj))):
                return super().eventFilter(obj, event)
            if (self.command_palette.isVisible()
                    and (obj is self.command_palette
                         or self.command_palette.isAncestorOf(obj))):
                return super().eventFilter(obj, event)
            editor = tab.editor if tab is not None else None
            if (editor is not None and getattr(editor, "_vi_feature_enabled", False)
                    and getattr(editor, "_vi_insert_mode", False)):
                return super().eventFilter(obj, event)
            if tab is not None:
                self.reveal_tree(tab.path)
            else:
                self._focus_tree_when_tabs_empty()
            return True
        release_keys = {history_cycle_modifier_release_key()}
        if sys.platform == "darwin":
            # Accept both Qt's macOS Control aliases. This mirrors the two
            # QShortcut spellings installed for Control+Tab above.
            release_keys.update((Qt.Key_Control, Qt.Key_Meta))
        if (event.type() == QEvent.KeyRelease
                and event.key() in release_keys
                and self.tab_switcher_paths):
            self._activate_tab_switcher_selection()
            return True
        if (event.type() == QEvent.KeyPress
                and obj.property("folderNavigatorMarkdown")
                and event.key() == Qt.Key_Escape
                and event.modifiers() == Qt.NoModifier
                and getattr(obj, "_vi_feature_enabled", False)
                and getattr(obj, "_vi_mode_active", False)
                and not getattr(obj, "_vi_insert_mode", False)):
            # Markdown's normal-mode Escape also clears selections and empty
            # line transforms. In Folder Navigator, an already-normal editor
            # has no deeper mode to leave, so the first Escape is the pane
            # handoff regardless of those transient cursor states.
            tab = next((candidate for candidate in self.all_tabs()
                        if candidate.editor is obj), None)
            if tab is not None:
                self._focus_tree_from_editor(tab)
                return True
        if (event.type() == QEvent.KeyPress
                and obj.property("folderNavigatorMarkdown")
                and event.key() == Qt.Key_T
                and event.modifiers() == Qt.NoModifier
                and getattr(obj, "_vi_feature_enabled", False)
                and getattr(obj, "_vi_mode_active", False)
                and not getattr(obj, "_vi_insert_mode", False)):
            tab = next((candidate for candidate in self.all_tabs()
                        if candidate.editor is obj), None)
            if tab:
                self._show_heading_picker(tab)
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):  # type: ignore[override]
        """Use an otherwise-unhandled Escape to return to file navigation.

        Handling this on the window, rather than in the application event
        filter, lets editors, dialogs, and inline controls consume Escape for
        their own cancellation and vi-mode behavior first.
        """
        if (event.key() == Qt.Key_Escape
                and event.modifiers() == Qt.NoModifier):
            tab = self.active_tab()
            if tab is not None:
                self.reveal_tree(tab.path)
            else:
                self._focus_tree_when_tabs_empty()
            event.accept()
            return
        super().keyPressEvent(event)

    def _modified(self, tab, dirty):
        if tab.property("folderMarkdownRendering"):
            return
        if dirty and tab.markdown and tab.loaded:
            try:
                if tab.text_for_save() == tab.clean_text:
                    tab.editor.document().setModified(False)
                    return
            except RuntimeError:
                return
        self._apply_tab_dirty_state(tab, dirty)

    def _refresh_markdown_dirty(self, tab):
        """Reconcile Markdown dirty state after display-text transformations."""
        try:
            if (tab.property("folderMarkdownRendering") or not tab.editor
                    or not tab.loaded or self.tabs.indexOf(tab) < 0):
                return
            dirty = tab.text_for_save() != tab.clean_text
            if tab.editor.document().isModified() != dirty:
                tab.editor.document().setModified(dirty)
            self._apply_tab_dirty_state(tab, dirty)
        except RuntimeError:
            # A queued reconciliation can outlive a rapidly replaced preview.
            return

    def _apply_tab_dirty_state(self, tab, dirty):
        if dirty:
            tab.pinned = True
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, ("● " if dirty else "") + tab.path.name)
            color = (
                QColor(str(theme_value("main_window.badge.dirty_bg", "#e57373")))
                if dirty else self.tabs.tabBar().palette().color(QPalette.WindowText)
            )
            self.tabs.tabBar().setTabTextColor(index, color)
            tab.setAccessibleName(f"{tab.path.name}{', unsaved changes' if dirty else ''}")
            self._update_tab_tooltip(tab)

    def _update_tab_tooltip(self, tab):
        index = self.tabs.indexOf(tab)
        if index < 0:
            return
        state = "Pinned" if tab.pinned else "Preview · Enter or double-click to keep open"
        if tab.dirty:
            state = "Unsaved changes · pinned"
        self.tabs.setTabToolTip(index, f"{tab.path}\n{state}")

    def keep_open(self, index):
        if index >= 0 and isinstance(self.tabs.widget(index), Tab):
            tab = self.tabs.widget(index)
            tab.pinned = True
            self._update_tab_tooltip(tab)
            if getattr(tab, "preview_kind", None) == "image":
                self._request_full_image_preview(tab)
            elif (getattr(tab, "preview_kind", None)
                    and not tab.property("folderPreviewHydrated")):
                self._schedule_preview_hydration(tab, immediate=True)

    def _tab_changed(self, index):
        tab = self.active_tab()
        if self.chat_panel is not None:
            ref = "/" + tab.path.relative_to(self.root).as_posix() if tab else None
            self.chat_panel.set_current_page(ref)
        self._update_window_identity_title(tab)
        if tab and not self._cycling_tabs:
            self.mru = [tab.path] + [p for p in self.mru if p != tab.path]
        if hasattr(self, "save_action"):
            self.save_action.setEnabled(bool(tab and tab.editor and not tab.editor.isReadOnly()))
        if hasattr(self, "reveal_active_tab_action"):
            self.reveal_active_tab_action.setEnabled(tab is not None)
        if hasattr(self, "table_preview_action"):
            delimited = bool(tab and tab.path.suffix.casefold() in DELIMITED_SUFFIXES)
            table = isinstance(getattr(tab, "viewer", None), SpreadsheetView)
            self.table_preview_action.setEnabled(delimited and not table)
            self.raw_text_action.setEnabled(delimited and table)
        # Heavy Markdown rendering and source highlighting remain deferred until
        # the newly selected tab has settled, so Ctrl+Tab itself stays instant.
        if tab and getattr(tab, "preview_kind", None) and not tab.property("folderPreviewHydrated"):
            self._schedule_preview_hydration(tab)
        self._schedule_markdown_preview(tab)

    def _focus_clicked_tab_editor(self, index):
        """Give a mouse-selected tab's content focus after the click completes."""
        tab = self.tabs.widget(index)
        if not isinstance(tab, Tab):
            return

        def apply_focus():
            if self.tabs.currentWidget() is not tab:
                return
            if tab.editor is not None:
                tab.editor.setFocus(Qt.MouseFocusReason)
            elif getattr(tab, "viewer", None) is not None:
                tab.viewer.setFocus(Qt.MouseFocusReason)
            else:
                self.tabs.setFocus(Qt.MouseFocusReason)

        QTimer.singleShot(0, apply_focus)

    def _focus_tab_content(self, tab):
        """Place keyboard focus in a selected tab after a popup closes."""
        if not isinstance(tab, Tab) or self.tabs.indexOf(tab) < 0:
            return
        self.raise_()
        self.activateWindow()
        self.tabs.setCurrentWidget(tab)
        if tab.editor is not None:
            tab.editor.setFocus(Qt.OtherFocusReason)
        elif getattr(tab, "viewer", None) is not None:
            tab.viewer.setFocus(Qt.OtherFocusReason)
        else:
            self.tabs.setFocus(Qt.OtherFocusReason)

    def cycle_tab(self, delta):
        current = self.active_tab()
        paths = [p for p in self.mru if self._index_for(p) >= 0]
        if len(paths) < 2:
            return
        target = paths[(paths.index(current.path) + delta) % len(paths)] if current and current.path in paths else paths[0]
        self._cycling_tabs = True
        try:
            self.tabs.setCurrentIndex(self._index_for(target))
        finally:
            self._cycling_tabs = False
        self.statusBar().showMessage(f"Tab switcher: {target.name}", 1800)

    def _ensure_tab_switcher(self):
        if self.tab_switcher is not None:
            return
        popup = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(12, 8, 12, 8)
        title = QLabel("Recent tabs", popup)
        title.setStyleSheet("font-weight: bold;")
        layout.addWidget(title)
        self.tab_switcher_list = QListWidget(popup)
        layout.addWidget(self.tab_switcher_list)
        popup_bg = theme_value("main_window.picker_popup.bg", "rgba(32,32,32,240)")
        popup_border = theme_value("main_window.picker_popup.border", "#666666")
        list_text = theme_value("main_window.picker_popup.list_text", "#f5f5f5")
        selected_bg = theme_value("main_window.picker_popup.list_selected_bg", "rgba(90,161,255,80)")
        popup.setStyleSheet(f"""
            QWidget {{ background: {popup_bg}; border: 1px solid {popup_border}; border-radius: 6px; }}
            QListWidget {{ background: transparent; color: {list_text}; border: none; }}
            QListWidget::item {{ padding: 4px 6px; }}
            QListWidget::item:selected {{ background: {selected_bg}; }}
        """)
        self.tab_switcher = popup

    def _recent_tab_candidates(self):
        current = self.active_tab()
        current_path = current.path if current else None
        return [path for path in self.mru
                if path != current_path and self._index_for(path) >= 0]

    def _cycle_tab_popup(self, reverse=False):
        paths = self._recent_tab_candidates()
        if not paths:
            return
        # Do not let a pending Pygments/Markdown enhancement for the outgoing
        # tab land synchronously while the user is holding the tab-cycle chord.
        self.markdown_preview_timer.stop()
        self.pending_markdown_preview = None
        if not self.tab_switcher_paths or not self.tab_switcher or not self.tab_switcher.isVisible():
            focused = self.focusWidget()
            self._tab_switcher_focus_editor_on_activate = (
                focused is self.rail
                or (focused is not None and self.rail.isAncestorOf(focused))
            )
            self.tab_switcher_paths = paths
            self.tab_switcher_index = 0
        else:
            self.tab_switcher_paths = paths
            delta = -1 if reverse else 1
            self.tab_switcher_index = (self.tab_switcher_index + delta) % len(paths)
        self._show_tab_switcher()

    def _show_tab_switcher(self):
        self._ensure_tab_switcher()
        if self._tab_switcher_rendered_paths != self.tab_switcher_paths:
            self.tab_switcher_list.clear()
            for path in self.tab_switcher_paths:
                try:
                    parent = path.parent.relative_to(self.root)
                    label = f"{path.name}  —  {parent if str(parent) != '.' else self.root.name}"
                except ValueError:
                    label = path.name
                self.tab_switcher_list.addItem(label)
            self._tab_switcher_rendered_paths = list(self.tab_switcher_paths)
        self.tab_switcher_list.setCurrentRow(self.tab_switcher_index)
        area = self.tabs.rect()
        origin = self.tabs.mapToGlobal(area.topLeft())
        width = min(max(420, self.tab_switcher.sizeHint().width()), max(420, area.width() - 40))
        height = min(max(220, self.tab_switcher.sizeHint().height()), max(220, area.height() - 48))
        self.tab_switcher.resize(width, height)
        self.tab_switcher.move(origin.x() + (area.width() - width) // 2, origin.y() + 24)
        self.tab_switcher.show()
        self.tab_switcher.raise_()

    def _activate_tab_switcher_selection(self):
        if not self.tab_switcher_paths or self.tab_switcher_index < 0:
            return
        target = self.tab_switcher_paths[self.tab_switcher_index]
        focus_editor = self._tab_switcher_focus_editor_on_activate
        self.tab_switcher.hide()
        self.tab_switcher_paths = []
        self.tab_switcher_index = -1
        self._tab_switcher_focus_editor_on_activate = False
        index = self._index_for(target)
        if index >= 0:
            self.tabs.setCurrentIndex(index)
            tab = self.active_tab()
            if focus_editor:
                if tab is not None and tab.editor is not None:
                    tab.editor.setFocus(Qt.OtherFocusReason)
                else:
                    self.tabs.setFocus(Qt.OtherFocusReason)

    def _review_dirty(self, tabs):
        dirty = [t for t in tabs if t.dirty]
        if not dirty:
            return True
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Unsaved files")
        dialog.setText("Review unsaved changes in:\n" + "\n".join(str(t.path) for t in dirty))
        save = dialog.addButton("Save All", QMessageBox.AcceptRole)
        discard = dialog.addButton("Discard", QMessageBox.DestructiveRole)
        cancel = dialog.addButton("Cancel", QMessageBox.RejectRole)
        dialog.setDefaultButton(cancel)
        dialog.exec()
        if dialog.clickedButton() == cancel:
            return False
        if dialog.clickedButton() == save:
            return all(self.save_tab(t) for t in dirty)
        return dialog.clickedButton() == discard

    def close_tab(self, index):
        if index < 0:
            return
        tab = self.tabs.widget(index)
        if self._review_dirty([tab]):
            if tab is self.pending_preview_hydration:
                self._cancel_pending_preview_hydration()
            closed_path = tab.path
            self.tabs.removeTab(index)
            tab.deleteLater()
            self._watch_files()
            self._update_welcome()
            self._focus_tree_when_tabs_empty(closed_path)

    def _update_welcome(self):
        self.welcome.setVisible(self.tabs.count() == 0)
        self.tabs.setVisible(self.tabs.count() > 0)
        self._update_window_identity_title(self.active_tab())

    def _update_window_identity_title(self, tab=None):
        title = f"Folder Navigator — {self.root.name}"
        if tab is not None:
            title += f" — {tab.path.name}"
        self.setWindowTitle(title)
        self._update_folder_breadcrumb(tab.path if tab is not None else self.root)

    def _folder_breadcrumb_items(self, path: Path) -> list[tuple[str, object, str]]:
        target = path if inside(self.root, path) else self.root
        items: list[tuple[str, object, str]] = [
            (self.root.name, self.root, str(self.root))
        ]
        if target == self.root:
            return items
        try:
            relative_parts = target.relative_to(self.root).parts
        except ValueError:
            return items
        current = self.root
        for part in relative_parts:
            current = current / part
            items.append((part, current, str(current)))
        return items

    def _update_folder_breadcrumb(self, path: Path) -> None:
        identity = getattr(self, "identity_bar", None)
        if identity is not None:
            identity.set_breadcrumb(self._folder_breadcrumb_items(path))

    def _open_folder_breadcrumb(self, target: object, modifiers=Qt.NoModifier) -> None:
        path = Path(str(target))
        if not path.is_dir():
            return
        control_modifier = (
            Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier
        )
        try:
            if not modifiers & control_modifier:
                if path.resolve() == self.root:
                    if self.isMinimized():
                        self.showNormal()
                    else:
                        self.show()
                    self.raise_()
                    self.activateWindow()
                    return
                from .instances import activate_existing
                if activate_existing(path, exclude_pid=os.getpid()):
                    return
            launch(path)
        except (OSError, ValueError) as exc:
            QMessageBox.warning(self, "Could not launch Folder Navigator",
                                f"{exc}\nCheck the selected folder and installation, then try again.")

    def _focus_tree_when_tabs_empty(self, preferred_path=None):
        """Return keyboard/vi navigation to the folder tree after the last close."""
        if self.tabs.count() != 0:
            return
        if self.rail.isHidden():
            self._set_folder_rail_visible(True)
        self._suppress_tree_preview = True
        self.rail.setCurrentIndex(0)
        if preferred_path is not None and inside(self.root, preferred_path):
            if not inside(self.scope, preferred_path):
                self.clear_filter()
            index = self.model.index(str(preferred_path))
            if index.isValid():
                self.tree.setCurrentIndex(index)
                self.tree.scrollTo(index)
        self.tree.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(0, self._focus_tree_if_still_empty)

    def _focus_tree_if_still_empty(self):
        if self.tabs.count() == 0:
            self.rail.setCurrentIndex(0)
            self.tree.setFocus(Qt.OtherFocusReason)
        QTimer.singleShot(0, self._resume_tree_preview)

    def _resume_tree_preview(self):
        self._suppress_tree_preview = False

    def save_active(self):
        if self.active_tab():
            self.save_tab(self.active_tab())

    def save_all(self):
        for tab in self.all_tabs():
            if tab.dirty and not self.save_tab(tab):
                return False
        return True

    def save_tab(self, tab):
        if not tab.editor or tab.editor.isReadOnly():
            return False
        if not inside(self.root, tab.path):
            tab.show_notice("Cannot save a missing or outside-root path")
            return False
        try:
            buffer_text = tab.text_for_save()
            try:
                changed = atomic_save(tab.path, buffer_text, tab.loaded)
            except ConflictError:
                dialog = QMessageBox(self)
                dialog.setWindowTitle("External changes")
                dialog.setText(f"{tab.path.name} changed on disk. Your edits are still in memory.")
                compare = dialog.addButton("Compare / Review", QMessageBox.ActionRole)
                overwrite = dialog.addButton("Overwrite", QMessageBox.DestructiveRole)
                reload = dialog.addButton("Reload from Disk", QMessageBox.ActionRole)
                cancel = dialog.addButton("Cancel", QMessageBox.RejectRole)
                dialog.setDefaultButton(cancel)
                dialog.exec()
                if dialog.clickedButton() == compare:
                    disk = read_text(tab.path)
                    import difflib
                    review = QDialog(self)
                    review.setWindowTitle("Compare with disk")
                    review.resize(750, 500)
                    layout = QVBoxLayout(review)
                    diff = QTextEdit()
                    diff.setReadOnly(True)
                    diff.setPlainText("".join(difflib.unified_diff(
                        disk.text.splitlines(True), buffer_text.splitlines(True),
                        fromfile="Disk", tofile="Buffer")))
                    layout.addWidget(diff)
                    review.exec()
                    return False
                if dialog.clickedButton() == reload:
                    tab.loaded = read_text(tab.path)
                    tab.load_text(tab.loaded.text)
                    return True
                if dialog.clickedButton() != overwrite:
                    return False
                changed = atomic_save(tab.path, buffer_text, tab.loaded, overwrite=True)
            tab.loaded.fingerprint = changed
            tab.loaded.text = buffer_text
            tab.clean_text = buffer_text
            tab.editor.document().setModified(False)
            self.statusBar().showMessage("Saved", 2000)
            return True
        except (OSError, ValueError, UnicodeError) as exc:
            tab.show_notice(f"Save failed: {exc}")
            self.statusBar().showMessage(f"Save failed: {exc}", 12000)
            return False

    def closeEvent(self, event):
        if not self._review_dirty(self.all_tabs()):
            event.ignore()
            return
        self.search_cancel.set()
        self.catalog_cancel.set()
        if self.chat_panel is not None:
            self.chat_panel.close()
        self._cancel_pending_preview_hydration()
        self._close_excalidraw_processes()
        self._persist()
        registration = getattr(self, "instance_registration", None)
        if registration is not None:
            registration.close()
        if self.window_trace is not None:
            self.window_trace.close()
        self.preview_executor.shutdown(wait=False, cancel_futures=True)
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def open_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Open Folder", str(self.root))
        if path:
            try:
                selected = Path(path)
                from .instances import activate_existing
                if not activate_existing(selected):
                    launch(selected)
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not launch Folder Navigator", f"{exc}\nCheck the installation and try again.")

    @staticmethod
    def _stillpoint_state(name):
        try:
            return (Path.home() / ".stillpoint" / name).read_text(
                encoding="utf-8"
            ).strip()
        except OSError:
            return ""

    def print_active(self):
        """Open the active page through StillPoint's server print pipeline."""
        tab = self.active_tab()
        if tab is None or tab.path.suffix.casefold() not in (".md", ".markdown", ".txt"):
            self.statusBar().showMessage(
                "StillPoint printing is available for Markdown and text pages", 6000
            )
            return
        vault_text = os.environ.get("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", "")
        relative = None
        try:
            vault = Path(vault_text).resolve(strict=True)
            relative = tab.path.resolve(strict=True).relative_to(vault).as_posix()
        except (OSError, ValueError):
            pass
        options = self._show_print_dialog(tab.path)
        if not options:
            return
        api_base = (
            os.environ.get("SP_FOLDER_NAVIGATOR_API_BASE", "").strip()
            or self._stillpoint_state("api-base")
        ).rstrip("/")
        if not api_base:
            self.statusBar().showMessage(
                "StillPoint's print server is not running", 8000
            )
            return
        try:
            token = self._get_stillpoint_print_token(api_base)
            if (relative is not None and not tab.dirty
                    and tab.path.suffix.casefold() in (".md", ".txt")):
                mode = "tree" if options["include_subpages"] else "page"
                path_to_use = relative
                if mode == "tree":
                    parent = Path(relative).parent.as_posix()
                    path_to_use = (
                        parent if parent and parent != "."
                        else Path(relative).with_suffix("").as_posix()
                    )
                url = self._build_print_url(
                    api_base,
                    path_to_use,
                    mode=mode,
                    depth=options["depth"],
                    token=token,
                    show_header=options["include_header"],
                    include_toc=options["include_toc"],
                    toc_title=options["toc_title"],
                    auto_pop=options["auto_pop_browser"],
                )
            else:
                if options["include_subpages"]:
                    raise RuntimeError(
                        "Subpage printing requires a saved page inside the connected vault"
                    )
                preview_id = self._create_stillpoint_print_preview(
                    api_base, tab.path, tab.text_for_save()
                )
                url = self._build_print_preview_url(
                    api_base,
                    preview_id,
                    token=token,
                    show_header=options["include_header"],
                    include_toc=options["include_toc"],
                    toc_title=options["toc_title"],
                    auto_pop=options["auto_pop_browser"],
                )
            if not QDesktopServices.openUrl(QUrl(url)):
                raise RuntimeError("The browser could not open the print view")
            self.statusBar().showMessage("Print view opened in browser", 3000)
        except (OSError, RuntimeError, ValueError, urllib.error.URLError) as exc:
            self.statusBar().showMessage(f"Failed to open print view: {exc}", 12000)

    def _show_print_dialog(self, path):
        from sp.app import config

        dialog = QDialog(self)
        dialog.setWindowTitle("Print to Browser")
        dialog.setMinimumWidth(400)
        dialog.setWindowModality(Qt.ApplicationModal)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Print options:"))
        include_header = QCheckBox("Include header (title/path)")
        layout.addWidget(include_header)
        auto_pop = QCheckBox("Auto pop the browser print dialogue?")
        auto_pop.setChecked(config.load_print_auto_pop_browser())
        layout.addWidget(auto_pop)
        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)
        include_subpages = QCheckBox("Include subpages")
        layout.addWidget(include_subpages)
        depth_row = QHBoxLayout()
        depth_label = QLabel("Max depth:")
        depth = QSpinBox()
        depth.setRange(1, 20)
        depth.setValue(1)
        depth.setEnabled(False)
        depth_label.setEnabled(False)
        depth_row.addWidget(depth_label)
        depth_row.addWidget(depth)
        depth_row.addStretch(1)
        layout.addLayout(depth_row)
        include_toc = QCheckBox("Include table of contents")
        include_toc.setEnabled(False)
        layout.addWidget(include_toc)
        title_row = QHBoxLayout()
        title_row.addSpacing(24)
        title_label = QLabel("Page header title:")
        title = QLineEdit(Path(path).stem)
        title.setEnabled(False)
        title_label.setEnabled(False)
        title_row.addWidget(title_label)
        title_row.addWidget(title)
        layout.addLayout(title_row)

        def toggle_subpages(checked):
            depth.setEnabled(checked)
            depth_label.setEnabled(checked)
            include_toc.setEnabled(checked)
            if checked and not include_toc.isChecked():
                include_toc.setChecked(True)
            title.setEnabled(checked and include_toc.isChecked())
            title_label.setEnabled(checked and include_toc.isChecked())

        include_subpages.toggled.connect(toggle_subpages)
        include_toc.toggled.connect(
            lambda checked: (
                title.setEnabled(checked and include_subpages.isChecked()),
                title_label.setEnabled(checked and include_subpages.isChecked()),
            )
        )
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setDefault(True)
            ok_button.setFocus()
        if dialog.exec() != QDialog.Accepted:
            return None
        config.save_print_auto_pop_browser(auto_pop.isChecked())
        return {
            "include_subpages": include_subpages.isChecked(),
            "depth": depth.value(),
            "include_header": include_header.isChecked(),
            "include_toc": include_toc.isChecked(),
            "toc_title": title.text().strip(),
            "auto_pop_browser": auto_pop.isChecked(),
        }

    def _get_stillpoint_print_token(self, api_base):
        local_token = self._stillpoint_local_ui_token()
        request = urllib.request.Request(
            f"{api_base}/auth/print-token",
            data=json.dumps({"ttl_seconds": 900}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"X-Local-UI-Token": local_token} if local_token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"StillPoint print server returned {exc.code}: {detail}") from exc
        return payload.get("token") or None

    def _stillpoint_local_ui_token(self):
        return (
            os.environ.get("SP_FOLDER_NAVIGATOR_LOCAL_UI_TOKEN", "").strip()
            or self._stillpoint_state("local-ui-token")
        )

    def _create_stillpoint_print_preview(self, api_base, path, content):
        local_token = self._stillpoint_local_ui_token()
        request = urllib.request.Request(
            f"{api_base}/api/print-preview",
            data=json.dumps({
                "title": Path(path).stem,
                "content": content,
                "path_label": str(path),
                "source_directory": str(Path(path).parent),
            }).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                **({"X-Local-UI-Token": local_token} if local_token else {}),
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"StillPoint preview server returned {exc.code}: {detail}") from exc
        preview_id = payload.get("preview_id")
        if not preview_id:
            raise RuntimeError("StillPoint did not return a print preview ID")
        return str(preview_id)

    @staticmethod
    def _build_print_url(api_base, path, *, mode, depth, token, show_header,
                         include_toc, toc_title, auto_pop=True):
        safe_path = quote(str(path).lstrip("/"), safe="/")
        url = f"{api_base.rstrip('/')}/print/{safe_path}?mode={mode}&auto={'1' if auto_pop else '0'}"
        if mode == "tree":
            url += f"&depth={depth}"
        if show_header:
            url += "&header=1"
        url += f"&toc={'1' if include_toc else '0'}"
        if include_toc and toc_title:
            url += f"&toc_title={quote(toc_title)}"
        if token:
            url += f"&token={quote(token)}"
        return url

    @staticmethod
    def _build_print_preview_url(api_base, preview_id, *, token, show_header,
                                 include_toc, toc_title, auto_pop=True):
        url = (
            f"{api_base.rstrip('/')}/print-preview/{quote(str(preview_id), safe='')}"
            f"?auto={'1' if auto_pop else '0'}"
        )
        if show_header:
            url += "&header=1"
        url += f"&toc={'1' if include_toc else '0'}"
        if include_toc and toc_title:
            url += f"&toc_title={quote(toc_title)}"
        if token:
            url += f"&token={quote(token)}"
        return url

    def _bookmark_in_stillpoint(self):
        """Add this root to the launching StillPoint vault's folder bookmarks."""
        from sp.app import config

        vault = os.environ.get("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT") or config.get_active_vault()
        if not vault:
            self.statusBar().showMessage(
                "No StillPoint vault is connected; launch Folder Navigator from StillPoint first",
                6000,
            )
            return
        token = config.push_active_vault_context(vault)
        try:
            bookmarks = config.load_folder_bookmarks()
            root = str(self.root)
            if root in bookmarks:
                self.statusBar().showMessage(
                    f"Already bookmarked in StillPoint: {self.root.name}", 3500
                )
                return
            bookmarks.append(root)
            config.save_folder_bookmarks(bookmarks)
            self.statusBar().showMessage(
                f"Added StillPoint bookmark: {self.root.name}", 3500
            )
        finally:
            config.reset_active_vault_context(token)

    def toggle_hidden(self, checked):
        filters = QDir.AllEntries | QDir.NoDotAndDotDot | QDir.AllDirs
        if checked:
            filters |= QDir.Hidden
        self.model.setFilter(filters)
        self.state["hidden"] = checked

    def _escape_tree(self):
        if self.scope != self.root:
            self.clear_filter()
        else:
            self.tree.collapseAll()

    def apply_filter(self, folder):
        if folder.is_dir() and inside(self.root, folder):
            if folder == self.root:
                self.clear_filter()
                return
            self.scope = folder
            self.tree.setRootIndex(self.model.index(str(folder)))
            self.filter_label.setToolTip(f"Filtered to {folder} (click to clear)")
            self.filter_label.show()
            self.clear_filter_action.setEnabled(True)
            self.statusBar().showMessage(f"Scope: {folder.name}", 2500)

    def filter_from_here(self):
        """Filter to the selected folder, or the selected file's parent."""
        index = self.tree.currentIndex()
        if index.isValid():
            path = Path(self.model.filePath(index))
            folder = path if self.model.isDir(index) else path.parent
        else:
            folder = self.scope
        self.apply_filter(folder)
        self.rail.setCurrentIndex(0)
        self.tree.setFocus(Qt.OtherFocusReason)

    def clear_filter(self):
        self.scope = self.root
        self.tree.setRootIndex(self.model.index(str(self.root)))
        self.filter_label.hide()
        self.clear_filter_action.setEnabled(False)
        self.statusBar().clearMessage()

    def _choose_file_mask(self):
        current = "; ".join(self.file_masks)
        text, accepted = QInputDialog.getText(
            self,
            "Filter Files by Mask",
            "File masks (for example: *.md; *.txt):",
            text=current,
        )
        if not accepted:
            return
        masks = [mask.strip() for mask in re.split(r"[;,]", text) if mask.strip()]
        normalized = []
        for mask in masks:
            if not any(character in mask for character in "*?["):
                mask = f"*{mask}*"
            normalized.append(mask)
        self.file_masks = normalized
        self.model.setNameFilters(self.file_masks)
        self.state["file_masks"] = list(self.file_masks)
        self.clear_file_mask_action.setEnabled(bool(self.file_masks))
        self._persist()
        self.statusBar().showMessage(
            f"File mask: {'; '.join(self.file_masks)}" if self.file_masks
            else "File mask cleared",
            4000,
        )

    def _clear_file_mask(self):
        self.file_masks = []
        self.model.setNameFilters([])
        self.state["file_masks"] = []
        self.clear_file_mask_action.setEnabled(False)
        self._persist()
        self.statusBar().showMessage("File mask cleared", 2500)

    def _bookmarks(self):
        return self.state.setdefault("bookmarks", [])

    def toggle_bookmark(self, path):
        if not inside(self.root, path):
            self.statusBar().showMessage(f"Cannot bookmark a target outside the root: {path}", 12000)
            return
        bookmarks = self._bookmarks()
        name = str(path)
        if name in bookmarks:
            bookmarks.remove(name)
        else:
            bookmarks.append(name)
        self._render_bookmarks()
        self._persist()

    def _render_bookmarks(self):
        while self.bookmarks_bar.count():
            item = self.bookmarks_bar.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for name in self._bookmarks():
            path = Path(name)
            button = QPushButton(("⚠ " if not path.exists() else "") + path.name)
            button.setToolTip(name + (" — missing (right-click to remove)" if not path.exists() else ""))
            button.clicked.connect(lambda _, p=path: self._activate_bookmark(p))
            button.setContextMenuPolicy(Qt.CustomContextMenu)
            button.customContextMenuRequested.connect(lambda pos, p=path, b=button: self._bookmark_menu(p, b.mapToGlobal(pos)))
            self.bookmarks_bar.addWidget(button)
        self.bookmarks_bar.addStretch()

    def _activate_bookmark(self, path):
        if path.is_file():
            self.open_file(path, pinned=True)
        elif path.is_dir() and inside(self.root, path):
            self.reveal_tree(path)
        else:
            self.statusBar().showMessage(f"Bookmark target is missing: {path}", 12000)

    def _bookmark_menu(self, path, point):
        menu = QMenu(self)
        if path.is_dir() and inside(self.root, path):
            menu.addAction("Filter Folder From Here", lambda: self.apply_filter(path))
        menu.addAction("Remove Bookmark", lambda: self.toggle_bookmark(path))
        menu.exec(point)

    def reveal_tree(self, path):
        if not inside(self.root, path):
            return
        if self.rail.isHidden():
            self._set_folder_rail_visible(True)
        if not inside(self.scope, path):
            self.clear_filter()
        index = self.model.index(str(path))
        root_index = self.tree.rootIndex()
        cursor = index.parent()
        # Only expand ancestors displayed beneath the tree's root. Expanding
        # QFileSystemModel parents all the way to the filesystem root is both
        # invisible here and noticeably expensive on an editor-to-tree Escape.
        while cursor.isValid() and cursor != root_index:
            self.tree.expand(cursor)
            cursor = cursor.parent()
        # Revealing the active editor file is a focus handoff, not a new
        # flyover selection. Avoid restarting preview/debounce work for a tab
        # whose content is already loaded and visible.
        was_suppressed = self._suppress_tree_preview
        self._suppress_tree_preview = True
        try:
            if self.tree.currentIndex() != index:
                self.tree.setCurrentIndex(index)
        finally:
            self._suppress_tree_preview = was_suppressed
        visible_rect = self.tree.visualRect(index)
        if not visible_rect.isValid() or not self.tree.viewport().rect().intersects(visible_rect):
            self.tree.scrollTo(index)
        self.rail.setCurrentIndex(0)
        self.tree.setFocus()

    def _reveal_active_tab_in_folder(self):
        """Reveal the tab that is active when the command is executed."""
        tab = self.active_tab()
        if tab is None:
            self.statusBar().showMessage("Open a file to reveal it in the folder", 3000)
            return
        self.reveal_tree(tab.path)

    def _tree_menu(self, point):
        index = self.tree.indexAt(point)
        menu = self._create_tree_context_menu(index)
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _create_tree_context_menu(self, index):
        menu = QMenu(self)
        if not index.isValid():
            root_path = self.model.filePath(self.tree.rootIndex())
            target_directory = Path(root_path) if root_path else self.scope
            target_index = self.tree.rootIndex()
            self._add_new_context_menu(menu, target_directory, target_index)
            menu.addSeparator()
            menu.addAction("Open Terminal Here", lambda: self.terminal(target_directory))
            return menu
        path = Path(self.model.filePath(index))
        folder = self.model.isDir(index)
        if folder:
            menu.addAction("Expand / Collapse", lambda: self.tree.setExpanded(index, not self.tree.isExpanded(index)))
            menu.addAction("Filter From Here", lambda: self.apply_filter(path))
            if self.scope != self.root:
                menu.addAction("Clear Filter", self.clear_filter)
        else:
            menu.addAction("Open", lambda: self.open_file(path))
            menu.addAction("Open in New Tab", lambda: self.open_file(path, pinned=True))
            menu.addAction("Keep Open", lambda: self.keep_open(self._index_for(path)))
            specialized_label = self._specialized_editor_label(path)
            if specialized_label:
                menu.addAction(specialized_label, lambda: self._open_specialized_editor(path))
            menu.addAction("Open in Default Application", lambda: self.system_open(path))
            menu.addSeparator()
            menu.addAction("Rename…", lambda: self._rename_file(path))
            menu.addAction("Delete File…", lambda: self._delete_file(path))
        target_directory = path if folder else path.parent
        target_index = index if folder else index.parent()
        menu.addSeparator()
        self._add_new_context_menu(menu, target_directory, target_index)
        menu.addSeparator()
        if self.chat_panel is not None:
            menu.addAction("Add to AI Chat Context", lambda: self._chat_add_path(path))
        menu.addAction("Remove Bookmark" if str(path) in self._bookmarks() else "Bookmark", lambda: self.toggle_bookmark(path))
        menu.addSeparator()
        menu.addAction("Reveal in File Manager", lambda: self.reveal(path))
        copy_menu = QMenu("Copy Path", menu)
        menu.addMenu(copy_menu)
        copy_menu.addAction("Full Path", lambda: QApplication.clipboard().setText(str(path)))
        copy_menu.addAction("Relative Path", lambda: QApplication.clipboard().setText(str(path.relative_to(self.root))))
        menu.addAction("Open Terminal Here", lambda: self.terminal(path if folder else path.parent))
        return menu

    def _add_new_context_menu(self, menu, directory, directory_index):
        new_menu = QMenu("New", menu)
        menu.addMenu(new_menu)
        new_menu.addAction("File", lambda: self._begin_new_file(directory, directory_index))
        new_menu.addAction(
            "Folder", lambda: self._begin_new_file(directory, directory_index, create_folder=True)
        )
        new_menu.addSeparator()
        self._add_new_diagram_actions(new_menu, directory, directory_index)

    def _rename_file(self, path):
        path = Path(path)
        if not path.is_file() or not inside(self.root, path):
            self.statusBar().showMessage(f"File is unavailable: {path}", 8000)
            return
        tab_index = self._index_for(path)
        if tab_index >= 0 and self.tabs.widget(tab_index).dirty:
            self.statusBar().showMessage("Save changes before renaming this file", 8000)
            return
        self._cancel_new_file()
        self._cancel_rename_file()
        index = self.model.index(str(path))
        if not index.isValid():
            self.statusBar().showMessage(f"File is not visible in the folder tree: {path}", 8000)
            return
        self.tree.scrollTo(index)
        rect = self.tree.visualRect(index)
        x = max(18, rect.x() + self.tree.indentation())
        edit = InlineFileNameEdit(self.tree.viewport())
        edit.setPlaceholderText("File name")
        edit.setAccessibleName("Rename file")
        edit.setText(path.name)
        edit.setSelection(0, len(path.name) - len(path.suffix))
        edit.setGeometry(x, rect.y(), max(180, self.tree.viewport().width() - x - 8), 28)
        edit.setStyleSheet(
            f"border: 2px solid {theme_value('main_window.focus_border.default', '#4A90E2')}; "
            "border-radius: 3px; padding: 2px 6px;"
        )
        edit.returnPressed.connect(self._commit_rename_file)
        edit.canceled.connect(self._cancel_rename_file)
        self.rename_file_path = path
        self.rename_file_edit = edit
        edit.show()
        edit.raise_()
        QTimer.singleShot(0, self._focus_rename_file_edit)

    def _focus_rename_file_edit(self):
        edit = self.rename_file_edit
        if edit is not None:
            edit.raise_()
            edit.setFocus(Qt.PopupFocusReason)

    def _cancel_rename_file(self):
        edit = self.rename_file_edit
        self.rename_file_edit = None
        self.rename_file_path = None
        if edit is not None:
            edit.hide()
            edit.deleteLater()

    def _commit_rename_file(self):
        edit = self.rename_file_edit
        path = self.rename_file_path
        if edit is None or path is None:
            return
        name = edit.text().strip()
        if not name or name in {".", ".."} or "/" in name or "\\" in name:
            self.statusBar().showMessage("Enter a file name without folder separators", 8000)
            edit.selectAll()
            return
        target = path.with_name(name)
        if target == path:
            self._cancel_rename_file()
            return
        if target.exists() or target.is_symlink():
            self.statusBar().showMessage(f"An item named {name} already exists", 8000)
            edit.selectAll()
            return
        if not path.is_file() or not inside(self.root, path):
            self.statusBar().showMessage(f"File is unavailable: {path}", 8000)
            self._cancel_rename_file()
            return
        tab_index = self._index_for(path)
        if tab_index >= 0 and self.tabs.widget(tab_index).dirty:
            self.statusBar().showMessage("Save changes before renaming this file", 8000)
            return
        try:
            path.rename(target)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not rename {path.name}: {exc}", 12000)
            edit.selectAll()
            return
        self._cancel_rename_file()
        active_path = self.active_tab().path if self.active_tab() else None
        was_pinned = self.tabs.widget(tab_index).pinned if tab_index >= 0 else False
        if tab_index >= 0:
            self.close_tab(tab_index)
        self._reconcile_file_change(path, target)
        if tab_index >= 0:
            self.open_file(target, pinned=was_pinned, replace_preview=False)
            if active_path is not None and active_path != path:
                previous_index = self._index_for(active_path)
                if previous_index >= 0:
                    self.tabs.setCurrentIndex(previous_index)
        self.statusBar().showMessage(f"Renamed {path.name} to {name}", 4000)

    @staticmethod
    def _move_file_to_trash(path):
        return QFile.moveToTrash(str(path))

    def _delete_file(self, path):
        path = Path(path)
        if not path.is_file() or not inside(self.root, path):
            self.statusBar().showMessage(f"File is unavailable: {path}", 8000)
            return
        tab_index = self._index_for(path)
        if tab_index >= 0 and self.tabs.widget(tab_index).dirty:
            self.statusBar().showMessage("Save or close unsaved changes before deleting this file", 8000)
            return
        answer = QMessageBox.question(
            self, "Delete File", f"Move {path.name} to Trash?",
            QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel,
        )
        if answer != QMessageBox.Yes:
            return
        if not self._move_file_to_trash(path):
            self.statusBar().showMessage(f"Could not move {path.name} to Trash", 12000)
            return
        if tab_index >= 0:
            self.close_tab(tab_index)
        self._reconcile_file_change(path)
        self.statusBar().showMessage(f"Moved {path.name} to Trash", 4000)

    def _reconcile_file_change(self, old_path, new_path=None):
        self._cancel_pending_tree_markdown()
        self._cancel_pending_preview_hydration()
        self.catalog.discard(old_path)
        was_ignored = old_path in self.ignored_paths
        self.ignored_paths.discard(old_path)
        self.mru = [new_path if item == old_path else item for item in self.mru
                    if new_path is not None or item != old_path]
        bookmarks = self._bookmarks()
        if str(old_path) in bookmarks:
            bookmarks.remove(str(old_path))
            if new_path is not None and str(new_path) not in bookmarks:
                bookmarks.append(str(new_path))
            self._render_bookmarks()
            self._persist()
        if new_path is not None:
            self.catalog.add(new_path)
            if was_ignored:
                self.ignored_paths.add(new_path)
        if self.catalog_db is not None:
            try:
                self.catalog_db.forget_path(old_path)
                if new_path is not None:
                    self.catalog_db.upsert_paths([new_path])
            except (OSError, sqlite3.Error) as exc:
                self.statusBar().showMessage(f"Quick Open cache update failed: {exc}", 8000)
        self._watch_files()
        self._refresh_quick_pickers()

    def _chat_add_path(self, path: Path) -> None:
        if self.chat_panel is None:
            return
        self._toggle_chat_panel(True)
        if hasattr(self, "chat_visibility_action"):
            self.chat_visibility_action.setChecked(True)
        if not self.chat_panel.add_path_to_context(path):
            self.statusBar().showMessage(f"Could not add chat context: {path}", 8000)

    def _add_new_diagram_actions(self, menu, directory, directory_index):
        for label, suffix in (
            ("New PlantUML Diagram", ".puml"),
            ("New Mermaid Diagram", ".mmd"),
            ("New Excalidraw Diagram", ".excalidraw"),
        ):
            menu.addAction(
                label,
                lambda checked=False, selected=suffix: self._begin_new_file(
                    directory,
                    directory_index,
                    diagram_suffix=selected,
                ),
            )

    def _begin_new_file(self, directory, directory_index=None, *, create_folder=False,
                        diagram_suffix=None):
        """Show an inline editor for a new file or folder name."""
        self._cancel_rename_file()
        self._cancel_new_file()
        directory = Path(directory)
        if not directory.is_dir() or not inside(self.root, directory):
            kind = "folder" if create_folder else "file"
            self.statusBar().showMessage(
                f"Cannot create a {kind} outside the folder root: {directory}", 8000
            )
            return
        index = directory_index if directory_index is not None else self.model.index(str(directory))
        if index.isValid():
            self.tree.expand(index)
            rect = self.tree.visualRect(index)
            x = max(18, rect.x() + self.tree.indentation())
            y = max(0, rect.bottom() + 1)
        else:
            x, y = 18, 4
        edit = InlineFileNameEdit(self.tree.viewport())
        kind = "folder" if create_folder else "file"
        edit.setPlaceholderText(f"New {kind} name")
        edit.setAccessibleName(f"New {kind} name")
        if diagram_suffix:
            basename = "diagram"
            edit.setText(f"{basename}{diagram_suffix}")
            edit.setSelection(0, len(basename))
        edit.setGeometry(x, y, max(180, self.tree.viewport().width() - x - 8), 28)
        edit.setStyleSheet(
            f"border: 2px solid {theme_value('main_window.focus_border.default', '#4A90E2')}; "
            "border-radius: 3px; padding: 2px 6px;"
        )
        edit.returnPressed.connect(self._commit_new_file)
        edit.canceled.connect(self._cancel_new_file)
        self.new_file_directory = directory
        self.new_file_is_folder = bool(create_folder)
        self.new_file_diagram_suffix = diagram_suffix
        self.new_file_edit = edit
        edit.show()
        edit.raise_()
        QTimer.singleShot(0, self._focus_new_file_edit)

    def _focus_new_file_edit(self):
        edit = self.new_file_edit
        if edit is not None:
            edit.raise_()
            edit.setFocus(Qt.PopupFocusReason)

    def _cancel_new_file(self):
        edit = self.new_file_edit
        self.new_file_edit = None
        self.new_file_directory = None
        self.new_file_is_folder = False
        self.new_file_diagram_suffix = None
        if edit is not None:
            edit.hide()
            edit.deleteLater()

    def _commit_new_file(self):
        edit = self.new_file_edit
        directory = self.new_file_directory
        create_folder = self.new_file_is_folder
        diagram_suffix = self.new_file_diagram_suffix
        if edit is None or directory is None:
            return
        name = edit.text().strip()
        if not name:
            edit.setFocus()
            return
        if name in {".", ".."} or Path(name).name != name:
            kind = "folder" if create_folder else "file"
            self.statusBar().showMessage(
                f"Enter a {kind} name without folder separators", 8000
            )
            edit.selectAll()
            return
        if diagram_suffix and not name.casefold().endswith(diagram_suffix):
            name += diagram_suffix
        target = directory / name
        try:
            if create_folder:
                target.mkdir()
            else:
                with target.open("x", encoding="utf-8") as stream:
                    if diagram_suffix:
                        stream.write(self._new_diagram_template(diagram_suffix))
        except FileExistsError:
            self.statusBar().showMessage(f"An item named {name} already exists", 8000)
            edit.selectAll()
            return
        except OSError as exc:
            self.statusBar().showMessage(f"Could not create {name}: {exc}", 12000)
            edit.selectAll()
            return
        self._cancel_new_file()
        if create_folder:
            self.tree.setFocus(Qt.OtherFocusReason)
            QTimer.singleShot(100, lambda: self.reveal_tree(target))
            return
        self.open_file(target, pinned=True)
        tab = self.active_tab()
        if tab and tab.editor:
            tab.editor.setFocus(Qt.OtherFocusReason)
        if diagram_suffix:
            QTimer.singleShot(0, lambda selected=target: self._open_specialized_editor(selected))

    @staticmethod
    def _new_diagram_template(suffix):
        return {
            ".puml": "@startuml\nA -> B: message\n@enduml\n",
            ".mmd": "flowchart TD\n  A[Start] --> B[End]\n",
            ".excalidraw": json.dumps({
                "type": "excalidraw",
                "version": 2,
                "source": "https://excalidraw.com",
                "elements": [],
                "appState": {"viewBackgroundColor": "#ffffff"},
                "files": {},
            }, ensure_ascii=False, indent=2) + "\n",
        }.get(str(suffix).casefold(), "")

    @staticmethod
    def _specialized_editor_label(path):
        return {
            ".puml": "Open PlantUML Editor",
            ".mmd": "Open Mermaid Editor",
            ".excalidraw": "Open Excalidraw",
        }.get(Path(path).suffix.casefold())

    def _open_specialized_editor(self, path):
        """Open a diagram in the matching standalone StillPoint editor."""
        path = Path(path)
        external_grant = None
        try:
            suffix = path.suffix.casefold()
            if suffix == ".puml":
                from sp.app.ui.plantuml_editor_window import PlantUMLEditorWindow
                window = PlantUMLEditorWindow(str(path), parent=None)
            elif suffix == ".mmd":
                from sp.app.ui.mermaid_editor_window import MermaidEditorWindow
                window = MermaidEditorWindow(str(path), parent=None)
            elif suffix == ".excalidraw":
                editor_url, external_grant = self._excalidraw_editor_url(
                    path, include_grant=True
                )
                self._launch_excalidraw_process(path, editor_url, external_grant)
                return
            else:
                return
            window.setWindowFlag(Qt.Window, True)
            window.setWindowFlag(Qt.Tool, False)
            window.setWindowModality(Qt.NonModal)
            window.setAttribute(Qt.WA_DeleteOnClose, True)
            if hasattr(window, "fileSaved"):
                window.fileSaved.connect(self._specialized_file_saved)
            self.specialized_editor_windows.append(window)
            window.destroyed.connect(
                lambda _=None, item=window: self.specialized_editor_windows.remove(item)
                if item in self.specialized_editor_windows else None
            )
            if suffix == ".excalidraw" and external_grant:
                window.destroyed.connect(
                    lambda _=None, grant=external_grant: self._revoke_excalidraw_grant(grant)
                )
            window.show()
        except Exception as exc:
            if external_grant:
                self._revoke_excalidraw_grant(external_grant)
            QMessageBox.warning(
                self,
                "Could not open diagram editor",
                f"Could not open {path.name} in its StillPoint editor:\n{exc}",
            )

    def _launch_excalidraw_process(self, path, editor_url, external_grant=None):
        """Host WebEngine outside the navigator's Qt process.

        Constructing QWebEngineView after QApplication is running can crash
        natively on Linux and macOS. StillPoint's main window therefore uses
        a dedicated process for Excalidraw, and the navigator must do the same.
        """
        from sp.app.ui.webengine_env import env_truthy

        path = Path(path)
        if env_truthy("SP_DISABLE_EXCALIDRAW_WEBENGINE"):
            if not QDesktopServices.openUrl(QUrl(editor_url)):
                raise RuntimeError("Could not open the Excalidraw URL in the default browser")
            if external_grant:
                # The browser has no child-process lifetime to observe. Keep
                # the scoped grant until this navigator closes (or its server
                # TTL expires).
                self.excalidraw_browser_grants.add(external_grant)
            return

        title = f"Excalidraw - {path.name}"
        if getattr(sys, "frozen", False):
            command = [
                sys.executable,
                "--excalidraw-webview",
                "--excalidraw-webview-url",
                editor_url,
                "--excalidraw-webview-title",
                title,
            ]
        else:
            command = [
                sys.executable,
                "-m",
                "sp.app.excalidraw_webview_process",
                "--url",
                editor_url,
                "--title",
                title,
            ]
        env = os.environ.copy()
        env.setdefault("SP_WEBENGINE_PROFILE", os.getenv("SP_WEBENGINE_PROFILE", "safe"))
        process = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parents[3]),
            env=env,
        )
        self.excalidraw_processes.append((process, external_grant, path))
        if not self.excalidraw_process_timer.isActive():
            self.excalidraw_process_timer.start()

    def _poll_excalidraw_processes(self):
        active = []
        for process, grant, path in self.excalidraw_processes:
            if process.poll() is None:
                active.append((process, grant, path))
                continue
            if grant:
                self._revoke_excalidraw_grant(grant)
            self._specialized_file_saved(str(path))
        self.excalidraw_processes = active
        if not active:
            self.excalidraw_process_timer.stop()

    def _close_excalidraw_processes(self):
        self.excalidraw_process_timer.stop()
        for process, grant, _path in self.excalidraw_processes:
            if process.poll() is None:
                try:
                    process.terminate()
                except OSError:
                    pass
            if grant:
                self._revoke_excalidraw_grant(grant)
        self.excalidraw_processes.clear()
        for grant in self.excalidraw_browser_grants:
            self._revoke_excalidraw_grant(grant)
        self.excalidraw_browser_grants.clear()

    def _excalidraw_editor_url(self, path, *, include_grant=False):
        """Build the authenticated editor URL for a drawing in the active vault."""
        from sp.app import config

        api_base = (
            os.environ.get("SP_FOLDER_NAVIGATOR_API_BASE", "").strip()
            or self._stillpoint_state("api-base")
        ).rstrip("/")
        vault_text = (
            os.environ.get("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT", "").strip()
            or config.get_active_vault()
        )
        if not api_base:
            raise RuntimeError("Start StillPoint before opening an Excalidraw editor")
        target = Path(path).expanduser().resolve(strict=True)
        access_path = None
        external_grant = None
        if vault_text:
            try:
                vault = Path(vault_text).expanduser().resolve(strict=True)
                access_path = "/" + target.relative_to(vault).as_posix()
            except (OSError, ValueError):
                pass
        if access_path is None:
            access_path, external_grant = self._create_excalidraw_grant(
                api_base, target
            )
        query = f"path={quote(access_path, safe='')}"
        token = self._stillpoint_local_ui_token()
        if token:
            query += f"&token={quote(token, safe='')}"
        url = f"{api_base}/excalidraw/edit?{query}"
        return (url, external_grant) if include_grant else url

    def _create_excalidraw_grant(self, api_base, path):
        token = self._stillpoint_local_ui_token()
        if not token:
            raise RuntimeError("StillPoint local UI authentication is unavailable")
        request = urllib.request.Request(
            f"{api_base}/api/excalidraw/external-grant",
            data=json.dumps({"path": str(path)}).encode("utf-8"),
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Local-UI-Token": token,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"StillPoint could not grant access to this drawing ({exc.code}): {detail}"
            ) from exc
        access_path = str(payload.get("path") or "")
        grant_id = str(payload.get("grant_id") or "")
        if not access_path.startswith("external:") or not grant_id:
            raise RuntimeError("StillPoint returned an invalid external-file grant")
        return access_path, grant_id

    def _revoke_excalidraw_grant(self, grant_id):
        api_base = (
            os.environ.get("SP_FOLDER_NAVIGATOR_API_BASE", "").strip()
            or self._stillpoint_state("api-base")
        ).rstrip("/")
        token = self._stillpoint_local_ui_token()
        if not api_base or not token:
            return
        request = urllib.request.Request(
            f"{api_base}/api/excalidraw/external-grant/{quote(str(grant_id), safe='')}",
            method="DELETE",
            headers={"X-Local-UI-Token": token},
        )
        try:
            urllib.request.urlopen(request, timeout=3).close()
        except (OSError, urllib.error.URLError):
            pass

    def _specialized_file_saved(self, saved_path):
        """Immediately reconcile a diagram save with its navigator preview/tab."""
        path = Path(saved_path)
        self.statusBar().showMessage(f"Saved {path.name}; refreshing preview", 3000)
        QTimer.singleShot(0, self._refresh_disk)

    def _tab_menu(self, point):
        index = self.tabs.tabBar().tabAt(point)
        if index < 0:
            return
        menu = QMenu(self)
        menu.addAction("Close", lambda: self.close_tab(index))
        menu.addAction("Close Others", lambda: self._close_indices([i for i in range(self.tabs.count()) if i != index]))
        menu.addAction("Close Tabs to the Right", lambda: self._close_indices(list(range(index + 1, self.tabs.count()))))
        menu.addAction("Close Saved Tabs", lambda: self._close_indices([i for i, tab in enumerate(self.all_tabs()) if not tab.dirty]))
        menu.addAction("Keep Open", lambda: self.keep_open(index))
        menu.addAction("Reveal in Folder", lambda: self.reveal_tree(self.tabs.widget(index).path))
        menu.exec(self.tabs.tabBar().mapToGlobal(point))

    def _create_editor_context_menu(self, tab, point):
        """Build the same compact edit menu for Markdown and source editors."""
        if not tab or not tab.editor:
            return None
        menu = tab.editor.createStandardContextMenu(point)
        if menu.actions() and not menu.actions()[-1].isSeparator():
            menu.addSeparator()
        if tab.markdown:
            menu.addAction(
                "Copy as Markdown",
                lambda: QApplication.clipboard().setText(tab.editor.to_markdown()),
            )
            menu.addAction("Format Markdown Table", self._format_active_markdown_table)
        reveal_action = menu.addAction("Reveal in Folder")
        reveal_action.triggered.connect(lambda: self.reveal_tree(tab.path))
        return menu

    def _show_editor_context_menu(self, tab, point):
        menu = self._create_editor_context_menu(tab, point)
        if menu is None:
            return
        menu.exec(tab.editor.viewport().mapToGlobal(point))

    def _install_preview_context_menu(self, tab, root):
        """Give every non-editor preview surface the same navigator action."""
        if root is None:
            return
        targets = [(root, root)]
        for widget in root.findChildren(QWidget):
            if isinstance(widget, QAbstractScrollArea):
                targets.append((widget.viewport(), widget))
                continue
            ancestor = widget.parentWidget()
            inside_scroll_area = False
            while ancestor is not None and ancestor is not root:
                if isinstance(ancestor, QAbstractScrollArea):
                    inside_scroll_area = True
                    break
                ancestor = ancestor.parentWidget()
            if not inside_scroll_area:
                targets.append((widget, widget))
        for target, menu_owner in targets:
            if target.property("folderRevealContextInstalled"):
                continue
            target.setProperty("folderRevealContextInstalled", True)
            target.setContextMenuPolicy(Qt.CustomContextMenu)
            target.customContextMenuRequested.connect(
                lambda point, t=tab, source=target, owner=menu_owner:
                    self._show_preview_context_menu(t, source, owner, point)
            )

    def _create_preview_context_menu(self, tab, owner=None, point=None):
        menu = None
        create_standard = getattr(owner, "createStandardContextMenu", None)
        if callable(create_standard):
            try:
                menu = create_standard(point)
            except TypeError:
                menu = create_standard()
        if menu is None:
            menu = QMenu(self)
        if menu.actions() and not menu.actions()[-1].isSeparator():
            menu.addSeparator()
        menu.addAction("Reveal in Folder", lambda: self.reveal_tree(tab.path))
        return menu

    def _show_preview_context_menu(self, tab, source, owner, point):
        menu = self._create_preview_context_menu(tab, owner, point)
        menu.exec(source.mapToGlobal(point))

    def _close_indices(self, indices):
        tabs = [self.tabs.widget(i) for i in indices]
        if not self._review_dirty(tabs):
            return
        active = self.active_tab()
        preferred_path = active.path if active in tabs else None
        for tab in tabs:
            self.tabs.removeTab(self.tabs.indexOf(tab))
            tab.deleteLater()
        self._watch_files()
        self._update_welcome()
        self._focus_tree_when_tabs_empty(preferred_path)

    def system_open(self, path):
        if not inside(self.root, path):
            answer = QMessageBox.question(self, "Outside selected folder", f"Open outside target in system application?\n{path}")
            if answer != QMessageBox.Yes:
                return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.statusBar().showMessage(f"No default application could open {path}", 12000)

    def _open_navigator_selection_in_system(self):
        path = None
        if self.rail.currentIndex() == 0:
            index = self.tree.currentIndex()
            if index.isValid():
                path = Path(self.model.filePath(index))
        else:
            item = self.search_results.currentItem()
            target = item.data(Qt.UserRole) if item else None
            if target:
                path = Path(target[0])
        if path is None or not path.exists():
            self.statusBar().showMessage("Select a file to open in its default application", 3000)
            return
        self.system_open(path)

    def reveal(self, path):
        target = path.parent if path.is_file() else path
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self.statusBar().showMessage(f"Could not open file manager at {target}", 12000)

    def terminal(self, folder):
        try:
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-a", "Terminal", str(folder)])
            elif sys.platform == "win32":
                subprocess.Popen(["cmd.exe", "/K", "cd", "/d", str(folder)], creationflags=subprocess.CREATE_NEW_CONSOLE)
            else:
                terminal = os.environ.get("TERMINAL")
                if not terminal:
                    raise OSError("Set the TERMINAL environment variable to your terminal application")
                subprocess.Popen([terminal], cwd=folder)
        except OSError as exc:
            self.statusBar().showMessage(f"Could not open terminal: {exc}", 12000)

    def folder_picker(self):
        self._warm_catalog()
        Picker(self, quick=True, folder_only=True).exec()

    def bookmark_picker(self):
        folder_navigators = self._stillpoint_folder_bookmarks()
        if not self._bookmarks() and not folder_navigators:
            self.statusBar().showMessage("No bookmarks to jump to", 3000)
            return
        picker = BookmarkPicker(self, folder_navigators)
        if picker.exec() == QDialog.Accepted and picker.selected_path is not None:
            if picker.selected_folder_navigator:
                self._open_folder_breadcrumb(picker.selected_path)
            else:
                self._activate_bookmark(picker.selected_path)

    def _stillpoint_folder_bookmarks(self):
        from sp.app import config

        vault = os.environ.get("SP_FOLDER_NAVIGATOR_STILLPOINT_VAULT") or config.get_active_vault()
        if not vault:
            return []
        token = config.push_active_vault_context(vault)
        try:
            return config.load_folder_bookmarks()
        finally:
            config.reset_active_vault_context(token)

    def quick_open(self):
        self._warm_catalog()
        Picker(self, quick=True).exec()

    def command_bar(self):
        self.command_palette.show_actions(self._collect_command_actions())

    @staticmethod
    def _command_menu_text(text):
        return str(text or "").replace("&", "").strip()

    def _collect_command_actions(self):
        """Collect every leaf menu action, matching StillPoint's command bar."""
        actions = []

        def walk(menu, path):
            for action in menu.actions():
                if action.isSeparator():
                    continue
                submenu = action.menu()
                label = self._command_menu_text(action.text())
                if submenu is not None:
                    walk(submenu, path + ([label] if label else []))
                elif label:
                    action.setProperty("commandLabel", " / ".join(path + [label]))
                    actions.append(action)

        for top_action in self.menuBar().actions():
            menu = top_action.menu()
            if menu is not None:
                label = self._command_menu_text(top_action.text())
                walk(menu, [label] if label else [])
        return actions

    @staticmethod
    def _run_command_action(action):
        if action.isEnabled():
            action.trigger()

    def _git_ignored(self, path):
        try:
            result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", str(path)],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=1,
                                    **_BACKGROUND_SUBPROCESS_OPTIONS)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def excluded(self, path):
        hidden = any(part.startswith(".") for part in path.relative_to(self.root).parts)
        if hidden or path in self.ignored_paths:
            return True
        if self.catalog_db is not None:
            try:
                return self.catalog_db.is_excluded(path)
            except (OSError, sqlite3.Error):
                pass
        return False

    def _catalog_batch(self, batch, generation, *, git_filtered=False):
        ignored = set()
        if not git_filtered:
            try:
                result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-z", "--stdin"],
                                        input=b"".join(os.fsencode(str(path)) + b"\0" for path in batch),
                                        capture_output=True, timeout=5,
                                        **_BACKGROUND_SUBPROCESS_OPTIONS)
                ignored = {Path(os.fsdecode(name)) for name in result.stdout.split(b"\0") if name}
            except (OSError, subprocess.TimeoutExpired):
                pass
        if self.catalog_db is not None:
            self.catalog_db.upsert_paths(batch, ignored, generation)
        self.bridge.result.emit(("catalog", batch, ignored))

    def _git_catalog_paths(self):
        """Use Git's optimized index walk and never traverse ignored outputs."""
        try:
            result = subprocess.run(
                [
                    "git", "-C", str(self.root), "ls-files", "-z",
                    "--cached", "--others", "--exclude-standard",
                ],
                capture_output=True,
                timeout=max(10.0, MAX_INDEX_SECONDS),
                **_BACKGROUND_SUBPROCESS_OPTIONS,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if result.returncode != 0:
            return None
        paths = []
        for raw in result.stdout.split(b"\0"):
            if not raw:
                continue
            relative = Path(os.fsdecode(raw))
            if relative.is_absolute() or ".." in relative.parts:
                continue
            if not pruned_relative_path(relative):
                paths.append(self.root / relative)
        return paths

    def _refresh_quick_pickers(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, Picker) and widget.window is self and widget.quick:
                widget.refresh()

    def _warm_catalog(self, *, force=False):
        if self.catalog_running or self.catalog_warmed:
            return
        self.catalog_cancel = threading.Event()
        self.catalog_warmed = True
        self.catalog_running = True
        self.catalog_state = "running"
        self.catalog_indexed_this_run = 0
        self.catalog_force_scan = force
        self.index_cancel_button.setEnabled(True)
        self._update_index_notice()

        def job():
            generation = None
            indexed = 0
            started = time.monotonic()
            state = "complete"
            try:
                if self.catalog_db is not None:
                    generation = self.catalog_db.begin_refresh()
                batch = []
                git_paths = self._git_catalog_paths()
                paths = git_paths if git_paths is not None else walk_files(
                    self.root,
                    self.root,
                    hidden=True,
                    canceled=self.catalog_cancel.is_set,
                    # Dense legitimate directories should remain searchable.
                    # Global file/time budgets protect the application instead.
                    max_directory_entries=None,
                )
                for path in paths:
                    if not force and indexed >= MAX_INDEX_FILES:
                        state = "partial"
                        break
                    if not force and time.monotonic() - started >= MAX_INDEX_SECONDS:
                        state = "partial"
                        break
                    batch.append(path)
                    indexed += 1
                    if len(batch) >= 500:
                        self._catalog_batch(
                            batch, generation, git_filtered=git_paths is not None
                        )
                        batch = []
                if batch:
                    self._catalog_batch(
                        batch, generation, git_filtered=git_paths is not None
                    )
                if self.catalog_cancel.is_set():
                    state = "canceled"
                complete = state == "complete"
                if self.catalog_db is not None and generation is not None:
                    self.catalog_db.finish_refresh(
                        generation,
                        complete=complete,
                        state=state,
                    )
                self.bridge.finished.emit(("catalog", state, indexed))
            except (OSError, sqlite3.Error) as exc:
                if self.catalog_db is not None and generation is not None:
                    try:
                        self.catalog_db.finish_refresh(
                            generation, complete=False, state="error"
                        )
                    except (OSError, sqlite3.Error):
                        pass
                self.bridge.finished.emit(("catalog-error", str(exc)))
        self.executor.submit(job)

    def run_search(self):
        query = self.search_input.text()
        if not query:
            return
        self.search_cancel.set()
        self.search_cancel = threading.Event()
        self.search_generation += 1
        generation = self.search_generation
        canceled = self.search_cancel
        self.search_results.clear()
        self.search_results.setFocus(Qt.OtherFocusReason)
        self._last_search_path = None
        rg = shutil.which("rg")
        engine = "ripgrep" if rg else "filesystem fallback"
        self.search_progress.setText(
            f"Searching {self.scope} with {engine}… Cancel remains available"
        )
        scope = self.scope
        case, whole, regex = self.search_case.isChecked(), self.search_word.isChecked(), self.search_regex.isChecked()
        ignore = not self.search_ignored.isChecked()
        def job():
            count = skipped = 0
            large_directories = []
            try:
                name_expression = re.compile((rf"\b(?:{query if regex else re.escape(query)})\b" if whole
                                              else query if regex else re.escape(query)),
                                             0 if case else re.IGNORECASE)
                if canceled.is_set():
                    self.bridge.finished.emit((
                        "search", generation, 0, 0, True, False, [],
                    ))
                    return
                if rg:
                    records = self._ripgrep_search(
                        rg,
                        scope,
                        query,
                        name_expression,
                        case=case,
                        whole=whole,
                        regex=regex,
                        include_ignored=not ignore,
                        canceled=canceled,
                    )
                    for path, line, excerpt in records:
                        if canceled.is_set():
                            break
                        self.bridge.result.emit(
                            ("search", generation, path, line, excerpt)
                        )
                        count += 1
                    self.bridge.finished.emit((
                        "search", generation, count, 0,
                        canceled.is_set(), count >= MAX_RESULTS, [],
                    ))
                    return
                for path in walk_files(
                    self.root,
                    scope,
                    ignore=self._git_ignored if ignore else None,
                    canceled=canceled.is_set,
                    max_directory_entries=MAX_DIRECTORY_ENTRIES,
                    skipped=lambda path, entries: large_directories.append((path, entries)),
                ):
                    if canceled.is_set():
                        break
                    if count >= MAX_RESULTS:
                        break
                    if name_expression.search(path.name):
                        self.bridge.result.emit(("search", generation, path, None, "Filename match"))
                        count += 1
                    if count >= MAX_RESULTS:
                        break
                    try:
                        content = read_text(path, MAX_SEARCH_BYTES)
                        for line, excerpt in content_matches(content.text, query, case=case, whole=whole, regex=regex):
                            self.bridge.result.emit(("search", generation, path, line, excerpt))
                            count += 1
                            if count >= MAX_RESULTS or canceled.is_set():
                                break
                    except (OSError, ValueError, UnicodeError):
                        skipped += 1
                self.bridge.finished.emit((
                    "search", generation, count, skipped,
                    canceled.is_set(), count >= MAX_RESULTS, large_directories,
                ))
            except Exception as exc:
                self.bridge.finished.emit(("search-error", generation, str(exc)))
        self.executor.submit(job)

    def _ripgrep_search(
        self,
        rg,
        scope,
        query,
        name_expression,
        *,
        case,
        whole,
        regex,
        include_ignored,
        canceled,
    ):
        """Return bounded filename/content matches using ripgrep's native walk."""
        target = "." if scope == self.root else scope.relative_to(self.root).as_posix()
        common = ["--no-messages"]
        if include_ignored:
            common.extend(("--hidden", "--no-ignore"))
        for name in sorted(DEFAULT_PRUNED_DIRECTORY_NAMES):
            common.extend(("--glob", f"!**/{name}/**"))
        for pattern in sorted(DEFAULT_PRUNED_DIRECTORY_GLOBS):
            common.extend(("--glob", f"!**/{pattern}/**"))
        for suffix in sorted(DEFAULT_PRUNED_FILE_SUFFIXES):
            common.extend(("--glob", f"!**/*{suffix}"))
        for pattern in sorted(DEFAULT_PRUNED_FILE_GLOBS):
            common.extend(("--glob", f"!**/{pattern}"))

        def lines(command):
            process = subprocess.Popen(
                command,
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                **_BACKGROUND_SUBPROCESS_OPTIONS,
            )
            done = threading.Event()

            def cancel_process():
                while not done.wait(.05):
                    if canceled.is_set():
                        try:
                            process.terminate()
                        except OSError:
                            pass
                        return

            threading.Thread(target=cancel_process, daemon=True).start()
            exhausted = False
            try:
                if process.stdout is not None:
                    for output_line in process.stdout:
                        yield output_line
                exhausted = True
            finally:
                done.set()
                if process.stdout is not None:
                    process.stdout.close()
                if not exhausted and process.poll() is None:
                    try:
                        process.terminate()
                    except OSError:
                        pass
                return_code = process.wait()
            if exhausted and return_code not in (0, 1) and not canceled.is_set():
                raise RuntimeError(f"ripgrep exited with status {return_code}")

        records = []
        files_command = [rg, "--files", *common, "--", target]
        for raw_path in lines(files_command):
            if canceled.is_set() or len(records) >= MAX_RESULTS:
                break
            relative = raw_path.rstrip("\r\n")
            path = self.root / relative
            if name_expression.search(path.name):
                records.append((path, None, "Filename match"))

        if not canceled.is_set() and len(records) < MAX_RESULTS:
            content_command = [
                rg,
                "--json",
                "--line-number",
                "--max-filesize",
                str(MAX_SEARCH_BYTES),
                *common,
            ]
            content_command.append("--case-sensitive" if case else "--ignore-case")
            if whole:
                content_command.append("--word-regexp")
            if not regex:
                content_command.append("--fixed-strings")
            content_command.extend(("--", query, target))
            for raw_event in lines(content_command):
                if canceled.is_set() or len(records) >= MAX_RESULTS:
                    break
                try:
                    event = json.loads(raw_event)
                    if event.get("type") != "match":
                        continue
                    data = event["data"]
                    relative = data["path"]["text"]
                    line = int(data["line_number"])
                    excerpt = data["lines"]["text"].strip()[:220]
                except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                    continue
                records.append((self.root / relative, line, excerpt))

        records.sort(
            key=lambda item: (
                str(item[0].relative_to(self.root)).casefold(),
                -1 if item[1] is None else item[1],
            )
        )
        return records[:MAX_RESULTS]

    def _search_result(self, payload):
        if payload[0] == "table":
            _, path, preview, allow_source, error = payload
            self._show_table_preview(path, preview, allow_source, error)
            return
        if payload[0] == "diagram":
            _, path, signature, svg, pixels, error = payload
            self._show_diagram_preview(path, signature, svg, pixels, error)
            return
        if payload[0] == "document":
            _, path, signature, preview, error = payload
            self._show_document_preview(path, signature, preview, error)
            return
        if payload[0] == "image":
            _, path, signature, quality, pixels, source_dimensions, error = payload
            self._show_image_preview(
                path, signature, quality, pixels, source_dimensions, error
            )
            return
        if payload[0] == "catalog":
            if self.catalog_db is None:
                self.catalog.update(payload[1])
                self.ignored_paths.update(payload[2])
            self.catalog_indexed_this_run += len(payload[1])
            self._update_index_notice()
            # Avoid re-running a large Quick Open query for every 500-file
            # batch. The last batch wins and refreshes after indexing settles.
            self.catalog_refresh_timer.start(750)
            return
        if payload[0] == "catalog-skip":
            self.skipped_directories[payload[1]] = payload[2]
            self._update_index_notice()
            return
        _, generation, path, line, excerpt = payload
        if generation != self.search_generation:
            return
        if path != self._last_search_path:
            heading = QListWidgetItem(str(path.relative_to(self.root)))
            heading.setFlags(Qt.NoItemFlags)
            heading.setData(Qt.AccessibleTextRole, f"File: {path.relative_to(self.root)}")
            self.search_results.addItem(heading)
            self._last_search_path = path
        item = QListWidgetItem(f"  {'Line ' + str(line) + ': ' if line else ''}{excerpt}")
        item.setData(Qt.UserRole, (path, line))
        item.setToolTip(str(path))
        self.search_results.addItem(item)
        if self.search_results.currentRow() < 0:
            self.search_results.setCurrentItem(item)

    def _search_finished(self, payload):
        if payload[0] == "drop-copy":
            _, copied, errors = payload
            self._schedule_refresh()
            if self.catalog_db is not None:
                try:
                    self.catalog_count = self.catalog_db.count()
                except (OSError, sqlite3.Error):
                    pass
            self._update_index_notice()
            self._refresh_quick_pickers()
            if errors:
                detail = "; ".join(errors[:3])
                if len(errors) > 3:
                    detail += f"; and {len(errors) - 3} more"
                self.statusBar().showMessage(
                    f"Copied {len(copied)} item(s); {len(errors)} skipped: {detail}",
                    15000,
                )
            else:
                self.statusBar().showMessage(
                    f"Copied {len(copied)} dropped item(s)", 5000
                )
        elif payload[0] == "catalog":
            _, state, indexed = payload
            self.catalog_running = False
            self.catalog_state = state
            self.catalog_indexed_this_run = indexed
            self.index_cancel_button.setEnabled(True)
            if self.catalog_db is not None:
                try:
                    self.catalog_count = self.catalog_db.count()
                    self.skipped_directories = dict(
                        self.catalog_db.skipped_directories()
                    )
                except (OSError, sqlite3.Error):
                    pass
            self._update_index_notice()
            self._refresh_quick_pickers()
            if state == "partial":
                self.statusBar().showMessage(
                    "Large folder detected: Quick Open is using a partial index. "
                    "Use Continue Full Index only if full-root coverage is required.",
                    20000,
                )
            elif state == "canceled":
                self.statusBar().showMessage(
                    f"Indexing canceled; {self.catalog_count:,} cached files remain available",
                    10000,
                )
        elif payload[0] == "catalog-error":
            self.catalog_running = False
            self.catalog_warmed = False
            self.catalog_state = "error"
            self._update_index_notice()
            self.statusBar().showMessage(f"Quick Open indexing failed: {payload[1]}", 15000)
        elif payload[0] == "search-error":
            if payload[1] == self.search_generation:
                self.search_progress.setText(f"Search failed: {payload[2]}")
        else:
            _, generation, count, skipped, canceled, limited, *extra = payload
            if generation != self.search_generation:
                return
            large_directories = extra[0] if extra else []
            self.skipped_directories.update(large_directories)
            self._update_index_notice()
            self.search_progress.setText(f"{'Canceled' if canceled else 'Complete'}: {count} results, {skipped} skipped" +
                                         (f" (limit {MAX_RESULTS} reached)" if limited else "") +
                                         (f", {len(large_directories)} large folders not searched" if large_directories else ""))

    def _open_search_item(self, item):
        target = item.data(Qt.UserRole)
        if not target:
            return
        path, line = target
        self.open_file(path, line=line)

    def _copy_dropped_paths(self, sources, target):
        """Copy local file-manager drops into an in-root directory."""
        try:
            target = Path(target).resolve(strict=True)
        except OSError as exc:
            self.statusBar().showMessage(f"Drop failed: {exc}", 12000)
            return
        if not target.is_dir() or not inside(self.root, target):
            self.statusBar().showMessage("Drop target is outside the folder root", 12000)
            return

        self.statusBar().showMessage(f"Copying {len(sources)} dropped item(s)…")

        def job():
            copied = []
            errors = []
            for candidate in sources:
                try:
                    source = Path(candidate).resolve(strict=True)
                    destination = target / source.name
                    if destination.exists():
                        errors.append(f"{source.name}: already exists")
                        continue
                    if source.is_dir():
                        if inside(source, target):
                            errors.append(f"{source.name}: cannot copy a folder into itself")
                            continue
                        shutil.copytree(source, destination)
                    elif source.is_file():
                        shutil.copy2(source, destination)
                    else:
                        errors.append(f"{source.name}: unsupported item")
                        continue
                    copied.append(destination)
                except (OSError, shutil.Error) as exc:
                    errors.append(f"{Path(candidate).name}: {exc}")

            batch = []
            for destination in copied:
                paths = ([destination] if destination.is_file() else walk_files(
                    self.root, destination, hidden=True,
                    canceled=self.catalog_cancel.is_set,
                    max_directory_entries=MAX_DIRECTORY_ENTRIES,
                ))
                for path in paths:
                    batch.append(path)
                    if len(batch) >= 500:
                        self._catalog_batch(batch, None)
                        batch = []
            if batch:
                self._catalog_batch(batch, None)
            self.bridge.finished.emit(("drop-copy", copied, errors))

        try:
            self.executor.submit(job)
        except RuntimeError:
            self.statusBar().showMessage("Drop failed: navigator is closing", 8000)

    def _move_dropped_paths(self, sources, target):
        """Move selected in-root items to another folder without overwriting."""
        target = Path(target)
        if not target.is_dir() or not inside(self.root, target):
            self.statusBar().showMessage("Move target is outside the folder root", 8000)
            return
        candidates = sorted(set(map(Path, sources)), key=lambda path: len(path.parts))
        selected = []
        for source in candidates:
            if (not source.is_relative_to(self.root) or not inside(self.root, source)
                    or not (source.is_file() or source.is_dir()) or source == self.root):
                self.statusBar().showMessage(f"Cannot move unavailable item: {source}", 8000)
                return
            if not any(parent.is_dir() and source.is_relative_to(parent) for parent in selected):
                selected.append(source)
        moves = []
        destinations = set()
        for source in selected:
            if source.parent == target:
                continue
            if source.is_dir() and target.is_relative_to(source):
                self.statusBar().showMessage("Cannot move a folder into itself", 8000)
                return
            destination = target / source.name
            if destination in destinations or destination.exists() or destination.is_symlink():
                self.statusBar().showMessage(f"An item named {source.name} already exists in {target.name}", 8000)
                return
            destinations.add(destination)
            moves.append((source, destination, source.is_dir()))
        if not moves:
            self.statusBar().showMessage("Items are already in that folder", 4000)
            return
        for tab in self.all_tabs():
            if tab.dirty and self._relocated_path(tab.path, moves) != tab.path:
                self.statusBar().showMessage(
                    "Save or close unsaved files before moving them", 8000
                )
                return
        completed = []
        errors = []
        for source, destination, directory in moves:
            try:
                source.rename(destination)
                completed.append((source, destination, directory))
            except OSError as exc:
                errors.append(f"{source.name}: {exc}")
        if completed:
            self._reconcile_moved_paths(completed)
        if errors:
            self.statusBar().showMessage(
                f"Moved {len(completed)} item(s); failed: {'; '.join(errors[:3])}", 12000
            )
        else:
            self.statusBar().showMessage(f"Moved {len(completed)} item(s) to {target.name}", 4000)

    @staticmethod
    def _relocated_path(path, moves):
        for source, destination, directory in moves:
            if path == source:
                return destination
            if directory and path.is_relative_to(source):
                return destination / path.relative_to(source)
        return path

    def _reconcile_moved_paths(self, moves):
        self._cancel_pending_tree_markdown()
        self._cancel_pending_preview_hydration()
        active_path = self.active_tab().path if self.active_tab() else None
        affected = [
            (tab.path, self._relocated_path(tab.path, moves), tab.pinned)
            for tab in self.all_tabs()
            if self._relocated_path(tab.path, moves) != tab.path
        ]
        for old_path, _new_path, _pinned in affected:
            index = self._index_for(old_path)
            if index >= 0:
                self.close_tab(index)
        remap = lambda path: self._relocated_path(path, moves)
        self.catalog = {remap(path) for path in self.catalog}
        self.ignored_paths = {remap(path) for path in self.ignored_paths}
        self.mru = [remap(path) for path in self.mru]
        bookmarks = self._bookmarks()
        updated_bookmarks = list(dict.fromkeys(str(remap(Path(name))) for name in bookmarks))
        if updated_bookmarks != bookmarks:
            bookmarks[:] = updated_bookmarks
            self._render_bookmarks()
            self._persist()
        if self.catalog_db is not None:
            try:
                for source, destination, directory in moves:
                    self.catalog_db.relocate_path(source, destination, directory=directory)
                    if not directory:
                        self.catalog_db.upsert_paths([destination])
            except (OSError, sqlite3.Error, ValueError) as exc:
                self.statusBar().showMessage(f"Quick Open cache update failed: {exc}", 8000)
        if self.scope != self.root and remap(self.scope) != self.scope:
            self.clear_filter()
        for _old_path, new_path, pinned in affected:
            self.open_file(new_path, pinned=pinned, replace_preview=False)
        if active_path is not None:
            current_index = self._index_for(remap(active_path))
            if current_index >= 0:
                self.tabs.setCurrentIndex(current_index)
        self._watch_files()
        self._refresh_quick_pickers()
        self._schedule_refresh()

    def _watch_files(self):
        desired = {str(self.root)}
        desired |= {str(t.path) for t in self.all_tabs() if t.path.exists()}
        desired |= {
            str(sidecar)
            for tab in self.all_tabs()
            if tab.path.suffix.casefold() == ".excalidraw"
            for sidecar in [tab.path.with_name(f"{tab.path.name}.png")]
            if sidecar.exists()
        }
        for path in set(self.watcher.files() + self.watcher.directories()) - desired:
            self.watcher.removePath(path)
        for path in desired - set(self.watcher.files() + self.watcher.directories()):
            self.watcher.addPath(path)

    def _schedule_refresh(self):
        self.refresh_timer.start(350)

    def _refresh_disk(self):
        if not self.root.is_dir():
            self.statusBar().showMessage("Root folder is missing, unmounted, or inaccessible", 15000)
        for tab in self.all_tabs():
            actual = fingerprint(tab.path)
            if actual is None:
                tab.detached = True
                tab.show_notice("File removed or renamed; buffer retained")
            elif tab.loaded and actual != tab.loaded.fingerprint:
                if tab.dirty:
                    tab.show_notice("File changed externally; save will require conflict review")
                else:
                    try:
                        cursor = tab.editor.textCursor()
                        position = cursor.position()
                        tab.loaded = read_text(tab.path)
                        tab.load_text(tab.loaded.text)
                        cursor.setPosition(min(position, len(tab.loaded.text)))
                        tab.editor.setTextCursor(cursor)
                        tab.show_notice("File reloaded after an external change")
                    except (OSError, ValueError) as exc:
                        tab.show_notice(f"Reload failed: {exc}")
            elif (tab.path.suffix.casefold() in DIAGRAM_SUFFIXES
                    and self._diagram_preview_signature(tab.path)
                    != getattr(tab, "preview_signature", None)):
                tab.show_notice("Refreshing diagram preview…")
                self._load_diagram_preview(tab.path)
            elif (getattr(tab, "preview_kind", None) == "image"
                    and fingerprint(tab.path) != getattr(tab, "preview_signature", None)):
                tab.show_notice("Refreshing image preview…")
                self._load_image_preview(
                    tab.path,
                    quality=getattr(tab, "requested_image_quality", "full"),
                    disposable=not tab.pinned,
                )
            elif tab.path.suffix.casefold() in DOCUMENT_SUFFIXES:
                try:
                    from .documents import document_signature
                    changed = document_signature(tab.path) != getattr(
                        tab, "preview_signature", None
                    )
                except OSError:
                    changed = False
                if changed:
                    tab.show_notice("Refreshing document preview…")
                    self._load_document_preview(tab.path)
        self.catalog = {p for p in self.catalog if p.exists() and inside(self.root, p)}
        self.ignored_paths.intersection_update(self.catalog)
        self._watch_files()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    configure_folder_navigator_process()
    app = QApplication.instance() or QApplication([sys.argv[0]])
    configure_folder_navigator_application(app)
    try:
        from sp.app.ui.theme import apply_qt_palette
        apply_qt_palette(app)
    except Exception:
        pass
    root = Path(args[0]).expanduser() if args else None
    if root is None or not root.is_dir():
        selected = QFileDialog.getExistingDirectory(None, "Choose a folder for Folder Navigator")
        if not selected:
            return 0
        root = Path(selected)
    window = Window(root)
    window.show()
    return app.exec()
