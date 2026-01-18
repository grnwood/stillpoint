from __future__ import annotations

from pathlib import Path
import os
import time
from typing import Optional

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from sp.server.adapters.files import PAGE_SUFFIX


class AddVaultDialog(QDialog):
    """Dialog for capturing a vault name and folder path."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: Optional[dict[str, str]] = None
        self.setWindowTitle("Add Vault")
        self.setModal(True)
        self.resize(420, 180)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My Notes")
        form.addRow("Vault Name:", self.name_edit)

        path_row = QHBoxLayout()
        self.path_edit = QLineEdit()
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse)
        path_row.addWidget(self.path_edit, 1)
        path_row.addWidget(browse_btn)
        path_container = QWidget()
        path_container.setLayout(path_row)
        form.addRow("Vault Folder:", path_container)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _browse(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Vault Folder", str(Path.home()))
        if directory:
            self.path_edit.setText(directory)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(directory).name)

    def accept(self) -> None:  # type: ignore[override]
        name = self.name_edit.text().strip()
        path = self.path_edit.text().strip()
        if not name or not path:
            QMessageBox.warning(self, "Missing Info", "Please provide both a vault name and folder.")
            return
        path_obj = Path(path)
        if not path_obj.exists() or not path_obj.is_dir():
            QMessageBox.warning(self, "Folder Not Found", "Please choose an existing vault folder.")
            return
        self._result = {"name": name, "path": path}
        super().accept()

    def selected_vault(self) -> Optional[dict[str, str]]:
        return self._result


class OpenVaultDialog(QDialog):
    """Dialog for selecting, adding, and managing vaults."""

    def __init__(
        self,
        parent=None,
        current_vault: Optional[str] = None,
        vaults: Optional[list[dict[str, str]]] = None,
        select_id: Optional[str] = None,
        on_add_remote=None,
        on_load_remote=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Open Vault")
        self.setModal(True)
        self.resize(520, 520)

        self._on_add_remote = on_add_remote
        self._on_load_remote = on_load_remote
        self.local_vaults: list[dict[str, str]] = vaults if vaults is not None else config.load_known_vaults()
        if not self.local_vaults and current_vault:
            self.local_vaults.append({"name": Path(current_vault).name, "path": current_vault})
        self.remote_vaults: list[dict[str, str]] = []
        self.remote_status_entries: list[dict[str, str]] = []
        self.default_vault: Optional[str] = config.load_default_vault()
        self._selected: Optional[dict[str, str]] = None
        self._select_id = select_id
        self._remote_loaded = False

        layout = QVBoxLayout(self)
        intro_row = QHBoxLayout()
        icon_label = QLabel()
        icon = QApplication.instance().windowIcon() if QApplication.instance() else None
        if icon:
            pixmap = icon.pixmap(48, 48)
            if not pixmap.isNull():
                icon_label.setPixmap(pixmap)
                icon_label.setAlignment(Qt.AlignTop)
                intro_row.addWidget(icon_label)
        intro = QLabel("Choose a vault to open. Double-click an entry to launch it immediately.")
        intro.setWordWrap(True)
        intro_row.addWidget(intro, 1)
        layout.addLayout(intro_row)

        self.tabs = QTabWidget()
        self.tabs.currentChanged.connect(self._on_tab_changed)
        layout.addWidget(self.tabs, 1)

        local_tab = QWidget()
        local_layout = QVBoxLayout(local_tab)
        local_layout.setContentsMargins(0, 0, 0, 0)
        local_layout.setSpacing(6)
        self.local_list_widget = QListWidget()
        self.local_list_widget.itemDoubleClicked.connect(self._accept_current)
        self.local_list_widget.currentItemChanged.connect(self._on_selection_changed)
        local_layout.addWidget(self.local_list_widget, 1)

        local_controls = QHBoxLayout()
        self.add_btn = QPushButton("Add Vault")
        self.add_btn.clicked.connect(self._add_vault)
        self.remove_btn = QPushButton("Remove Selected")
        self.remove_btn.clicked.connect(self._remove_selected)
        self.edit_configs_btn = QPushButton("Edit Vault Configs")
        self.edit_configs_btn.clicked.connect(self._open_config_file)
        local_controls.addWidget(self.add_btn)
        local_controls.addWidget(self.remove_btn)
        local_controls.addWidget(self.edit_configs_btn)
        local_controls.addStretch(1)
        local_layout.addLayout(local_controls)
        self.tabs.addTab(local_tab, "Local Vaults")

        remote_tab = QWidget()
        remote_layout = QVBoxLayout(remote_tab)
        remote_layout.setContentsMargins(0, 0, 0, 0)
        remote_layout.setSpacing(6)
        self.remote_list_widget = QListWidget()
        self.remote_list_widget.itemDoubleClicked.connect(self._accept_current)
        self.remote_list_widget.currentItemChanged.connect(self._on_selection_changed)
        remote_layout.addWidget(self.remote_list_widget, 1)

        remote_controls = QHBoxLayout()
        self.add_remote_btn = QPushButton("Add Remote")
        self.add_remote_btn.clicked.connect(self._add_remote)
        if not self._on_add_remote:
            self.add_remote_btn.setEnabled(False)
        self.remove_remote_btn = QPushButton("Remove Selected")
        self.remove_remote_btn.clicked.connect(self._remove_remote_selected)
        self.edit_configs_remote_btn = QPushButton("Edit Vault Configs")
        self.edit_configs_remote_btn.clicked.connect(self._open_config_file)
        remote_controls.addWidget(self.add_remote_btn)
        remote_controls.addWidget(self.remove_remote_btn)
        remote_controls.addWidget(self.edit_configs_remote_btn)
        remote_controls.addStretch(1)
        remote_layout.addLayout(remote_controls)
        self.tabs.addTab(remote_tab, "Remote Vaults")

        default_row = QHBoxLayout()
        default_row.addWidget(QLabel("Default vault:"))
        self.default_combo = QComboBox()
        self.default_combo.currentIndexChanged.connect(self._on_default_changed)
        default_row.addWidget(self.default_combo, 1)
        layout.addLayout(default_row)

        self.button_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.button_box.accepted.connect(self._accept_current)
        self.button_box.rejected.connect(self.reject)
        open_new_btn = self.button_box.addButton("Open in New Window", QDialogButtonBox.ActionRole)
        open_new_btn.clicked.connect(self._accept_new_window)
        layout.addWidget(self.button_box)

        self._refresh_local_list(select_path=current_vault or self.default_vault)
        if self._select_id and str(self._select_id).startswith("remote::"):
            self._select_id = None

    def selected_vault(self) -> Optional[dict[str, str]]:
        return self._selected

    def selected_vault_new_window(self) -> Optional[dict[str, str]]:
        if getattr(self, "_open_new_window", False):
            return self._selected
        return None

    def _populate_list(
        self,
        list_widget: QListWidget,
        vaults: list[dict[str, str]],
        *,
        select_path: Optional[str] = None,
        select_id: Optional[str] = None,
    ) -> None:
        list_widget.clear()
        for vault in vaults:
            if "id" not in vault:
                vault["id"] = vault.get("path")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, vault)
            widget = self._build_item_widget(vault)
            item.setSizeHint(widget.sizeHint())
            list_widget.addItem(item)
            list_widget.setItemWidget(item, widget)

        if vaults:
            target_id = select_id or select_path or vaults[0].get("path")
            for idx in range(list_widget.count()):
                item = list_widget.item(idx)
                data = item.data(Qt.UserRole)
                if data and data.get("id") == target_id:
                    list_widget.setCurrentItem(item)
                    break

    def _refresh_local_list(self, select_path: Optional[str] = None) -> None:
        self._populate_list(
            self.local_list_widget,
            self.local_vaults,
            select_path=select_path,
            select_id=self._select_id,
        )
        self._refresh_default_combo()
        self._update_buttons()

    def _refresh_remote_list(self, select_id: Optional[str] = None) -> None:
        self._populate_remote_list(select_id=select_id)
        self._update_buttons()

    def _refresh_default_combo(self) -> None:
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("No default", None)
        for vault in self.local_vaults:
            self.default_combo.addItem(vault["name"], vault["path"])
        idx = self.default_combo.findData(self.default_vault)
        if idx != -1:
            self.default_combo.setCurrentIndex(idx)
        else:
            if self.default_vault is not None:
                config.save_default_vault(None)
            self.default_vault = None
            self.default_combo.setCurrentIndex(0)
        self.default_combo.blockSignals(False)

    def _build_item_widget(self, vault: dict[str, str]) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        # Create a horizontal layout for name and status indicator
        name_row = QHBoxLayout()
        name_row.setSpacing(8)
        
        name_label = QLabel(vault.get("name") or Path(vault["path"]).name)
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        name_row.addWidget(name_label, 1)
        
        # Add status indicator for remote vaults
        if vault.get("kind") == "remote":
            status = vault.get("status", "unknown")
            if status == "error":
                status_label = QLabel("●")
                status_label.setStyleSheet("color: #d32f2f; font-size: 16pt;")
                status_label.setToolTip(vault.get("error", "Connection failed"))
                name_row.addWidget(status_label)
                # Make the name label red too
                name_label.setStyleSheet("color: #d32f2f;")
            elif status == "ok":
                status_label = QLabel("●")
                status_label.setStyleSheet("color: #4caf50; font-size: 16pt;")
                status_label.setToolTip("Connected")
                name_row.addWidget(status_label)
        
        layout.addLayout(name_row)

        path_label = QLabel(self._format_vault_path(vault))
        path_label.setWordWrap(True)
        path_font = path_label.font()
        path_font.setPointSize(max(path_font.pointSize() - 2, 8))
        path_label.setFont(path_font)
        # Gray color unless there's an error
        if vault.get("kind") == "remote" and vault.get("status") == "error":
            path_label.setStyleSheet("color: #d32f2f;")
        else:
            path_label.setStyleSheet("color: #666;")
        layout.addWidget(path_label)
        
        # Add error message if present
        if vault.get("kind") == "remote" and vault.get("error"):
            error_label = QLabel(f"Error: {vault.get('error')}")
            error_label.setWordWrap(True)
            error_font = error_label.font()
            error_font.setPointSize(max(error_font.pointSize() - 2, 8))
            error_label.setFont(error_font)
            error_label.setStyleSheet("color: #d32f2f;")
            layout.addWidget(error_label)

        return container

    @staticmethod
    def _format_vault_path(vault: dict[str, str]) -> str:
        if vault.get("kind") == "remote":
            server = vault.get("server_url") or ""
            display = server.replace("http://", "").replace("https://", "")
            path = vault.get("path") or ""
            if path and not path.startswith("/"):
                path = f"/{path}"
            return f"{display}{path}"
        return vault.get("path") or ""

    def _on_selection_changed(self, current, previous) -> None:  # noqa: ARG002
        self._update_buttons()

    def _on_tab_changed(self, index: int) -> None:
        if index == 1:
            self._load_remote_vaults(select_id=self._select_id)
        self._update_buttons()

    def _load_remote_vaults(self, select_id: Optional[str] = None) -> None:
        debug = os.getenv("ZIMX_DEBUG_REMOTE_VAULTS", "0") not in ("0", "false", "False", "")
        start = time.perf_counter()
        if self._remote_loaded:
            if select_id:
                self._refresh_remote_list(select_id=select_id)
            return
        if not self._on_load_remote:
            self.remote_vaults = []
            self.remote_status_entries = []
            self._remote_loaded = True
            self._refresh_remote_list(select_id=select_id)
            if debug:
                print(f"[RemoteVaults] load skipped (no loader) dt={(time.perf_counter()-start)*1000:.1f}ms")
            return
        self._set_remote_loading_entries()
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            updated = self._on_load_remote() if callable(self._on_load_remote) else None
        finally:
            QApplication.restoreOverrideCursor()
        status_entries: list[dict[str, str]] = []
        if isinstance(updated, tuple) and len(updated) == 2:
            updated, status_entries = updated
        if updated is not None:
            if any(v.get("kind") == "remote" for v in updated):
                self.remote_vaults = [v for v in updated if v.get("kind") == "remote"]
            else:
                self.remote_vaults = list(updated)
        self.remote_status_entries = list(status_entries)
        self._remote_loaded = True
        self._refresh_remote_list(select_id=select_id)
        if debug:
            print(
                f"[RemoteVaults] loaded {len(self.remote_vaults)} vault(s) "
                f"dt={(time.perf_counter()-start)*1000:.1f}ms"
            )

    def _split_vaults(self, vaults: list[dict[str, str]]) -> None:
        self.local_vaults = [v for v in vaults if v.get("kind") != "remote"]
        self.remote_vaults = [v for v in vaults if v.get("kind") == "remote"]

    def _active_list_widget(self) -> QListWidget:
        return self.remote_list_widget if self.tabs.currentIndex() == 1 else self.local_list_widget

    def _update_buttons(self) -> None:
        if not hasattr(self, "button_box"):
            return
        current_list = self._active_list_widget()
        current_item = current_list.currentItem()
        has_selection = current_item is not None
        current_data = current_item.data(Qt.UserRole) if current_item else None
        is_remote_vault = isinstance(current_data, dict) and current_data.get("kind") == "remote"
        can_remove = False
        if has_selection and current_list is self.local_list_widget:
            data = current_list.currentItem().data(Qt.UserRole)
            can_remove = bool(data)
        self.remove_btn.setEnabled(can_remove)
        self.remove_remote_btn.setEnabled(
            current_list is self.remote_list_widget and is_remote_vault
        )
        ok_button = self.button_box.button(QDialogButtonBox.Ok)
        if ok_button:
            ok_button.setEnabled(has_selection)

    def _accept_current(self) -> None:
        item = self._active_list_widget().currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault:
            return
        self._selected = dict(vault)
        self._open_new_window = False
        self.accept()

    def _accept_new_window(self) -> None:
        item = self._active_list_widget().currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault:
            return
        self._selected = dict(vault)
        self._open_new_window = True
        self.accept()

    def _add_vault(self) -> None:
        dlg = AddVaultDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        result = dlg.selected_vault()
        if not result:
            return
        self._seed_new_vault(Path(result["path"]))
        self.local_vaults = [v for v in self.local_vaults if v.get("path") != result["path"]]
        self.local_vaults.insert(0, result)
        config.remember_vault(result["path"], result["name"])
        self._refresh_local_list(select_path=result["path"])

    def _add_remote(self) -> None:
        if not self._on_add_remote:
            return
        updated = self._on_add_remote()
        if not updated:
            return
        self._split_vaults(updated)
        self._select_id = None
        self._refresh_local_list()
        if self._remote_loaded:
            self._refresh_remote_list()

    def _remove_selected(self) -> None:
        item = self.local_list_widget.currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault:
            return
        if vault.get("kind") == "remote":
            return
        path = vault.get("path")
        self.local_vaults = [v for v in self.local_vaults if v.get("path") != path]
        if path:
            config.delete_known_vault(path)
            if self.default_vault == path:
                self.default_vault = None
                config.save_default_vault(None)
        next_selection = self.local_vaults[0]["path"] if self.local_vaults else None
        self._refresh_local_list(select_path=next_selection)

    def _remove_remote_selected(self) -> None:
        item = self.remote_list_widget.currentItem()
        if not item:
            return
        vault = item.data(Qt.UserRole)
        if not vault or vault.get("kind") != "remote":
            return
        
        server_url = vault.get("server_url")
        path = vault.get("path")
        if not server_url or not path:
            return
        try:
            from urllib.parse import urlparse

            parsed = urlparse(server_url)
            host = parsed.hostname
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            scheme = parsed.scheme or "http"
        except Exception:
            return
        if not host:
            return
        
        servers = config.load_remote_servers()
        changed = False
        for entry in servers:
            if (
                entry.get("host") == host
                and str(entry.get("port")) == str(port)
                and entry.get("scheme", "http") == scheme
            ):
                selected = entry.get("selected_vaults", [])
                if isinstance(selected, list) and path in selected:
                    entry["selected_vaults"] = [p for p in selected if p != path]
                    changed = True
        if changed:
            config.save_remote_servers(servers)
        self.remote_vaults = [v for v in self.remote_vaults if v.get("id") != vault.get("id")]
        self._refresh_remote_list()

    def _remove_remote_server_by_url(self, server_url: str) -> None:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(server_url)
            host = parsed.hostname
            port = parsed.port
            scheme = parsed.scheme or "http"
        except Exception:
            return
        if not host or not port:
            return
        config.delete_remote_server(host, int(port), scheme=scheme)

    def _populate_remote_list(self, select_id: Optional[str] = None) -> None:
        self.remote_list_widget.clear()
        
        # Only show configured vaults with embedded status
        for vault in self.remote_vaults:
            if "id" not in vault:
                vault["id"] = vault.get("path")
            item = QListWidgetItem()
            item.setData(Qt.UserRole, vault)
            widget = self._build_item_widget(vault)
            item.setSizeHint(widget.sizeHint())
            self.remote_list_widget.addItem(item)
            self.remote_list_widget.setItemWidget(item, widget)

        if select_id:
            for idx in range(self.remote_list_widget.count()):
                item = self.remote_list_widget.item(idx)
                data = item.data(Qt.UserRole)
                if data and data.get("id") == select_id:
                    self.remote_list_widget.setCurrentItem(item)
                    break

    def _build_status_item_widget(self, entry: dict[str, str]) -> QWidget:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        name_label = QLabel(entry.get("display") or entry.get("server_url") or entry.get("message") or "Remote Server")
        name_font = name_label.font()
        name_font.setBold(True)
        name_label.setFont(name_font)
        level = entry.get("level")
        if level == "error":
            name_label.setStyleSheet("color: #ff3b30;")
        layout.addWidget(name_label)

        path_label = QLabel(entry.get("server_url") or "")
        path_label.setWordWrap(True)
        path_font = path_label.font()
        path_font.setPointSize(max(path_font.pointSize() - 2, 8))
        path_label.setFont(path_font)
        if level == "error":
            path_label.setStyleSheet("color: #ff3b30;")
        else:
            path_label.setStyleSheet("color: #666;")
        layout.addWidget(path_label)

        message = entry.get("message") or ""
        if message:
            container.setToolTip(message)
        return container

    def _set_remote_loading_entries(self) -> None:
        entries: list[dict[str, str]] = []
        for server in config.load_remote_servers():
            host = server.get("host")
            port = server.get("port")
            scheme = server.get("scheme") or "http"
            if not host or not port:
                continue
            base_url = f"{scheme}://{host}:{port}"
            display = base_url.replace("http://", "").replace("https://", "")
            entries.append(
                {
                    "kind": "remote_status",
                    "level": "loading",
                    "server_url": base_url,
                    "display": display,
                    "message": "Loading remote vaults…",
                }
            )
        self.remote_status_entries = entries
        self._refresh_remote_list()
        try:
            QApplication.processEvents()
        except Exception:
            pass

    def _open_config_file(self) -> None:
        try:
            path = config.GLOBAL_CONFIG
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("{}", encoding="utf-8")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))
        except Exception:
            pass

    def _on_default_changed(self, index: int) -> None:
        path = self.default_combo.itemData(index)
        self.default_vault = path
        config.save_default_vault(path)

    def _seed_new_vault(self, root: Path) -> None:
        """
        Ensure vault is seeded only in subfolder: /vaultfolder/vaultfolder/vaultfolder.md
        Never create a file in /vaultfolder/vaultfolder.md
        """
        try:
            existing_items = list(root.iterdir())
        except Exception:
            return
        # Only seed if the root folder is empty
        if existing_items:
            return
        root_dir = root / root.name
        try:
            root_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            return
        root_page = root_dir / f"{root.name}{PAGE_SUFFIX}"
        if not root_page.exists():
            root_page.write_text(
                f"# {root.name}\n\nWelcome to your vault. Use the tree to add new pages.\n",
                encoding="utf-8",
            )
        # Ensure no file is created in the root vault folder (root/vaultname.md)
        root_file = root / f"{root.name}{PAGE_SUFFIX}"
        if root_file.exists():
            try:
                root_file.unlink()
            except Exception:
                pass
