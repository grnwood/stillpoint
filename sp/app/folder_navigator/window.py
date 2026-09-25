"""Independent Qt window for browsing one ordinary filesystem root."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
import json
import mimetypes
import os
import re
import subprocess
import sys
import threading

from PySide6.QtCore import QDir, QFileInfo, QFileSystemWatcher, QObject, Qt, QTimer, QUrl, Signal, QEvent
from PySide6.QtGui import QAction, QDesktopServices, QImageReader, QKeySequence, QShortcut, QTextCursor, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QDialogButtonBox, QFileDialog,
    QFileIconProvider, QFileSystemModel, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMenu, QMessageBox, QPushButton, QSplitter, QTabWidget,
    QTabBar, QTextEdit, QPlainTextEdit, QTreeView, QVBoxLayout, QWidget, QScrollArea, QSpinBox)

from .core import (MAX_CONCURRENT_WORK, MAX_EDIT_BYTES, MAX_IMAGE_PIXELS, MAX_RESULTS, MAX_SEARCH_BYTES,
    ConflictError, TextFile, atomic_save, content_matches, fingerprint, fuzzy_score, inside,
    read_text, walk_files)
from .launch import launch


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
    escapePressed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False
        self.setHeaderHidden(True)
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
                self.openFile.emit(model.filePath(index), True)
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event):
        index = self.indexAt(event.pos())
        if index.isValid() and event.modifiers() & (Qt.MetaModifier if sys.platform == "darwin" else Qt.ControlModifier):
            if not self.model().isDir(index):
                self.openFile.emit(self.model().filePath(index), True)
                return
        super().mousePressEvent(event)


class Editor(QPlainTextEdit):
    folderPickerRequested = Signal()

    def __init__(self, markdown=False):
        super().__init__()
        self.markdown = markdown
        self.vi_enabled = False
        self.setAccessibleName("Markdown editor" if markdown else "Plain text editor")

    def keyPressEvent(self, event):
        if self.vi_enabled and event.key() == Qt.Key_V and event.modifiers() == Qt.NoModifier:
            self.folderPickerRequested.emit()
            return
        super().keyPressEvent(event)


class Tab(QWidget):
    def __init__(self, path: Path, loaded: TextFile | None = None, *, markdown=False, details=""):
        super().__init__()
        self.path = path
        self.loaded = loaded
        self.pinned = False
        self.detached = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.notice = QLabel("")
        self.notice.setAccessibleName("File status")
        self.notice.hide()
        layout.addWidget(self.notice)
        if loaded:
            self.editor = Editor(markdown)
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

    def show_notice(self, message):
        self.notice.setText(message)
        self.notice.show()

    def _show_find(self):
        self.find_bar.show()
        self.find_query.setFocus()

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
        self.actual()

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
        self.scope.setText(f"Scope: {scope}  ·  {'Including' if self.include.isChecked() else 'Excluding'} hidden and ignored files")
        query = self.query.text().strip()
        opened = {tab.path for tab in self.window.all_tabs()}
        candidates = set(self.window.catalog)
        candidates.update(opened)
        ranked = []
        for path in candidates:
            if not path.is_file() or not inside(self.window.root, path):
                continue
            if not inside(scope, path):
                continue
            if not self.include.isChecked() and self.window.excluded(path):
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
            item = QListWidgetItem("No files in the current scope and exclusion settings")
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
        self.root = root.resolve(strict=True)
        self.scope = self.root
        self.catalog: set[Path] = set()
        self.ignored_paths: set[Path] = set()
        self.catalog_cancel = threading.Event()
        self.recent: list[Path] = []
        self.executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT_WORK, thread_name_prefix="folder-navigator")
        self.bridge = Bridge(self)
        self.search_cancel = threading.Event()
        self.search_generation = 0
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
        self.tree.expanded.connect(lambda index: self._remember_expansion(index, True))
        self.tree.collapsed.connect(lambda index: self._remember_expansion(index, False))
        self.tree.selectionModel().currentChanged.connect(self._tree_selected)
        self.tree.openFile.connect(lambda path, pin: self.open_file(Path(path), pinned=pin))
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
        self.rail.addTab(self.tree, "Folder")
        self.rail.addTab(search_page, "Search")
        self.tabs = QTabWidget()
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
        self.statusBar().showMessage(str(self.root))
        self.watcher = QFileSystemWatcher(self)
        self.watcher.addPath(str(self.root))
        self.watcher.directoryChanged.connect(lambda _: self._schedule_refresh())
        self.watcher.fileChanged.connect(lambda _: self._schedule_refresh())
        self.refresh_timer = QTimer(self)
        self.refresh_timer.setSingleShot(True)
        self.refresh_timer.timeout.connect(self._refresh_disk)
        self._make_actions()
        self._render_bookmarks()
        self._restore()
        self.model.directoryLoaded.connect(lambda _: self._schedule_refresh())
        self.model.rowsInserted.connect(lambda parent, first, last: self._catalog_rows(parent, first, last))
        QTimer.singleShot(150, self._warm_catalog)

    def _load_settings(self):
        try:
            return json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _persist(self):
        self.state.update(splitter=self.splitter.sizes(), rail=self.rail.currentIndex(),
                          pinned=[str(t.path) for t in self.all_tabs() if t.pinned],
                          active=str(self.active_tab().path) if self.active_tab() and self.active_tab().pinned else None,
                          geometry=bytes(self.saveGeometry().toBase64()).decode("ascii"))
        temporary = self.settings_path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.settings, indent=2), encoding="utf-8")
        temporary.replace(self.settings_path)

    def _restore(self):
        from PySide6.QtCore import QByteArray
        if self.state.get("geometry"):
            self.restoreGeometry(QByteArray.fromBase64(self.state["geometry"].encode("ascii")))
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
        for row in range(first, last + 1):
            index = self.model.index(row, 0, parent)
            if not self.model.isDir(index):
                path = Path(self.model.filePath(index))
                if inside(self.root, path):
                    self.catalog.add(path)

    def _make_actions(self):
        file_menu = self.menuBar().addMenu("&File")
        view_menu = self.menuBar().addMenu("&View")
        go_menu = self.menuBar().addMenu("&Go")
        self.commands = []
        def add(menu, title, callback, shortcut=None):
            action = QAction(title, self)
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
        add(go_menu, "Folder", lambda: (self.rail.setCurrentIndex(0), self.tree.setFocus()))
        add(go_menu, "Search", lambda: (self.rail.setCurrentIndex(1), self.search_input.setFocus()))
        add(go_menu, "Editor", lambda: self.active_tab().editor.setFocus() if self.active_tab() and self.active_tab().editor else None)
        add(go_menu, "Quick Open", self.quick_open, "Ctrl+P" if sys.platform != "darwin" else "Meta+P")
        add(go_menu, "Folder Picker", self.folder_picker, "Ctrl+Alt+V")
        add(go_menu, "Next Tab", lambda: self.cycle_tab(1), "Ctrl+Tab")
        add(go_menu, "Previous Tab", lambda: self.cycle_tab(-1), "Ctrl+Shift+Tab")
        add(go_menu, "Command Bar", self.command_bar, "Alt+G")
        add(go_menu, "Clear Filter", self.clear_filter)
        close_shortcut = QShortcut(QKeySequence.Close, self)
        close_shortcut.activated.connect(lambda: self.close_tab(self.tabs.currentIndex()))
        self.mru = []

    def active_tab(self):
        widget = self.tabs.currentWidget()
        return widget if isinstance(widget, Tab) else None

    def all_tabs(self):
        return [self.tabs.widget(i) for i in range(self.tabs.count())]

    def _index_for(self, path):
        return next((i for i, tab in enumerate(self.all_tabs()) if tab.path == path), -1)

    def _tree_selected(self, current, previous):
        if current.isValid() and not self.model.isDir(current):
            self.open_file(Path(self.model.filePath(current)))

    def open_file(self, path: Path, pinned=False, line=None):
        if not path.is_file() or not inside(self.root, path):
            self.statusBar().showMessage(f"Unavailable or outside root: {path}", 12000)
            return
        index = self._index_for(path)
        if index >= 0:
            if pinned:
                self.keep_open(index)
            self.tabs.setCurrentIndex(index)
            return
        preview = next((i for i, tab in enumerate(self.all_tabs()) if not tab.pinned and not tab.dirty), -1)
        if preview >= 0:
            self.tabs.removeTab(preview)
        try:
            if path.suffix.casefold() == ".pdf":
                tab = Tab(path, details="Loading PDF…")
                try:
                    view = pdf_view(path)
                    tab.layout().addWidget(view)
                    tab.layout().itemAt(1).widget().hide()
                except (ImportError, ValueError, RuntimeError) as exc:
                    raise ValueError(f"PDF preview unavailable: {exc}") from exc
            elif QImageReader.imageFormat(str(path)):
                tab = Tab(path, details="Loading image…")
                size = QImageReader(str(path)).size()
                if size.width() * size.height() > MAX_IMAGE_PIXELS:
                    raise ValueError(f"Image exceeds the configured {MAX_IMAGE_PIXELS:,} pixel preview limit")
                def decode():
                    reader = QImageReader(str(path))
                    image = reader.read()
                    self.bridge.result.emit(("image", path, image, reader.errorString()))
                self.executor.submit(decode)
            else:
                loaded = read_text(path)
                tab = Tab(path, loaded, markdown=path.suffix.casefold() in (".md", ".markdown"))
        except (OSError, ValueError) as exc:
            info = path.stat()
            details = (f"{path.name}\nType: {mimetypes.guess_type(path.name)[0] or 'Unknown'}\n"
                       f"Size: {info.st_size:,} bytes\nModified: {datetime.fromtimestamp(info.st_mtime)}\n"
                       f"Path: {path}\n\n{exc}")
            tab = Tab(path, details=details)
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
            tab.editor.vi_enabled = self.tree.vi_enabled
            tab.editor.folderPickerRequested.connect(self.folder_picker)
            tab.editor.document().modificationChanged.connect(lambda dirty, t=tab: self._modified(t, dirty))
            if line:
                cursor = tab.editor.textCursor()
                cursor.movePosition(QTextCursor.Start)
                cursor.movePosition(QTextCursor.Down, QTextCursor.MoveAnchor, line - 1)
                tab.editor.setTextCursor(cursor)
        self.recent.insert(0, path)
        self.recent = list(dict.fromkeys(self.recent))[:100]
        self._watch_files()
        self._update_welcome()

    def _modified(self, tab, dirty):
        if dirty:
            tab.pinned = True
        index = self.tabs.indexOf(tab)
        if index >= 0:
            self.tabs.setTabText(index, ("● " if dirty else "") + tab.path.name)
            self.tabs.tabBar().setTabAccessibleName(index, f"{tab.path.name}{', unsaved changes' if dirty else ''}")

    def keep_open(self, index):
        if index >= 0 and isinstance(self.tabs.widget(index), Tab):
            self.tabs.widget(index).pinned = True

    def _tab_changed(self, index):
        tab = self.active_tab()
        if tab:
            self.mru = [tab.path] + [p for p in self.mru if p != tab.path]
        if hasattr(self, "save_action"):
            self.save_action.setEnabled(bool(tab and tab.editor and not tab.editor.isReadOnly()))

    def cycle_tab(self, delta):
        current = self.active_tab()
        paths = [p for p in self.mru if self._index_for(p) >= 0]
        if len(paths) < 2:
            return
        target = paths[(paths.index(current.path) + delta) % len(paths)] if current and current.path in paths else paths[0]
        self.tabs.setCurrentIndex(self._index_for(target))
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
                changed = atomic_save(tab.path, tab.editor.toPlainText(), tab.loaded)
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
                    diff.setPlainText("".join(difflib.unified_diff(disk.text.splitlines(True), tab.editor.toPlainText().splitlines(True), fromfile="Disk", tofile="Buffer")))
                    layout.addWidget(diff)
                    review.exec()
                    return False
                if dialog.clickedButton() == reload:
                    tab.loaded = read_text(tab.path)
                    tab.editor.setPlainText(tab.loaded.text)
                    tab.editor.document().setModified(False)
                    return True
                if dialog.clickedButton() != overwrite:
                    return False
                changed = atomic_save(tab.path, tab.editor.toPlainText(), tab.loaded, overwrite=True)
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
            self.filter_label.setText(f"Filtered: {folder.name}  ·  Clear Filter")
            self.filter_label.show()
            self.statusBar().showMessage(f"Scope: {folder}")

    def clear_filter(self):
        self.scope = self.root
        self.tree.setRootIndex(self.model.index(str(self.root)))
        self.filter_label.hide()
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
        menu.addAction("Remove Bookmark" if str(path) in self._bookmarks() else "Bookmark", lambda: self.toggle_bookmark(path))
        menu.addAction("Reveal in File Manager", lambda: self.reveal(path))
        if not folder:
            menu.addAction("Open in Default Application", lambda: self.system_open(path))
        menu.addAction("Copy Full Path", lambda: QApplication.clipboard().setText(str(path)))
        menu.addAction("Copy Relative Path", lambda: QApplication.clipboard().setText(str(path.relative_to(self.root))))
        menu.addAction("Open Terminal Here", lambda: self.terminal(path if folder else path.parent))
        menu.exec(self.tree.viewport().mapToGlobal(point))

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
        dialog = QDialog(self)
        dialog.setWindowTitle("Command Bar")
        dialog.resize(540, 400)
        layout = QVBoxLayout(dialog)
        query = QLineEdit()
        query.setPlaceholderText("Type a command")
        matches = QListWidget()
        layout.addWidget(query)
        layout.addWidget(matches)
        def update():
            matches.clear()
            for action in self.commands:
                if action.isEnabled() and query.text().casefold() in action.text().casefold():
                    item = QListWidgetItem(action.text().replace("&", ""))
                    item.setData(Qt.UserRole, action)
                    matches.addItem(item)
            matches.setCurrentRow(0)
        def activate():
            if matches.currentItem():
                action = matches.currentItem().data(Qt.UserRole)
                dialog.accept()
                action.trigger()
        query.textChanged.connect(update)
        query.returnPressed.connect(activate)
        matches.itemActivated.connect(lambda _: activate())
        update()
        dialog.exec()

    def _git_ignored(self, path):
        try:
            result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-q", str(path)],
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=1)
            return result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False

    def excluded(self, path):
        return any(part.startswith(".") for part in path.relative_to(self.root).parts) or path in self.ignored_paths

    def _catalog_batch(self, batch):
        ignored = set()
        try:
            result = subprocess.run(["git", "-C", str(self.root), "check-ignore", "-z", "--stdin"],
                                    input=b"".join(os.fsencode(str(path)) + b"\0" for path in batch),
                                    capture_output=True, timeout=5)
            ignored = {Path(os.fsdecode(name)) for name in result.stdout.split(b"\0") if name}
        except (OSError, subprocess.TimeoutExpired):
            pass
        self.bridge.result.emit(("catalog", batch, ignored))

    def _warm_catalog(self):
        if getattr(self, "catalog_running", False):
            return
        self.catalog_running = True
        def job():
            batch = []
            for path in walk_files(self.root, self.root, hidden=True, canceled=self.catalog_cancel.is_set):
                batch.append(path)
                if len(batch) >= 100:
                    self._catalog_batch(batch)
                    batch = []
            if batch:
                self._catalog_batch(batch)
            self.bridge.finished.emit(("catalog", None))
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
        self._last_search_path = None
        self.search_progress.setText(f"Searching {self.scope}…")
        scope = self.scope
        case, whole, regex = self.search_case.isChecked(), self.search_word.isChecked(), self.search_regex.isChecked()
        ignore = not self.search_ignored.isChecked()
        def job():
            count = skipped = 0
            try:
                name_expression = re.compile((rf"\b(?:{query if regex else re.escape(query)})\b" if whole
                                              else query if regex else re.escape(query)),
                                             0 if case else re.IGNORECASE)
                for path in walk_files(self.root, scope, ignore=self._git_ignored if ignore else None, canceled=canceled.is_set):
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
                self.bridge.finished.emit(("search", generation, count, skipped, canceled.is_set(), count >= MAX_RESULTS))
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
            self.catalog.update(payload[1])
            self.ignored_paths.update(payload[2])
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, Picker) and widget.window is self and widget.quick:
                    widget.refresh()
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

    def _search_finished(self, payload):
        if payload[0] == "catalog":
            self.catalog_running = False
        elif payload[0] == "search-error":
            if payload[1] == self.search_generation:
                self.search_progress.setText(f"Search failed: {payload[2]}")
        else:
            _, generation, count, skipped, canceled, limited = payload
            if generation != self.search_generation:
                return
            self.search_progress.setText(f"{'Canceled' if canceled else 'Complete'}: {count} results, {skipped} skipped" +
                                         (f" (limit {MAX_RESULTS} reached)" if limited else ""))

    def _open_search_item(self, item):
        path, line = item.data(Qt.UserRole)
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
                        tab.editor.setPlainText(tab.loaded.text)
                        cursor.setPosition(min(position, len(tab.loaded.text)))
                        tab.editor.setTextCursor(cursor)
                        tab.show_notice("File reloaded after an external change")
                    except (OSError, ValueError) as exc:
                        tab.show_notice(f"Reload failed: {exc}")
        self.catalog = {p for p in self.catalog if p.exists() and inside(self.root, p)}
        self.ignored_paths.intersection_update(self.catalog)
        self._watch_files()


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    app = QApplication.instance() or QApplication([sys.argv[0]])
    root = Path(args[0]).expanduser() if args else None
    if root is None or not root.is_dir():
        selected = QFileDialog.getExistingDirectory(None, "Choose a folder for Folder Navigator")
        if not selected:
            return 0
        root = Path(selected)
    window = Window(root)
    window.show()
    return app.exec()
