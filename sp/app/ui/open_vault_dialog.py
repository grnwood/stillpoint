from __future__ import annotations

import hashlib
from pathlib import Path
import os
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from sp.logging_flags import log_enabled
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


class AddHomebaseVaultDialog(QDialog):
    """Dialog for creating/connecting a Homebase vault profile."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._result: Optional[dict[str, str]] = None
        self.setWindowTitle("Add Homebase Vault")
        self.setModal(True)
        self.resize(560, 420)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("My Homebase Vault")
        form.addRow("Display Name:", self.name_edit)

        local_row = QHBoxLayout()
        self.local_path_edit = QLineEdit()
        local_btn = QPushButton("Browse…")
        local_btn.clicked.connect(self._browse_local)
        local_row.addWidget(self.local_path_edit, 1)
        local_row.addWidget(local_btn)
        local_wrap = QWidget()
        local_wrap.setLayout(local_row)
        form.addRow("Local Vault Folder:", local_wrap)

        self.server_url_edit = QLineEdit()
        self.server_url_edit.setPlaceholderText("http://127.0.0.1:8080")
        form.addRow("Homebase Server URL:", self.server_url_edit)
        self.ignore_invalid_ssl_checkbox = QCheckBox("Ignore invalid SSL certificates")
        self.ignore_invalid_ssl_checkbox.setChecked(False)
        form.addRow("", self.ignore_invalid_ssl_checkbox)

        self.admin_password_edit = QLineEdit()
        self.admin_password_edit.setEchoMode(QLineEdit.Password)
        self.admin_password_edit.setPlaceholderText("Required for create/query")
        form.addRow("Server Admin Password:", self.admin_password_edit)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Connect Existing", "connect")
        self.mode_combo.addItem("Create New", "create")
        self.mode_combo.currentIndexChanged.connect(self._update_mode)
        form.addRow("Mode:", self.mode_combo)

        self.vault_id_edit = QLineEdit()
        self.vault_id_edit.setPlaceholderText("Existing Homebase vault id")
        vault_id_row = QHBoxLayout()
        vault_id_row.setContentsMargins(0, 0, 0, 0)
        vault_id_row.setSpacing(6)
        self.query_vaults_btn = QPushButton("Query…")
        self.query_vaults_btn.clicked.connect(self._query_homebase_vaults)
        vault_id_row.addWidget(self.vault_id_edit, 1)
        vault_id_row.addWidget(self.query_vaults_btn)
        vault_id_wrap = QWidget()
        vault_id_wrap.setLayout(vault_id_row)
        form.addRow("Homebase Vault ID:", vault_id_wrap)

        self.vault_name_edit = QLineEdit()
        self.vault_name_edit.setPlaceholderText("Optional remote vault name")
        form.addRow("Remote Vault Name:", self.vault_name_edit)

        self.username_edit = QLineEdit()
        self.username_edit.setPlaceholderText("username")
        form.addRow("Username:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("password")
        form.addRow("Password:", self.password_edit)

        self.passphrase_edit = QLineEdit()
        self.passphrase_edit.setEchoMode(QLineEdit.Password)
        self.passphrase_edit.setPlaceholderText("Shared encryption passphrase")
        form.addRow("Homebase Passphrase:", self.passphrase_edit)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self._update_mode()

    def _browse_local(self) -> None:
        directory = QFileDialog.getExistingDirectory(self, "Select Local Vault Folder", str(Path.home()))
        if directory:
            self.local_path_edit.setText(directory)
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(directory).name)

    def _update_mode(self) -> None:
        create_mode = self.mode_combo.currentData() == "create"
        self.vault_id_edit.setEnabled(not create_mode)
        self.vault_name_edit.setEnabled(create_mode)
        self.query_vaults_btn.setEnabled(not create_mode)
        self.admin_password_edit.setEnabled(True)

    def _query_homebase_vaults(self) -> None:
        server_url = self.server_url_edit.text().strip().rstrip("/")
        admin_password = self.admin_password_edit.text().strip()
        verify_ssl = not bool(self.ignore_invalid_ssl_checkbox.isChecked())
        if not server_url:
            QMessageBox.warning(self, "Missing Server URL", "Enter the Homebase server URL first.")
            return
        if not admin_password:
            QMessageBox.warning(self, "Missing Admin Password", "Vault discovery requires server admin password.")
            return
        headers = {"x-server-admin-password": hashlib.sha256(admin_password.encode("utf-8")).hexdigest()}
        url = f"{server_url}/v1/homebase/bootstrap/vaults"
        try:
            resp = httpx.get(url, headers=headers, timeout=20.0, verify=verify_ssl)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            details = str(exc)
            try:
                details = resp.text[:500] or details  # type: ignore[name-defined]
            except Exception:
                pass
            QMessageBox.critical(self, "Homebase Query Failed", details)
            return
        raw_vaults = data.get("vaults")
        if not isinstance(raw_vaults, list):
            QMessageBox.warning(self, "No Vaults", "Server returned no vault entries.")
            return
        label_to_id: dict[str, str] = {}
        label_to_name: dict[str, str] = {}
        labels: list[str] = []
        for item in raw_vaults:
            if not isinstance(item, dict):
                continue
            vault_id = str(item.get("vault_id") or "").strip()
            if not vault_id:
                continue
            vault_name = str(item.get("vault_name") or "").strip()
            created_at = str(item.get("created_at") or "").strip()
            name_part = vault_name or "(unnamed)"
            created_part = f"  |  {created_at}" if created_at else ""
            label = f"{name_part}  |  {vault_id}{created_part}"
            labels.append(label)
            label_to_id[label] = vault_id
            label_to_name[label] = vault_name
        if not labels:
            QMessageBox.information(self, "No Vaults", "No Homebase vaults found on this server.")
            return
        selected, ok = QInputDialog.getItem(
            self,
            "Select Homebase Vault",
            "Choose a Homebase vault ID:",
            labels,
            0,
            False,
        )
        if not ok or not selected:
            return
        vault_id = label_to_id.get(selected, "")
        if vault_id:
            self.vault_id_edit.setText(vault_id)
        vault_name = label_to_name.get(selected, "")
        if vault_name and not self.name_edit.text().strip():
            self.name_edit.setText(vault_name)

    def accept(self) -> None:  # type: ignore[override]
        local_path = self.local_path_edit.text().strip()
        server_url = self.server_url_edit.text().strip().rstrip("/")
        verify_ssl = not bool(self.ignore_invalid_ssl_checkbox.isChecked())
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        passphrase = self.passphrase_edit.text()
        mode = str(self.mode_combo.currentData() or "connect")
        if not local_path or not server_url or not username or not password or not passphrase:
            QMessageBox.warning(self, "Missing Info", "Local path, server URL, username, password, and passphrase are required.")
            return
        local_root = Path(local_path)
        if not local_root.exists() or not local_root.is_dir():
            QMessageBox.warning(self, "Folder Not Found", "Choose an existing local vault folder.")
            return
        headers: dict[str, str] = {}
        payload: dict[str, str] = {"username": username, "password": password}
        if mode == "create":
            admin_password = self.admin_password_edit.text().strip()
            if not admin_password:
                QMessageBox.warning(self, "Missing Admin Password", "Create mode requires server admin password.")
                return
            headers["x-server-admin-password"] = hashlib.sha256(admin_password.encode("utf-8")).hexdigest()
            remote_name = self.vault_name_edit.text().strip()
            if remote_name:
                payload["vault_name"] = remote_name
            url = f"{server_url}/v1/homebase/bootstrap/create"
        else:
            vault_id = self.vault_id_edit.text().strip()
            if not vault_id:
                QMessageBox.warning(self, "Missing Vault ID", "Connect mode requires Homebase vault id.")
                return
            payload["vault_id"] = vault_id
            url = f"{server_url}/v1/homebase/bootstrap/connect"
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=20.0, verify=verify_ssl)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            details = str(exc)
            try:
                details = resp.text[:500] or details  # type: ignore[name-defined]
            except Exception:
                pass
            QMessageBox.critical(self, "Homebase Setup Failed", details)
            return

        vault_id = str(data.get("vault_id") or "").strip()
        access_token = str(data.get("access_token") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
        if not vault_id or not access_token:
            QMessageBox.critical(self, "Homebase Setup Failed", "Server did not return a valid token payload.")
            return
        display_name = self.name_edit.text().strip() or local_root.name
        self._result = {
            "id": f"homebase::{server_url}::{vault_id}::{local_path}",
            "kind": "homebase",
            "name": display_name,
            "path": str(local_root),
            "server_url": server_url,
            "verify_ssl": bool(verify_ssl),
            "vault_id": vault_id,
            "username": username,
            "access_token": access_token,
            "refresh_token": refresh_token,
            "passphrase": passphrase,
            "auto_sync": True,
            "interval_seconds": 60,
            "push_debounce_seconds": 3,
            "max_parallel_transfers": 6,
        }
        super().accept()

    def selected_profile(self) -> Optional[dict[str, str]]:
        return self._result


class OpenVaultDialog(QDialog):
    """Dialog for selecting, adding, and managing vaults."""
    @staticmethod
    def _is_help_vault_path(path: Optional[str]) -> bool:
        if not path:
            return False
        try:
            return Path(path).name.lower() == "help-vault"
        except Exception:
            return False

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
        self.homebase_vaults: list[dict[str, str]] = config.load_homebase_vault_profiles()
        homebase_paths = {
            str(v.get("path") or "").strip()
            for v in self.homebase_vaults
            if str(v.get("path") or "").strip()
        }
        self.local_vaults = [
            v
            for v in self.local_vaults
            if not self._is_help_vault_path(v.get("path"))
            and str(v.get("path") or "").strip() not in homebase_paths
        ]
        if not self.local_vaults and current_vault and not self._is_help_vault_path(current_vault):
            self.local_vaults.append({"name": Path(current_vault).name, "path": current_vault})
        self.remote_vaults: list[dict[str, str]] = []
        self.remote_status_entries: list[dict[str, str]] = []
        self.default_vault: Optional[str] = config.load_default_vault()
        self._selected: Optional[dict[str, str]] = None
        self._select_id = select_id
        self._remote_loaded = False
        self._remote_vaults_enabled = config.load_feature_remote_vaults_enabled()
        self._homebase_vaults_enabled = config.load_feature_homebase_vaults_enabled()

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

        self.remote_list_widget = None
        self.add_remote_btn = None
        self.remove_remote_btn = None
        self.edit_configs_remote_btn = None
        if self._remote_vaults_enabled:
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

        self.homebase_list_widget = None
        self.add_homebase_btn = None
        self.remove_homebase_btn = None
        self.edit_configs_homebase_btn = None
        if self._homebase_vaults_enabled:
            homebase_tab = QWidget()
            homebase_layout = QVBoxLayout(homebase_tab)
            homebase_layout.setContentsMargins(0, 0, 0, 0)
            homebase_layout.setSpacing(6)
            self.homebase_list_widget = QListWidget()
            self.homebase_list_widget.itemDoubleClicked.connect(self._accept_current)
            self.homebase_list_widget.currentItemChanged.connect(self._on_selection_changed)
            homebase_layout.addWidget(self.homebase_list_widget, 1)

            homebase_controls = QHBoxLayout()
            self.add_homebase_btn = QPushButton("Add Homebase")
            self.add_homebase_btn.clicked.connect(self._add_homebase)
            self.remove_homebase_btn = QPushButton("Remove Selected")
            self.remove_homebase_btn.clicked.connect(self._remove_homebase_selected)
            self.edit_configs_homebase_btn = QPushButton("Edit Vault Configs")
            self.edit_configs_homebase_btn.clicked.connect(self._open_config_file)
            homebase_controls.addWidget(self.add_homebase_btn)
            homebase_controls.addWidget(self.remove_homebase_btn)
            homebase_controls.addWidget(self.edit_configs_homebase_btn)
            homebase_controls.addStretch(1)
            homebase_layout.addLayout(homebase_controls)
            self.tabs.addTab(homebase_tab, "Homebase Vaults")

        self.tabs.currentChanged.connect(self._on_tab_changed)

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
        self._refresh_homebase_list(select_id=self._select_id)
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
            if target_id and self._is_help_vault_path(str(target_id)):
                target_id = vaults[0].get("path")
            for idx in range(list_widget.count()):
                item = list_widget.item(idx)
                data = item.data(Qt.UserRole)
                if data and data.get("id") == target_id:
                    list_widget.setCurrentItem(item)
                    break
            if list_widget.currentItem() is None and list_widget.count() > 0:
                list_widget.setCurrentItem(list_widget.item(0))

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
        if not self.remote_list_widget:
            return
        self._populate_remote_list(select_id=select_id)
        self._update_buttons()

    def _refresh_homebase_list(self, select_id: Optional[str] = None) -> None:
        if not self.homebase_list_widget:
            return
        self._populate_list(
            self.homebase_list_widget,
            self.homebase_vaults,
            select_id=select_id,
        )
        self._update_buttons()

    def _refresh_default_combo(self) -> None:
        self.default_combo.blockSignals(True)
        self.default_combo.clear()
        self.default_combo.addItem("No default", None)
        for vault in self.local_vaults:
            if self._is_help_vault_path(vault.get("path")):
                continue
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
            display = OpenVaultDialog._format_remote_server(server, include_scheme=False)
            path = vault.get("path") or ""
            if path and not path.startswith("/"):
                path = f"/{path}"
            return f"{display}{path}"
        if vault.get("kind") == "homebase":
            server = vault.get("server_url") or ""
            vault_id = vault.get("vault_id") or ""
            local_path = vault.get("path") or ""
            return f"{OpenVaultDialog._format_remote_server(server, include_scheme=False)}  |  id:{vault_id}  |  {local_path}"
        return vault.get("path") or ""

    @staticmethod
    def _format_remote_server(server_url: str, include_scheme: bool = False) -> str:
        parsed = urlparse(server_url or "")
        scheme = parsed.scheme or "http"
        host = parsed.hostname or server_url
        port = parsed.port
        is_standard = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        host_port = f"{host}:{port}" if port and not is_standard else host
        if include_scheme:
            return f"{scheme}://{host_port}"
        return host_port

    def _on_selection_changed(self, current, previous) -> None:  # noqa: ARG002
        self._update_buttons()

    def _on_tab_changed(self, index: int) -> None:
        current_widget = self.tabs.widget(index)
        remote_list = getattr(self, "remote_list_widget", None)
        homebase_list = getattr(self, "homebase_list_widget", None)
        if self._remote_vaults_enabled and remote_list and current_widget and remote_list.parentWidget() == current_widget:
            self._load_remote_vaults(select_id=self._select_id)
        if self._homebase_vaults_enabled and homebase_list and current_widget and homebase_list.parentWidget() == current_widget:
            self._refresh_homebase_list(select_id=self._select_id)
        self._update_buttons()

    def _load_remote_vaults(self, select_id: Optional[str] = None) -> None:
        if not self._remote_vaults_enabled or not self.remote_list_widget:
            return
        debug = log_enabled("remote_vaults")
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
        current = self.tabs.currentWidget()
        if self._remote_vaults_enabled and self.remote_list_widget and current and self.remote_list_widget.parentWidget() == current:
            return self.remote_list_widget
        if self._homebase_vaults_enabled and self.homebase_list_widget and current and self.homebase_list_widget.parentWidget() == current:
            return self.homebase_list_widget
        return self.local_list_widget

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
        if self.remove_remote_btn and self.remote_list_widget:
            self.remove_remote_btn.setEnabled(
                current_list is self.remote_list_widget and is_remote_vault
            )
        if self.remove_homebase_btn and self.homebase_list_widget:
            self.remove_homebase_btn.setEnabled(
                current_list is self.homebase_list_widget and isinstance(current_data, dict) and current_data.get("kind") == "homebase"
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
        if not self._remote_vaults_enabled:
            return
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

    def _add_homebase(self) -> None:
        if not self._homebase_vaults_enabled:
            return
        dlg = AddHomebaseVaultDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        profile = dlg.selected_profile()
        if not profile:
            return
        config.upsert_homebase_vault_profile(profile)
        profile_path = str(profile.get("path") or "").strip()
        if profile_path:
            config.delete_known_vault(profile_path)
        self.homebase_vaults = config.load_homebase_vault_profiles()
        self._refresh_homebase_list(select_id=profile.get("id"))

    def _remove_homebase_selected(self) -> None:
        if not self.homebase_list_widget:
            return
        item = self.homebase_list_widget.currentItem()
        if not item:
            return
        data = item.data(Qt.UserRole)
        if not isinstance(data, dict) or data.get("kind") != "homebase":
            return
        profile_id = str(data.get("id") or "").strip()
        if not profile_id:
            return
        config.delete_homebase_vault_profile(profile_id)
        self.homebase_vaults = config.load_homebase_vault_profiles()
        self._refresh_homebase_list()

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
        if not self.remote_list_widget:
            return
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
        if not self.remote_list_widget:
            return
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
        if not self.remote_list_widget:
            return
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
