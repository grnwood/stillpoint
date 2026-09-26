"""Independent Qt window for browsing one ordinary filesystem root."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json
import mimetypes
import os
import re
import sqlite3
import subprocess
import sys
import threading

from PySide6.QtCore import (QDir, QEvent, QFileInfo, QFileSystemWatcher, QObject,
                            QPoint, Qt, QTimer, QUrl, Signal)
from PySide6.QtGui import (QAction, QDesktopServices, QImageReader, QKeySequence, QPalette,
    QPixmap, QShortcut, QTextCursor, QTextFormat)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QAbstractItemView, QFileIconProvider, QFileSystemModel, QHeaderView, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QStyle, QTabBar, QTextEdit, QPlainTextEdit, QTreeView, QVBoxLayout, QWidget, QScrollArea, QSpinBox)

from .core import (MAX_CONCURRENT_WORK, MAX_DIRECTORY_ENTRIES, MAX_EDIT_BYTES, MAX_IMAGE_PIXELS, MAX_RESULTS, MAX_SEARCH_BYTES,
    ConflictError, TextFile, atomic_save, content_matches, fingerprint, fuzzy_score, inside,
    read_text, walk_files)
from .catalog import CatalogError, FolderCatalog
from .editors import MarkdownEditor, SourceEditor, configure_markdown_editor, configure_source_editor
from .icon import (
    configure_folder_navigator_application,
    configure_folder_navigator_process,
    get_folder_navigator_icon,
)
from .launch import launch
from sp.app.ui.keyboard_shortcuts import is_vi_navigation_chord
from sp.app.ui.theme import theme_color, theme_value


class Bridge(QObject):
    result = Signal(object)
    finished = Signal(object)


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
    openFile = Signal(str, bool)
    openFileAndFocus = Signal(str, bool)
    escapePressed = Signal()

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
        self.setSortingEnabled(True)
        self.sortByColumn(0, Qt.AscendingOrder)

    def keyPressEvent(self, event):
        key = event.key()
        mods = event.modifiers()
        if key == Qt.Key_Escape:
            self.escapePressed.emit()
            return
        if self.vi_enabled and mods == Qt.NoModifier:
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
        index = self.indexAt(event.pos())
        if index.isValid() and event.modifiers() & (Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier):
            if not self.model().isDir(index):
                self.openFile.emit(self.model().filePath(index), True)
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


class Tab(QWidget):
    def __init__(self, path: Path, loaded: TextFile | None = None, *, markdown=False,
                 details="", root: Path | None = None, defer_enhancements=False):
        super().__init__()
        self.path = path
        self.loaded = loaded
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
            find_next.clicked.connect(lambda: self.editor.find(self.find_query.text()) or
                                      (self.editor.moveCursor(QTextCursor.Start), self.editor.find(self.find_query.text())))
            replace.clicked.connect(self._replace_current)
            for control in (self.find_query, find_next, self.replace_query, replace):
                find_layout.addWidget(control)
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
        else:
            self.editor = None
            label = QLabel(details)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            label.setWordWrap(True)
            layout.addWidget(label)

    @property
    def dirty(self):
        return bool(self.editor and self.editor.document().isModified())

    def text_for_save(self):
        if isinstance(self.editor, MarkdownEditor):
            return self.editor.to_markdown()
        return self.editor.toPlainText() if self.editor else ""

    def show_notice(self, message):
        self.notice.setText(message)
        self.notice.show()

    def _show_find(self):
        self.find_bar.show()
        self.find_query.setFocus()
        self.find_query.selectAll()

    def _replace_current(self):
        if not self.editor or self.editor.isReadOnly() or not self.find_query.text():
            return
        cursor = self.editor.textCursor()
        if cursor.selectedText() != self.find_query.text():
            if not self.editor.find(self.find_query.text()):
                return
            cursor = self.editor.textCursor()
        cursor.insertText(self.replace_query.text())


class ImageView(QWidget):
    def __init__(self, path, pixels):
        super().__init__()
        self.original = QPixmap.fromImage(pixels)
        self.zoom = 1.0
        layout = QVBoxLayout(self)
        controls = QHBoxLayout()
        self.scroll = QScrollArea()
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.label)
        self.scroll.setWidgetResizable(True)
        for title, callback in (("Fit to Window", self.fit), ("Actual Size", self.actual),
                                ("Zoom In", lambda: self.scale(1.25)), ("Zoom Out", lambda: self.scale(.8)),
                                ("Reset Zoom", self.actual)):
            button = QPushButton(title)
            button.clicked.connect(callback)
            controls.addWidget(button)
        layout.addLayout(controls)
        layout.addWidget(QLabel(f"{pixels.width()} × {pixels.height()} pixels · {path.stat().st_size:,} bytes"))
        layout.addWidget(self.scroll)
        QTimer.singleShot(0, self.fit)

    def scale(self, factor):
        self.zoom = min(8, max(.05, self.zoom * factor))
        self.label.setPixmap(self.original.scaled(self.original.size() * self.zoom,
                                                   Qt.KeepAspectRatio, Qt.SmoothTransformation))

    def actual(self):
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
    return widget


class Picker(QDialog):
    def __init__(self, window, *, quick=False):
        super().__init__(window)
        self.window = window
        self.quick = quick
        self.setWindowTitle("Quick Open" if quick else "Folder Picker")
        self.resize(650, 430)
        layout = QVBoxLayout(self)
        if quick:
            self.scope = QLabel()
            layout.addWidget(self.scope)
            self.query = QLineEdit()
            self.query.setPlaceholderText("Find a file by name or relative path")
            layout.addWidget(self.query)
            toggles = QHBoxLayout()
            self.include = QCheckBox("Include hidden and ignored")
            self.full = QCheckBox("Search full root")
            toggles.addWidget(self.include)
            toggles.addWidget(self.full)
            layout.addLayout(toggles)
            self.list = QListWidget()
            layout.addWidget(self.list)
            self.query.textChanged.connect(self.refresh)
            self.query.installEventFilter(self)
            self.include.toggled.connect(self.refresh)
            self.full.toggled.connect(self.refresh)
            self.list.itemActivated.connect(lambda item: self.accept_file(False))
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
        self.accept()
        tab = self.window.active_tab()
        if tab and tab.editor:
            tab.editor.setFocus()

    def refresh(self):
        if not self.quick:
            return
        scope = self.window.root if self.full.isChecked() else self.window.scope
        cache_state = "indexing" if self.window.catalog_running else f"{self.window.catalog_count:,} cached"
        self.scope.setText(
            f"Scope: {scope}  ·  "
            f"{'Including' if self.include.isChecked() else 'Excluding'} hidden and ignored files"
            f"  ·  {cache_state}"
        )
        query = self.query.text().strip()
        opened = {tab.path for tab in self.window.all_tabs()}
        candidates = set(self.window.catalog_candidates(
            query, scope, include_excluded=self.include.isChecked()
        ))
        candidates.update(opened)
        ranked = []
        for path in candidates:
            if not path.is_file() or not inside(self.window.root, path):
                continue
            if not inside(scope, path):
                continue
            if (not self.include.isChecked()
                    and (self.window.catalog_db is None or path in opened)
                    and self.window.excluded(path)):
                continue
            relative = str(path.relative_to(self.window.root))
            score = fuzzy_score(query, relative, recent=path in self.window.recent, opened=path in opened)
            if score is not None:
                ranked.append((-score, relative.casefold(), path))
        ranked.sort()
        self.list.clear()
        for _, relative, path in ranked[:150]:
            item = QListWidgetItem(f"{path.name}    {Path(relative).parent}")
            item.setData(Qt.UserRole, path)
            item.setToolTip(str(path))
            self.list.addItem(item)
        if not ranked:
            message = ("Indexing folder… results will appear as they are discovered"
                       if self.window.catalog_running else
                       "No files in the current scope and exclusion settings")
            item = QListWidgetItem(message)
            item.setFlags(Qt.NoItemFlags)
            self.list.addItem(item)
        else:
            self.list.setCurrentRow(0)

    def accept_file(self, pinned=False):
        item = self.list.currentItem()
        if item and item.data(Qt.UserRole):
            self.window.open_file(Path(item.data(Qt.UserRole)), pinned=pinned)
            self.accept()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.reject()
        elif self.quick and event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.accept_file(bool(event.modifiers() & (Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier)))
        elif self.quick and (event.key() in (Qt.Key_Down, Qt.Key_Up) or
                event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_J, Qt.Key_K)):
            delta = 1 if event.key() in (Qt.Key_Down, Qt.Key_J) else -1
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, self.list.currentRow() + delta)))
        else:
            super().keyPressEvent(event)

    def eventFilter(self, obj, event):
        if self.quick and obj is self.query and event.type() == QEvent.KeyPress:
            key = event.key()
            if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Down, Qt.Key_Up) or (
                    event.modifiers() == Qt.ControlModifier and key in (Qt.Key_J, Qt.Key_K)):
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
        self.scope = self.root
        self.catalog: set[Path] = set()
        self.ignored_paths: set[Path] = set()
        self.catalog_cancel = threading.Event()
        self.catalog_running = False
        self.catalog_warmed = False
        self.catalog_db_error = None
        try:
            self.catalog_db = FolderCatalog(self.root)
            self.catalog_count = self.catalog_db.count()
            self.catalog_ui_state = self.catalog_db.ui_states()
            self.skipped_directories = dict(self.catalog_db.skipped_directories())
        except (CatalogError, OSError, sqlite3.Error) as exc:
            self.catalog_db = None
            self.catalog_count = 0
            self.catalog_ui_state = {}
            self.skipped_directories = {}
            self.catalog_db_error = str(exc)
        self.recent: list[Path] = []
        self.executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORK, thread_name_prefix="folder-navigator")
        self.bridge = Bridge(self)
        self.search_cancel = threading.Event()
        self.search_generation = 0
        self.new_file_edit = None
        self.new_file_directory = None
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
        self.specialized_editor_windows: list[QMainWindow] = []
        self.bridge.result.connect(self._search_result)
        self.bridge.finished.connect(self._search_finished)
        self.setWindowTitle(f"{self.root.name} — Folder Navigator")
        self.resize(1100, 760)
        self.settings_path = Path.home() / ".stillpoint_folder_navigator.json"
        self.settings = self._load_settings()
        self.state = self.settings.setdefault(str(self.root), {})
        self.model = FolderModel(self.root, self)
        self.tree = NavigatorTree()
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
        self.tree.expanded.connect(lambda index: self._remember_expansion(index, True))
        self.tree.collapsed.connect(lambda index: self._remember_expansion(index, False))
        self.tree.selectionModel().currentChanged.connect(self._tree_selected)
        self.tree.openFile.connect(self._open_tree_file_keep_focus)
        self.tree.openFileAndFocus.connect(self._open_tree_file)
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
        self.rail = QTabWidget()
        self.rail.setObjectName("folderNavigatorRail")
        self.rail.addTab(self.tree, "Folder")
        self.rail.addTab(search_page, "Search")
        self.tabs = QTabWidget()
        self.tabs.setObjectName("folderNavigatorEditors")
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.tabs.tabBarDoubleClicked.connect(lambda i: self.keep_open(i))
        self.tabs.tabBar().setContextMenuPolicy(Qt.CustomContextMenu)
        self.tabs.tabBar().customContextMenuRequested.connect(self._tab_menu)
        self.welcome = QLabel(f"{self.root.name}\n\nSelect a file to preview · Enter to keep it open · Ctrl+P to find a file")
        self.welcome.setAlignment(Qt.AlignCenter)
        self.content = QSplitter(Qt.Vertical)
        self.content.addWidget(self.tabs)
        self.content.addWidget(self.welcome)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.rail)
        self.splitter.addWidget(self.content)
        self.splitter.setSizes(self.state.get("splitter", [300, 800]))
        outer = QWidget()
        layout = QVBoxLayout(outer)
        self.bookmarks_bar = QHBoxLayout()
        layout.addLayout(self.bookmarks_bar)
        self.filter_label = QPushButton()
        self.filter_label.clicked.connect(self.clear_filter)
        self.filter_label.hide()
        layout.addWidget(self.filter_label)
        layout.addWidget(self.splitter)
        self.setCentralWidget(outer)
        status = str(self.root)
        if self.catalog_db_error:
            status += f" · Quick Open cache unavailable: {self.catalog_db_error}"
        elif self.catalog_count:
            status += f" · {self.catalog_count:,} cached files"
        self.statusBar().showMessage(status)
        self.index_notice = QLabel()
        self.index_notice.setAccessibleName("Folder indexing status")
        self.statusBar().addPermanentWidget(self.index_notice, 1)
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
        app = QApplication.instance()
        if app is not None:
            app.focusChanged.connect(self._on_focus_changed)
        self._apply_focus_borders()
        self._render_bookmarks()
        self._restore()
        self.model.directoryLoaded.connect(lambda _: self._schedule_refresh())
        self.model.rowsInserted.connect(lambda parent, first, last: self._catalog_rows(parent, first, last))

    def _load_settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _on_focus_changed(self, _old, _current):
        self._apply_focus_borders()

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
        except RuntimeError:
            return
        focus_border = (
            theme_value("main_window.focus_border.filtered", "#D9534F")
            if self.scope != self.root and folder_has_focus
            else theme_value("main_window.focus_border.default", "#4A90E2")
        )
        self.rail.setStyleSheet(
            "QTabWidget#folderNavigatorRail::pane { "
            f"border: 2px solid {focus_border if folder_has_focus else 'transparent'}; "
            "border-radius: 3px; }"
        )
        self.tabs.setStyleSheet(
            "QTabWidget#folderNavigatorEditors::pane { "
            f"border: 2px solid {focus_border if editor_has_focus else 'transparent'}; "
            "border-radius: 3px; }"
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
        if self.skipped_directories:
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
        elif self.catalog_running:
            self.index_notice.setText("Indexing filenames for Quick Open…")
            self.index_notice.setToolTip("")
            self.index_notice.show()
        else:
            self.index_notice.clear()
            self.index_notice.hide()

    def _persist(self):
        self.state.update(splitter=self.splitter.sizes(), rail=self.rail.currentIndex(),
                          pinned=[str(t.path) for t in self.all_tabs() if t.pinned],
                          active=str(self.active_tab().path) if self.active_tab() and self.active_tab().pinned else None,
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

    def _make_actions(self):
        file_menu = self.menuBar().addMenu("&File")
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
        self.save_action = add(file_menu, "Save", self.save_active, QKeySequence.Save)
        add(file_menu, "Save All", self.save_all)
        add(file_menu, "Close Window", self.close)
        self.hidden_action = add(view_menu, "Show Hidden Files", self.toggle_hidden)
        self.hidden_action.setCheckable(True)
        self.hidden_action.setChecked(self.state.get("hidden", False))
        self.toggle_hidden(self.hidden_action.isChecked())
        columns_menu = view_menu.addMenu("Columns")
        columns_toolbar = self.addToolBar("File columns")
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
        add(go_menu, "Folder", lambda: (self.rail.setCurrentIndex(0), self.tree.setFocus()))
        add(go_menu, "Search", lambda: (self.rail.setCurrentIndex(1), self.search_input.setFocus()))
        add(go_menu, "Editor", lambda: self.active_tab().editor.setFocus() if self.active_tab() and self.active_tab().editor else None)
        add(go_menu, "Markdown Headings…", self._show_active_heading_picker, "Ctrl+Alt+T")
        add(go_menu, "Quick Open", self.quick_open, "Ctrl+P" if sys.platform != "darwin" else "Meta+P")
        add(go_menu, "Folder Picker", self.folder_picker, "Ctrl+Alt+V")
        add(go_menu, "Next Tab", lambda: self.cycle_tab(1), "Ctrl+Tab")
        add(go_menu, "Previous Tab", lambda: self.cycle_tab(-1), "Ctrl+Shift+Tab")
        add(go_menu, "Command Bar", self.command_bar, "Alt+G")
        self.clear_filter_action = add(go_menu, "Remove Filter", self.clear_filter)
        self.clear_filter_action.setEnabled(self.scope != self.root)
        self.command_bar_shortcuts = []
        for sequence in (["Ctrl+Shift+P", "Meta+Shift+P"] if sys.platform == "darwin"
                         else ["Ctrl+Shift+P"]):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(self.command_bar)
            self.command_bar_shortcuts.append(shortcut)
        close_shortcut = QShortcut(QKeySequence.Close, self)
        close_shortcut.activated.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        self.mru = []
        self._cycling_tabs = False

    def _set_column_visible(self, column, visible):
        self.tree.setColumnHidden(column, not visible)
        self.layout_save_timer.start(250)

    def _sync_column_actions(self):
        for column, action in getattr(self, "column_actions", {}).items():
            action.blockSignals(True)
            action.setChecked(not self.tree.isColumnHidden(column))
            action.blockSignals(False)

    def active_tab(self):
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, Tab) else None

    def all_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _index_for(self, path):
        return next((i for i, tab in enumerate(self.all_tabs()) if tab.path == path), -1)

    def _tree_selected(self, current, previous):
        self._cancel_pending_tree_markdown()
        self.markdown_preview_timer.stop()
        self.pending_markdown_preview = None
        if current.isValid() and not self.model.isDir(current):
            path = Path(self.model.filePath(current))
            if path.suffix.casefold() in (".md", ".markdown"):
                self.pending_tree_markdown_path = path
                self.tree_markdown_open_timer.start(self.tree_markdown_open_delay_ms)
            else:
                self.open_file(path, defer_enhancements=True)

    def _cancel_pending_tree_markdown(self):
        self.tree_markdown_open_timer.stop()
        self.pending_tree_markdown_path = None

    def _open_pending_tree_markdown(self):
        path = self.pending_tree_markdown_path
        self.pending_tree_markdown_path = None
        if path is None:
            return
        index = self.tree.currentIndex()
        if (index.isValid() and not self.model.isDir(index)
                and Path(self.model.filePath(index)) == path):
            self.open_file(path, defer_enhancements=True)

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
            tab.editor.setFocus(Qt.OtherFocusReason)

    def open_file(self, path: Path, pinned=False, line=None, defer_enhancements=False):
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
            self._schedule_markdown_preview(self.tabs.widget(index))
            return
        for existing_tab in self.all_tabs():
            self._clear_initial_formatting_dirty(existing_tab)
        preview = next((i for i, tab in enumerate(self.all_tabs()) if not tab.pinned and not tab.dirty), -1)
        if preview >= 0:
            self.tabs.removeTab(preview)
        try:
            if path.suffix.casefold() == ".pdf":
                tab = Tab(path, details="Loading PDF…", root=self.root)
                try:
                    view = pdf_view(path)
                    tab.layout().addWidget(view)
                    tab.layout().itemAt(1).widget().hide()
                except (ImportError, ValueError, RuntimeError) as exc:
                    raise ValueError(f"PDF preview unavailable: {exc}") from exc
            elif QImageReader.imageFormat(str(path)):
                tab = Tab(path, details="Loading image…", root=self.root)
                sizing_reader = QImageReader(str(path))
                sizing_reader.setAutoTransform(True)
                size = sizing_reader.size()
                if size.width() * size.height() > MAX_IMAGE_PIXELS:
                    raise ValueError(f"Image exceeds the configured {MAX_IMAGE_PIXELS:,} pixel preview limit")
                def decode():
                    reader = QImageReader(str(path))
                    reader.setAutoTransform(True)
                    image = reader.read()
                    self.bridge.result.emit(("image", path, image, reader.errorString()))
                self.executor.submit(decode)
            else:
                loaded = read_text(path)
                tab = Tab(path, loaded, markdown=path.suffix.casefold() in (".md", ".markdown"),
                          root=self.root, defer_enhancements=defer_enhancements)
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
        index = self.tabs.addTab(tab, path.name)
        self.tabs.setTabToolTip(index, str(path.parent))
        self.tabs.setCurrentIndex(index)
        if tab.editor:
            tab.editor.viNavigationEscapePressed.connect(
                lambda t=tab: self._focus_tree_from_editor(t)
            )
            if isinstance(tab.editor, MarkdownEditor):
                tab.editor.setProperty("folderNavigatorMarkdown", True)
                tab.editor.installEventFilter(self)
                tab.editor.headingPickerRequested.connect(
                    lambda _point, _prefer_above, t=tab: self._show_heading_picker(t)
                )
            tab.editor.document().modificationChanged.connect(lambda dirty, t=tab: self._modified(t, dirty))
            # Markdown's initial display formatting can toggle Qt's modified
            # flag after the file is loaded. Clear only formatting-only changes;
            # never clear the flag once the text differs from disk.
            for delay in (0, 100):
                QTimer.singleShot(delay, lambda t=tab: self._clear_initial_formatting_dirty(t))
            if line:
                self._reveal_editor_line(tab, line)
        self.recent.insert(0, path)
        self.recent = list(dict.fromkeys(self.recent))[:100]
        self._watch_files()
        self._update_welcome()
        self._schedule_markdown_preview(tab if defer_enhancements else None)

    def _schedule_markdown_preview(self, tab, *, immediate=False):
        self.markdown_preview_timer.stop()
        self.pending_markdown_preview = None
        if not tab or not tab.editor:
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
                or not tab.editor or not tab.loaded or tab.dirty):
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
            tab.editor.document().setModified(False)
            tab.setProperty("folderMarkdownRendered", True)
        finally:
            tab.setProperty("folderMarkdownRendering", False)

    @staticmethod
    def _clear_initial_formatting_dirty(tab):
        try:
            if (tab.editor and tab.loaded
                    and tab.text_for_save() == tab.loaded.text
                    and tab.editor.document().isModified()):
                tab.editor.document().setModified(False)
        except RuntimeError:
            # A fast preview replacement may delete the tab before this queued
            # formatting-only dirty-state cleanup runs.
            pass

    def _focus_tree_from_editor(self, tab):
        """Return vi navigation to the file that owns the focused editor."""
        if tab is self.active_tab():
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

    def eventFilter(self, obj, event):  # type: ignore[override]
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

    def _modified(self, tab, dirty):
        if tab.property("folderMarkdownRendering"):
            return
        if dirty and tab.markdown and tab.loaded:
            try:
                if tab.text_for_save() == tab.loaded.text:
                    tab.editor.document().setModified(False)
                    return
            except RuntimeError:
                return
        if dirty:
            tab.pinned = True
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, ("● " if dirty else "") + tab.path.name)
            tab.setAccessibleName(f"{tab.path.name}{', unsaved changes' if dirty else ''}")

    def keep_open(self, index):
        if index >= 0 and isinstance(self.tabs.widget(index), Tab):
            self.tabs.widget(index).pinned = True

    def _tab_changed(self, index):
        tab = self.active_tab()
        if tab and not self._cycling_tabs:
            self.mru = [tab.path] + [p for p in self.mru if p != tab.path]
        if hasattr(self, "save_action"):
            self.save_action.setEnabled(bool(tab and tab.editor and not tab.editor.isReadOnly()))

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
            self.tabs.removeTab(index)
            tab.deleteLater()
            self._watch_files()
            self._update_welcome()

    def _update_welcome(self):
        self.welcome.setVisible(self.tabs.count() == 0)
        self.tabs.setVisible(self.tabs.count() > 0)

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
            try:
                changed = atomic_save(tab.path, tab.text_for_save(), tab.loaded)
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
                        disk.text.splitlines(True), tab.text_for_save().splitlines(True),
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
                changed = atomic_save(tab.path, tab.text_for_save(), tab.loaded, overwrite=True)
            tab.loaded.fingerprint = changed
            tab.editor.document().setModified(False)
            tab.show_notice("Saved")
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
        self._persist()
        self.executor.shutdown(wait=False, cancel_futures=True)
        super().closeEvent(event)

    def open_folder(self):
        path = QFileDialog.getExistingDirectory(self, "Open Folder", str(self.root))
        if path:
            try:
                launch(Path(path))
            except (OSError, ValueError) as exc:
                QMessageBox.warning(self, "Could not launch Folder Navigator", f"{exc}\nCheck the installation and try again.")

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
            self.scope = folder
            self.tree.setRootIndex(self.model.index(str(folder)))
            self.filter_label.setText(f"Filtered: {folder.name}  ·  Clear Filter ×")
            self.filter_label.show()
            self.clear_filter_action.setEnabled(True)
            self.statusBar().showMessage(f"Scope: {folder}")

    def clear_filter(self):
        self.scope = self.root
        self.tree.setRootIndex(self.model.index(str(self.root)))
        self.filter_label.hide()
        self.clear_filter_action.setEnabled(False)
        self.statusBar().showMessage(str(self.root))

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
        if not inside(self.scope, path):
            self.clear_filter()
        index = self.model.index(str(path))
        cursor = index.parent()
        while cursor.isValid():
            self.tree.expand(cursor)
            cursor = cursor.parent()
        self.tree.setCurrentIndex(index)
        self.tree.scrollTo(index)
        self.rail.setCurrentIndex(0)
        self.tree.setFocus()

    def _tree_menu(self, point):
        index = self.tree.indexAt(point)
        if not index.isValid():
            return
        path = Path(self.model.filePath(index))
        folder = self.model.isDir(index)
        menu = QMenu(self)
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
        target_directory = path if folder else path.parent
        target_index = index if folder else index.parent()
        menu.addAction("New File", lambda: self._begin_new_file(target_directory, target_index))
        menu.addAction("Remove Bookmark" if str(path) in self._bookmarks() else "Bookmark", lambda: self.toggle_bookmark(path))
        menu.addAction("Reveal in File Manager", lambda: self.reveal(path))
        if not folder:
            menu.addAction("Open in Default Application", lambda: self.system_open(path))
        menu.addAction("Copy Full Path", lambda: QApplication.clipboard().setText(str(path)))
        menu.addAction("Copy Relative Path", lambda: QApplication.clipboard().setText(str(path.relative_to(self.root))))
        menu.addAction("Open Terminal Here", lambda: self.terminal(path if folder else path.parent))
        menu.exec(self.tree.viewport().mapToGlobal(point))

    def _begin_new_file(self, directory, directory_index=None):
        """Show an inline filename editor beneath a folder tree row."""
        self._cancel_new_file()
        directory = Path(directory)
        if not directory.is_dir() or not inside(self.root, directory):
            self.statusBar().showMessage(f"Cannot create a file outside the folder root: {directory}", 8000)
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
        edit.setPlaceholderText("New file name")
        edit.setAccessibleName("New file name")
        edit.setGeometry(x, y, max(180, self.tree.viewport().width() - x - 8), 28)
        edit.setStyleSheet(
            f"border: 2px solid {theme_value('main_window.focus_border.default', '#4A90E2')}; "
            "border-radius: 3px; padding: 2px 6px;"
        )
        edit.returnPressed.connect(self._commit_new_file)
        edit.canceled.connect(self._cancel_new_file)
        self.new_file_directory = directory
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
        if edit is not None:
            edit.hide()
            edit.deleteLater()

    def _commit_new_file(self):
        edit = self.new_file_edit
        directory = self.new_file_directory
        if edit is None or directory is None:
            return
        name = edit.text().strip()
        if not name:
            edit.setFocus()
            return
        if name in {".", ".."} or Path(name).name != name:
            self.statusBar().showMessage("Enter a file name without folder separators", 8000)
            edit.selectAll()
            return
        target = directory / name
        try:
            with target.open("x", encoding="utf-8"):
                pass
        except FileExistsError:
            self.statusBar().showMessage(f"A file named {name} already exists", 8000)
            edit.selectAll()
            return
        except OSError as exc:
            self.statusBar().showMessage(f"Could not create {name}: {exc}", 12000)
            edit.selectAll()
            return
        self._cancel_new_file()
        self.open_file(target, pinned=True)
        tab = self.active_tab()
        if tab and tab.editor:
            tab.editor.setFocus(Qt.OtherFocusReason)

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
        try:
            suffix = path.suffix.casefold()
            if suffix == ".puml":
                from sp.app.ui.plantuml_editor_window import PlantUMLEditorWindow
                window = PlantUMLEditorWindow(str(path), parent=None)
            elif suffix == ".mmd":
                from sp.app.ui.mermaid_editor_window import MermaidEditorWindow
                window = MermaidEditorWindow(str(path), parent=None)
            elif suffix == ".excalidraw":
                from sp.app.ui.excalidraw_window import ExcalidrawWindow
                window = ExcalidrawWindow(str(path), parent=None)
            else:
                return
            window.setWindowFlag(Qt.Window, True)
            window.setWindowFlag(Qt.Tool, False)
            window.setWindowModality(Qt.NonModal)
            self.specialized_editor_windows.append(window)
            window.destroyed.connect(
                lambda _=None, item=window: self.specialized_editor_windows.remove(item)
                if item in self.specialized_editor_windows else None
            )
            window.show()
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Could not open diagram editor",
                f"Could not open {path.name} in its StillPoint editor:\n{exc}",
            )

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
        menu.addAction("Reveal in Folder Tree", lambda: self.reveal_tree(self.tabs.widget(index).path))
        menu.exec(self.tabs.tabBar().mapToGlobal(point))

    def _close_indices(self, indices):
        tabs = [self.tabs.widget(i) for i in indices]
        if not self._review_dirty(tabs):
            return
        for tab in tabs:
            self.tabs.removeTab(self.tabs.indexOf(tab))
            tab.deleteLater()
        self._watch_files()
        self._update_welcome()

    def system_open(self, path):
        if not inside(self.root, path):
            answer = QMessageBox.question(self, "Outside selected folder", f"Open outside target in system application?\n{path}")
            if answer != QMessageBox.Yes:
                return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            self.statusBar().showMessage(f"No default application could open {path}", 12000)

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
        Picker(self).exec()

    def quick_open(self):
        self._warm_catalog()
        Picker(self, quick=True).exec()

    def command_bar(self):
        self.command_palette.show_actions(self.commands)

    def _git_ignored(self, path):
        try:
            result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", str(path)],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=1)
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

    def _catalog_batch(self, batch, generation):
        ignored = set()
        try:
            result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-z", "--stdin"],
                                    input=b"".join(os.fsencode(str(path)) + b"\0" for path in batch),
                                    capture_output=True, timeout=5)
            ignored = {Path(os.fsdecode(name)) for name in result.stdout.split(b"\0") if name}
        except (OSError, subprocess.TimeoutExpired):
            pass
        if self.catalog_db is not None:
            self.catalog_db.upsert_paths(batch, ignored, generation)
        self.bridge.result.emit(("catalog", batch, ignored))

    def _refresh_quick_pickers(self):
        for widget in QApplication.topLevelWidgets():
            if isinstance(widget, Picker) and widget.window is self and widget.quick:
                widget.refresh()

    def _warm_catalog(self):
        if self.catalog_running or self.catalog_warmed:
            return
        self.catalog_warmed = True
        self.catalog_running = True
        self._update_index_notice()
        def job():
            generation = None
            scan_skipped = []
            def skipped_directory(path, entry_count):
                scan_skipped.append((path, entry_count))
                self.bridge.result.emit(("catalog-skip", path, entry_count))
            try:
                if self.catalog_db is not None:
                    generation = self.catalog_db.begin_refresh()
                batch = []
                for path in walk_files(
                    self.root, self.root, hidden=True,
                    canceled=self.catalog_cancel.is_set,
                    max_directory_entries=MAX_DIRECTORY_ENTRIES,
                    skipped=skipped_directory,
                ):
                    batch.append(path)
                    if len(batch) >= 500:
                        self._catalog_batch(batch, generation)
                        batch = []
                if batch:
                    self._catalog_batch(batch, generation)
                complete = not self.catalog_cancel.is_set()
                if self.catalog_db is not None and generation is not None:
                    self.catalog_db.record_skipped_directories(
                        scan_skipped, generation
                    )
                    self.catalog_db.finish_refresh(generation, complete=complete)
                self.bridge.finished.emit(("catalog", complete))
            except (OSError, sqlite3.Error) as exc:
                if self.catalog_db is not None and generation is not None:
                    try:
                        self.catalog_db.finish_refresh(generation, complete=False)
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
        self.search_progress.setText(f"Searching {self.scope}…")
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

    def _search_result(self, payload):
        if payload[0] == "image":
            _, path, pixels, error = payload
            index = self._index_for(path)
            if index >= 0:
                tab = self.tabs.widget(index)
                if pixels.isNull():
                    tab.show_notice(f"Image preview failed: {error}")
                else:
                    tab.layout().itemAt(1).widget().hide()
                    tab.layout().addWidget(ImageView(path, pixels))
            return
        if payload[0] == "catalog":
            if self.catalog_db is None:
                self.catalog.update(payload[1])
                self.ignored_paths.update(payload[2])
            self.catalog_refresh_timer.start(250)
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
        if payload[0] == "catalog":
            self.catalog_running = False
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
        elif payload[0] == "catalog-error":
            self.catalog_running = False
            self.catalog_warmed = False
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

    def _watch_files(self):
        desired = {str(self.root)} | {str(t.path) for t in self.all_tabs() if t.path.exists()}
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
        self.catalog = {p for p in self.catalog if p.exists() and inside(self.root, p)}
        self.ignored_paths.intersection_update(self.catalog)
        self.catalog_warmed = False
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
