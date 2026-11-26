from __future__ import annotations

import contextlib
import hashlib
import itertools
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Optional, Callable, Any
from urllib.parse import urlparse

import httpx

from PySide6.QtCore import Qt, QUrl, QSize, QMimeData, Signal, QEvent, QSignalBlocker, QTimer, QSettings
from PySide6.QtGui import QIcon, QDesktopServices, QDrag, QPainter, QPixmap, QPalette, QColor
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QToolButton,
    QFileDialog,
    QFileIconProvider,
    QMenu,
    QInputDialog,
    QDialog,
    QMessageBox,
    QLineEdit,
    QComboBox,
    QLabel,
)

from .page_load_logger import PAGE_LOGGING_ENABLED
from .theme import apply_menu_theme
from .canvas_navigation import install_primary_wheel_zoom
from .diagram_editor_icons import get_diagram_editor_icon
from .attachment_thumbnails import ThumbnailLoader, ThumbnailSource
from sp.app.external_launch import open_path_in_system
from sp.logging_flags import log_enabled

_DETAILED_LOGGING = log_enabled("attachments_media")
_GROUP_ROLE = Qt.UserRole + 1
_SIZE_ROLE = Qt.UserRole + 2
_TYPE_ROLE = Qt.UserRole + 3
_SORT_ROLE = Qt.UserRole + 4
_THUMB_SOURCE_ROLE = Qt.UserRole + 5
_THUMB_KEY_ROLE = Qt.UserRole + 6
_FILE_ICON_ROLE = Qt.UserRole + 7
_ATTACHMENT_TYPES = {
    "Images": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".tif", ".tiff", ".ico", ".heic", ".avif"},
    "Diagrams": {".puml", ".plantuml", ".mmd", ".mermaid", ".excalidraw", ".drawio", ".dot"},
    "Documents": {".pdf", ".txt", ".md", ".markdown", ".doc", ".docx", ".odt", ".rtf", ".xls", ".xlsx", ".ods", ".csv", ".tsv", ".ppt", ".pptx", ".odp", ".epub"},
    "Audio": {".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".aiff", ".opus"},
    "Video": {".mp4", ".mov", ".webm", ".avi", ".mkv", ".m4v", ".wmv"},
    "Archives": {".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".tgz"},
    "Other": set(),
}


def _attachment_type(path: str) -> str:
    suffix = Path(path).suffix.casefold()
    return next((name for name, suffixes in _ATTACHMENT_TYPES.items() if suffix in suffixes), "Other")


def _item_path(item) -> str:
    data = item.data(Qt.UserRole)
    return str(data.get("path", "")) if isinstance(data, dict) else str(data or "")


def _format_size(size: int | None) -> str:
    if size is None:
        return "—"
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} B" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024


class AttachmentTableItem(QTableWidgetItem):
    def setHidden(self, hidden):
        self.tableWidget().setRowHidden(self.row(), hidden)

    def isHidden(self):
        return self.tableWidget().isRowHidden(self.row())

    def __lt__(self, other):
        table = self.tableWidget()
        if table.grouped:
            groups = list(_ATTACHMENT_TYPES)
            left = (groups.index(self.data(_TYPE_ROLE)), not bool(self.data(_GROUP_ROLE)))
            right = (groups.index(other.data(_TYPE_ROLE)), not bool(other.data(_GROUP_ROLE)))
            if left != right:
                # Keep headings before their files and groups in the same order
                # while sorting the files within each group in either direction.
                return left < right if table.horizontalHeader().sortIndicatorOrder() == Qt.AscendingOrder else left > right
        return self.data(_SORT_ROLE) < other.data(_SORT_ROLE)


class AttachmentsListWidget(QTableWidget):
    """Attachment details table with row-based file actions and drag data."""

    deleteRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(0, 3, parent)
        self.grouped = False
        self.setHorizontalHeaderLabels(["Filename", "Type", "Size"])
        self.verticalHeader().hide()
        self.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.horizontalHeader().setMinimumSectionSize(40)
        self.setColumnWidth(1, 75)
        self.setColumnWidth(2, 75)
        self.setShowGrid(False)
        self.setWordWrap(False)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.sortItems(0, Qt.AscendingOrder)
        self.setSortingEnabled(True)

    # Keep file actions independent of the number of displayed detail columns.
    def count(self):
        return self.rowCount()

    def clear(self):
        self.setRowCount(0)

    def item(self, row, column=0):
        return super().item(row, column)

    def selectedItems(self):
        return [self.item(row) for row in sorted({index.row() for index in self.selectedIndexes()})]

    def setCurrentRow(self, row):
        self.setCurrentCell(row, 0)

    def takeItem(self, row, column=None):
        if column is not None:
            return super().takeItem(row, column)
        item = super().takeItem(row, 0)
        self.removeRow(row)
        return item

    def addItem(self, item):
        sorting = self.isSortingEnabled()
        self.setSortingEnabled(False)
        row = self.rowCount()
        self.insertRow(row)
        path = _item_path(item)
        group = item.data(_GROUP_ROLE) or _attachment_type(path)
        suffix = Path(path).suffix.lstrip(".").upper()
        type_name = {"MMD": "Mermaid", "MERMAID": "Mermaid", "PUML": "PlantUML", "PLANTUML": "PlantUML",
                     "EXCALIDRAW": "Excalidraw"}.get(suffix, suffix or "File")
        size = item.data(_SIZE_ROLE)
        cells = [item, AttachmentTableItem(type_name if path else ""),
                 AttachmentTableItem(_format_size(size) if path else "")]
        for column, cell in enumerate(cells):
            cell.setData(Qt.UserRole, item.data(Qt.UserRole))
            cell.setData(_TYPE_ROLE, group)
            cell.setData(_GROUP_ROLE, item.data(_GROUP_ROLE))
            cell.setData(_SORT_ROLE, (size if size is not None else -1) if column == 2 else cell.text().casefold())
            cell.setFlags(item.flags())
            self.setItem(row, column, cell)
        cells[2].setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.setRowHeight(row, max(self.fontMetrics().height() + 10, self.iconSize().height() + 6)
                          if path else self.fontMetrics().height() + 12)
        self.setSortingEnabled(sorting)

    def keyPressEvent(self, event):  # type: ignore[override]
        delete_key = event.key() == Qt.Key_Delete or (
            sys.platform == "darwin" and event.key() == Qt.Key_Backspace
        )
        if delete_key and event.modifiers() == Qt.NoModifier:
            self.deleteRequested.emit()
            event.accept()
            return
        super().keyPressEvent(event)
    
    def mimeData(self, items):
        """Create mime data with file URLs for dragging."""
        mime = QMimeData()
        urls = []
        
        seen = set()
        for item in items:
            file_path_str = item.data(Qt.UserRole)
            if isinstance(file_path_str, str) and file_path_str and file_path_str not in seen:
                seen.add(file_path_str)
                url = QUrl.fromLocalFile(file_path_str)
                urls.append(url)
        
        if urls:
            mime.setUrls(urls)
        
        return mime


class AttachmentsPanel(QWidget):
    """Panel showing attachments (files) in the current page's folder."""
    
    # Signal emitted when user wants to open a .puml file in the PlantUML editor
    plantumlEditorRequested = Signal(object)  # file_path or payload
    # Signal emitted when user wants to open a Mermaid diagram file in the Mermaid editor
    mermaidEditorRequested = Signal(object)  # file_path or payload
    # Signal emitted when user wants to open an Excalidraw file in the Excalidraw editor
    excalidrawEditorRequested = Signal(object)  # file_path or payload
    # Signal emitted when local attachment files changed on disk.
    attachmentsModified = Signal(str)
    # Emitted only after the current page's attachment listing completed.
    countReady = Signal(object, int)  # page path, file count before filename/type filters
    hiddenFilesVisibilityChanged = Signal(bool)

    def __init__(
        self,
        parent=None,
        api_client: Optional[httpx.Client] = None,
        auth_prompt: Optional[Callable[[], bool]] = None,
    ) -> None:
        super().__init__(parent)
        self.vault_root: Optional[Path] = None
        self._page_attachment_cache: dict[str, set[str]] = {}
        self._http_client = api_client
        self._remote_mode = False
        self._api_base: Optional[str] = None
        self._auth_prompt = auth_prompt
        self._remote_cache_root: Optional[Path] = None
        self._remote_cache_key: Optional[str] = None
        self._remote_vault_root: Optional[Path] = None
        self._voice_dialog = None
        self._screen_dialog = None
        self._audio_player = None
        self._video_player = None
        
        self.current_page_path: Optional[Path] = None
        self.zoom_level = 0  # 0=list, 1=small icons, 2=medium icons, 3=large icons
        self._show_hidden_files = False
        self._thumbnail_settings = QSettings("StillPoint", "StillPoint")
        self._show_thumbnails = self._thumbnail_settings.value("attachments/showThumbnails", False, type=bool)
        self._thumbnail_loader = ThumbnailLoader(self)
        self._thumbnail_loader.ready.connect(self._thumbnail_ready)
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.setInterval(250)
        self._thumbnail_timer.timeout.connect(self._load_visible_thumbnails)
        self._cached_thumbnail_timer = QTimer(self)
        self._cached_thumbnail_timer.setSingleShot(True)
        self._cached_thumbnail_timer.setInterval(0)
        self._cached_thumbnail_timer.timeout.connect(lambda: self._load_visible_thumbnails(cached_only=True))
        self.icon_provider = QFileIconProvider()
        
        # Create toolbar with folder, refresh, and zoom buttons
        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(5, 5, 5, 5)
        
        self.add_button = QToolButton()
        self.add_button.setText("+")
        self.add_button.setToolTip("Add attachments")
        self.add_button.clicked.connect(self._add_attachments)
        self.add_button.setEnabled(False)
        toolbar.addWidget(self.add_button)

        self.record_button = QToolButton()
        self._set_toolbar_icon(self.record_button, "microphone.svg", QColor("#e53935"))
        self.record_button.setText("Record")
        self.record_button.setToolTip("Record a voice note")
        self.record_button.setAccessibleName("Record a voice note")
        self.record_button.setEnabled(False)
        self.record_button.clicked.connect(self._record_voice_note)
        toolbar.addWidget(self.record_button)

        self.screenshot_button = QToolButton()
        self._set_toolbar_icon(self.screenshot_button, "screenshot.svg")
        self.screenshot_button.setToolTip("Capture and annotate a screen region")
        self.screenshot_button.setAccessibleName("Capture screen region")
        self.screenshot_button.setEnabled(False)
        self.screenshot_button.clicked.connect(self._capture_screen)
        toolbar.addWidget(self.screenshot_button)

        self.screen_record_button = QToolButton()
        self._set_toolbar_icon(self.screen_record_button, "video-record.svg", QColor("#e53935"))
        self.screen_record_button.setToolTip("Record screen")
        self.screen_record_button.setAccessibleName("Record screen")
        self.screen_record_button.setEnabled(False)
        self.screen_record_button.clicked.connect(self._record_screen)
        toolbar.addWidget(self.screen_record_button)

        self.remove_button = QToolButton()
        self.remove_button.setText("−")
        self.remove_button.setToolTip("Remove selected attachments")
        self.remove_button.clicked.connect(self._remove_selected_attachments)
        self.remove_button.setEnabled(False)
        toolbar.addWidget(self.remove_button)

        self.open_folder_button = QToolButton()
        folder_icon = None
        style = QApplication.instance().style() if QApplication.instance() else None
        if style:
            folder_icon = style.standardIcon(QStyle.SP_DirIcon)
        if folder_icon:
            self.open_folder_button.setIcon(folder_icon)
        else:
            self.open_folder_button.setText("📁")
        self.open_folder_button.setToolTip("Open containing folder")
        self.open_folder_button.setAccessibleName("Open containing folder")
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_folder_button.setEnabled(False)
        toolbar.addWidget(self.open_folder_button)
        
        self.refresh_button = QToolButton()
        self.refresh_button.setText("↺")  # Refresh icon
        self.refresh_button.setToolTip("Refresh attachments list")
        self.refresh_button.clicked.connect(self._refresh_attachments)
        self.refresh_button.setEnabled(False)
        toolbar.addWidget(self.refresh_button)
        
        toolbar.addStretch()

        self.thumbnails_button = QToolButton()
        self.thumbnails_button.setCheckable(True)
        self.thumbnails_button.setAccessibleName("Show image thumbnails")
        self.thumbnails_button.setIconSize(QSize(18, 18))
        self.thumbnails_button.setChecked(self._show_thumbnails)
        self.thumbnails_button.toggled.connect(self._toggle_thumbnails)
        self._update_thumbnails_icon()
        toolbar.addWidget(self.thumbnails_button)

        self.hidden_files_button = QToolButton()
        self.hidden_files_button.setCheckable(True)
        self.hidden_files_button.setAccessibleName("Show hidden attachment files")
        self.hidden_files_button.setIconSize(QSize(18, 18))
        self.hidden_files_button.toggled.connect(self._toggle_hidden_files)
        self._update_hidden_files_icon()
        toolbar.addWidget(self.hidden_files_button)
        
        self.zoom_out_button = QToolButton()
        self.zoom_out_button.setText("−")  # Minus
        self.zoom_out_button.setToolTip("Zoom out (smaller icons)")
        self.zoom_out_button.clicked.connect(self._zoom_out)
        self.zoom_out_button.setEnabled(True)
        toolbar.addWidget(self.zoom_out_button)
        
        self.zoom_in_button = QToolButton()
        self.zoom_in_button.setText("+")  # Plus
        self.zoom_in_button.setToolTip("Zoom in (larger icons)")
        self.zoom_in_button.clicked.connect(self._zoom_in)
        self.zoom_in_button.setEnabled(True)
        toolbar.addWidget(self.zoom_in_button)
        
        # Create list widget for attachments
        self.attachments_list = AttachmentsListWidget()
        self.attachments_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.attachments_list.deleteRequested.connect(self._remove_selected_attachments)
        self.attachments_list.itemDoubleClicked.connect(self._open_attachment)
        self.attachments_list.setDragEnabled(True)
        self.attachments_list.setDragDropMode(QAbstractItemView.DragOnly)
        self.attachments_list.itemSelectionChanged.connect(self._update_remove_button_state)
        self.attachments_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.attachments_list.customContextMenuRequested.connect(self._on_attachments_context_menu)
        self.attachments_list.viewport().installEventFilter(self)
        self.attachments_list.installEventFilter(self)
        self.attachments_list.verticalScrollBar().valueChanged.connect(self._schedule_thumbnails)

        filters = QHBoxLayout()
        filters.setContentsMargins(5, 0, 5, 0)
        self.filename_filter = QLineEdit()
        self.filename_filter.setPlaceholderText("Filter filenames…")
        self.filename_filter.setAccessibleName("Filter attachments by filename")
        self.filename_filter.setClearButtonEnabled(True)
        self.filename_filter.setMinimumWidth(0)
        self.filename_filter.textChanged.connect(self._apply_filters)
        filters.addWidget(self.filename_filter, 1)

        self.type_filter = QComboBox()
        self.type_filter.setAccessibleName("Filter attachments by file type")
        self.type_filter.addItem("All types", "")
        for name in _ATTACHMENT_TYPES:
            self.type_filter.addItem(name, name)
        self.type_filter.currentIndexChanged.connect(self._apply_filters)
        filters.addWidget(self.type_filter)

        self.group_button = QToolButton()
        self.group_button.setText("Group by type")
        self.group_button.setCheckable(True)
        self.group_button.setToolTip("Group attachments by file type")
        self.group_button.setAccessibleName("Group attachments by file type")
        self.group_button.toggled.connect(self._regroup_attachments)
        filters.addWidget(self.group_button)

        self.empty_label = QLabel("No attachments.")
        self.empty_label.setContentsMargins(8, 4, 8, 4)
        self.empty_label.setWordWrap(True)
        self.attachments_list.horizontalHeader().sortIndicatorChanged.connect(
            lambda *_: QTimer.singleShot(0, self._apply_filters)
        )
        
        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addLayout(filters)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.attachments_list)
        self.setLayout(layout)
        install_primary_wheel_zoom(
            self, lambda direction: self._zoom_in() if direction > 0 else self._zoom_out()
        )
    
    def set_page(self, page_path: Optional[Path]) -> None:
        """Set the current page and update the attachments list."""
        t0 = time.perf_counter()
        self.current_page_path = page_path
        self._refresh_attachments()
        #if PAGE_LOGGING_ENABLED:
            #print(f"[PageLoadAndRender] attachments set_page elapsed={(time.perf_counter()-t0)*1000:.1f}ms")
    
    def focusInEvent(self, event) -> None:
        """Refresh attachments whenever the panel gains focus."""
        super().focusInEvent(event)
        self._refresh_attachments()

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Track the active vault root so attachments can be normalized."""
        self.vault_root = Path(vault_root) if vault_root else None
        self._page_attachment_cache.clear()
        if not self._remote_mode:
            self._remote_vault_root = None

    def set_http_client(self, api_client: Optional[httpx.Client]) -> None:
        """Update the API client used for remote attachment operations."""
        self._http_client = api_client

    def set_remote_mode(self, remote_mode: bool, api_base: Optional[str]) -> None:
        """Toggle remote mode for attachments."""
        self._remote_mode = bool(remote_mode)
        self.screen_record_button.setEnabled(not self._remote_mode and self.current_page_path is not None)
        self._api_base = api_base.rstrip("/") if api_base else None
        self._remote_cache_root = None
        self._remote_cache_key = None
        if not self._remote_mode:
            self._remote_vault_root = None
        if self._remote_mode:
            self.open_folder_button.setEnabled(False)
        self._page_attachment_cache.clear()

    def set_remote_vault_root(self, vault_root: Optional[str]) -> None:
        """Track the server-side vault root for remote attachment lookups."""
        self._remote_vault_root = Path(vault_root) if vault_root else None

    def set_auth_prompt(self, auth_prompt: Optional[Callable[[], bool]]) -> None:
        """Set a callback to prompt for auth when needed."""
        self._auth_prompt = auth_prompt
    
    def _refresh_attachments(self) -> None:
        """Refresh the list of attachments for the current page."""
        t0 = time.perf_counter()
        self.record_button.setEnabled(False)
        self.screenshot_button.setEnabled(False)
        self.screen_record_button.setEnabled(False)
        self.attachments_list.clear()
        self.empty_label.setText("No attachments.")
        self.empty_label.show()

        if self._remote_mode:
            self._refresh_remote_attachments(t0)
            return
        
        if not self.current_page_path:
            self.add_button.setEnabled(False)
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            #if PAGE_LOGGING_ENABLED:
                #print(f"[PageLoadAndRender] attachments refresh skipped (no page) elapsed={(time.perf_counter()-t0)*1000:.1f}ms")
            return
        
        # Get the folder for this page
        # For a page like "/vault/Notes/MyPage/MyPage.txt", the folder is "/vault/Notes/MyPage/"
        # Images are stored in the same folder as the page text file
        page_folder = self.current_page_path.parent
        
        if _DETAILED_LOGGING:
            print(f"[Attachments] current_page_path: {self.current_page_path}")
            print(f"[Attachments] page_folder: {page_folder}")
            print(f"[Attachments] page_folder exists: {page_folder.exists()}")
            print(f"[Attachments] page_folder is_dir: {page_folder.is_dir()}")
        
        if not page_folder.exists() or not page_folder.is_dir():
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.add_button.setEnabled(False)
            return
        
        self.open_folder_button.setEnabled(True)
        self.refresh_button.setEnabled(True)
        self.add_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.screenshot_button.setEnabled(True)
        self.screen_record_button.setEnabled(True)
        
        # Update view mode based on zoom level
        self._update_view_mode()
        
        # List all files in the folder (excluding the page text file itself)
        attachments: list[Path] = []
        try:
            files = sorted(page_folder.iterdir(), key=lambda p: p.name.lower())
            t_list = time.perf_counter()
            if _DETAILED_LOGGING:
                print(f"[Attachments] Found {len(files)} files in folder")
            for file_path in files:
                if _DETAILED_LOGGING:
                    print(f"[Attachments] Checking file: {file_path}")
                if file_path.is_file() and file_path != self.current_page_path:
                    attachments.append(file_path)
                    item = AttachmentTableItem()
                    item.setData(Qt.UserRole, str(file_path))
                    try:
                        stat = file_path.stat()
                        item.setData(_SIZE_ROLE, stat.st_size)
                        if _attachment_type(str(file_path)) == "Images":
                            item.setData(_THUMB_SOURCE_ROLE, ThumbnailSource(
                                str(file_path.resolve()), f"{stat.st_mtime_ns}:{stat.st_size}", path=file_path,
                            ))
                    except OSError:
                        pass

                    # Set icon based on view mode
                    item.setText(file_path.name if self.zoom_level > 0 else file_path.name)
                    icon = self._get_file_icon(file_path)
                    if icon:
                        item.setIcon(icon)
                        item.setData(_FILE_ICON_ROLE, icon)

                    self.attachments_list.addItem(item)
            #if PAGE_LOGGING_ENABLED:
            #    print(
            #        f"[PageLoadAndRender] attachments refresh listed={len(files)} elapsed={(time.perf_counter()-t_list)*1000:.1f}ms total={(time.perf_counter()-t0)*1000:.1f}ms"
            #    )
        except (OSError, PermissionError):
            pass
        else:
            self._regroup_attachments()
            #if PAGE_LOGGING_ENABLED:
                #print(f"[PageLoadAndRender] attachments refresh total={(time.perf_counter()-t0)*1000:.1f}ms")
            self._sync_with_server(attachments)
        self._update_remove_button_state()

    def _refresh_remote_attachments(self, t0: float) -> None:
        if not self.current_page_path:
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.add_button.setEnabled(False)
            return
        if not self._http_client:
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            self.add_button.setEnabled(False)
            return
        page_key = self._current_page_key()
        if not page_key:
            return
        self.open_folder_button.setEnabled(False)
        self.refresh_button.setEnabled(True)
        self.add_button.setEnabled(True)
        self.record_button.setEnabled(True)
        self.screenshot_button.setEnabled(True)
        self.screen_record_button.setEnabled(False)
        self._update_view_mode()
        try:
            resp = self._http_client.get("/files/", params={"page_path": page_key})
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.get("/files/", params={"page_path": page_key})
            resp.raise_for_status()
            payload = resp.json()
            attachments = payload.get("attachments", [])
            if not isinstance(attachments, list):
                attachments = []
        except httpx.HTTPError as exc:
            print(f"[Attachments] failed to list remote attachments: {exc}")
            return
        for entry in attachments:
            if not isinstance(entry, dict):
                continue
            attachment_path = entry.get("attachment_path") or entry.get("stored_path")
            if not attachment_path:
                continue
            item = AttachmentTableItem()
            size = entry.get("size_bytes")
            item.setData(_SIZE_ROLE, size if isinstance(size, int) and size >= 0 else None)
            name = Path(str(attachment_path)).name
            label = name
            cache_root = self._get_remote_cache_root()
            if cache_root is not None:
                cache_path = (cache_root / "attachments" / str(attachment_path).lstrip("/")).resolve()
                if cache_path.exists():
                    label = f"{name} (local cache)"
                else:
                    label = f"{name} (remote)"
            item.setText(label)
            item.setData(Qt.UserRole, {"kind": "remote", "path": str(attachment_path)})
            if _attachment_type(str(attachment_path)) == "Images":
                revision = entry.get("mtime_ns")
                if revision is None:
                    revision = entry.get("updated")
                if revision is None:
                    revision = time.time_ns()  # Older servers cannot validate a persistent cache entry.
                item.setData(_THUMB_SOURCE_ROLE, ThumbnailSource(
                    f"{self._api_base}\0{attachment_path}", f"{revision}:{size}",
                    client=self._http_client, remote_path=str(attachment_path),
                ))
            icon = self._get_file_icon(Path(str(attachment_path)))
            if icon:
                item.setIcon(icon)
                item.setData(_FILE_ICON_ROLE, icon)
            self.attachments_list.addItem(item)
        self._regroup_attachments()
        #if PAGE_LOGGING_ENABLED:
            #print(f"[PageLoadAndRender] attachments refresh remote total={(time.perf_counter()-t0)*1000:.1f}ms")
        self._update_remove_button_state()

    def _toggle_hidden_files(self, visible: bool) -> None:
        self._show_hidden_files = visible
        self._update_hidden_files_icon()
        self.hiddenFilesVisibilityChanged.emit(visible)
        self._apply_filters()

    def _regroup_attachments(self) -> None:
        listing = self.attachments_list
        selected = {_item_path(item) for item in listing.selectedItems()}
        current = _item_path(listing.currentItem()) if listing.currentItem() else None
        with QSignalBlocker(listing):
            listing.setSortingEnabled(False)
            listing.grouped = self.group_button.isChecked()
            items = [listing.takeItem(0) for _ in range(listing.count())]
            files = [item for item in items if _item_path(item)]
            groups = list(_ATTACHMENT_TYPES)
            grouped = self.group_button.isChecked()
            files.sort(key=lambda item: (
                groups.index(_attachment_type(_item_path(item))) if grouped else 0,
                Path(_item_path(item)).name.casefold(),
            ))
            previous_group = None
            for item in files:
                group = _attachment_type(_item_path(item))
                if grouped and group != previous_group:
                    header = AttachmentTableItem(group)
                    header.setData(_GROUP_ROLE, group)
                    header.setFlags(Qt.ItemIsEnabled)
                    font = header.font()
                    font.setBold(True)
                    header.setFont(font)
                    header.setSizeHint(QSize(1, self.fontMetrics().height() + 12))
                    listing.addItem(header)
                    previous_group = group
                listing.addItem(item)
            for item in files:
                if _item_path(item) == current:
                    listing.setCurrentItem(item)
            for item in files:
                item.setSelected(_item_path(item) in selected)
            listing.setSortingEnabled(True)
        self._update_view_mode()
        self._apply_filters()

    def _apply_filters(self) -> None:
        query = self.filename_filter.text().strip().casefold()
        file_type = self.type_filter.currentData()
        attachment_count = matched_count = 0
        visible_groups = set()
        headers = []
        for row in range(self.attachments_list.count()):
            item = self.attachments_list.item(row)
            if item.data(_GROUP_ROLE):
                headers.append(item)
                self.attachments_list.setRowHeight(row, self.fontMetrics().height() + 12)
                continue
            self.attachments_list.setRowHeight(row, max(self.fontMetrics().height() + 10,
                                                        self.attachments_list.iconSize().height() + 6))
            path = _item_path(item)
            name = Path(path).name
            group = _attachment_type(path)
            eligible = self._show_hidden_files or not name.startswith(".")
            attachment_count += eligible
            hidden = not (eligible and query in name.casefold() and (not file_type or file_type == group))
            if hidden:
                item.setSelected(False)
            item.setHidden(hidden)
            if not hidden:
                matched_count += 1
                visible_groups.add(group)
        for header in headers:
            header.setHidden(header.data(_GROUP_ROLE) not in visible_groups)
        self.empty_label.setText("No attachments match these filters." if query or file_type else "No attachments.")
        self.empty_label.setVisible(matched_count == 0)
        self._update_remove_button_state()
        # Keep every file in the list/cache for sync, and keep the page's
        # attachment indicator independent of temporary filename/type filters.
        self.countReady.emit(self.current_page_path, attachment_count)
        self._load_visible_thumbnails(cached_only=True)
        self._schedule_thumbnails()

    def _toggle_thumbnails(self, enabled: bool) -> None:
        self._show_thumbnails = enabled
        self._thumbnail_settings.setValue("attachments/showThumbnails", enabled)
        self._update_thumbnails_icon()
        self._thumbnail_timer.stop()
        self._cached_thumbnail_timer.stop()
        if not enabled:
            for row in range(self.attachments_list.count()):
                item = self.attachments_list.item(row)
                if item.data(_THUMB_KEY_ROLE):
                    item.setIcon(item.data(_FILE_ICON_ROLE) or QIcon())
                    item.setData(_THUMB_KEY_ROLE, None)
        self._update_view_mode()
        self._apply_filters()

    def _schedule_thumbnails(self, *_):
        if self._show_thumbnails and self.isVisible():
            self._cached_thumbnail_timer.start()
            self._thumbnail_timer.start()

    def _load_visible_thumbnails(self, *, cached_only=False) -> None:
        if not self._show_thumbnails or not self.isVisible():
            return
        listing = self.attachments_list
        first = listing.rowAt(0)
        if first < 0:
            return
        last = listing.rowAt(listing.viewport().height() - 1)
        if last < 0:
            last = listing.count() - 1
        for row in range(first, last + 1):
            item = listing.item(row)
            source = item.data(_THUMB_SOURCE_ROLE)
            if item.isHidden() or source is None or not listing.visualItemRect(item).intersects(listing.viewport().rect()):
                continue
            if item.data(_THUMB_KEY_ROLE) == source.key:
                continue
            image = (self._thumbnail_loader.cached_image(source, include_disk=True) if cached_only
                     else self._thumbnail_loader.request(source))
            if image is not None:
                item.setData(_THUMB_KEY_ROLE, source.key)
                if not image.isNull():
                    item.setIcon(QIcon(QPixmap.fromImage(image)))

    def _thumbnail_ready(self, key, image):
        # Look up the current rows again: the page, sorting, or filters may
        # have changed while decoding. Never retain a table item in a worker.
        self._schedule_thumbnails()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Hide:
            self._thumbnail_timer.stop()
            self._cached_thumbnail_timer.stop()
        elif event.type() in (QEvent.Show, QEvent.Resize, QEvent.MouseButtonPress, QEvent.KeyPress):
            if event.type() == QEvent.Show:
                self._load_visible_thumbnails(cached_only=True)
            self._schedule_thumbnails()
        return super().eventFilter(watched, event)

    def _update_thumbnails_icon(self):
        self._set_toolbar_icon(self.thumbnails_button, "show-thumbnails.svg")
        self.thumbnails_button.setToolTip("Hide image thumbnails" if self._show_thumbnails else "Show image thumbnails")

    def _update_hidden_files_icon(self) -> None:
        asset = "open-eye.svg" if self._show_hidden_files else "closed-eye.svg"
        self._set_toolbar_icon(self.hidden_files_button, asset)
        self.hidden_files_button.setToolTip(
            "Hide hidden files" if self._show_hidden_files else "Show hidden files"
        )

    def _set_toolbar_icon(self, button, asset, color=None):
        path = Path(__file__).resolve().parents[2] / "assets" / asset
        pixmap = QIcon(str(path)).pixmap(button.iconSize())
        if not pixmap.isNull():
            tinted = QPixmap(pixmap.size())
            tinted.setDevicePixelRatio(pixmap.devicePixelRatio())
            tinted.fill(Qt.transparent)
            painter = QPainter(tinted)
            painter.drawPixmap(0, 0, pixmap)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(tinted.rect(), color if color is not None else self.palette().color(QPalette.ButtonText))
            painter.end()
            button.setIcon(QIcon(tinted))

    def changeEvent(self, event) -> None:  # type: ignore[override]
        super().changeEvent(event)
        if event.type() == QEvent.PaletteChange and hasattr(self, "hidden_files_button"):
            self._update_hidden_files_icon()
            self._update_thumbnails_icon()
            self._set_toolbar_icon(self.record_button, "microphone.svg", QColor("#e53935"))
            self._set_toolbar_icon(self.screen_record_button, "video-record.svg", QColor("#e53935"))
            self._set_toolbar_icon(self.screenshot_button, "screenshot.svg")

    def _get_remote_cache_root(self) -> Optional[Path]:
        if not self._api_base:
            return None
        if self._remote_cache_root is not None and self._remote_cache_key == self._api_base:
            return self._remote_cache_root
        parsed = urlparse(self._api_base)
        host = parsed.hostname or "remote"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        key = f"{parsed.scheme}://{host}:{port}"
        digest = hashlib.sha1(key.encode("ascii", errors="ignore")).hexdigest()[:12]
        cache_root = Path.home() / ".stillpoint" / "remote" / f"{host}-{port}-{digest}"
        cache_root.mkdir(parents=True, exist_ok=True)
        self._remote_cache_root = cache_root
        self._remote_cache_key = self._api_base
        return cache_root

    def _remote_attachment_names(self) -> set[str]:
        if not self._http_client:
            return set()
        page_key = self._current_page_key()
        if not page_key:
            return set()
        try:
            resp = self._http_client.get("/files/", params={"page_path": page_key})
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.get("/files/", params={"page_path": page_key})
            resp.raise_for_status()
            payload = resp.json()
            attachments = payload.get("attachments", [])
        except httpx.HTTPError:
            return set()
        names: set[str] = set()
        if isinstance(attachments, list):
            for entry in attachments:
                if not isinstance(entry, dict):
                    continue
                attachment_path = entry.get("attachment_path") or entry.get("stored_path")
                if attachment_path:
                    names.add(Path(str(attachment_path)).name)
        return names

    def _capture_screen(self):
        from .screen_capture import start_capture
        existing = getattr(self, "_capture_overlay", None)
        if existing is not None:
            existing.raise_()
            existing.activateWindow()
            return
        page = self.current_page_path
        remote = self._remote_mode
        client = self._http_client
        page_key = self._current_page_key()

        def save_image(image):
            from datetime import datetime
            import uuid
            filename = f"capture-{datetime.now():%Y-%m-%d_%H-%M-%S}-{uuid.uuid4().hex[:8]}.png"
            from PySide6.QtCore import QBuffer, QIODevice
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            if not image.save(buffer, "PNG"):
                raise OSError("Could not encode the screenshot.")
            if remote:
                response = client.post("/files/attach", data={"page_path": page_key},
                                       files={"files": (filename, bytes(buffer.data()), "image/png")})
                response.raise_for_status()
            else:
                if not page.is_file():
                    raise OSError("The destination page no longer exists.")
                destination = page.parent / filename
                output = destination.open("xb")
                try:
                    with output:
                        output.write(bytes(buffer.data()))
                except OSError:
                    destination.unlink(missing_ok=True)
                    raise
            if self.current_page_path == page and self._remote_mode == remote:
                self._refresh_attachments()
            if not remote:
                self.attachmentsModified.emit("screen capture saved")
            return filename

        # Bind the page and connection now; clipboard capture works without a page.
        writable = page is not None and (not remote or (client is not None and page_key))
        owner = self.window()
        check = getattr(owner, "_ensure_writable", None)
        if writable and check is not None:
            writable = check("save a screen capture", interactive=False)
        def opened(overlay):
            self._capture_overlay = overlay
            overlay.destroyed.connect(lambda: setattr(self, "_capture_overlay", None))
        start_capture(self, save_image if writable else None, opened)

    def _record_voice_note(self) -> None:
        self._record_attachment("voice")

    def _record_screen(self, *, auto_start=False) -> None:
        if not self._remote_mode:
            self._record_attachment("screen", auto_start=auto_start)

    def _record_attachment(self, kind: str, *, auto_start=False) -> None:
        if kind == "screen" and self._remote_mode:
            return
        if not self.current_page_path:
            return
        dialog_attribute = "_screen_dialog" if kind == "screen" else "_voice_dialog"
        existing = getattr(self, dialog_attribute)
        if existing is not None:
            if auto_start and existing._state == "idle" and getattr(existing, "_attachment_page", None) != self.current_page_path:
                existing.reject()
            else:
                existing.show()
                existing.raise_()
                existing.activateWindow()
                if auto_start and existing._state == "idle":
                    existing._record()
                return
        try:
            if kind == "screen":
                from .screen_record_dialog import ScreenRecordDialog as RecordingDialog
            else:
                from .voice_note_dialog import VoiceNoteDialog as RecordingDialog
        except (ImportError, RuntimeError) as exc:
            QMessageBox.warning(self, "Recording unavailable", f"Qt Multimedia could not be loaded: {exc}")
            return
        # Bind the destination now; a later page/vault switch must never redirect
        # a recording. The worker uses these snapshots, not mutable panel state.
        page = self.current_page_path
        page_key = self._current_page_key()
        remote = self._remote_mode
        client = self._http_client
        if remote and (client is None or not page_key):
            return

        def save_attachment(source: Path, filename: str) -> str:
            if remote:
                with source.open("rb") as stream:
                    response = client.post(
                        "/files/attach", data={"page_path": page_key},
                        files={"files": (filename, stream, "audio/mp4" if source.suffix == ".m4a" else "audio/wav")},
                    )
                response.raise_for_status()
                return filename
            if not page.is_file():
                raise OSError("The destination page no longer exists.")
            # Exclusive creation prevents overwriting an existing attachment.
            for index in itertools.count():
                name = filename if not index else f"{Path(filename).stem} ({index}){Path(filename).suffix}"
                destination = page.parent / name
                try:
                    output = destination.open("xb")
                except FileExistsError:
                    continue
                try:
                    with output, source.open("rb") as stream:
                        shutil.copyfileobj(stream, output)
                except OSError:
                    destination.unlink(missing_ok=True)
                    raise
                return name

        from .path_utils import path_to_colon

        page_link = path_to_colon(page_key or page.name)
        if kind == "screen":
            dialog = RecordingDialog(self, page_link, save_attachment)
            dialog._attachment_page = page
        else:
            dialog = RecordingDialog(self, page_link, save_attachment,
                                     auth_prompt=self._auth_prompt if remote else None)
        setattr(self, dialog_attribute, dialog)

        def saved(filename):
            if self.current_page_path == page and self._remote_mode == remote:
                self._refresh_attachments()
                for row in range(self.attachments_list.count()):
                    item = self.attachments_list.item(row)
                    if Path(_item_path(item)).name == filename:
                        self.attachments_list.setCurrentItem(item)
                        break
            if not remote:
                self.attachmentsModified.emit("screen recording saved" if kind == "screen" else "voice note recorded")

        dialog.attachmentSaved.connect(saved)
        dialog.finished.connect(lambda _result: setattr(self, dialog_attribute, None))
        dialog.show()
        if auto_start:
            dialog.raise_()
            dialog.activateWindow()
            QTimer.singleShot(0, dialog._record)

    def _add_attachments(self) -> None:
        """Prompt user to add attachments via the OS file picker."""
        if not self.current_page_path:
            return
        if self._remote_mode:
            self._add_remote_attachments()
            return
        page_folder = self.current_page_path.parent
        if not page_folder.exists():
            page_folder.mkdir(parents=True, exist_ok=True)
        options = QFileDialog.Options()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Attachments",
            str(page_folder),
            options=options,
        )
        if not files:
            return
        for file_path in files:
            src = Path(file_path)
            if not src.exists():
                continue
            dest = self._unique_destination(page_folder, src.name)
            try:
                shutil.copy2(src, dest)
            except OSError as exc:
                print(f"[Attachments] Failed to copy {src}: {exc}")
        self._refresh_attachments()
        self.attachmentsModified.emit("attachments added")

    def _add_remote_attachments(self) -> None:
        if not self._http_client:
            return
        page_key = self._current_page_key()
        if not page_key:
            return
        options = QFileDialog.Options()
        files, _ = QFileDialog.getOpenFileNames(
            self,
            "Add Attachments",
            str(Path.home()),
            options=options,
        )
        if not files:
            return
        multipart = []
        handles = []
        try:
            for file_path in files:
                src = Path(file_path)
                if not src.exists():
                    continue
                handle = open(src, "rb")
                handles.append(handle)
                multipart.append(("files", (src.name, handle, "application/octet-stream")))
            if not multipart:
                return
            resp = self._http_client.post("/files/attach", data={"page_path": page_key}, files=multipart)
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.post("/files/attach", data={"page_path": page_key}, files=multipart)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            print(f"[Attachments] failed to upload remote attachments: {exc}")
        finally:
            for handle in handles:
                try:
                    handle.close()
                except Exception:
                    pass
        self._refresh_attachments()

    def _unique_destination(self, folder: Path, name: str) -> Path:
        base = Path(name)
        candidate = folder / name
        if not candidate.exists():
            return candidate
        for idx in itertools.count(1):
            candidate = folder / f"{base.stem} ({idx}){base.suffix}"
            if not candidate.exists():
                return candidate

    def _remove_selected_attachments(self) -> None:
        selected = self.attachments_list.selectedItems()
        if not selected or not self.current_page_path:
            return
        to_delete: set[str] = set()
        for item in selected:
            data: Any = item.data(Qt.UserRole)
            if not data:
                continue
            if self._remote_mode and isinstance(data, dict):
                rel = data.get("path")
                if rel:
                    to_delete.add(rel)
                continue
            if isinstance(data, str):
                path = Path(data)
                try:
                    if path.exists():
                        path.unlink()
                except OSError as exc:
                    print(f"[Attachments] Failed to delete {path}: {exc}")
                rel = self._attachment_relative_path(path)
                if rel:
                    to_delete.add(rel)
        if to_delete:
            self._delete_removed_attachments(to_delete)
        self._refresh_attachments()
        self.attachmentsModified.emit("attachments removed")

    def _rename_selected_attachment(self) -> None:
        selected = self.attachments_list.selectedItems()
        if len(selected) != 1 or not self.current_page_path:
            return
        data = selected[0].data(Qt.UserRole)
        old_path = data.get("path") if isinstance(data, dict) else data
        if not old_path:
            return
        old_name = Path(old_path).name
        name, accepted = QInputDialog.getText(
            self, "Rename Attachment", "Filename:", text=old_name,
        )
        if not accepted or name == old_name:
            return
        try:
            if not name.strip() or name in {".", ".."} or any(c in name for c in '/\\\x00\r\n'):
                raise ValueError("Enter a filename without folders or line breaks.")
            if self._http_client is not None:
                path = old_path if self._remote_mode else self._attachment_relative_path(Path(old_path))
                page = self._current_page_key()
                if not path or not page:
                    raise ValueError("The attachment must belong to the current vault.")
                payload = {"page_path": page, "attachment_path": path, "name": name}
                response = self._http_client.post("/files/rename", json=payload)
                if response.status_code == 401 and self._auth_prompt and self._auth_prompt():
                    response = self._http_client.post("/files/rename", json=payload)
                response.raise_for_status()
                known = self._page_attachment_cache.get(page)
                if known is not None:
                    known.discard(path)
                    known.add(Path(path).with_name(name).as_posix())
            else:
                from sp.attachment_ops import rename_attachment

                root = self.vault_root or self.current_page_path.parent
                rename_attachment(root, str(self.current_page_path.relative_to(root)),
                                  str(Path(old_path).relative_to(root)), name)
        except (ValueError, OSError, httpx.HTTPError) as exc:
            detail = str(exc)
            if isinstance(exc, httpx.HTTPStatusError):
                try:
                    detail = str(exc.response.json().get("detail", detail))
                except ValueError:
                    pass
            QMessageBox.warning(self, "Rename Attachment", detail)
            return
        self._refresh_attachments()
        for row in range(self.attachments_list.count()):
            item = self.attachments_list.item(row)
            value = item.data(Qt.UserRole)
            path = value.get("path") if isinstance(value, dict) else value
            if path and Path(path).name == name and not item.isHidden():
                self.attachments_list.setCurrentItem(item)
                break
        self.attachmentsModified.emit("attachment renamed")

    def _current_page_key(self) -> Optional[str]:
        if not self.current_page_path:
            return None
        root = self._remote_vault_root if self._remote_mode else self.vault_root
        if not root:
            return None
        try:
            rel = self.current_page_path.relative_to(root)
        except ValueError:
            return None
        return f"/{rel.as_posix()}"

    def _attachment_relative_path(self, path: Path) -> Optional[str]:
        if not self.vault_root:
            return None
        try:
            rel = path.relative_to(self.vault_root)
        except ValueError:
            return None
        return f"/{rel.as_posix()}"

    def _sync_with_server(self, attachments: list[Path]) -> None:
        page_key = self._current_page_key()
        if not page_key or not self._http_client:
            return
        current_map: dict[str, Path] = {}
        for attachment in attachments:
            rel = self._attachment_relative_path(attachment)
            if rel:
                current_map[rel] = attachment
        known = self._page_attachment_cache.setdefault(page_key, set())
        added = set(current_map.keys()) - known
        removed = set(known) - set(current_map.keys())
        if added and self._upload_new_attachments(page_key, added, current_map):
            known.update(added)
        if removed and self._delete_removed_attachments(removed):
            known.difference_update(removed)

    def _update_remove_button_state(self) -> None:
        enabled = bool(self.attachments_list.selectedItems())
        self.remove_button.setEnabled(enabled)

    def _upload_new_attachments(
        self,
        page_key: str,
        added: set[str],
        mapping: dict[str, Path],
    ) -> bool:
        with contextlib.ExitStack() as stack:
            multipart = []
            for rel_path in sorted(added):
                file_path = mapping.get(rel_path)
                if not file_path or not file_path.exists():
                    continue
                stream = stack.enter_context(open(file_path, "rb"))
                multipart.append(("files", (file_path.name, stream, "application/octet-stream")))
            if not multipart:
                return False
            try:
                resp = self._http_client.post("/files/attach", data={"page_path": page_key}, files=multipart)
                if resp.status_code == 401 and self._auth_prompt:
                    if self._auth_prompt():
                        resp = self._http_client.post("/files/attach", data={"page_path": page_key}, files=multipart)
                resp.raise_for_status()
                if log_enabled("attachments_media"):
                    print(f"[Attachments] uploaded {len(multipart)} attachment(s) for {page_key}")
                return True
            except httpx.HTTPError as exc:
                print(f"[Attachments] failed to upload attachments for {page_key}: {exc}")
                return False

    def _delete_removed_attachments(self, removed: set[str]) -> bool:
        if not removed:
            return True
        if not self._http_client:
            if log_enabled("attachments_media"):
                print(f"[Attachments] skipped server delete for {len(removed)} file(s) (no API client)")
            return True
        try:
            resp = self._http_client.post("/files/delete", json={"paths": sorted(removed)})
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.post("/files/delete", json={"paths": sorted(removed)})
            resp.raise_for_status()
            if log_enabled("attachments_media"):
                print(f"[Attachments] deleted {len(removed)} attachment(s) for panel")
            return True
        except httpx.HTTPError as exc:
            print(f"[Attachments] failed to delete attachments on server: {exc}")
            return False
    
    def _get_file_icon(self, file_path: Path) -> QIcon:
        """Use StillPoint diagram identities, then the OS's file-type icons."""
        suffix = file_path.suffix.lower()
        editor = {".puml": "plantuml", ".plantuml": "plantuml",
                  ".mmd": "mermaid", ".mermaid": "mermaid",
                  ".excalidraw": "excalidraw"}.get(suffix)
        if editor:
            icon = get_diagram_editor_icon(editor)
            if not icon.isNull():
                return icon
        from PySide6.QtCore import QFileInfo
        return self.icon_provider.icon(QFileInfo(str(file_path)))
    
    def _update_view_mode(self) -> None:
        """Resize file icons while retaining the sortable detail columns."""
        size = (16, 48, 96, 144)[self.zoom_level]
        if self._show_thumbnails:
            size = max(48, size)
        self.attachments_list.setIconSize(QSize(size, size))
    
    def _open_folder(self) -> None:
        """Open the attachments folder in the system file manager."""
        if self._remote_mode or not self.current_page_path:
            return
        
        page_folder = self.current_page_path.parent
        
        if page_folder.exists() and page_folder.is_dir():
            open_path_in_system(page_folder)
    
    def _zoom_in(self) -> None:
        """Zoom in to show larger icons."""
        if self.zoom_level < 3:
            self.zoom_level += 1
            self._refresh_attachments()
    
    def _zoom_out(self) -> None:
        """Zoom out to show smaller icons or list view."""
        if self.zoom_level > 0:
            self.zoom_level -= 1
            self._refresh_attachments()
    
    def _image_attachment_path(self, item):
        """Materialize a remote image for editing or the native viewer."""
        path = _item_path(item)
        if not path:
            return None
        if not self._remote_mode:
            return Path(path)
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict) or self._http_client is None:
            return None
        root = self._get_remote_cache_root()
        if root is None:
            return None
        cached = Path(data["cache_path"]) if data.get("cache_path") else root / "attachments" / path.lstrip("/")
        response = self._http_client.get("/api/file/raw", params={"path": path})
        if response.status_code == 401 and self._auth_prompt and self._auth_prompt():
            response = self._http_client.get("/api/file/raw", params={"path": path})
        response.raise_for_status()
        cached.parent.mkdir(parents=True, exist_ok=True)
        cached.write_bytes(response.content)
        return cached

    def _open_image_attachment(self, item):
        from .image_editor_window import open_image_editor, remote_image_saver
        try:
            path = self._image_attachment_path(item)
            if path is None:
                return
            remote = self._remote_mode
            owner = self.window()
            check = getattr(owner, "_ensure_writable", None)
            read_only = not check("edit an image", interactive=False) if check else False
            callback = (remote_image_saver(self._http_client, _item_path(item), self._current_page_key(), self._auth_prompt)
                        if remote else None)
            def saved(filename):
                self._refresh_attachments()
                if not remote:
                    self.attachmentsModified.emit("image edited")
            return open_image_editor(path, self, read_only=read_only, save_callback=callback, on_saved=saved)
        except (OSError, httpx.HTTPError) as exc:
            QMessageBox.warning(self, "Could not open image", str(exc))

    def _open_in_default_viewer(self, item):
        try:
            path = self._image_attachment_path(item)
            if path is not None:
                open_path_in_system(path)
        except (OSError, httpx.HTTPError) as exc:
            QMessageBox.warning(self, "Could not open image", str(exc))

    def _open_attachment(self, item) -> None:
        """Open supported media in Stillpoint and other files externally."""
        data = item.data(Qt.UserRole)
        path = _item_path(item)
        if path and Path(path).suffix.lower() in _ATTACHMENT_TYPES["Images"]:
            self._open_image_attachment(item)
            return
        if path and Path(path).suffix.lower() in _ATTACHMENT_TYPES["Audio"]:
            self._play_audio_attachment(path)
            return
        if path and Path(path).suffix.lower() in _ATTACHMENT_TYPES["Video"]:
            self._play_audio_attachment(path, video=True)
            return
        mermaid_suffixes = {".mmd", ".mermaid"}
        if self._remote_mode and isinstance(data, dict):
            rel_path = data.get("path")
            if not rel_path or not self._api_base:
                return
            rel_path_lower = str(rel_path).lower()
            if rel_path_lower.endswith(".puml"):
                self.plantumlEditorRequested.emit(
                    {
                        "kind": "remote",
                        "path": str(rel_path),
                        "page_path": self._current_page_key(),
                    }
                )
                return
            if any(rel_path_lower.endswith(suffix) for suffix in mermaid_suffixes):
                self.mermaidEditorRequested.emit(
                    {
                        "kind": "remote",
                        "path": str(rel_path),
                        "page_path": self._current_page_key(),
                    }
                )
                return
            try:
                from urllib.parse import quote
                url = f"{self._api_base}/api/file/raw?path={quote(str(rel_path))}"
                QDesktopServices.openUrl(QUrl(url))
            except Exception:
                return
            return
        if isinstance(data, str):
            file_path = Path(data)
            if file_path.exists():
                # Check if it's a PlantUML file
                if file_path.suffix.lower() == ".puml":
                    if log_enabled("attachments_media"):
                        print(f"[Attachments] Double-click .puml -> open editor: {data}")
                    self.plantumlEditorRequested.emit(data)
                elif file_path.suffix.lower() in mermaid_suffixes:
                    if log_enabled("attachments_media"):
                        print(f"[Attachments] Double-click Mermaid -> open editor: {data}")
                    self.mermaidEditorRequested.emit(data)
                elif file_path.suffix.lower() == ".excalidraw":
                    if log_enabled("attachments_media"):
                        print(f"[Attachments] Double-click Excalidraw -> open editor: {data}")
                    self.excalidrawEditorRequested.emit(data)
                else:
                    # Open with default system handler
                    if log_enabled("attachments_media"):
                        print(f"[Attachments] Double-click non-puml -> open default: {data}")
                    open_path_in_system(file_path)

    def _play_audio_attachment(self, path: str, *, video=False) -> None:
        if self._remote_mode and self._http_client is None:
            return
        try:
            from .audio_player_dialog import AudioPlayerDialog
        except (ImportError, RuntimeError) as exc:
            QMessageBox.warning(self, "Audio playback unavailable", f"Qt Multimedia could not be loaded: {exc}")
            return
        attribute = "_video_player" if video else "_audio_player"
        if getattr(self, attribute) is None:
            player = AudioPlayerDialog(self, video=True) if video else AudioPlayerDialog(self)
            setattr(self, attribute, player)
            player.finished.connect(lambda _result: setattr(self, attribute, None))
        dialog = getattr(self, attribute)
        if self._remote_mode:
            dialog.open_remote(self._http_client, path, self._auth_prompt)
        else:
            dialog.open_local(Path(path))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()
    
    def refresh(self) -> None:
        """Refresh the attachments list."""
        self._refresh_attachments()

    def _on_attachments_context_menu(self, pos) -> None:
        """Handle right-click context menu on attachments list."""
        menu = QMenu(self)
        apply_menu_theme(menu, self.attachments_list)
        clicked = self.attachments_list.itemAt(pos)
        if clicked is not None and _item_path(clicked) and not clicked.isSelected():
            self.attachments_list.clearSelection()
            clicked.setSelected(True)
        selected = self.attachments_list.selectedItems()
        if len(selected) == 1 and Path(_item_path(selected[0]) or "").suffix.lower() in _ATTACHMENT_TYPES["Images"]:
            item = selected[0]
            edit_action = menu.addAction("Edit image")
            edit_action.triggered.connect(lambda: self._open_image_attachment(item))
            viewer_action = menu.addAction("Open in default viewer")
            viewer_action.triggered.connect(lambda: self._open_in_default_viewer(item))
            menu.addSeparator()

        # Add "Add new PlantUML..." action
        add_plantuml_action = menu.addAction("Add new PlantUML...")
        add_plantuml_action.triggered.connect(self._create_new_plantuml)

        add_mermaid_action = menu.addAction("Add new Mermaid...")
        add_mermaid_action.triggered.connect(self._create_new_mermaid)

        add_excalidraw_action = menu.addAction("Add new Excalidraw...")
        add_excalidraw_action.triggered.connect(self._create_new_excalidraw)

        rename_action = menu.addAction("Rename…")
        rename_action.setEnabled(len(selected) == 1)
        rename_action.triggered.connect(self._rename_selected_attachment)

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(bool(selected))
        delete_action.triggered.connect(self._remove_selected_attachments)

        folder_action = menu.addAction("Open containing folder")
        folder_action.setEnabled(self.open_folder_button.isEnabled())
        folder_action.triggered.connect(self._open_folder)
        
        terminal_action = menu.addAction("Open Terminal Here")
        terminal_action.triggered.connect(self._open_terminal_here)
        
        # Show context menu at cursor position
        menu.exec(self.attachments_list.mapToGlobal(pos))

    def _create_new_plantuml(self) -> None:
        """Create a new .puml file in the attachments folder."""
        if not self.current_page_path:
            return

        # Prompt user for file name
        name, ok = QInputDialog.getText(
            self,
            "New PlantUML Diagram",
            "Enter diagram name (without .puml extension):",
            text="diagram",
        )

        if not ok or not name.strip():
            return

        name = name.strip()
        if not name:
            return

        if not name.lower().endswith(".puml"):
            name = name + ".puml"

        if self._remote_mode:
            if not self._http_client:
                return
            page_key = self._current_page_key()
            if not page_key:
                return
            if name in self._remote_attachment_names():
                QMessageBox.warning(self, "File Exists", f"File {name} already exists.")
                return
            template = """@startuml
' PlantUML diagram
' https://plantuml.com/

' Add your diagram here

@enduml
"""
            try:
                resp = self._http_client.post(
                    "/files/attach",
                    data={"page_path": page_key},
                    files={"files": (name, template.encode("utf-8"), "text/plain")},
                )
                if resp.status_code == 401 and self._auth_prompt:
                    if self._auth_prompt():
                        resp = self._http_client.post(
                            "/files/attach",
                            data={"page_path": page_key},
                            files={"files": (name, template.encode("utf-8"), "text/plain")},
                        )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                QMessageBox.critical(self, "Error", f"Failed to create file: {exc}")
                return
            self._refresh_attachments()
            return

        page_folder = self.current_page_path.parent
        if not page_folder.exists() or not page_folder.is_dir():
            return

        file_path = page_folder / name

        if file_path.exists():
            QMessageBox.warning(self, "File Exists", f"File {name} already exists.")
            return

        template = """@startuml
' PlantUML diagram
' https://plantuml.com/

' Add your diagram here

@enduml
"""
        try:
            file_path.write_text(template, encoding="utf-8")
            self._refresh_attachments()
            # Emit signal to open the file in the PlantUML editor
            self.plantumlEditorRequested.emit(str(file_path))
            self.attachmentsModified.emit("plantuml created")
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed to create file: {exc}")

    def _create_new_mermaid(self) -> None:
        """Create a new .mmd file in the attachments folder."""
        if not self.current_page_path:
            return

        name, ok = QInputDialog.getText(
            self,
            "New Mermaid Diagram",
            "Enter diagram name (without .mmd extension):",
            text="diagram",
        )

        if not ok or not name.strip():
            return

        name = name.strip()
        if not name:
            return

        if not name.lower().endswith(".mmd"):
            name = name + ".mmd"

        if self._remote_mode:
            if not self._http_client:
                return
            page_key = self._current_page_key()
            if not page_key:
                return
            if name in self._remote_attachment_names():
                QMessageBox.warning(self, "File Exists", f"File {name} already exists.")
                return
            template = """flowchart TD
  A[Start] --> B[End]
"""
            try:
                resp = self._http_client.post(
                    "/files/attach",
                    data={"page_path": page_key},
                    files={"files": (name, template.encode("utf-8"), "text/plain")},
                )
                if resp.status_code == 401 and self._auth_prompt:
                    if self._auth_prompt():
                        resp = self._http_client.post(
                            "/files/attach",
                            data={"page_path": page_key},
                            files={"files": (name, template.encode("utf-8"), "text/plain")},
                        )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                QMessageBox.critical(self, "Error", f"Failed to create file: {exc}")
                return
            self._refresh_attachments()
            return

        page_folder = self.current_page_path.parent
        if not page_folder.exists() or not page_folder.is_dir():
            return

        file_path = page_folder / name

        if file_path.exists():
            QMessageBox.warning(self, "File Exists", f"File {name} already exists.")
            return

        template = """flowchart TD
  A[Start] --> B[End]
"""
        try:
            file_path.write_text(template, encoding="utf-8")
            self._refresh_attachments()
            self.mermaidEditorRequested.emit(str(file_path))
            self.attachmentsModified.emit("mermaid created")
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed to create file: {exc}")

    def _create_new_excalidraw(self) -> None:
        """Create a new .excalidraw file in the attachments folder."""
        if not self.current_page_path:
            return

        name, ok = QInputDialog.getText(
            self,
            "New Excalidraw Drawing",
            "Enter drawing name (without .excalidraw extension):",
            text="drawing",
        )

        if not ok or not name.strip():
            return

        name = name.strip()
        if not name.lower().endswith(".excalidraw"):
            name = name + ".excalidraw"

        if self._remote_mode:
            QMessageBox.information(
                self,
                "Local Only",
                "Excalidraw editing is local-vault only for now.",
            )
            return

        page_folder = self.current_page_path.parent
        if not page_folder.exists() or not page_folder.is_dir():
            return

        file_path = page_folder / name
        if file_path.exists():
            QMessageBox.warning(self, "File Exists", f"File {name} already exists.")
            return

        template = """{
  "type": "excalidraw",
  "version": 2,
  "source": "stillpoint",
  "elements": [],
  "appState": {},
  "files": {}
}
"""
        try:
            file_path.write_text(template, encoding="utf-8")
            self._refresh_attachments()
            self.excalidrawEditorRequested.emit(str(file_path))
            self.attachmentsModified.emit("excalidraw created")
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to create file: {exc}")

    def _open_terminal_here(self) -> None:
        """Open a terminal in the attachments folder for the current page."""
        if not self.current_page_path:
            return

        import subprocess
        import platform
        import shlex

        folder_path = str(self.current_page_path.parent)

        try:
            system = platform.system()
            if system == "Windows":
                subprocess.Popen(["cmd.exe", "/K", "cd", "/D", folder_path], cwd=folder_path)
            elif system == "Darwin":
                # macOS: Use osascript to open Terminal.app
                script = f'tell application "Terminal" to do script "cd {shlex.quote(folder_path)}"'
                subprocess.Popen(["osascript", "-e", script])
            else:
                # Linux: Try common terminals
                for term_cmd in [
                    ["gnome-terminal", "--working-directory=" + folder_path],
                    ["konsole", "--workdir", folder_path],
                    ["xfce4-terminal", "--working-directory=" + folder_path],
                    ["xterm", "-e", f"cd {shlex.quote(folder_path)} && exec $SHELL"]
                ]:
                    try:
                        subprocess.Popen(term_cmd)
                        break
                    except FileNotFoundError:
                        continue
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Could not open terminal: {e}")
