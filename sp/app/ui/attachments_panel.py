from __future__ import annotations

import contextlib
import itertools
import os
import shutil
import time
from pathlib import Path
from typing import Optional, Callable, Any

import httpx

from PySide6.QtCore import Qt, QUrl, QSize, QMimeData, Signal
from PySide6.QtGui import QIcon, QPixmap, QDesktopServices, QDrag
from PySide6.QtWidgets import (
    QApplication,
    QStyle,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QToolButton,
    QFileDialog,
    QFileIconProvider,
    QMenu,
    QInputDialog,
    QDialog,
    QMessageBox,
)

from .page_load_logger import PAGE_LOGGING_ENABLED

_DETAILED_LOGGING = os.getenv("ZIMX_DETAILED_LOGGING", "0") not in ("0", "false", "False", "", None)


class AttachmentsListWidget(QListWidget):
    """Custom list widget that provides proper drag data for file attachments."""
    
    def mimeData(self, items):
        """Create mime data with file URLs for dragging."""
        mime = QMimeData()
        urls = []
        
        for item in items:
            file_path_str = item.data(Qt.UserRole)
            if file_path_str:
                url = QUrl.fromLocalFile(file_path_str)
                urls.append(url)
        
        if urls:
            mime.setUrls(urls)
        
        return mime


class AttachmentsPanel(QWidget):
    """Panel showing attachments (files) in the current page's folder."""
    
    # Signal emitted when user wants to open a .puml file in the PlantUML editor
    plantumlEditorRequested = Signal(object)  # file_path or payload

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
        
        self.current_page_path: Optional[Path] = None
        self.zoom_level = 0  # 0=list, 1=small icons, 2=medium icons, 3=large icons
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
        self.open_folder_button.setToolTip("Open folder in file manager")
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
        self.attachments_list.itemDoubleClicked.connect(self._open_attachment)
        self.attachments_list.setDragEnabled(True)
        self.attachments_list.setDragDropMode(QListWidget.DragOnly)
        self.attachments_list.itemSelectionChanged.connect(self._update_remove_button_state)
        self.attachments_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.attachments_list.customContextMenuRequested.connect(self._on_attachments_context_menu)
        
        # Layout
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(toolbar)
        layout.addWidget(self.attachments_list)
        self.setLayout(layout)
    
    def set_page(self, page_path: Optional[Path]) -> None:
        """Set the current page and update the attachments list."""
        t0 = time.perf_counter()
        self.current_page_path = page_path
        self._refresh_attachments()
        if PAGE_LOGGING_ENABLED:
            print(f"[PageLoadAndRender] attachments set_page elapsed={(time.perf_counter()-t0)*1000:.1f}ms")
    
    def focusInEvent(self, event) -> None:
        """Refresh attachments whenever the panel gains focus."""
        super().focusInEvent(event)
        self._refresh_attachments()

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Track the active vault root so attachments can be normalized."""
        self.vault_root = Path(vault_root) if vault_root else None
        self._page_attachment_cache.clear()

    def set_http_client(self, api_client: Optional[httpx.Client]) -> None:
        """Update the API client used for remote attachment operations."""
        self._http_client = api_client

    def set_remote_mode(self, remote_mode: bool, api_base: Optional[str]) -> None:
        """Toggle remote mode for attachments."""
        self._remote_mode = bool(remote_mode)
        self._api_base = api_base.rstrip("/") if api_base else None
        if self._remote_mode:
            self.open_folder_button.setEnabled(False)
        self._page_attachment_cache.clear()

    def set_auth_prompt(self, auth_prompt: Optional[Callable[[], bool]]) -> None:
        """Set a callback to prompt for auth when needed."""
        self._auth_prompt = auth_prompt
    
    def _refresh_attachments(self) -> None:
        """Refresh the list of attachments for the current page."""
        t0 = time.perf_counter()
        self.attachments_list.clear()

        if self._remote_mode:
            self._refresh_remote_attachments(t0)
            return
        
        if not self.current_page_path:
            self.open_folder_button.setEnabled(False)
            self.refresh_button.setEnabled(False)
            if PAGE_LOGGING_ENABLED:
                print(f"[PageLoadAndRender] attachments refresh skipped (no page) elapsed={(time.perf_counter()-t0)*1000:.1f}ms")
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
                    item = QListWidgetItem()
                    item.setData(Qt.UserRole, str(file_path))

                    # Set icon based on view mode
                    item.setText(file_path.name if self.zoom_level > 0 else file_path.name)
                    icon = self._get_file_icon(file_path)
                    if icon:
                        item.setIcon(icon)

                    self.attachments_list.addItem(item)
            if PAGE_LOGGING_ENABLED:
                print(
                    f"[PageLoadAndRender] attachments refresh listed={len(files)} elapsed={(time.perf_counter()-t_list)*1000:.1f}ms total={(time.perf_counter()-t0)*1000:.1f}ms"
                )
        except (OSError, PermissionError):
            pass
        else:
            if PAGE_LOGGING_ENABLED:
                print(f"[PageLoadAndRender] attachments refresh total={(time.perf_counter()-t0)*1000:.1f}ms")
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
            item = QListWidgetItem()
            name = Path(str(attachment_path)).name
            item.setText(name)
            item.setData(Qt.UserRole, {"kind": "remote", "path": str(attachment_path)})
            icon = self.icon_provider.icon(QFileIconProvider.File)
            if icon:
                item.setIcon(icon)
            self.attachments_list.addItem(item)
        if PAGE_LOGGING_ENABLED:
            print(f"[PageLoadAndRender] attachments refresh remote total={(time.perf_counter()-t0)*1000:.1f}ms")
        self._update_remove_button_state()

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

    def _current_page_key(self) -> Optional[str]:
        if not self.current_page_path or not self.vault_root:
            return None
        try:
            rel = self.current_page_path.relative_to(self.vault_root)
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
                print(f"[Attachments] uploaded {len(multipart)} attachment(s) for {page_key}")
                return True
            except httpx.HTTPError as exc:
                print(f"[Attachments] failed to upload attachments for {page_key}: {exc}")
                return False

    def _delete_removed_attachments(self, removed: set[str]) -> bool:
        if not removed:
            return True
        if not self._http_client:
            print(f"[Attachments] skipped server delete for {len(removed)} file(s) (no API client)")
            return True
        try:
            resp = self._http_client.post("/files/delete", json={"paths": sorted(removed)})
            if resp.status_code == 401 and self._auth_prompt:
                if self._auth_prompt():
                    resp = self._http_client.post("/files/delete", json={"paths": sorted(removed)})
            resp.raise_for_status()
            print(f"[Attachments] deleted {len(removed)} attachment(s) for panel")
            return True
        except httpx.HTTPError as exc:
            print(f"[Attachments] failed to delete attachments on server: {exc}")
            return False
    
    def _get_file_icon(self, file_path: Path) -> QIcon:
        """Get an icon for a file - thumbnail for images, OS icon for others."""
        if file_path.suffix.lower() in ['.png', '.jpg', '.jpeg', '.gif', '.bmp']:
            # Create thumbnail for image files
            load_t0 = time.perf_counter()
            pixmap = QPixmap(str(file_path))
            if not pixmap.isNull():
                # Scale based on zoom level
                if self.zoom_level == 1:  # Small
                    size = 48
                elif self.zoom_level == 2:  # Medium
                    size = 96
                else:  # Large
                    size = 144
                
                scaled = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                if PAGE_LOGGING_ENABLED:
                    print(f"[PageLoadAndRender] attachments thumbnail {file_path.name} load+scale={(time.perf_counter()-load_t0)*1000:.1f}ms")
                return QIcon(scaled)
        
        # Use OS file icon for non-images or if thumbnail failed
        from PySide6.QtCore import QFileInfo
        return self.icon_provider.icon(QFileInfo(str(file_path)))
    
    def _update_view_mode(self) -> None:
        """Update the list widget view mode based on zoom level."""
        if self.zoom_level == 0:
            # List view
            self.attachments_list.setViewMode(QListWidget.ListMode)
            self.attachments_list.setIconSize(QSize(16, 16))
            self.attachments_list.setGridSize(QSize())
        else:
            # Icon view
            self.attachments_list.setViewMode(QListWidget.IconMode)
            self.attachments_list.setResizeMode(QListWidget.Adjust)
            self.attachments_list.setSpacing(10)
            
            if self.zoom_level == 1:  # Small
                self.attachments_list.setIconSize(QSize(48, 48))
                self.attachments_list.setGridSize(QSize(80, 80))
            elif self.zoom_level == 2:  # Medium
                self.attachments_list.setIconSize(QSize(96, 96))
                self.attachments_list.setGridSize(QSize(128, 128))
            else:  # Large
                self.attachments_list.setIconSize(QSize(144, 144))
                self.attachments_list.setGridSize(QSize(176, 176))
    
    def _open_folder(self) -> None:
        """Open the attachments folder in the system file manager."""
        if not self.current_page_path:
            return
        
        page_folder = self.current_page_path.parent
        
        if page_folder.exists() and page_folder.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(page_folder)))
    
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
    
    def _open_attachment(self, item: QListWidgetItem) -> None:
        """Open the selected attachment. .puml files open in PlantUML editor, others use default handler."""
        data = item.data(Qt.UserRole)
        if self._remote_mode and isinstance(data, dict):
            rel_path = data.get("path")
            if not rel_path or not self._api_base:
                return
            if str(rel_path).lower().endswith(".puml"):
                self.plantumlEditorRequested.emit(
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
                    print(f"[Attachments] Double-click .puml -> open editor: {data}")
                    self.plantumlEditorRequested.emit(data)
                else:
                    # Open with default system handler
                    print(f"[Attachments] Double-click non-puml -> open default: {data}")
                    QDesktopServices.openUrl(QUrl.fromLocalFile(str(file_path)))
    
    def refresh(self) -> None:
        """Refresh the attachments list."""
        self._refresh_attachments()

    def _on_attachments_context_menu(self, pos) -> None:
        """Handle right-click context menu on attachments list."""
        menu = QMenu(self)
        
        # Add "Add new PlantUML..." action
        add_plantuml_action = menu.addAction("Add new PlantUML...")
        add_plantuml_action.triggered.connect(self._create_new_plantuml)
        
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
        except Exception as exc:
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.critical(self, "Error", f"Failed to create file: {exc}")
