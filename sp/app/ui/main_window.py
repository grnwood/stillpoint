from __future__ import annotations

from pathlib import Path
from typing import Optional, Callable, Any
import errno
import ctypes
import hashlib
import json
import os
import shutil
import socket
import platform
import sqlite3
import subprocess
import sys
import tempfile 
import time
from datetime import datetime, timezone, timedelta
import faulthandler
import re
import warnings
from urllib.parse import quote, urlparse

import httpx
import traceback
from PySide6.QtCore import (
    QEvent,
    QModelIndex,
    QPoint,
    Qt,
    Signal,
    QTimer,
    QThread,
    QObject,
    QElapsedTimer,
    QAbstractEventDispatcher,
    QByteArray,
    QUrl,
    QPropertyAnimation,
    QMimeData,
    QSignalBlocker,
    QSize,
)
from PySide6.QtGui import (
    QAction,
    QKeySequence,
    QShortcut,
    QStandardItem,
    QStandardItemModel,
    QTextCursor,
    QTextCharFormat,
    QColor,
    QFont,
    QPen,
    QPalette,
    QBrush,
    QDesktopServices,
    QTextFormat,
    QDrag,
    QCursor,
    QIcon,
    QPainter,
    QPixmap,
    QTextOption,
    QGuiApplication,
)
from PySide6.QtWidgets import (
    QApplication,
    QAbstractItemView,
    QFileDialog,
    QTextEdit,
    QListWidget,
    QListWidgetItem,
    QInputDialog,
    QLineEdit,
    QMenu,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTreeView,
    QDialog,
    QProgressDialog,
    QWidget,
    QVBoxLayout,
    QFrame,
    QLabel,
    QHBoxLayout,
    QGridLayout,
    QToolButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QDialogButtonBox,
    QPushButton,
    QStyleOptionViewItem,
    QStyledItemDelegate,
    QTabWidget,
    QTabBar,
    QStackedLayout,
    QCheckBox,
    QFormLayout,
    QSpinBox,
    QDoubleSpinBox,
    QSystemTrayIcon,
    QScrollArea,
    QComboBox,
)

from sp.app import config, indexer
from sp import VERSION as SP_VERSION, GITHUB_OWNER, GITHUB_PROJECT, GITHUB_ISSUE_URL
from sp.logging_flags import log_enabled
from sp.sync import HomebaseSyncEngine, HomebaseSyncStatus
from sp.sync.engine import HomebaseSyncConfig
from .theme import theme_color, theme_value
from sp.app.ui.ai_actions_data import AI_ACTION_GROUPS
from sp.server import search_index
from sp.server.adapters.files import LEGACY_SUFFIX, PAGE_SUFFIX, PAGE_SUFFIXES, strip_page_suffix
from sp.app import zim_import
from sp import VERSION as APP_VERSION

_ONE_SHOT_PROMPT_CACHE: Optional[str] = None


def _load_one_shot_prompt() -> str:
    """Load the one-shot system prompt once and cache it."""
    global _ONE_SHOT_PROMPT_CACHE
    if _ONE_SHOT_PROMPT_CACHE is not None:
        return _ONE_SHOT_PROMPT_CACHE
    default_prompt = "you are a helpful assistent, you will respond with markdown formatting"
    try:
        prompt_path = Path(__file__).parent.parent / "one-shot-prompt.txt"
        if prompt_path.exists():
            content = prompt_path.read_text(encoding="utf-8").strip()
            if content:
                _ONE_SHOT_PROMPT_CACHE = content
                return content
    except Exception:
        pass
    _ONE_SHOT_PROMPT_CACHE = default_prompt
    return default_prompt

_ONE_SHOT_PROMPT_CACHE: Optional[str] = None


class PageRenameDialog(QDialog):
    """Dialog to collect source→target page renames for Zim import."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Rename Pages for Import")
        self.resize(520, 320)

        self.table = QTableWidget(0, 2, self)
        self.table.setHorizontalHeaderLabels(["Source segment", "Target segment"])
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)

        add_btn = QPushButton("Add…")
        remove_btn = QPushButton("Remove")
        add_btn.clicked.connect(self._add_row)
        remove_btn.clicked.connect(self._remove_selected)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)

        controls = QHBoxLayout()
        controls.addWidget(add_btn)
        controls.addWidget(remove_btn)
        controls.addStretch(1)

        layout = QVBoxLayout(self)
        hint = QLabel(
            "Renames pages in the StillPoint import.\n"
            "Example:\n"
            "Old Zim Wiki Page:\n"
            "9-Journal:Page:Link\n"
            "New Wiki Page:\n"
            "Journal:Page:Link"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addWidget(QLabel("Rename page path segments before import:"))
        layout.addWidget(self.table, 1)
        layout.addLayout(controls)
        layout.addWidget(btns)

    def _add_row(self) -> None:
        src, ok1 = QInputDialog.getText(self, "Source name", "Source segment (e.g., 9-Journal):")
        if not ok1 or not src.strip():
            return
        dst, ok2 = QInputDialog.getText(self, "Target name", "Target segment (e.g., Journal):", text=src.strip())
        if not ok2 or not dst.strip():
            return
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(src.strip()))
        self.table.setItem(row, 1, QTableWidgetItem(dst.strip()))

    def _remove_selected(self) -> None:
        rows = {idx.row() for idx in self.table.selectedIndexes()}
        for row in sorted(rows, reverse=True):
            self.table.removeRow(row)

    def mapping(self) -> dict[str, str]:
        result: dict[str, str] = {}
        for row in range(self.table.rowCount()):
            src_item = self.table.item(row, 0)
            dst_item = self.table.item(row, 1)
            if not src_item or not dst_item:
                continue
            src = src_item.text().strip()
            dst = dst_item.text().strip()
            if src and dst:
                result[src] = dst
        return result


class RemoteLoginDialog(QDialog):
    """Prompt for remote server credentials."""

    def __init__(self, parent=None, username: str = "", remember_default: bool = True) -> None:
        super().__init__(parent)
        self.setWindowTitle("Server Login")
        self.setModal(True)
        self.resize(360, 180)

        self._username = ""
        self._password = ""
        self._remember = remember_default

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.username_edit = QLineEdit()
        self.username_edit.setText(username)
        form.addRow("Username:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        layout.addLayout(form)

        self.remember_checkbox = QCheckBox("Remember on this device")
        self.remember_checkbox.setChecked(remember_default)
        layout.addWidget(self.remember_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # type: ignore[override]
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            QMessageBox.warning(self, "Missing Info", "Please enter both username and password.")
            return
        self._username = username
        self._password = password
        self._remember = bool(self.remember_checkbox.isChecked())
        super().accept()

    def credentials(self) -> tuple[str, str, bool]:
        return self._username, self._password, self._remember


class RemoteChangePasswordDialog(QDialog):
    """Prompt to change a remote vault password after server password changes."""

    def __init__(
        self,
        parent=None,
        username: str = "",
        old_password: str = "",
        remember_default: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Update Vault Password")
        self.setModal(True)
        self.resize(380, 220)

        self._username = ""
        self._old_password = ""
        self._new_password = ""
        self._remember = remember_default

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.username_edit = QLineEdit()
        self.username_edit.setText(username)
        form.addRow("Username:", self.username_edit)

        self.old_password_edit = QLineEdit()
        self.old_password_edit.setEchoMode(QLineEdit.Password)
        self.old_password_edit.setText(old_password)
        form.addRow("Current password:", self.old_password_edit)

        self.new_password_edit = QLineEdit()
        self.new_password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("New password:", self.new_password_edit)

        self.confirm_password_edit = QLineEdit()
        self.confirm_password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Confirm:", self.confirm_password_edit)

        layout.addLayout(form)

        self.remember_checkbox = QCheckBox("Remember on this device")
        self.remember_checkbox.setChecked(remember_default)
        layout.addWidget(self.remember_checkbox)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # type: ignore[override]
        username = self.username_edit.text().strip()
        old_password = self.old_password_edit.text()
        new_password = self.new_password_edit.text()
        confirm = self.confirm_password_edit.text()
        if not username or not old_password or not new_password:
            QMessageBox.warning(self, "Missing Info", "Please fill in all password fields.")
            return
        if new_password != confirm:
            QMessageBox.warning(self, "Mismatch", "New password entries do not match.")
            return
        self._username = username
        self._old_password = old_password
        self._new_password = new_password
        self._remember = bool(self.remember_checkbox.isChecked())
        super().accept()

    def values(self) -> tuple[str, str, str, bool]:
        return self._username, self._old_password, self._new_password, self._remember


class UserCreateDialog(QDialog):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create User")
        self.setModal(True)
        self.resize(360, 220)

        self._username = ""
        self._password = ""
        self._role = "normal"
        self._perm = "read"

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.username_edit = QLineEdit()
        form.addRow("Username:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        form.addRow("Password:", self.password_edit)

        self.role_combo = QComboBox()
        self.role_combo.addItem("Admin", "admin")
        self.role_combo.addItem("Normal", "normal")
        self.role_combo.setCurrentIndex(1)
        self.role_combo.currentIndexChanged.connect(self._sync_perm_enabled)
        form.addRow("Role:", self.role_combo)

        self.perm_combo = QComboBox()
        self.perm_combo.addItem("Read", "read")
        self.perm_combo.addItem("Read + Write", "read_write")
        self.perm_combo.setCurrentIndex(0)
        form.addRow("Permission:", self.perm_combo)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_perm_enabled()

    def _sync_perm_enabled(self) -> None:
        role = self.role_combo.currentData()
        is_admin = role == "admin"
        self.perm_combo.setEnabled(not is_admin)
        if is_admin:
            self.perm_combo.setCurrentIndex(1)

    def accept(self) -> None:  # type: ignore[override]
        username = self.username_edit.text().strip()
        password = self.password_edit.text()
        if not username or not password:
            QMessageBox.warning(self, "Missing Info", "Please enter a username and password.")
            return
        self._username = username
        self._password = password
        self._role = str(self.role_combo.currentData() or "normal")
        self._perm = str(self.perm_combo.currentData() or "read")
        super().accept()

    def values(self) -> tuple[str, str, str, str]:
        return self._username, self._password, self._role, self._perm


class UserEditDialog(QDialog):
    def __init__(self, parent=None, *, username: str, role: str, perm: str) -> None:
        super().__init__(parent)
        self.setWindowTitle("Edit User")
        self.setModal(True)
        self.resize(360, 250)

        self._username = username
        self._password = ""
        self._role = role
        self._perm = perm

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.username_edit = QLineEdit()
        self.username_edit.setText(username)
        form.addRow("Username:", self.username_edit)

        self.password_edit = QLineEdit()
        self.password_edit.setEchoMode(QLineEdit.Password)
        self.password_edit.setPlaceholderText("Leave blank to keep existing")
        form.addRow("Reset Password:", self.password_edit)

        self.role_combo = QComboBox()
        self.role_combo.addItem("Admin", "admin")
        self.role_combo.addItem("Normal", "normal")
        self.role_combo.setCurrentIndex(0 if role == "admin" else 1)
        self.role_combo.currentIndexChanged.connect(self._sync_perm_enabled)
        form.addRow("Role:", self.role_combo)

        self.perm_combo = QComboBox()
        self.perm_combo.addItem("Read", "read")
        self.perm_combo.addItem("Read + Write", "read_write")
        self.perm_combo.setCurrentIndex(1 if perm in ("read_write", "read+write") else 0)
        form.addRow("Permission:", self.perm_combo)

        layout.addLayout(form)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._sync_perm_enabled()

    def _sync_perm_enabled(self) -> None:
        role = self.role_combo.currentData()
        is_admin = role == "admin"
        self.perm_combo.setEnabled(not is_admin)
        if is_admin:
            self.perm_combo.setCurrentIndex(1)

    def accept(self) -> None:  # type: ignore[override]
        username = self.username_edit.text().strip()
        if not username:
            QMessageBox.warning(self, "Missing Info", "Please enter a username.")
            return
        self._username = username
        self._password = self.password_edit.text()
        self._role = str(self.role_combo.currentData() or "normal")
        self._perm = str(self.perm_combo.currentData() or "read")
        super().accept()

    def values(self) -> tuple[str, str, str, str]:
        return self._username, self._password, self._role, self._perm


class ManageUsersDialog(QDialog):
    def __init__(
        self,
        parent=None,
        *,
        fetch_users: Callable[[], list[dict]],
        create_user: Callable[[str, str, str, str], None],
        edit_user: Callable[[str, str, str, str, str], None],
        delete_user: Callable[[str], None],
        title: str = "Manage Users",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.resize(720, 360)

        self._fetch_users = fetch_users
        self._create_user = create_user
        self._edit_user = edit_user
        self._delete_user = delete_user

        layout = QVBoxLayout(self)

        self.table = QTableWidget(0, 6, self)
        self.table.setHorizontalHeaderLabels(
            [
                "Username",
                "Role",
                "Permission",
                "Logged In",
                "Last Login",
                "Last Password Change",
            ]
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.table, 1)

        button_row = QHBoxLayout()
        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh)
        button_row.addWidget(self.refresh_btn)

        self.create_btn = QPushButton("Create User")
        self.create_btn.clicked.connect(self._create)
        button_row.addWidget(self.create_btn)

        self.edit_btn = QPushButton("Edit User")
        self.edit_btn.clicked.connect(self._edit)
        button_row.addWidget(self.edit_btn)

        self.delete_btn = QPushButton("Delete User")
        self.delete_btn.clicked.connect(self._delete)
        button_row.addWidget(self.delete_btn)

        button_row.addStretch(1)

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        button_row.addWidget(close_btn)

        layout.addLayout(button_row)
        self._refresh()

    def _refresh(self) -> None:
        try:
            users = self._fetch_users() or []
        except Exception as exc:
            QMessageBox.critical(self, "User List Failed", str(exc))
            return
        self.table.setRowCount(0)
        for entry in users:
            row = self.table.rowCount()
            self.table.insertRow(row)
            username = str(entry.get("username") or "")
            role = str(entry.get("role") or "")
            perm = str(entry.get("perm") or "")
            logged_in = "Yes" if entry.get("logged_in") else "No"
            last_login = str(entry.get("last_login_at") or "—")
            last_password = str(entry.get("last_password_change_at") or "—")
            values = [username, role, perm, logged_in, last_login, last_password]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                self.table.setItem(row, col, item)

    def _create(self) -> None:
        dlg = UserCreateDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        username, password, role, perm = dlg.values()
        try:
            self._create_user(username, password, role, perm)
        except Exception as exc:
            QMessageBox.critical(self, "Create User Failed", str(exc))
            return
        self._refresh()

    def _edit(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Edit User", "Select a user to edit.")
            return
        username_item = self.table.item(row, 0)
        role_item = self.table.item(row, 1)
        perm_item = self.table.item(row, 2)
        username = username_item.text() if username_item else ""
        role = role_item.text() if role_item else "normal"
        perm = perm_item.text() if perm_item else "read"
        if not username:
            return
        dlg = UserEditDialog(self, username=username, role=role, perm=perm)
        if dlg.exec() != QDialog.Accepted:
            return
        new_username, password, role, perm = dlg.values()
        try:
            self._edit_user(username, new_username, password, role, perm)
        except Exception as exc:
            QMessageBox.critical(self, "Edit User Failed", str(exc))
            return
        self._refresh()

    def _delete(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            QMessageBox.information(self, "Delete User", "Select a user to delete.")
            return
        username_item = self.table.item(row, 0)
        username = username_item.text() if username_item else ""
        if not username:
            return
        confirm = QMessageBox.question(
            self,
            "Delete User",
            f"Delete user '{username}'?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self._delete_user(username)
        except Exception as exc:
            QMessageBox.critical(self, "Delete User Failed", str(exc))
            return
        self._refresh()


class AddRemoteDialog(QDialog):
    """Prompt for remote server host/port."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Remote Server")
        self.setModal(True)
        self.resize(360, 280)

        self._host = ""
        self._port = 443
        self._use_https = True
        self._no_verify = False
        self._server_password = ""
        self._remember_server_password = False
        self._connect_timeout_s = config.load_remote_connect_timeout(3.0)
        self._read_timeout_s = config.load_remote_read_timeout(10.0)

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.host_edit = QLineEdit()
        self.host_edit.setPlaceholderText("example.com or 192.168.1.77")
        self.host_edit.textChanged.connect(self._on_host_changed)
        form.addRow("Server:", self.host_edit)

        self.port_edit = QLineEdit()
        self.port_edit.setText("443")
        form.addRow("Port:", self.port_edit)

        layout.addLayout(form)

        self.https_checkbox = QCheckBox("Use HTTPS")
        self.https_checkbox.setChecked(True)
        layout.addWidget(self.https_checkbox)

        self.no_verify_checkbox = QCheckBox("Do not verify SSL")
        self.no_verify_checkbox.setChecked(False)
        layout.addWidget(self.no_verify_checkbox)

        timeout_row = QHBoxLayout()
        timeout_row.addWidget(QLabel("Connect timeout (s):"))
        self.connect_timeout_spin = QDoubleSpinBox()
        self.connect_timeout_spin.setRange(0.1, 120.0)
        self.connect_timeout_spin.setDecimals(1)
        self.connect_timeout_spin.setSingleStep(0.5)
        self.connect_timeout_spin.setValue(self._connect_timeout_s)
        timeout_row.addWidget(self.connect_timeout_spin, 1)
        layout.addLayout(timeout_row)

        read_timeout_row = QHBoxLayout()
        read_timeout_row.addWidget(QLabel("Read timeout (s):"))
        self.read_timeout_spin = QDoubleSpinBox()
        self.read_timeout_spin.setRange(0.1, 300.0)
        self.read_timeout_spin.setDecimals(1)
        self.read_timeout_spin.setSingleStep(0.5)
        self.read_timeout_spin.setValue(self._read_timeout_s)
        read_timeout_row.addWidget(self.read_timeout_spin, 1)
        layout.addLayout(read_timeout_row)

        # Server admin password (only for remote servers)
        self.server_password_container = QWidget()
        server_password_layout = QVBoxLayout(self.server_password_container)
        server_password_layout.setContentsMargins(0, 10, 0, 0)
        
        server_password_label = QLabel("Server Admin Password:")
        server_password_layout.addWidget(server_password_label)
        
        self.server_password_edit = QLineEdit()
        self.server_password_edit.setEchoMode(QLineEdit.Password)
        self.server_password_edit.setPlaceholderText("Required for vault operations")
        server_password_layout.addWidget(self.server_password_edit)
        
        self.remember_server_password_checkbox = QCheckBox("Remember server password")
        self.remember_server_password_checkbox.setChecked(False)
        server_password_layout.addWidget(self.remember_server_password_checkbox)
        
        layout.addWidget(self.server_password_container)
        self.server_password_container.hide()  # Hidden by default

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_host_changed(self, text: str) -> None:
        """Show/hide server password field based on whether host is localhost."""
        host = text.strip().lower()
        is_localhost = host in ("localhost", "127.0.0.1", "::1") or host.startswith("127.0.0.1:")
        if is_localhost:
            self.server_password_container.hide()
        else:
            self.server_password_container.show()

    def accept(self) -> None:  # type: ignore[override]
        host = self.host_edit.text().strip()
        port_str = self.port_edit.text().strip()
        if not host or not port_str:
            QMessageBox.warning(self, "Missing Info", "Please enter both server and port.")
            return
        try:
            port = int(port_str)
        except ValueError:
            QMessageBox.warning(self, "Invalid Port", "Port must be a number.")
            return
        
        # Check if server password is required (non-localhost)
        is_localhost = host.lower() in ("localhost", "127.0.0.1", "::1") or host.lower().startswith("127.0.0.1:")
        if not is_localhost:
            server_password = self.server_password_edit.text().strip()
            if not server_password:
                QMessageBox.warning(self, "Missing Password", "Server admin password is required for remote servers.")
                return
            self._server_password = server_password
            self._remember_server_password = bool(self.remember_server_password_checkbox.isChecked())
        
        self._host = host
        self._port = port
        self._use_https = bool(self.https_checkbox.isChecked())
        self._no_verify = bool(self.no_verify_checkbox.isChecked())
        self._connect_timeout_s = float(self.connect_timeout_spin.value())
        self._read_timeout_s = float(self.read_timeout_spin.value())
        super().accept()

    def values(self) -> tuple[str, int, bool, bool, str, bool, float, float]:
        return (
            self._host,
            self._port,
            self._use_https,
            self._no_verify,
            self._server_password,
            self._remember_server_password,
            self._connect_timeout_s,
            self._read_timeout_s,
        )


class RemoteVaultSelectDialog(QDialog):
    """Prompt to select a vault from a remote server."""

    def __init__(self, vaults: list[dict[str, str]], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Select Remote Vault")
        self.setModal(True)
        self.resize(480, 360)
        self._selected_path: Optional[str] = None
        self._create_new: bool = False

        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose a vault to add from the remote server:"))

        self.list_widget = QListWidget()
        new_item = QListWidgetItem()
        new_item.setText("Add New Vault...")
        new_item.setData(Qt.UserRole, {"create_new": True})
        self.list_widget.addItem(new_item)
        for vault in vaults:
            item = QListWidgetItem()
            name = vault.get("name") or Path(vault.get("path") or "").name
            item.setText(name)
            item.setData(Qt.UserRole, vault)
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def accept(self) -> None:  # type: ignore[override]
        item = self.list_widget.currentItem()
        if not item:
            QMessageBox.warning(self, "No Selection", "Please select a vault.")
            return
        vault = item.data(Qt.UserRole)
        if vault and vault.get("create_new"):
            self._create_new = True
            self._selected_path = None
            super().accept()
            return
        if not vault or not vault.get("path"):
            QMessageBox.warning(self, "No Selection", "Please select a vault.")
            return
        self._selected_path = vault["path"]
        super().accept()

    def selected_path(self) -> Optional[str]:
        return self._selected_path

    def create_new(self) -> bool:
        return self._create_new

from .markdown_editor import MarkdownEditor
from .tabbed_right_panel import TabbedRightPanel
from .task_panel import TaskPanel
from .link_navigator_panel import LinkNavigatorPanel
from .ai_chat_panel import AIChatPanel, AIChatStore
from .calendar_panel import CalendarPanel
from .jump_dialog import JumpToPageDialog
from .toc_widget import TableOfContentsWidget
from .heading_utils import heading_slug
from .preferences_dialog import PreferencesDialog
from .insert_link_dialog import InsertLinkDialog
from .new_page_dialog import NewPageDialog
from .folder_template_dialog import FolderTemplateDialog
from .merge_conflict_dialog import MergeConflictDialog
from .path_utils import colon_to_path, path_to_colon, ensure_root_colon_link
from .date_insert_dialog import DateInsertDialog
from .open_vault_dialog import OpenVaultDialog
from .vault_preferences_dialog import VaultPreferencesDialog
from .quick_capture_overlay import QuickCaptureOverlay
from .page_editor_window import PageEditorWindow
from .page_load_logger import PageLoadLogger, PAGE_LOGGING_ENABLED
from .mode_window import ModeWindow
from .find_replace_bar import FindReplaceBar
from .search_tab import SearchTab
from .search_index_sync import PeriodicSearchIndexSync
from .tags_tab import TagsTab


PATH_ROLE = int(Qt.ItemDataRole.UserRole)
TYPE_ROLE = PATH_ROLE + 1
OPEN_ROLE = TYPE_ROLE + 1
FILTER_BANNER = "__NAV_FILTER_BANNER__"
TREE_LAZY_LOAD_THRESHOLD = 500  # Load full tree if vault has fewer than 500 folders
_DETAILED_LOGGING = log_enabled("ui_state")
_ANSI_BLUE = "\033[94m"
_ANSI_RESET = "\033[0m"


def _log_api_client(message: str) -> None:
    if log_enabled("api_client"):
        print(message)


def _log_navigation(message: str) -> None:
    if log_enabled("navigation"):
        print(message)


def _log_sorting(message: str) -> None:
    if log_enabled("sorting_reorder"):
        print(message)


def _log_search(message: str) -> None:
    if log_enabled("search_index"):
        print(message)


def _log_ui_state(message: str) -> None:
    if log_enabled("ui_state"):
        print(message)


class VaultTreeModel(QStandardItemModel):
    """Custom model that only allows reordering within the same parent."""
    
    reorderRequested = Signal(str, list)  # parent_path, ordered_page_paths
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._drag_source_parent_path = None
    
    def mimeData(self, indexes):  # type: ignore[override]
        # Store the source parent path when drag starts
        if indexes:
            parent_index = indexes[0].parent()
            if parent_index.isValid():
                parent_item = self.itemFromIndex(parent_index)
                self._drag_source_parent_path = parent_item.data(PATH_ROLE) if parent_item else None
            else:
                self._drag_source_parent_path = "/"  # Root level
        _log_sorting(f"[MODEL] mimeData: source parent path = {self._drag_source_parent_path}")
        
        # Get the standard mime data from parent class
        mime = super().mimeData(indexes)
        
        # Add custom formats for external drops (editor)
        if indexes:
            idx = indexes[0]
            path = idx.data(OPEN_ROLE) or idx.data(PATH_ROLE)
            if path:
                mime.setText(path)
                mime.setData("application/x-stillpoint-path", path.encode("utf-8"))
                _log_sorting(f"[MODEL] Added custom mime data: text={path}, has stillpoint-path={mime.hasFormat('application/x-stillpoint-path')}")
        
        return mime
    
    def canDropMimeData(self, data, action, row, column, parent):  # type: ignore[override]
        # Get target parent path
        if parent.isValid():
            parent_item = self.itemFromIndex(parent)
            target_parent_path = parent_item.data(PATH_ROLE) if parent_item else None
        else:
            target_parent_path = "/"
        
        _log_sorting(f"[MODEL] canDropMimeData: source={self._drag_source_parent_path}, target={target_parent_path}")
        
        # Check if this is a same-parent drop
        if self._drag_source_parent_path != target_parent_path:
            _log_sorting("[MODEL] Blocking drop - different parent")
            return False
        
        return super().canDropMimeData(data, action, row, column, parent)
    
    def dropMimeData(self, data, action, row, column, parent):  # type: ignore[override]
        # Get target parent path
        if parent.isValid():
            parent_item = self.itemFromIndex(parent)
            target_parent_path = parent_item.data(PATH_ROLE) if parent_item else None
        else:
            target_parent_path = "/"
        
        _log_sorting(f"[MODEL] dropMimeData called: source={self._drag_source_parent_path}, target={target_parent_path}, row={row}")
        
        # Verify same parent (belt and suspenders)
        if self._drag_source_parent_path != target_parent_path:
            _log_sorting("[MODEL] dropMimeData blocked - different parent")
            return False
        
        _log_sorting("[MODEL] Calling super().dropMimeData()")
        # Let the model handle the internal reordering
        result = super().dropMimeData(data, action, row, column, parent)
        _log_sorting(f"[MODEL] super().dropMimeData() returned {result}")
        
        if result:
            _log_sorting("[MODEL] Reorder successful, emitting reorderRequested signal")
            # Use the parent path we already have
            parent_path = target_parent_path or "/"
            
            # Collect ordered paths from the parent after the reorder
            parent_item = self.itemFromIndex(parent) if parent.isValid() else self.invisibleRootItem()
            ordered_paths = []
            seen = set()  # Prevent duplicates
            if parent_item:
                for row_idx in range(parent_item.rowCount()):
                    child = parent_item.child(row_idx)
                    if child:
                        # Prefer OPEN_ROLE (the .md file path) for actual content
                        child_path = child.data(OPEN_ROLE) or child.data(PATH_ROLE)
                        if child_path and child_path not in seen:
                            ordered_paths.append(child_path)
                            seen.add(child_path)
                            _log_sorting(f"[MODEL]   {row_idx}: {child_path}")
            
            _log_sorting(f"[MODEL] Emitting reorderRequested: parent={parent_path}, count={len(ordered_paths)}")
            # Emit signal to trigger API call
            self.reorderRequested.emit(parent_path, ordered_paths)
        
        return result


class RemoteTokenAuth(httpx.Auth):
    """Attach bearer tokens and refresh on 401 for remote servers."""

    def __init__(self, get_access, refresh_tokens) -> None:
        self._get_access = get_access
        self._refresh_tokens = refresh_tokens

    def auth_flow(self, request):
        access = self._get_access()
        if access:
            request.headers["Authorization"] = f"Bearer {access}"
        response = yield request
        if response.status_code != 401:
            return
        try:
            response.read()
        except Exception:
            pass
        if not self._refresh_tokens():
            return
        access = self._get_access()
        if access:
            request.headers["Authorization"] = f"Bearer {access}"
            yield request
class InlineNameEdit(QLineEdit):
    submitted = Signal(str)
    cancelled = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.returnPressed.connect(self._emit_submit)

    def _emit_submit(self) -> None:
        self.submitted.emit(self.text())

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape:
            event.accept()
            self.cancelled.emit()
            self.deleteLater()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event):  # type: ignore[override]
        super().focusOutEvent(event)
        self.cancelled.emit()
        self.deleteLater()


class VaultTreeView(QTreeView):
    enterActivated = Signal()
    arrowNavigated = Signal()
    escapePressed = Signal()
    rowClicked = Signal(QModelIndex)
    dragStarted = Signal()
    dragFinished = Signal()
    moveRequested = Signal(str, str)  # from_path, to_path
    reorderRequested = Signal(str, list)  # parent_path, ordered_page_paths
    dragStatusChanged = Signal(str)  # status message for status bar

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._press_pos: QPoint | None = None
        self._press_index: QModelIndex | None = None
        self._dragging: bool = False
        self._drag_src_index: QModelIndex | None = None
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDefaultDropAction(Qt.MoveAction)
        self.setDragDropMode(QAbstractItemView.InternalMove)

    def keyPressEvent(self, event):  # type: ignore[override]
        if event.key() == Qt.Key_Escape and event.modifiers() == Qt.NoModifier:
            self.escapePressed.emit()
            self.collapseAll()
            event.accept()
            return
        if event.modifiers() == Qt.ControlModifier and event.key() in (Qt.Key_Down, Qt.Key_Up):
            direction = 1 if event.key() == Qt.Key_Down else -1
            self._walk_tree(direction)
            self.arrowNavigated.emit()
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and event.modifiers() == Qt.NoModifier:
            super().keyPressEvent(event)
            self.enterActivated.emit()
            return
        if event.key() in (Qt.Key_Up, Qt.Key_Down, Qt.Key_Left, Qt.Key_Right):
            self.arrowNavigated.emit()
        super().keyPressEvent(event)

    def _walk_tree(self, direction: int) -> None:
        indexes = self._flatten()
        if not indexes:
            return
        current = self.currentIndex()
        try:
            idx = indexes.index(current)
        except ValueError:
            idx = -1 if direction > 0 else 0
        new_idx = idx + direction
        new_idx = max(0, min(len(indexes) - 1, new_idx))
        if new_idx == idx:
            return
        target = indexes[new_idx]
        # Don't auto-collapse/expand - just navigate to the target
        self.setCurrentIndex(target)
        self.scrollTo(target)

    def _flatten(self) -> list[QModelIndex]:
        """Get list of all VISIBLE (expanded) nodes in tree order."""
        model = self.model()
        if model is None:
            return []
        order: list[QModelIndex] = []

        def recurse(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                order.append(idx)
                # Only recurse into expanded nodes
                if self.isExpanded(idx):
                    recurse(idx)

        recurse(QModelIndex())
        return order

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self._press_pos = event.pos()
            self._dragging = False
            # Store the source index for potential reorder operation
            self._drag_src_index = self.indexAt(event.pos())
            self._press_index = self._drag_src_index
        self.setFocus(Qt.MouseFocusReason)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if (
            event.buttons() & Qt.LeftButton
            and self._press_pos is not None
            and (event.pos() - self._press_pos).manhattanLength() >= QApplication.startDragDistance()
        ):
            if not self._dragging:
                self._dragging = True
                _log_sorting("[TREE] Drag detected, calling startDrag()")
                self.dragStarted.emit()
                self.dragStatusChanged.emit("Reorder item in the tree...")
                # Manually trigger drag with our custom mime data
                self.startDrag(Qt.CopyAction | Qt.MoveAction)
                return  # Don't call super - we handled the drag
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton and not self._dragging:
            idx = self.indexAt(event.pos())
            if idx.isValid():
                self.rowClicked.emit(idx)
        if self._dragging:
            self.dragFinished.emit()
            # Don't clear status here - let dropEvent or handlers clear it
        self._dragging = False
        self._press_pos = None
        self._press_index = None
        self._drag_src_index = None
        super().mouseReleaseEvent(event)

    def _clicked_disclosure(self, index: QModelIndex, pos: QPoint) -> bool:
        if not index.isValid():
            return False
        option = QStyleOptionViewItem()
        option.initFrom(self)
        option.rect = self.visualRect(index)
        option.state |= QStyle.State_Enabled
        if self.model() and self.model().hasChildren(index):
            option.state |= QStyle.State_Children
        if self.isExpanded(index):
            option.state |= QStyle.State_Open
        branch = self.style().subElementRect(QStyle.SE_TreeViewDisclosureItem, option, self)
        return branch.isValid() and branch.contains(pos)

    def is_dragging(self) -> bool:
        return self._dragging


class NavTreeDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):  # type: ignore[override]
        if index.data(PATH_ROLE) == FILTER_BANNER:
            painter.save()
            painter.fillRect(option.rect, theme_color("main_window.tree.drag_invalid_bg", "#c62828"))
            painter.setPen(theme_color("main_window.tree.drag_invalid_text", "#ffffff"))
            text = index.data(Qt.DisplayRole) or ""
            text_rect = option.rect.adjusted(6, 0, -6, 0)
            painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, str(text))
            painter.restore()
            return
        super().paint(painter, option, index)

    def dragEnterEvent(self, event):  # type: ignore[override]
        _log_sorting("[TREE DRAG] dragEnterEvent")
        event.acceptProposedAction()
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # type: ignore[override]
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_index = self.indexAt(pos)
        drop_pos = self.dropIndicatorPosition()
        
        # Block ALL OnItem drops - we only allow reordering between items
        if drop_pos == QAbstractItemView.OnItem:
            _log_sorting("[TREE DRAG] Blocking OnItem drop")
            event.setDropAction(Qt.IgnoreAction)
            event.ignore()
            return
        
        # For AboveItem/BelowItem, let it through and the model will validate parent
        event.acceptProposedAction()
        super().dragMoveEvent(event)

    def dropEvent(self, event):  # type: ignore[override]
        _log_sorting("[TREE DROP] dropEvent called")
        src_indexes = self.selectedIndexes()
        if not src_indexes:
            _log_sorting("[TREE DROP] No src_indexes, ignoring")
            event.ignore()
            self.dragStatusChanged.emit("")  # Clear status on failed drop
            return
        src_index = src_indexes[0]
        src_path = src_index.data(PATH_ROLE)
        if not src_path:
            event.ignore()
            self.dragStatusChanged.emit("")  # Clear status on failed drop
            return
        pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_index = self.indexAt(pos)
        drop_pos = self.dropIndicatorPosition()
        
        _log_sorting(f"[TREE DROP] src_path={src_path}, drop_pos={drop_pos}")
        
        # Determine the parent of the source and target
        src_parent_index = src_index.parent()
        target_parent_index = target_index.parent() if target_index.isValid() else QModelIndex()
        
        _log_sorting(f"[TREE DROP] src_parent==target_parent? {src_parent_index == target_parent_index}")
        
        # ONLY allow reordering within the same parent
        # Block any attempt to move to a different parent
        if drop_pos == QAbstractItemView.OnItem:
            # Dropping onto another folder - BLOCKED for moves between parents
            _log_sorting("[TREE DROP] OnItem drop blocked - use Move... menu option instead")
            event.ignore()
            self.dragStatusChanged.emit("")
            return
        
        # Check if we're reordering within the same parent
        if drop_pos in (QAbstractItemView.AboveItem, QAbstractItemView.BelowItem) and src_parent_index == target_parent_index:
            # This is a reorder operation within the same folder - ALLOWED
            _log_sorting("[TREE DROP] Handling as REORDER")
            event.acceptProposedAction()
            self._handle_reorder_drop(src_index, target_index, drop_pos)
            return
        
        # Any other case is a move to different parent - BLOCKED
        _log_sorting("[TREE DROP] Cross-parent move blocked - use Move... menu option instead")
        event.ignore()
        self.dragStatusChanged.emit("")
    
    def _handle_reorder_drop(self, src_index: QModelIndex, target_index: QModelIndex, drop_pos) -> None:
        """Handle reordering items within the same parent."""
        if not src_index.isValid() or not target_index.isValid():
            return
        
        parent_index = src_index.parent()
        model = self.model()
        if not model:
            return
        
        # Get parent path for the API call
        if parent_index.isValid():
            parent_path = parent_index.data(PATH_ROLE) or "/"
        else:
            parent_path = "/"
        
        # Collect all children of the parent in their current order
        row_count = model.rowCount(parent_index)
        children: list[tuple[int, str]] = []
        for row in range(row_count):
            child_index = model.index(row, 0, parent_index)
            # Use OPEN_ROLE to get the actual page path (.txt file), not the folder path
            child_path = child_index.data(OPEN_ROLE) or child_index.data(PATH_ROLE)
            if child_path:
                children.append((row, child_path))
        
        if not children:
            return
        
        # Find source and target positions
        src_row = src_index.row()
        target_row = target_index.row()
        
        # Remove source from list
        src_path = None
        for i, (row, path) in enumerate(children):
            if row == src_row:
                src_path = path
                children.pop(i)
                break
        
        if not src_path:
            return
        
        # Determine insertion position based on drop indicator
        insert_pos = target_row
        if drop_pos == QAbstractItemView.BelowItem:
            insert_pos = target_row + 1
        
        # Adjust insertion position if we removed an item before it
        if src_row < target_row:
            insert_pos -= 1
        
        # Insert at new position
        children.insert(insert_pos, (insert_pos, src_path))
        
        # Extract ordered paths
        ordered_paths = [path for _, path in children]
        
        # Store info for visual update after successful reorder
        self._pending_reorder = {
            "parent_index": parent_index,
            "src_row": src_row,
            "dest_row": insert_pos
        }
        
        # Emit reorder signal
        self.reorderRequested.emit(parent_path, ordered_paths)

    def startDrag(self, supportedActions):  # type: ignore[override]
        """Start drag with path text so editor drops can create links."""
        # Use the stored drag source index from mousePressEvent
        if not self._drag_src_index or not self._drag_src_index.isValid():
            _log_sorting("[TREE DRAG] No valid drag source index, using selected indexes")
            indexes = self.selectedIndexes()
            if not indexes:
                _log_sorting("[TREE DRAG] No selection, calling super()")
                super().startDrag(supportedActions)
                return
            idx = indexes[0]
        else:
            idx = self._drag_src_index
        
        path = idx.data(OPEN_ROLE) or idx.data(PATH_ROLE)
        _log_sorting(f"[TREE DRAG] Starting drag: path={path}")
        if not path:
            super().startDrag(supportedActions)
            return
        
        # Create QMimeData with just our custom format and text
        mime = QMimeData()
        mime.setText(path)
        mime.setData("application/x-stillpoint-path", path.encode("utf-8"))
        
        _log_sorting(f"[TREE DRAG] Created mime with formats: {mime.formats()}")
        _log_sorting(f"[TREE DRAG] Has text: {mime.hasText()}, text: {mime.text()}")
        _log_sorting(f"[TREE DRAG] Has stillpoint-path: {mime.hasFormat('application/x-stillpoint-path')}")
        
        drag = QDrag(self)
        drag.setMimeData(mime)
        # Execute the drag with Copy action to allow external drops
        _log_sorting("[TREE DRAG] About to exec drag")
        result = drag.exec(Qt.CopyAction)
        _log_sorting(f"[TREE DRAG] Drag completed with result={result}")


class MenuCommandBar(QWidget):
    """Popup command bar for menu actions."""

    actionTriggered = Signal(QAction)
    closed = Signal()

    class Entry:
        def __init__(self, label: str, action: QAction) -> None:
            self.label = label
            self.action = action

    def __init__(self, parent=None) -> None:
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self._entries: list[MenuCommandBar.Entry] = []
        self.setStyleSheet(
            "background: "
            f"{theme_value('main_window.menu_command_bar.bg', '#000000')}; "
            "color: "
            f"{theme_value('main_window.menu_command_bar.text', '#ffffff')}; "
            "border-radius: 10px; border: 1px solid "
            f"{theme_value('main_window.menu_command_bar.border', '#222222')};"
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)
        self._search = QLineEdit()
        self._search.setPlaceholderText("Type a command…")
        self._search.setStyleSheet(
            "font-size: "
            f"{theme_value('main_window.menu_command_bar.search_font_size_px', 18)}px; "
            "color: "
            f"{theme_value('main_window.menu_command_bar.search_text', '#ffffff')}; "
            "background: "
            f"{theme_value('main_window.menu_command_bar.search_bg', 'rgba(255, 255, 255, 0.08)')}; "
            "border: 1px solid "
            f"{theme_value('main_window.menu_command_bar.search_border', 'rgba(255, 255, 255, 0.5)')}; "
            "padding: 8px; border-radius: 6px;"
        )
        self._search.textChanged.connect(self._refresh_list)
        layout.addWidget(self._search)
        self._list = QListWidget()
        self._list.setUniformItemSizes(True)
        self._list.setStyleSheet(
            "font-size: "
            f"{theme_value('main_window.menu_command_bar.list_font_size_px', 18)}px; "
            "color: "
            f"{theme_value('main_window.menu_command_bar.list_text', '#ffffff')}; "
            "background: transparent; padding: 4px;"
        )
        self._list.itemActivated.connect(self._activate_current_item)
        self._list.itemClicked.connect(lambda *_: self._activate_current_item())
        layout.addWidget(self._list)
        self._list.setMinimumHeight(220)
        self._search.installEventFilter(self)

    def open(
        self,
        entries: list[tuple[str, QAction]],
        *,
        anchor: Optional[QPoint] = None,
        query: str = "",
    ) -> None:
        self._entries = [MenuCommandBar.Entry(label, action) for label, action in entries]
        self._search.clear()
        if query:
            self._search.setText(query)
        self._search.setFocus()
        parent = self.parent()
        if parent:
            geo = parent.rect()
            width = max(420, int(geo.width() * 0.8))
            height = min(280, max(200, geo.height() - 100))
            parent_top_left = parent.mapToGlobal(geo.topLeft())
            left = parent_top_left.x() + (geo.width() - width) // 2
            top = parent_top_left.y() + int(geo.height() * 0.2)
            self.setGeometry(left, top, width, height)
        self.show()
        self.raise_()
        self._refresh_list()
        if self._list.count():
            self._list.setCurrentRow(0)

    def _refresh_list(self) -> None:
        search_text = self._search.text().lower().strip()
        self._list.clear()
        for entry in self._entries:
            if search_text and search_text not in entry.label.lower():
                continue
            item = QListWidgetItem(entry.label)
            item.setData(Qt.UserRole, entry)
            if not entry.action.isEnabled():
                item.setFlags(item.flags() & ~Qt.ItemIsEnabled)
            self._list.addItem(item)
        if self._list.count():
            self._list.setCurrentRow(0)

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj == self._search and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Down, Qt.Key_Up):
                delta = 1 if event.key() == Qt.Key_Down else -1
                self._move_selection(delta)
                return True
            if event.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                if event.key() == Qt.Key_J:
                    self._move_selection(1)
                    return True
                if event.key() == Qt.Key_K:
                    self._move_selection(-1)
                    return True
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self._activate_current_item()
                return True
            if event.key() == Qt.Key_Escape:
                self.hide()
                return True
        return super().eventFilter(obj, event)

    def _move_selection(self, delta: int) -> None:
        count = self._list.count()
        if not count:
            return
        row = self._list.currentRow()
        row = max(0, min(count - 1, row + delta))
        self._list.setCurrentRow(row)

    def _activate_current_item(self) -> None:
        item = self._list.currentItem()
        if not item:
            return
        entry = item.data(Qt.UserRole)
        if not entry:
            return
        if entry.action.isEnabled():
            self.actionTriggered.emit(entry.action)
        self.hide()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self._activate_current_item()
            return
        if event.key() == Qt.Key_Escape:
            self.hide()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        self.hide()
        super().focusOutEvent(event)

    def hide(self) -> None:  # type: ignore[override]
        super().hide()
        self.closed.emit()


class BookmarkChickletButton(QPushButton):
    dragStartRequested = Signal(str)
    dragMoveRequested = Signal(str, QPoint)
    dragEndRequested = Signal(str, QPoint)

    def __init__(self, bookmark_path: str, label: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(label, parent)
        self.bookmark_path = bookmark_path
        self._press_pos: Optional[QPoint] = None
        self._drag_active = False
        self._drag_hold_timer = QTimer(self)
        self._drag_hold_timer.setSingleShot(True)
        self._drag_hold_timer.setInterval(220)
        self._drag_hold_timer.timeout.connect(self._activate_drag)

    def _activate_drag(self) -> None:
        if self.isDown():
            self._drag_active = True
            self.dragStartRequested.emit(self.bookmark_path)

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            try:
                self._press_pos = event.position().toPoint()
            except Exception:
                self._press_pos = event.pos()
            self._drag_active = False
            self._drag_hold_timer.start()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # type: ignore[override]
        if self._drag_active and (event.buttons() & Qt.LeftButton):
            self.dragMoveRequested.emit(self.bookmark_path, event.globalPosition().toPoint())
            event.accept()
            return
        if self._press_pos is not None and (event.buttons() & Qt.LeftButton) and not self._drag_active:
            try:
                distance = (event.position().toPoint() - self._press_pos).manhattanLength()
            except Exception:
                distance = (event.pos() - self._press_pos).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._drag_hold_timer.stop()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # type: ignore[override]
        self._drag_hold_timer.stop()
        if event.button() == Qt.LeftButton and self._drag_active:
            self.setDown(False)
            self.dragEndRequested.emit(self.bookmark_path, event.globalPosition().toPoint())
            self._drag_active = False
            self._press_pos = None
            event.accept()
            return
        self._drag_active = False
        self._press_pos = None
        super().mouseReleaseEvent(event)


def logNav(message: str) -> None:
    """Log navigation operations when navigation logging is enabled."""
    _log_navigation(f"[Nav] {message}")


class MainWindow(QMainWindow):
    def __init__(
        self,
        api_base: str,
        local_auth_token: Optional[str] = None,
        embedded_server_admin_password: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle("StillPoint Desktop")
        # Ensure standard window controls (including maximize) are present.
        self.setWindowFlags(self.windowFlags() | Qt.WindowMaximizeButtonHint | Qt.WindowMinimizeButtonHint)
        
        # Set window icon explicitly (especially important on Windows)
        from sp.app.main import get_app_icon
        self.setWindowIcon(get_app_icon())
        
        self._local_api_base = api_base.rstrip("/")
        self.api_base = self._local_api_base
        self._remote_mode = False
        self._server_url: Optional[str] = None
        self._verify_tls = True
        self._remote_cache_root: Optional[Path] = None
        self._local_auth_token = local_auth_token
        self._embedded_server_admin_password = embedded_server_admin_password
        self._session_server_passwords: dict[str, str] = {}  # Cache server passwords for current session
        self._access_token: Optional[str] = None
        self._refresh_token: Optional[str] = None
        self._remember_refresh: bool = False
        self._remote_username: Optional[str] = None
        self._remote_health_state: str = "unknown"
        self._remote_health_message: str = ""
        self._remote_last_latency_ms: Optional[float] = None
        self._remote_slow_threshold_ms: float = 2500.0
        self._remote_degraded_threshold_ms: float = 6000.0
        self._remote_slow_strikes_required: int = 2
        self._remote_timeout_strikes_required: int = 2
        self._remote_slow_strikes: int = 0
        self._remote_timeout_strikes: int = 0
        self._remote_feedback_timer = QTimer(self)
        self._remote_feedback_timer.setSingleShot(True)
        self._remote_feedback_timer.timeout.connect(self._hide_remote_feedback)
        self._remote_user_is_admin: bool = False
        self._remote_user_can_write: bool = True
        self._user_read_only: bool = False
        self._homebase_user_is_admin: bool = False
        self._homebase_user_can_write: bool = True
        self._homebase_user_info_loaded: bool = False
        self._homebase_user_info_refreshing: bool = False
        self._homebase_sync_engine: Optional[HomebaseSyncEngine] = None
        self._homebase_status_poll_timer: Optional[QTimer] = None
        # Stable selected remote vault path; may differ from API-reported root.
        self._remote_vault_ref_path: Optional[str] = None
        def _log_request(request):
            request.extensions["sp_request_started_at"] = time.perf_counter()
            try:
                path = request.url.raw_path.decode("utf-8") if hasattr(request.url, "raw_path") else request.url.path
            except Exception:
                path = str(request.url)
            _log_api_client(f"{_ANSI_BLUE}[API] {request.method} {path}{_ANSI_RESET}")

        def _log_response(response):
            started = response.request.extensions.get("sp_request_started_at")
            on_ui_thread = QThread.currentThread() == self.thread()
            if isinstance(started, (int, float)) and self._remote_mode and on_ui_thread:
                latency_ms = (time.perf_counter() - float(started)) * 1000.0
                if response.status_code >= 500:
                    self._set_remote_health_state(
                        "degraded",
                        f"{response.request.method} {response.request.url.path} -> HTTP {response.status_code}",
                        latency_ms=latency_ms,
                    )
                else:
                    self._record_remote_latency(
                        latency_ms,
                        context=f"{response.request.method} {response.request.url.path}",
                    )
            try:
                path = response.request.url.raw_path.decode("utf-8") if hasattr(response.request.url, "raw_path") else response.request.url.path
            except Exception:
                path = str(response.request.url)
            _log_api_client(f"{_ANSI_BLUE}[API] {response.status_code} {path}{_ANSI_RESET}")

        self.http = self._build_http_client(
            base_url=self.api_base,
            is_remote=False,
            local_auth_token=local_auth_token,
            request_hooks=(_log_request, _log_response),
        )
        self.vault_root: Optional[str] = None
        self.vault_root_name: Optional[str] = None
        self.current_path: Optional[str] = None
        self._nav_filter_path: Optional[str] = None
        self._full_tree_data: list[dict] = []
        self._skip_next_selection_open: bool = False
        self._history_popup: Optional[QWidget] = None
        self._history_popup_label: Optional[QLabel] = None
        self._history_popup_list: Optional[QListWidget] = None
        self._popup_items: list = []
        self._popup_index: int = -1
        self._popup_mode: Optional[str] = None  # "history" or "heading"
        self._tree_nav_open_timer: Optional[QTimer] = None
        self._tree_nav_open_target: Optional[str] = None
        self._history_cursor_positions: dict[str, int] = {}
        self._history_scroll_positions: dict[str, int] = {}
        self._hierarchy_last_child_by_parent: dict[str, str] = {}
        self._homebase_pending_reload_path: Optional[str] = None
        self._homebase_conflict_seen_keys: set[str] = set()
        self._homebase_conflict_popup_open: bool = False
        self._tree_refresh_in_progress: bool = False
        self._pending_tree_refresh: bool = False
        self._tree_cache: dict[str, list[dict]] = {}
        self._expanded_paths: set[str] = set()
        self._use_lazy_loading: bool = True  # Will be updated on tree load
        self.rewrite_backlinks_on_move: bool = config.load_rewrite_backlinks_on_move()
        try:
            self._main_soft_scroll_enabled = config.load_enable_main_soft_scroll()
        except Exception:
            self._main_soft_scroll_enabled = True
        try:
            self._main_soft_scroll_lines = config.load_main_soft_scroll_lines(5)
        except Exception:
            self._main_soft_scroll_lines = 5
        self._feature_tasks_enabled = config.load_feature_tasks_enabled()
        self._feature_calendar_enabled = config.load_feature_calendar_enabled()
        self._feature_link_navigator_enabled = config.load_feature_link_navigator_enabled()
        self._feature_tags_enabled = config.load_feature_tags_enabled()
        self._feature_remember_cursor_position_enabled = config.load_feature_remember_cursor_position_enabled()
        
        # Page navigation history
        self.page_history: list[str] = []
        self.history_index: int = -1
        self._page_revisions: dict[str, dict[str, int | None]] = {}
        self._merge_dialog_open = False
        # Guard to suppress auto-open on tree selection during programmatic navigation
        self._suspend_selection_open: bool = False
        # Remember cursor positions for history navigation
        # Track last-saved content to detect dirty buffers
        self._last_saved_content: Optional[str] = None
        self._undo_cache_path: Optional[Path] = None
        self._undo_cache_pages_limit: int = 20
        self._undo_cache_states_limit: int = 10
        self._undo_cache_replaying: bool = False
        self._undo_cache: dict[str, Any] = {
            "schema_version": 1,
            "pages": {},
            "order": [],
        }
        self._scroll_anim: Optional[QPropertyAnimation] = None
        self._vi_enabled: bool = False
        self._vi_insert_active: bool = False
        self._vi_initial_page_loaded: bool = False
        self._vi_enable_pending: bool = False
        self._dirty_flag: bool = False
        self._suspend_dirty_tracking: bool = False
        self._homebase_has_unsynced_local_changes: bool = False
        self._homebase_unsynced_marked_at: Optional[datetime] = None
        self._suppress_focus_borders: bool = False
        
        # Track virtual (unsaved) pages
        self.virtual_pages: set[str] = set()
        # Track original content of virtual pages to detect actual edits
        self.virtual_page_original_content: dict[str, str] = {}
        
        # Track pending link path maps for backlink rewriting
        self._pending_link_path_maps: list[dict[str, str]] = []
        
        # Bookmarks
        self.bookmarks: list[str] = []
        self.bookmark_buttons: dict[str, QPushButton] = {}
        self._bookmark_drag_source_path: Optional[str] = None
        self._bookmark_drag_insert_index: Optional[int] = None
        self._bookmark_drag_divider: Optional[QFrame] = None
        
        # History buttons
        self.history_buttons: list[QPushButton] = []
        
        # Template cursor position for newly created pages
        self._template_cursor_position: int = -1

        self.tree_view = VaultTreeView()
        self.tree_model = VaultTreeModel()
        self.tree_model.setHorizontalHeaderLabels(["Vault"])
        self.tree_view.setModel(self.tree_model)
        
        # Connect model's reorder signal
        self.tree_model.reorderRequested.connect(self._on_tree_reorder_requested)
        
        self.tree_view.setItemDelegate(NavTreeDelegate(self.tree_view))
        self.tree_view.setHeaderHidden(False)
        self.tree_view.setIndentation(12)
        self.tree_view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self._open_context_menu)
        self.tree_view.selectionModel().currentChanged.connect(self._on_selection_changed)
        self.tree_view.enterActivated.connect(self._focus_editor_from_tree)
        self.tree_view.arrowNavigated.connect(self._mark_tree_arrow_nav)
        self.tree_view.escapePressed.connect(self._clear_nav_filter)
        self.tree_view.rowClicked.connect(self._on_tree_row_clicked)
        self.tree_view.moveRequested.connect(self._on_tree_move_requested)
        self.tree_view.reorderRequested.connect(self._on_tree_reorder_requested)
        self.tree_view.dragStatusChanged.connect(self._on_drag_status_changed)
        self.tree_view.expanded.connect(self._on_tree_expanded)
        self.tree_view.collapsed.connect(self._on_tree_collapsed)
        icon_color = self._main_icon_color()
        self._tree_parent_expanded_icon = QIcon()
        self._tree_parent_collapsed_icon = QIcon()
        self._tree_arrow_focus_pending = False
        self._tree_enter_focus = False
        self._tree_keyboard_nav = False
        self._suspend_cursor_history = False
        self._suppress_nav_sync_path: Optional[str] = None
        self._show_journal_in_nav = config.load_show_journal()
        
        # Create custom header widget
        self.tree_header_widget = QWidget()
        tree_header_layout = QHBoxLayout()
        tree_header_layout.setContentsMargins(8, 4, 8, 4)
        tree_header_layout.setSpacing(8)
        
        tree_header_label = QLabel("Vault")
        tree_header_label.setStyleSheet("font-weight: bold;")
        tree_header_layout.addWidget(tree_header_label)
        pal = QApplication.instance().palette()
        tooltip_fg = pal.color(QPalette.ToolTipText).name()
        tooltip_bg = pal.color(QPalette.ToolTipBase).name()
        
        # Search button to switch to search tab
        self.search_tree_button = QToolButton()
        tree_header_layout.addStretch()

        # Manual refresh button to reload tree data from the API
        self.refresh_tree_button = QToolButton()
        refresh_icon_path = self._find_asset("reload.svg")
        refresh_icon = self._load_icon(refresh_icon_path, icon_color, size=16)
        if refresh_icon is None:
            refresh_icon = self.style().standardIcon(QStyle.SP_BrowserReload)
        self.refresh_tree_button.setIcon(refresh_icon)
        self.refresh_tree_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.refresh_tree_button.setAutoRaise(True)
        self.refresh_tree_button.setToolTip(
            f"<div style='color:{tooltip_fg}; background:{tooltip_bg}; padding:2px 4px;'>Refresh tree</div>"
        )
        self.refresh_tree_button.clicked.connect(self._refresh_tree)
        self.refresh_tree_button.setEnabled(False)
        tree_header_layout.addWidget(self.refresh_tree_button)

        self.journal_tree_button = QToolButton()
        journal_icon_path = self._find_asset("calendar-days.svg")
        journal_icon = self._load_icon(journal_icon_path, icon_color, size=16)
        if journal_icon is None:
            journal_icon = self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        self.journal_tree_button.setIcon(journal_icon)
        self.journal_tree_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.journal_tree_button.setAutoRaise(True)
        self.journal_tree_button.setCheckable(True)
        self.journal_tree_button.setChecked(self._show_journal_in_nav)
        self.journal_tree_button.setToolTip(
            f"<div style='color:{tooltip_fg}; background:{tooltip_bg}; padding:2px 4px;'>Show journal in navigator</div>"
        )
        self.journal_tree_button.toggled.connect(self._toggle_show_journal_in_nav)
        self.journal_tree_button.setEnabled(False)
        tree_header_layout.addWidget(self.journal_tree_button)

        # Collapse-all button (aligned to the right, more prominent with white foreground)
        self.collapse_tree_button = QToolButton()
        icon_path = self._find_asset("collapse.svg")
        base_icon = self._load_icon(icon_path, icon_color, size=16) or self.style().standardIcon(QStyle.SP_ToolBarVerticalExtensionButton)
        self.collapse_tree_button.setIcon(base_icon)
        self.collapse_tree_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self.collapse_tree_button.setAutoRaise(True)
        self.collapse_tree_button.setStyleSheet(
            "QToolButton { color: "
            f"{theme_value('main_window.tree.collapse_button', '#ffffff')}; "
            "}"
        )
        self.collapse_tree_button.setToolTip(
            f"<div style='color:{tooltip_fg}; background:{tooltip_bg}; padding:2px 4px;'>Collapse all folders</div>"
        )
        self.collapse_tree_button.clicked.connect(self._collapse_tree_to_root)
        tree_header_layout.addWidget(self.collapse_tree_button)

        self.tree_header_widget.setLayout(tree_header_layout)
        self.tree_header_widget.setStyleSheet(
            "background: palette(midlight); border-bottom: 1px solid "
            f"{theme_value('main_window.tree.header_border', '#555555')};"
        )
        
        # Set the custom header widget
        self.tree_view.header().hide()
        self.tree_view.setHeaderHidden(True)

        self.editor = MarkdownEditor()
        try:
            temp_widget = QWidget()
            self._default_editor_max_width = temp_widget.maximumWidth()
            temp_widget.deleteLater()
        except Exception:
            self._default_editor_max_width = 16777215
        self.autosave_timer = QTimer(self)
        self.autosave_timer.setInterval(30_000)
        self.autosave_timer.setSingleShot(True)
        self.autosave_timer.timeout.connect(lambda: self._save_current_file(auto=True, reason="autosave timer"))
        self._search_sync = PeriodicSearchIndexSync(
            self,
            is_enabled=lambda: config.load_global_feature_keep_search_index_sync_enabled(default=False),
            is_remote_mode=lambda: self._remote_mode,
            get_vault_root=lambda: self.vault_root,
            get_db_path=config._vault_db_path,
            log_fn=_log_search,
        )
        self._search_sync.statusReady.connect(
            lambda message, timeout_ms: self.statusBar().showMessage(message, timeout_ms)
        )
        self.editor.imageSaved.connect(self._on_image_saved)
        self.editor.textChanged.connect(lambda: self.autosave_timer.start())
        self.editor.focusLost.connect(self._on_editor_focus_lost)
        self.editor.cursorMoved.connect(self._on_editor_cursor_moved)
        self.editor.linkHovered.connect(self._on_link_hovered)
        self.editor.linkCopied.connect(self._on_link_copied)
        self.editor.insertDateRequested.connect(self._insert_date)
        self.editor.editPageSourceRequested.connect(self._view_page_source)
        self.editor.openFileLocationRequested.connect(self._open_tree_file_location)
        self.editor.locateInNavigatorRequested.connect(self._locate_current_page_in_tree)
        self.editor.deletePageRequested.connect(self._delete_current_page_from_editor)
        self.editor.printPageRequested.connect(self._print_page_for_path)
        self.editor.attachmentDropped.connect(self._on_attachment_dropped)
        self.editor.backlinksRequested.connect(
            lambda path="": self._show_link_navigator_for_path(path or self.current_path)
        )
        self.editor.aiChatRequested.connect(
            lambda path="": self._open_ai_chat_for_path(path or self.current_path, create=True, focus_tab=True)
        )
        self.editor.aiChatSendRequested.connect(self._send_selection_to_ai_chat)
        self.editor.aiChatPageFocusRequested.connect(self._focus_ai_chat_for_page)
        self.editor.aiInlinePromptRequested.connect(self._open_inline_ai_prompt)
        self.editor.aiActionRequested.connect(self._handle_ai_action)
        self.editor.headingPickerRequested.connect(self._show_heading_picker_popup)
        self.editor.linkActivated.connect(self._open_link_in_context)
        self.editor.set_open_in_window_callback(self._open_page_editor_window)
        self.editor.set_filter_nav_callback(self._set_nav_filter)
        self.editor.set_move_text_callback(self._append_text_to_page_from_editor)
        self.editor.set_persisted_undo_callback(self._persisted_undo_fallback)
        self.editor.set_persisted_redo_callback(self._persisted_redo_fallback)
        self.editor.findBarRequested.connect(self._on_editor_find_requested)
        self.editor.pageTagInserted.connect(self._on_page_tag_inserted)
        self.find_bar = FindReplaceBar(self)
        self.find_bar.findNextRequested.connect(self._on_find_next_requested)
        self.find_bar.replaceRequested.connect(self._on_replace_requested)
        self.find_bar.replaceAllRequested.connect(self._on_replace_all_requested)
        self.find_bar.closed.connect(lambda: self.editor.setFocus(Qt.ShortcutFocusReason))
        try:
            md_font = config.load_default_markdown_font()
            if md_font:
                font = self.editor.font()
                font.setFamily(md_font)
                self.editor.setFont(font)
                self.editor.document().setDefaultFont(font)
        except Exception:
            pass
        try:
            md_font_size = config.load_default_markdown_font_size()
        except Exception:
            md_font_size = 12
        try:
            app_family = config.load_application_font()
            app_font_size = config.load_application_font_size()
            if app_font_size is None and QApplication.instance():
                app_font_size = QApplication.instance().font().pointSize()
        except Exception:
            app_family = None
            app_font_size = 11
        # Apply application font immediately (respect user preference)
        app = QApplication.instance()
        if app and app_font_size:
            try:
                font = app.font()
                if app_family:
                    font.setFamily(app_family)
                font.setPointSize(max(6, app_font_size))
                app.setFont(font)
                if app_font_size is not None:
                    config.save_application_font_size(app_font_size)
            except Exception:
                pass
        # Normalize and clamp the stored editor font size to a safe point size
        try:
            base_md_size = max(6, int(md_font_size))
        except Exception:
            base_md_size = 12
        self.font_size = config.load_global_editor_font_size(base_md_size)
        self.editor.set_font_point_size(self.font_size)
        self.editor.viInsertModeChanged.connect(self._on_vi_insert_state_changed)
        self._apply_vi_preferences()
        self.toc_widget = TableOfContentsWidget(self)
        self.toc_widget.set_headings([])
        self.toc_widget.set_base_path("")
        self.toc_widget.headingActivated.connect(self._toc_jump_to_position)
        self.toc_widget.collapsedChanged.connect(self._on_toc_collapsed_changed)
        self.toc_widget.linkCopied.connect(
            lambda link: self.statusBar().showMessage(f"Copied link: {link}", 2500)
        )
        self.toc_widget.set_collapsed(config.load_toc_collapsed())
        self.toc_widget.show()
        self._toc_headings: list[dict] = []
        self.editor.headingsChanged.connect(self._on_headings_changed)
        self.editor.viewportResized.connect(self._position_toc_widget)
        self.editor.verticalScrollBar().valueChanged.connect(lambda *_: (self._update_toc_visibility(), self._position_toc_widget()))
        self.editor.verticalScrollBar().rangeChanged.connect(lambda *_: (self._update_toc_visibility(), self._position_toc_widget()))

        # AI chat font starts two points below application font (clamped), but honors saved override
        base_ai_font = max(6, (self.font_size or 14) - 2)
        ai_font_size = config.load_ai_chat_font_size(base_ai_font)
        self.right_panel = TabbedRightPanel(
            enable_tasks=self._feature_tasks_enabled,
            enable_calendar=self._feature_calendar_enabled,
            enable_link_navigator=self._feature_link_navigator_enabled,
            enable_ai_chats=config.load_enable_ai_chats(),
            ai_chat_font_size=ai_font_size,
            http_client=self.http,
            auth_prompt=self._prompt_remote_login,
        )
        try:
            self.right_panel.setMinimumWidth(0)
            self.right_panel.setSizePolicy(QSizePolicy.MinimumExpanding, QSizePolicy.Expanding)
        except Exception:
            pass
        if self._feature_tasks_enabled:
            QTimer.singleShot(0, self.right_panel.refresh_tasks)
        self.right_panel.taskActivated.connect(self._open_task_from_panel)
        self.right_panel.linkActivated.connect(self._open_link_from_panel)
        self.right_panel.dateActivated.connect(self._open_journal_date)
        self.right_panel.calendarPageActivated.connect(self._open_calendar_page)
        self.right_panel.calendarTaskActivated.connect(self._open_task_from_calendar_panel)
        self.right_panel.aiChatNavigateRequested.connect(self._on_ai_chat_navigate)
        self.right_panel.aiChatPageWritten.connect(self._on_ai_chat_page_written)
        self.right_panel.aiChatResponseCopied.connect(
            lambda msg: self.statusBar().showMessage(msg or "Last chat response copied to buffer", 4000)
        )
        self.right_panel.aiOverlayRequested.connect(self._on_ai_overlay_requested)
        self.right_panel.openInWindowRequested.connect(self._open_page_editor_window)
        self.right_panel.pageAboutToBeDeleted.connect(self._handle_page_about_to_be_deleted)
        self.right_panel.pageDeleted.connect(self._remove_deleted_paths_from_history)
        self.right_panel.openTaskWindowRequested.connect(self._open_task_panel_window)
        self.right_panel.openCalendarWindowRequested.connect(self._open_calendar_panel_window)
        self.right_panel.openLinkWindowRequested.connect(self._open_link_panel_window)
        self.right_panel.openAiWindowRequested.connect(self._open_ai_chat_window)
        self.right_panel.filterClearRequested.connect(self._clear_nav_filter)
        self.right_panel.remoteRequestObserved.connect(
            self._on_right_panel_remote_request_observed,
            Qt.QueuedConnection,
        )
        self.right_panel.taskDatesWillApply.connect(self._on_task_dates_will_apply)
        self.right_panel.taskDatesApplied.connect(self._on_task_dates_applied)
        self.right_panel.linkBackRequested.connect(self._navigate_history_back)
        self.right_panel.linkForwardRequested.connect(self._navigate_history_forward)
        self.right_panel.linkHomeRequested.connect(self._go_home)
        try:
            self.right_panel.attachments_panel.plantumlEditorRequested.connect(self._open_plantuml_editor)
            if log_enabled("startup"):
                print("[MainWindow] Connected PlantUML editor request signal")
        except Exception as exc:
            print(f"[MainWindow] Failed to connect PlantUML editor signal: {exc}")
        try:
            self.right_panel.attachments_panel.mermaidEditorRequested.connect(self._open_mermaid_editor)
            if log_enabled("startup"):
                print("[MainWindow] Connected Mermaid editor request signal")
        except Exception as exc:
            print(f"[MainWindow] Failed to connect Mermaid editor signal: {exc}")
        try:
            self.right_panel.attachments_panel.attachmentsModified.connect(self._on_local_attachment_changed)
        except Exception:
            pass
        self.right_panel.set_page_text_provider(self._get_editor_text_for_path)
        self.right_panel.set_calendar_font_size(self.font_size)
        try:
            if self.right_panel.task_panel:
                self.right_panel.task_panel.focusGained.connect(self._suspend_vi_for_tasks)
        except Exception:
            pass

        self._minibar_width = 28
        self.right_minibar, self._right_minibar_bar, self._right_minibar_toggle = self._build_minibar(
            self._right_minibar_labels(),
            side="right",
        )
        self._right_minibar_bar.tabBarClicked.connect(self._expand_right_from_minibar)
        self._right_minibar_bar.setContextMenuPolicy(Qt.CustomContextMenu)
        self._right_minibar_bar.customContextMenuRequested.connect(self._show_right_minibar_context_menu)
        self._right_minibar_toggle.clicked.connect(lambda *_: self._set_right_panel_collapsed(False))
        self.right_panel.tabs.currentChanged.connect(self._sync_right_minibar_selection)
        self._right_toggle_button = QToolButton()
        self._right_toggle_button.setAutoRaise(True)
        self._right_toggle_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._right_toggle_button.clicked.connect(lambda *_: self._toggle_right_panel())
        self.right_panel.tabs.setCornerWidget(self._right_toggle_button, Qt.TopRightCorner)
        self.right_panel_container = QWidget()
        self._right_panel_stack = QStackedLayout(self.right_panel_container)
        self._right_panel_stack.setContentsMargins(0, 0, 0, 0)
        self._right_panel_stack.addWidget(self.right_panel)
        self._right_panel_stack.addWidget(self.right_minibar)
        self._right_panel_stack.setCurrentWidget(self.right_panel)
        self._refresh_right_minibar_tabs()
        self._sync_right_minibar_selection(self.right_panel.tabs.currentIndex())
        self._inline_editor: Optional[InlineNameEdit] = None
        self._pending_selection: Optional[str] = None
        self._suspend_autosave = False
        self._vault_lock_path: Optional[Path] = None
        self._vault_lock_owner: Optional[dict] = None
        self._read_only: bool = False
        self._ai_chat_store: Optional[AIChatStore] = None
        self._ai_badge_icon: Optional[QIcon] = None
        self._page_windows: list[PageEditorWindow] = []
        self._mode_window: Optional[ModeWindow] = None
        self._detached_ai_chat_panel: Optional[AIChatPanel] = None
        self._detached_ai_chat_window: Optional[QMainWindow] = None
        self._apply_read_only_state()

        # Geometry save timer (debounce frequent resize/splitter move events)
        self.geometry_save_timer = QTimer(self)
        self.geometry_save_timer.setInterval(500)  # 500ms debounce
        self.geometry_save_timer.setSingleShot(True)
        self.geometry_save_timer.timeout.connect(self._save_geometry)

        # Vi-mode state
       
        editor_container = QWidget()
        editor_layout = QVBoxLayout(editor_container)
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.setSpacing(0)
        editor_layout.addWidget(self.editor, 1)
        editor_layout.addWidget(self.find_bar)

        self.editor_split = QSplitter()
        self.editor_split.addWidget(editor_container)
        self.editor_split.addWidget(self.right_panel_container)
        self.editor_split.setChildrenCollapsible(False)
        self.editor_split.setHandleWidth(8)
        # Allow the editor to shrink enough so the right panel can expand comfortably
        self.editor.setMinimumWidth(200)
        self.right_panel.setMinimumWidth(240)
        self.editor_split.setStretchFactor(0, 4)
        self.editor_split.setStretchFactor(1, 2)
        self.editor_split.splitterMoved.connect(self._on_splitter_moved)

        # Create left panel with tabs for Vault, Tags, and Search
        self.left_tab_widget = QTabWidget()
        self.left_tab_widget.setMinimumWidth(80)
        self._left_toggle_button = QToolButton()
        self._left_toggle_button.setAutoRaise(True)
        self._left_toggle_button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        self._left_toggle_button.clicked.connect(lambda *_: self._toggle_left_panel())
        self.left_tab_widget.setCornerWidget(self._left_toggle_button, Qt.TopRightCorner)
        
        # Vault tab (tree with header)
        vault_tab = QWidget()
        vault_layout = QVBoxLayout()
        vault_layout.setContentsMargins(0, 0, 0, 0)
        vault_layout.setSpacing(0)
        vault_layout.addWidget(self.tree_header_widget)
        vault_layout.addWidget(self.tree_view)
        vault_tab.setLayout(vault_layout)
        self.left_tab_widget.addTab(vault_tab, "Vault")
        
        # Tags tab
        self.tags_tab = None
        if self._feature_tags_enabled:
            self.tags_tab = TagsTab(http_client=self.http)
            self.tags_tab.pageNavigationRequested.connect(self._on_search_result_selected)
            self.tags_tab.pageNavigationWithEditorFocusRequested.connect(self._on_search_result_selected_with_editor_focus)
            self.left_tab_widget.addTab(self.tags_tab, "Tags")
            if self._nav_filter_path:
                try:
                    self.tags_tab.set_navigation_filter(
                        self._nav_filter_path,
                        path_to_colon(self._nav_filter_path),
                        self._clear_nav_filter,
                    )
                except Exception:
                    pass
        
        # Search tab
        self.search_tab = SearchTab(http_client=self.http)
        self.search_tab.pageNavigationRequested.connect(self._on_search_result_selected)
        self.search_tab.pageNavigationWithEditorFocusRequested.connect(self._on_search_result_selected_with_editor_focus)
        self.left_tab_widget.addTab(self.search_tab, "Search")

        self.left_minibar, self._left_minibar_bar, self._left_minibar_toggle = self._build_minibar(
            self._left_minibar_labels(),
            side="left",
        )
        self._left_minibar_bar.tabBarClicked.connect(self._expand_left_from_minibar)
        self._left_minibar_toggle.clicked.connect(lambda *_: self._set_left_panel_collapsed(False))
        self.left_tab_widget.currentChanged.connect(self._sync_left_minibar_selection)
        self.left_panel_container = QWidget()
        self._left_panel_stack = QStackedLayout(self.left_panel_container)
        self._left_panel_stack.setContentsMargins(0, 0, 0, 0)
        self._left_panel_stack.addWidget(self.left_tab_widget)
        self._left_panel_stack.addWidget(self.left_minibar)
        self._left_panel_stack.setCurrentWidget(self.left_tab_widget)
        self._sync_left_minibar_selection(self.left_tab_widget.currentIndex())
        self._update_sidebar_toggle_icons()
        
        self.main_splitter = QSplitter()
        self.main_splitter.addWidget(self.left_panel_container)
        self.main_splitter.addWidget(self.editor_split)
        self.main_splitter.setStretchFactor(1, 5)
        self.main_splitter.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.main_splitter.splitterMoved.connect(self._on_splitter_moved)

        # Create history bar (separate row for history buttons)
        self.history_bar = QWidget()
        self.history_bar.setMaximumHeight(40)
        self.history_bar.setStyleSheet(
            "border-top: 1px solid "
            f"{theme_value('main_window.history.border', '#555555')};"
        )
        history_bar_layout = QHBoxLayout(self.history_bar)
        history_bar_layout.setContentsMargins(5, 2, 5, 2)
        history_bar_layout.setSpacing(4)
        
        # Add history buttons container
        self.history_container = QWidget()
        self.history_container.setStyleSheet("")  # Clear any inherited styles
        self.history_container.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.history_container.setMinimumWidth(0)
        self.history_layout = QHBoxLayout(self.history_container)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(4)
        self.history_layout.setAlignment(Qt.AlignLeft)
        history_bar_layout.addWidget(self.history_container, 1)
        
        # Add spacer to push buttons to the left
        history_spacer = QWidget()
        history_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        history_bar_layout.addWidget(history_spacer)

        # Container (no vi-mode banner)
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.history_bar)
        layout.addWidget(self.main_splitter, 1)
        self.setCentralWidget(container)
        self._position_toc_widget()

        # No overlay/indicator widgets; vi-mode is represented by editor cursor style

        # Build toolbar and main menus
        self._build_toolbar()
        # Vault menu (now left of File)
        vault_menu = self.menuBar().addMenu("&Vault")
        file_menu = self.menuBar().addMenu("F&ile")
        open_vault_new_win_action = QAction("Open Vault in New Window", self)
        open_vault_new_win_action.setToolTip("Launch a separate StillPoint process for a vault")
        open_vault_new_win_action.triggered.connect(lambda checked=False: self._select_vault(spawn_new_process=True))
        vault_menu.addAction(open_vault_new_win_action)
        vault_prefs_action = QAction("Vault Preferences...", self)
        vault_prefs_action.setToolTip("Override global preferences for this vault")
        vault_prefs_action.triggered.connect(self._open_vault_preferences)
        vault_menu.addAction(vault_prefs_action)
        self._action_homebase_sync_now = QAction("Sync Now", self)
        self._action_homebase_sync_now.setToolTip("Run Homebase sync immediately")
        self._action_homebase_sync_now.triggered.connect(
            lambda checked=False: self._trigger_homebase_sync_now("menu")
        )
        self._action_homebase_reset_sync = QAction("Reset Sync State (Server Authoritative)", self)
        self._action_homebase_reset_sync.setToolTip(
            "Discard local sync state/conflicts and re-seed local files from the current server snapshot"
        )
        self._action_homebase_reset_sync.triggered.connect(self._reset_homebase_sync_state_server_authoritative)
        reload_vault_action = QAction("Reload Vault", self)
        reload_vault_action.setToolTip("Close and reopen the current vault")
        reload_vault_action.triggered.connect(self._reload_vault)
        vault_menu.addAction(reload_vault_action)
        close_vault_action = QAction("Close Vault", self)
        close_vault_action.setToolTip("Close this window")
        close_vault_action.triggered.connect(self._close_vault_window)
        vault_menu.addAction(close_vault_action)
        self._remote_vault_menu = vault_menu.addMenu("Remote Vault")
        try:
            self._remote_vault_menu.menuAction().setVisible(False)
        except Exception:
            self._remote_vault_menu.setVisible(False)
        self._action_server_login = QAction("Login - Authenticate to Remote Vault", self)
        self._action_server_login.setToolTip("Authenticate to the remote vault")
        self._action_server_login.triggered.connect(self._handle_remote_vault_login)
        self._remote_vault_menu.addAction(self._action_server_login)
        self._action_server_logout = QAction("Logout - Clear Remote Vault Credentials", self)
        self._action_server_logout.setToolTip("Clear stored remote vault credentials")
        self._action_server_logout.triggered.connect(self._handle_remote_vault_logout)
        self._remote_vault_menu.addAction(self._action_server_logout)
        self._action_manage_users = QAction("Manage Users...", self)
        self._action_manage_users.setToolTip("Manage users for this vault")
        self._action_manage_users.triggered.connect(self._open_user_management)
        self._remote_vault_menu.addAction(self._action_manage_users)
        self._action_reset_password = QAction("Reset Password...", self)
        self._action_reset_password.setToolTip("Reset your vault password")
        self._action_reset_password.triggered.connect(self._handle_remote_vault_reset_password)
        self._remote_vault_menu.addAction(self._action_reset_password)
        self._remote_vault_menu.addSeparator()
        self._remote_vault_menu.addAction(self._action_homebase_sync_now)
        self._remote_vault_menu.addAction(self._action_homebase_reset_sync)
        self._action_new_vault = QAction("New Vault", self)
        self._action_new_vault.setToolTip("Create a new vault")
        self._action_new_vault.triggered.connect(self._create_vault)
        vault_menu.addAction(self._action_new_vault)
        view_vault_disk_action = QAction("View Vault on Disk", self)
        view_vault_disk_action.setToolTip("Open the vault folder in your system file manager")
        view_vault_disk_action.triggered.connect(self._open_vault_on_disk)
        vault_menu.addAction(view_vault_disk_action)
        open_templates_action = QAction("Open Template Folder", self)
        open_templates_action.setToolTip("Open or create ~/.stillpoint/templates in your file manager")
        open_templates_action.triggered.connect(self._open_user_templates_folder)
        vault_menu.addAction(open_templates_action)
        self._action_quick_capture = QAction("Quick Capture...", self)
        self._action_quick_capture.setToolTip("Capture a thought into your home vault")
        self._action_quick_capture.triggered.connect(self._show_quick_capture_overlay)
        vault_menu.addSeparator()
        search_vault_action = QAction("Search Across Vault...", self)
        search_vault_action.setToolTip("Search for text across all pages in the vault")
        search_vault_action.triggered.connect(self._search_across_vault)
        vault_menu.addAction(search_vault_action)
        import_menu = file_menu.addMenu("Import")
        zim_import_action = QAction("Zim Wiki…", self)
        zim_import_action.setToolTip("Import pages from a Zim wiki folder or .txt file")
        zim_import_action.triggered.connect(self._import_zim_wiki)
        import_menu.addAction(zim_import_action)
        new_page_action = QAction("New Page...", self)
        new_page_action.setToolTip("Create a new page in the current folder")
        new_page_action.triggered.connect(
            lambda checked=False: self._show_new_page_dialog(insert_link_in_editor=False)
        )
        file_menu.addAction(new_page_action)
        delete_page_action = QAction("Delete Page", self)
        delete_page_action.setToolTip("Delete the current page")
        delete_page_action.triggered.connect(self._delete_current_page_from_menu)
        file_menu.addSeparator()
        file_menu.addAction(delete_page_action)
        self._build_format_menu()
        view_menu = self.menuBar().addMenu("&View")
        view_mode_menu = view_menu.addMenu("View...")
        self._action_focus_mode = QAction("Focus Mode", self)
        self._action_focus_mode.setToolTip("Open in Focus Mode")
        self._action_focus_mode.triggered.connect(lambda checked=False: self._toggle_mode_overlay("focus"))
        view_mode_menu.addAction(self._action_focus_mode)
        self._action_audience_mode = QAction("Audience Mode", self)
        self._action_audience_mode.setToolTip("Open in Audience Mode")
        self._action_audience_mode.triggered.connect(lambda checked=False: self._toggle_mode_overlay("audience"))
        view_mode_menu.addAction(self._action_audience_mode)
        view_menu.addSeparator()
        reset_view_action = QAction("Reset View/Layout", self)
        reset_view_action.setToolTip("Reset window size and splitter positions to defaults")
        reset_view_action.triggered.connect(self._reset_view_layout)
        view_menu.addAction(reset_view_action)
        view_menu.addSeparator()
        if self._feature_tasks_enabled:
            task_window_action = QAction("Open Task Panel Window", self)
            task_window_action.triggered.connect(self._open_task_panel_window)
            view_menu.addAction(task_window_action)
        self._action_calendar_window = QAction("Open Calendar Window", self)
        self._action_calendar_window.triggered.connect(self._open_calendar_panel_window)
        self._action_calendar_window.setVisible(self._feature_calendar_enabled)
        view_menu.addAction(self._action_calendar_window)
        if self._feature_link_navigator_enabled:
            link_window_action = QAction("Open Link Navigator Window", self)
            link_window_action.triggered.connect(self._open_link_panel_window)
            view_menu.addAction(link_window_action)
        ai_window_action = QAction("Open AI Chat Window", self)
        ai_window_action.triggered.connect(self._open_ai_chat_window)
        view_menu.addAction(ai_window_action)
        tools_menu = self.menuBar().addMenu("&Tools")
        rebuild_index_action = QAction("Rebuild Vault Index", self)
        rebuild_index_action.setToolTip("Rebuild the vault database from disk (keeps bookmarks/kv/ai tables)")
        rebuild_index_action.triggered.connect(self._rebuild_vault_index_from_disk)
        tools_menu.addAction(rebuild_index_action)
        rebuild_search_index_action = QAction("Rebuild Vault Search Index", self)
        rebuild_search_index_action.setToolTip("Rebuild the full-text search index from disk")
        rebuild_search_index_action.triggered.connect(self._rebuild_vault_search_index)
        tools_menu.addAction(rebuild_search_index_action)

        webserver_action = QAction("Start Web Server", self)
        webserver_action.setToolTip("Start local web server to serve vault as HTML")
        webserver_action.triggered.connect(self._open_webserver_dialog)
        tools_menu.addAction(webserver_action)
        move_text_action = QAction("Move Text…", self)
        move_text_action.setToolTip("Move selected text to another page (Ctrl+Shift+M)")
        move_text_action.setShortcut(QKeySequence("Ctrl+Shift+M"))
        move_text_action.setShortcutContext(Qt.ApplicationShortcut)
        move_text_action.triggered.connect(self.editor._move_text_via_jump_dialog)
        tools_menu.addAction(move_text_action)
        tools_menu.addSeparator()
        tools_menu.addAction(self._action_quick_capture)
        self._action_view_vault_disk = view_vault_disk_action
        self._action_zim_import = zim_import_action
        self._action_rebuild_index = rebuild_index_action
        self._action_rebuild_search_index = rebuild_search_index_action
        self._action_webserver = webserver_action
        self._action_tooltips = {
            self._action_new_vault: self._action_new_vault.toolTip(),
            self._action_view_vault_disk: self._action_view_vault_disk.toolTip(),
            self._action_zim_import: self._action_zim_import.toolTip(),
            self._action_rebuild_index: self._action_rebuild_index.toolTip(),
            self._action_rebuild_search_index: self._action_rebuild_search_index.toolTip(),
            self._action_webserver: self._action_webserver.toolTip(),
            self._action_server_login: self._action_server_login.toolTip(),
            self._action_server_logout: self._action_server_logout.toolTip(),
            self._action_manage_users: self._action_manage_users.toolTip(),
            self._action_reset_password: self._action_reset_password.toolTip(),
            self._action_homebase_sync_now: self._action_homebase_sync_now.toolTip(),
            self._action_homebase_reset_sync: self._action_homebase_reset_sync.toolTip(),
        }
        self._apply_remote_mode_ui()
        self._setup_tray_icon()
        self._register_quick_capture_hook()
        self._command_bar = MenuCommandBar(self)
        self._command_bar.actionTriggered.connect(self._run_command_bar_action)
        self._command_bar.closed.connect(self._clear_command_bar_context)
        self._command_bar_ai_text_override: Optional[str] = None
        self._ai_command_actions: list[QAction] = []

        go_menu = self.menuBar().addMenu("&Go")
        home_action = QAction("Home", self)
        home_action.triggered.connect(self._go_home)
        go_menu.addAction(home_action)

        if self._feature_tasks_enabled:
            tasks_action = QAction("Tasks", self)
            tasks_action.triggered.connect(self._focus_tasks_search)
            go_menu.addAction(tasks_action)

        if self._feature_tags_enabled:
            tags_action = QAction("Tags", self)
            tags_action.triggered.connect(self._focus_tags_tab)
            go_menu.addAction(tags_action)

        self._action_go_calendar = QAction("Calendar", self)
        self._action_go_calendar.triggered.connect(self._focus_calendar_tab)
        self._action_go_calendar.setVisible(self._feature_calendar_enabled)
        go_menu.addAction(self._action_go_calendar)

        attach_action = QAction("Attachments", self)
        attach_action.triggered.connect(self._focus_attachments_tab)
        go_menu.addAction(attach_action)

        if self._feature_link_navigator_enabled:
            link_action = QAction("Link Navigator", self)
            link_action.triggered.connect(lambda: self._apply_navigation_focus("navigator"))
            go_menu.addAction(link_action)

        ai_action = QAction("AI Chat", self)
        ai_action.triggered.connect(self._open_ai_chat_window)
        go_menu.addAction(ai_action)

        jump_action = QAction("Jump To Page… (Ctrl+J)", self)
        jump_action.setToolTip("Jump to a page (Ctrl+J)")
        jump_action.triggered.connect(self._jump_to_page)
        go_menu.addAction(jump_action)

        self._action_go_today = QAction("Today", self)
        self._action_go_today.setToolTip("Today's journal entry (Alt+D)")
        self._action_go_today.triggered.connect(self._open_journal_today)
        self._action_go_today.setVisible(self._feature_calendar_enabled)
        go_menu.addAction(self._action_go_today)
        command_bar_action = QAction("Command Bar", self)
        command_bar_action.triggered.connect(self._show_command_bar)
        go_menu.addAction(command_bar_action)

        rename_action = QAction("Rename", self)
        rename_action.setShortcut(QKeySequence(Qt.Key_F2))
        rename_action.setShortcutContext(Qt.ApplicationShortcut)
        rename_action.triggered.connect(self._trigger_tree_rename)
        file_menu.addAction(rename_action)

        file_menu.addSeparator()
        
        print_page_action = QAction("Print Page", self)
        print_page_action.setShortcut(QKeySequence.Print)
        print_page_action.setShortcutContext(Qt.ApplicationShortcut)
        print_page_action.setToolTip("Print or export current page to PDF (Ctrl+P)")
        print_page_action.triggered.connect(self._print_current_page)
        file_menu.addAction(print_page_action)

        insert_link_action = QAction("Insert Link…", self)
        insert_link_action.setToolTip("Insert a link to another page (Ctrl+L)")
        insert_link_action.triggered.connect(self._insert_link)
        file_menu.addAction(insert_link_action)

        help_menu = self.menuBar().addMenu("Hel&p")
        documentation_action = QAction("Documentation", self)
        documentation_action.setShortcutContext(Qt.ApplicationShortcut)
        documentation_action.setToolTip("Open the built-in StillPoint documentation")
        documentation_action.triggered.connect(self._open_help_documentation)
        help_menu.addAction(documentation_action)
        shortcuts_action = QAction("Keyboard Shortcuts", self)
        shortcuts_action.setToolTip("Open the Keyboard Shortcuts page")
        shortcuts_action.triggered.connect(self._open_help_keyboard_shortcuts)
        help_menu.addAction(shortcuts_action)
        about_action = QAction("About", self)
        about_action.triggered.connect(self._show_about_dialog)
        help_menu.addAction(about_action)
        if os.getenv("STILLPOINT_ENABLE_CRASH_TEST") == "1":
            crash_action = QAction("Debug: Crash (Segfault)", self)
            crash_action.setToolTip("Force a native crash for testing error reporting")
            crash_action.triggered.connect(self._debug_crash_segfault)
            help_menu.addAction(crash_action)

        self._menu_roots = [
            vault_menu,
            file_menu,
            getattr(self, "_format_menu", None),
            view_menu,
            tools_menu,
            go_menu,
            help_menu,
        ]

        self._register_shortcuts()
        self._setup_quick_capture_shortcut(show_error=False)
        self._focus_recent = ["editor", "tree", "left", "right"]
        # Update focus borders and focus history when focus moves between widgets
        app = QApplication.instance()
        if app is not None:
            try:
                app.installEventFilter(self)
            except Exception:
                pass
            try:
                app.focusChanged.connect(lambda old, now: self._on_focus_changed(now))
            except Exception:
                pass
        # Apply initial border state
        self._apply_focus_borders()
        self.statusBar().showMessage("Select a vault to get started")
        self._default_status_stylesheet = self.statusBar().styleSheet()
        self._setup_eventloop_watchdog()

        # Create status badges (Dirty + VI)
        self._badge_base_style = (
            "border: 1px solid "
            f"{theme_value('main_window.badge.border', '#666666')}; "
            "padding: 2px 6px; border-radius: 3px;"
        )

        focus_icon = self._load_icon(
            self._find_asset("focus-mode.svg"),
            self._main_icon_color(),
            size=16,
        )
        self._focus_mode_button = QToolButton()
        self._focus_mode_button.setAutoRaise(True)
        if focus_icon:
            self._focus_mode_button.setIcon(focus_icon)
        self._focus_mode_button.setIconSize(QSize(16, 16))
        self._focus_mode_button.setToolTip("Open in Focus Mode")
        self._focus_mode_button.setCursor(QCursor(Qt.PointingHandCursor))
        self._focus_mode_button.setStyleSheet(
            "QToolButton { border: none; padding: 2px 4px; color: "
            f"{theme_value('main_window.mode_button.text', '#ffffff')}; "
            "}"
            "QToolButton:hover { background: "
            f"{theme_value('main_window.mode_button.hover_bg', '#2a2f36')}; "
            "border-radius: 3px; "
            "}"
        )
        self._focus_mode_button.clicked.connect(lambda checked=False: self._toggle_mode_overlay("focus"))
        self.statusBar().addPermanentWidget(self._focus_mode_button, 0)

        audience_icon = self._load_icon(
            self._find_asset("present-mode.svg"),
            self._main_icon_color(),
            size=16,
        )
        self._audience_mode_button = QToolButton()
        self._audience_mode_button.setAutoRaise(True)
        if audience_icon:
            self._audience_mode_button.setIcon(audience_icon)
        self._audience_mode_button.setIconSize(QSize(16, 16))
        self._audience_mode_button.setToolTip("Open in Audience Mode")
        self._audience_mode_button.setCursor(QCursor(Qt.PointingHandCursor))
        self._audience_mode_button.setStyleSheet(
            "QToolButton { border: none; padding: 2px 4px; color: "
            f"{theme_value('main_window.mode_button.text', '#ffffff')}; "
            "}"
            "QToolButton:hover { background: "
            f"{theme_value('main_window.mode_button.hover_bg', '#2a2f36')}; "
            "border-radius: 3px; "
            "}"
        )
        self._audience_mode_button.clicked.connect(lambda checked=False: self._toggle_mode_overlay("audience"))
        self.statusBar().addPermanentWidget(self._audience_mode_button, 0)

        self._dirty_status_label = QLabel("")
        self._dirty_status_label.setObjectName("dirtyStatusLabel")
        self._dirty_status_label.setStyleSheet(self._badge_base_style + " background-color: transparent; margin-right: 6px;")
        self._dirty_status_label.setToolTip("Unsaved changes")
        self.statusBar().addPermanentWidget(self._dirty_status_label, 0)

        self._filter_status_label = QLabel("")
        self._filter_status_label.setObjectName("filterStatusLabel")
        self._filter_status_label.setStyleSheet(
            "QLabel { "
            + self._badge_base_style
            + " background-color: "
            f"{theme_value('main_window.filter_badge.bg', '#c62828')}; "
            "margin-right: 6px; color: "
            f"{theme_value('main_window.filter_badge.text', '#ffffff')}; }}"
            + " QLabel a { color: "
            f"{theme_value('main_window.filter_badge.link', '#ffffff')}; "
            "text-decoration: none; }"
            + " QLabel a:hover { text-decoration: underline; }"
        )
        self._filter_status_label.setToolTip("Navigation filtered (click to clear)")
        self._filter_status_label.setCursor(QCursor(Qt.PointingHandCursor))
        self._filter_status_label.mousePressEvent = lambda event: self._clear_nav_filter()
        self._filter_status_label.hide()
        self.statusBar().addPermanentWidget(self._filter_status_label, 0)

        self._vi_status_label = QLabel("INS")
        self._vi_status_label.setObjectName("viStatusLabel")
        self._vi_badge_base_style = self._badge_base_style
        self._vi_status_label.setToolTip("Shows when vi insert mode is active")
        self.statusBar().addPermanentWidget(self._vi_status_label, 0)
        self._update_vi_badge_visibility()

        self._remote_feedback_label = QLabel("")
        self._remote_feedback_label.setObjectName("remoteFeedbackLabel")
        self._remote_feedback_label.setStyleSheet(
            self._badge_base_style
            + " background-color: "
            f"{theme_value('main_window.remote_badge.slow_bg', '#ed6c02')}; "
            "margin-right: 6px; color: "
            f"{theme_value('main_window.remote_badge.text', '#ffffff')};"
        )
        self._remote_feedback_label.hide()
        self.statusBar().addPermanentWidget(self._remote_feedback_label, 0)

        self._remote_status_label = QLabel("REMOTE")
        self._remote_status_label.setObjectName("remoteStatusLabel")
        self._remote_status_label.setStyleSheet(
            self._badge_base_style
            + " background-color: "
            f"{theme_value('main_window.remote_badge.bg', '#1e88e5')}; "
            "margin-right: 6px; color: "
            f"{theme_value('main_window.remote_badge.text', '#ffffff')};"
        )
        self._remote_status_label.setToolTip("")
        self._remote_status_label.hide()
        self.statusBar().addPermanentWidget(self._remote_status_label, 0)

        self._homebase_status_label = QLabel("HOMEBASE")
        self._homebase_status_label.setObjectName("homebaseStatusLabel")
        self._homebase_status_label.setStyleSheet(
            self._badge_base_style
            + " background-color: "
            f"{theme_value('main_window.homebase_badge.bg', '#2e7d32')}; "
            "margin-right: 6px; color: "
            f"{theme_value('main_window.homebase_badge.text', '#ffffff')};"
        )
        self._homebase_status_label.setToolTip("")
        self._homebase_status_label.setCursor(QCursor(Qt.PointingHandCursor))
        self._homebase_status_label.mousePressEvent = lambda event: self._show_homebase_sync_summary()
        self._homebase_status_label.hide()
        self.statusBar().addPermanentWidget(self._homebase_status_label, 0)

        self._detached_panels: list[QMainWindow] = []
        self._detached_link_panels: list[LinkNavigatorPanel] = []

        # Keep dirty indicator in sync with edits
        try:
            self.editor.document().modificationChanged.connect(self._on_document_modified)
        except Exception:
            pass
        self._update_dirty_indicator()
        self._update_filter_indicator()

        # Startup vault selection is orchestrated by main.py via .startup()
        self.editor.set_ai_actions_enabled(config.load_enable_ai_chats())

        # Tree caching and versioning (per-path cache keyed by server tree version)
        self._tree_version: int = 0
        self._tree_path_version: dict[str, int] = {}
        logNav("Initialized tree version tracking")

    def _setup_eventloop_watchdog(self) -> None:
        """Log when the Qt event loop appears stalled (high timer drift)."""
        if not PAGE_LOGGING_ENABLED:
            return
        try:
            self._loop_timer = QElapsedTimer()
            self._loop_timer.start()
            self._loop_watchdog = QTimer(self)
            self._loop_watchdog.setInterval(250)
            self._loop_watchdog.timeout.connect(self._check_eventloop_drift)
            self._loop_watchdog.start()
            dispatcher = QAbstractEventDispatcher.instance()
            if dispatcher:
                dispatcher.aboutToBlock.connect(lambda: self._mark_eventloop("aboutToBlock"))
                dispatcher.awake.connect(lambda: self._mark_eventloop("awake"))
        except Exception:
            pass

    def _mark_eventloop(self, phase: str) -> None:
        if not PAGE_LOGGING_ENABLED or not hasattr(self, "_loop_timer"):
            return
        elapsed = self._loop_timer.elapsed()
        #print(f"[PageLoadAndRender] eventloop {phase} dt={elapsed:.1f}ms")
        self._loop_timer.restart()

    def _check_eventloop_drift(self) -> None:
        if not PAGE_LOGGING_ENABLED or not hasattr(self, "_loop_timer"):
            return
        elapsed = self._loop_timer.elapsed()
        if elapsed > 500:  # 0.5s threshold suggests the loop was blocked
            #print(f"[PageLoadAndRender] eventloop drift warning dt={elapsed:.1f}ms (loop stall?)")
            self._loop_timer.restart()

    # --- UI wiring -----------------------------------------------------
    def _build_toolbar(self) -> None:
        self.toolbar = self.addToolBar("Main")
        self.toolbar.setMovable(False)
        icon_color = self._main_icon_color()

        home_icon = self._load_icon(self._find_asset("home.svg"), icon_color, size=18)
        search_icon = self._load_icon(self._find_asset("binoculars.svg"), icon_color, size=18)
        today_icon = self._load_icon(self._find_asset("calendar-days.svg"), icon_color, size=18)
        back_icon = self._load_icon(self._find_asset("left.svg"), icon_color, size=18)
        forward_icon = self._load_icon(self._find_asset("right.svg"), icon_color, size=18)
        up_icon = self._load_icon(self._find_asset("up.svg"), icon_color, size=18)
        down_icon = self._load_icon(self._find_asset("down.svg"), icon_color, size=18)

        # Home button (navigate to vault root page)
        home_action = QAction("Home", self)
        home_action.setIcon(home_icon if home_icon else self.style().standardIcon(QStyle.SP_DirHomeIcon))
        home_action.setToolTip("Go to vault home page")
        home_action.triggered.connect(self._go_home)
        self.toolbar.addAction(home_action)

        # Search button (search across vault)
        search_action = QAction("Search", self)
        search_action.setIcon(search_icon if search_icon else self.style().standardIcon(QStyle.SP_FileDialogContentsView))
        search_action.setToolTip("Search across vault (Ctrl+Shift+F)")
        search_action.triggered.connect(self._show_search_dialog)
        self.toolbar.addAction(search_action)

        # Today button (jump to today's journal entry)
        self._toolbar_today_action = QAction("Today", self)
        self._toolbar_today_action.setIcon(
            today_icon if today_icon else self.style().standardIcon(QStyle.SP_FileDialogDetailedView)
        )
        self._toolbar_today_action.setToolTip("Today's journal entry (Alt+D)")
        self._toolbar_today_action.triggered.connect(self._open_journal_today)
        self._toolbar_today_action.setVisible(self._feature_calendar_enabled)
        self.toolbar.addAction(self._toolbar_today_action)
        
        # Bookmark button (bold blue plus symbol)
        self.bookmark_button = QAction("Add Bookmark", self)
        self.bookmark_button.setToolTip("Toggle bookmark (Ctrl+Alt+B)")
        self.bookmark_button.triggered.connect(self._add_bookmark)
        bookmark_icon = self._load_icon(self._find_asset("bookmark.svg"), icon_color, size=18)
        if bookmark_icon:
            self.bookmark_button.setIcon(bookmark_icon)
        else:
            # Style the button text to be a bold blue plus symbol
            font = QFont()
            font.setPointSize(20)
            font.setBold(True)
            self.bookmark_button.setFont(font)
            # Set text as plus symbol
            self.bookmark_button.setText("+")
            # We'll apply color via stylesheet after adding to toolbar
        self.toolbar.addAction(self.bookmark_button)

        print_action = QAction("Print Page", self)
        print_action.setToolTip("Print or export current page to PDF (Ctrl+P)")
        print_icon = self._load_icon(self._find_asset("print.svg"), icon_color, size=18)
        if print_icon:
            print_action.setIcon(print_icon)
        print_action.triggered.connect(self._print_current_page)
        self.toolbar.addAction(print_action)

        # Add bookmark display area with horizontal scroll controls
        self.bookmark_strip = QWidget()
        self.bookmark_layout = QHBoxLayout(self.bookmark_strip)
        self.bookmark_layout.setContentsMargins(0, 0, 0, 0)
        self.bookmark_layout.setSpacing(4)

        self.bookmark_scroll_area = QScrollArea()
        self.bookmark_scroll_area.setFrameShape(QFrame.NoFrame)
        self.bookmark_scroll_area.setWidgetResizable(False)
        self.bookmark_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.bookmark_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.bookmark_strip.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.bookmark_scroll_area.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.bookmark_scroll_area.setWidget(self.bookmark_strip)

        self.bookmark_scroll_left = QToolButton()
        self.bookmark_scroll_left.setIcon(self.style().standardIcon(QStyle.SP_ArrowLeft))
        self.bookmark_scroll_left.setAutoRaise(True)
        self.bookmark_scroll_left.setToolTip("Scroll bookmarks left")
        self.bookmark_scroll_left.clicked.connect(lambda: self._scroll_bookmarks(-180))

        self.bookmark_scroll_right = QToolButton()
        self.bookmark_scroll_right.setIcon(self.style().standardIcon(QStyle.SP_ArrowRight))
        self.bookmark_scroll_right.setAutoRaise(True)
        self.bookmark_scroll_right.setToolTip("Scroll bookmarks right")
        self.bookmark_scroll_right.clicked.connect(lambda: self._scroll_bookmarks(180))

        self.bookmark_container = QWidget()
        self.bookmark_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._toolbar_height = self.toolbar.iconSize().height() + 8
        self.bookmark_container.setMinimumHeight(self._toolbar_height)
        self.bookmark_container.setMaximumHeight(self._toolbar_height)
        self.bookmark_scroll_area.setMinimumHeight(self._toolbar_height)
        self.bookmark_scroll_area.setMaximumHeight(self._toolbar_height)
        self.bookmark_scroll_left.setFixedHeight(self._toolbar_height)
        self.bookmark_scroll_right.setFixedHeight(self._toolbar_height)
        self.bookmark_strip.setMinimumHeight(self._toolbar_height)
        bookmark_container_layout = QHBoxLayout(self.bookmark_container)
        bookmark_container_layout.setContentsMargins(0, 0, 0, 0)
        bookmark_container_layout.setSpacing(2)
        bookmark_container_layout.addWidget(self.bookmark_scroll_left)
        bookmark_container_layout.addWidget(self.bookmark_scroll_area, 1)
        bookmark_container_layout.addWidget(self.bookmark_scroll_right)
        self.bookmark_container.setLayout(bookmark_container_layout)
        self.toolbar.addWidget(self.bookmark_container)

        self.bookmark_scroll_area.installEventFilter(self)
        self.toolbar.installEventFilter(self)
        self._update_bookmark_scroll_buttons()
        
        # Preferences/settings cog icon
        prefs_action = QAction("Preferences", self)
        cog_icon_path = self._find_asset("cog.svg")
        cog_icon = self._load_icon(cog_icon_path, icon_color, size=18)
        prefs_action.setIcon(cog_icon if cog_icon else self.style().standardIcon(QStyle.SP_FileDialogDetailedView))
        prefs_action.triggered.connect(self._open_preferences)
        self.toolbar.addAction(prefs_action)

        # Store default style to restore later
        self._default_toolbar_stylesheet = self.toolbar.styleSheet()
        
        # Apply blue color to bookmark button via stylesheet
        self.toolbar.setStyleSheet(
            "QToolButton[text=\"+\"] { "
            "color: "
            f"{theme_value('main_window.toolbar.bookmark_color', '#4A90E2')}; "
            "font-size: "
            f"{theme_value('main_window.toolbar.bookmark_size_pt', 20)}pt; "
            "font-weight: "
            f"{theme_value('main_window.toolbar.bookmark_weight', 'bold')}; "
            "}"
        )

    def _open_vault_on_disk(self):
        """Open the vault folder in the system file manager."""
        if not self._require_local_mode("Open the vault folder on disk"):
            return
        vault_path = self.vault_root
        if not vault_path:
            self.statusBar().showMessage("No vault selected.")
            return
        opened = self._open_in_file_manager(Path(vault_path))
        if opened:
            self.statusBar().showMessage(f"Opened vault folder: {vault_path}")
        else:
            self._alert(f"Could not open vault folder: {vault_path}")
    
    def _open_user_templates_folder(self) -> None:
        """Open or create the user template folder (~/.stillpoint/templates) in the system file manager."""
        tmpl_dir = Path.home() / ".stillpoint" / "templates"
        try:
            tmpl_dir.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            self._alert(f"Could not create template folder: {exc}")
            return
        opened = self._open_in_file_manager(tmpl_dir)
        if opened:
            self.statusBar().showMessage(f"Opened template folder: {tmpl_dir}", 3000)
        else:
            self._alert(f"Could not open template folder: {tmpl_dir}")

    def _register_shortcuts(self) -> None:
        save_shortcut = QShortcut(QKeySequence("Ctrl+S"), self)
        save_shortcut.activated.connect(lambda: self._save_current_file(reason="manual save"))
        zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        zoom_in.setContext(Qt.ApplicationShortcut)
        zoom_out.setContext(Qt.ApplicationShortcut)
        zoom_in.activated.connect(lambda: self._adjust_font_size(1))
        zoom_out.activated.connect(lambda: self._adjust_font_size(-1))
        jump_shortcut = QShortcut(QKeySequence("Ctrl+J"), self)
        jump_shortcut.activated.connect(self._jump_to_page)
        link_shortcut = QShortcut(QKeySequence("Ctrl+L"), self)
        link_shortcut.setContext(Qt.ApplicationShortcut)
        link_shortcut.activated.connect(self._insert_link)
        copy_link_shortcut = QShortcut(QKeySequence("Ctrl+Shift+L"), self)
        copy_link_shortcut.setContext(Qt.ApplicationShortcut)
        copy_link_shortcut.activated.connect(self._copy_current_page_link)
        focus_tasks_shortcut = QShortcut(QKeySequence("Ctrl+\\"), self)
        focus_tasks_shortcut.setContext(Qt.ApplicationShortcut)
        focus_tasks_shortcut.activated.connect(self._focus_tasks_search)
        focus_tasks_shortcut2 = QShortcut(QKeySequence("Ctrl+Backslash"), self)
        focus_tasks_shortcut2.setContext(Qt.ApplicationShortcut)
        focus_tasks_shortcut2.activated.connect(self._focus_tasks_search)
        date_shortcut = QShortcut(QKeySequence("Ctrl+D"), self)
        date_shortcut.activated.connect(self._insert_date)
        rename_shortcut = QShortcut(QKeySequence(Qt.Key_F2), self)
        rename_shortcut.setContext(Qt.ApplicationShortcut)
        rename_shortcut.activated.connect(self._trigger_tree_rename)
        switch_vault_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        switch_vault_shortcut.activated.connect(lambda: self._select_vault())
        open_vault_new_win_shortcut = QShortcut(QKeySequence("Ctrl+Shift+O"), self)
        open_vault_new_win_shortcut.activated.connect(lambda: self._select_vault(spawn_new_process=True))
        focus_mode_shortcut = QShortcut(QKeySequence("Ctrl+Alt+F"), self)
        focus_mode_shortcut.setContext(Qt.ApplicationShortcut)
        focus_mode_shortcut.activated.connect(lambda: self._toggle_mode_overlay("focus"))
        audience_mode_shortcut = QShortcut(QKeySequence("Ctrl+Alt+A"), self)
        audience_mode_shortcut.setContext(Qt.ApplicationShortcut)
        audience_mode_shortcut.activated.connect(lambda: self._toggle_mode_overlay("audience"))
        focus_toggle = QShortcut(QKeySequence("Ctrl+Shift+Space"), self)
        focus_toggle.activated.connect(self._toggle_focus_between_tree_and_editor)
        redo_shortcut = QShortcut(QKeySequence("Ctrl+Y"), self)
        redo_shortcut.activated.connect(self.editor._redo_or_status)
        # Explicit heading popup shortcut (Ctrl+Shift+Tab)
        heading_popup = QShortcut(QKeySequence("Ctrl+Shift+Tab"), self)
        heading_popup.setContext(Qt.WindowShortcut)
        heading_popup.activated.connect(lambda: self._cycle_popup("heading", reverse=False))
        new_page_shortcut = QShortcut(QKeySequence("Ctrl+N"), self)
        new_page_shortcut.activated.connect(lambda: self._show_new_page_dialog(insert_link_in_editor=False))
        journal_shortcut = QShortcut(QKeySequence("Alt+D"), self)
        journal_shortcut.activated.connect(self._open_journal_today)
        # Home shortcut: Alt+Home (works regardless of vi-mode state)
        home_shortcut = QShortcut(QKeySequence("Alt+Home"), self)
        home_shortcut.activated.connect(self._go_home)
        bookmark_shortcut = QShortcut(QKeySequence("Ctrl+Alt+B"), self)
        bookmark_shortcut.setContext(Qt.ApplicationShortcut)
        bookmark_shortcut.activated.connect(self._add_bookmark)
        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.setContext(Qt.ApplicationShortcut)
        find_shortcut.activated.connect(lambda: self._show_find_bar(replace=False))
        replace_shortcut = QShortcut(QKeySequence("Ctrl+H"), self)
        replace_shortcut.setContext(Qt.ApplicationShortcut)
        replace_shortcut.activated.connect(lambda: self._show_find_bar(replace=True))
        task_cycle = QShortcut(QKeySequence(Qt.Key_F12), self)
        task_cycle.setContext(Qt.ApplicationShortcut)
        task_cycle.activated.connect(self.editor.toggle_task_state)
        # Navigation shortcuts
        nav_back = QShortcut(QKeySequence("Alt+Left"), self)
        nav_forward = QShortcut(QKeySequence("Alt+Right"), self)
        nav_up = QShortcut(QKeySequence("Alt+Up"), self)
        nav_down = QShortcut(QKeySequence("Alt+Down"), self)
        nav_pg_up = QShortcut(QKeySequence("Alt+PgUp"), self)
        nav_pg_down = QShortcut(QKeySequence("Alt+PgDown"), self)
        reload_page = QShortcut(QKeySequence("Ctrl+R"), self)
        toggle_left = QShortcut(QKeySequence("Ctrl+Shift+B"), self)
        toggle_right = QShortcut(QKeySequence("Ctrl+Shift+N"), self)
        search_vault = QShortcut(QKeySequence("Ctrl+Shift+F"), self)
        search_vault.setContext(Qt.ApplicationShortcut)
        search_vault.activated.connect(self._show_search_dialog)
        # Tab switching shortcuts
        tab_vault = QShortcut(QKeySequence("Ctrl+1"), self.left_tab_widget)
        tab_vault.setContext(Qt.WidgetWithChildrenShortcut)
        tab_vault.activated.connect(lambda: self.left_tab_widget.setCurrentIndex(0))
        tab_tags = QShortcut(QKeySequence("Ctrl+2"), self.left_tab_widget)
        tab_tags.setContext(Qt.WidgetWithChildrenShortcut)
        tab_tags.activated.connect(self._focus_tags_tab)
        tab_search = QShortcut(QKeySequence("Ctrl+3"), self.left_tab_widget)
        tab_search.setContext(Qt.WidgetWithChildrenShortcut)
        tab_search.activated.connect(lambda: self.left_tab_widget.setCurrentIndex(self.left_tab_widget.indexOf(self.search_tab)))
        prefs_shortcut = QShortcut(QKeySequence("Ctrl+."), self)
        prefs_shortcut.setContext(Qt.ApplicationShortcut)
        prefs_shortcut.activated.connect(self._open_preferences)
        command_bar_shortcut = QShortcut(QKeySequence("Alt+G"), self)
        command_bar_shortcut.setContext(Qt.ApplicationShortcut)
        command_bar_shortcut.activated.connect(self._show_command_bar)
        command_bar_universal = QShortcut(QKeySequence("Ctrl+Shift+P"), self)
        command_bar_universal.setContext(Qt.ApplicationShortcut)
        command_bar_universal.activated.connect(self._show_command_bar)
        nav_back.activated.connect(self._navigate_history_back)
        nav_forward.activated.connect(self._navigate_history_forward)
        nav_up.activated.connect(self._on_nav_up_shortcut)
        nav_down.activated.connect(self._on_nav_down_shortcut)
        nav_pg_up.activated.connect(lambda: self._navigate_tree(-1, leaves_only=False))
        nav_pg_down.activated.connect(lambda: self._navigate_tree(1, leaves_only=False))
        reload_page.activated.connect(self._reload_current_page)
        toggle_left.activated.connect(self._toggle_left_panel)
        toggle_right.activated.connect(self._toggle_right_panel)

    @staticmethod
    def _menu_text(text: str) -> str:
        return (text or "").replace("&", "").strip()

    def _command_bar_selection_text(self) -> str:
        if self._command_bar_ai_text_override is not None:
            return self._command_bar_ai_text_override
        try:
            cursor = self.editor.textCursor()
            if cursor.hasSelection():
                return cursor.selectedText().replace("\u2029", "\n")
        except Exception:
            pass
        return ""

    def _command_bar_open_ai_chat(self, *, create: bool, global_chat: bool) -> None:
        if global_chat:
            self._handle_ai_action("Load Global Chat", "", self._command_bar_selection_text())
            return
        target_path = self.current_path
        if not target_path:
            return
        self._open_ai_chat_for_path(target_path, create=create, focus_tab=True)

    def _clear_command_bar_context(self) -> None:
        self._command_bar_ai_text_override = None

    def _build_ai_command_actions(self) -> list[tuple[str, QAction]]:
        entries: list[tuple[str, QAction]] = []
        self._ai_command_actions = []

        def _make_action(label: str, handler: Callable[[], None]) -> QAction:
            action = QAction(label, self)
            action.triggered.connect(handler)
            self._ai_command_actions.append(action)
            return action

        base_label = "AI"
        entries.append(
            (
                f"{base_label} / Send selection to Page Chat",
                _make_action(
                    "AI: Send selection to Page Chat",
                    lambda: self._handle_ai_action("Send selection to Page Chat", "", self._command_bar_selection_text()),
                ),
            )
        )
        entries.append(
            (
                f"{base_label} / Send selection to Global Chat",
                _make_action(
                    "AI: Send selection to Global Chat",
                    lambda: self._handle_ai_action("Send selection to Global Chat", "", self._command_bar_selection_text()),
                ),
            )
        )
        entries.append(
            (
                f"{base_label} / Chat: Start Chat with this Page",
                _make_action(
                    "AI: Start Chat with this Page",
                    lambda: self._command_bar_open_ai_chat(create=True, global_chat=False),
                ),
            )
        )
        entries.append(
            (
                f"{base_label} / Chat: with Page",
                _make_action(
                    "AI: Chat with Page",
                    lambda: self._command_bar_open_ai_chat(create=False, global_chat=False),
                ),
            )
        )
        entries.append(
            (
                f"{base_label} / Chat: With Global",
                _make_action(
                    "AI: Chat With Global",
                    lambda: self._command_bar_open_ai_chat(create=True, global_chat=True),
                ),
            )
        )
        entries.append(
            (
                f"{base_label} / Load Global Chat",
                _make_action(
                    "AI: Load Global Chat",
                    lambda: self._handle_ai_action("Load Global Chat", "", self._command_bar_selection_text()),
                ),
            )
        )
        entries.append(
            (
                f"{base_label} / One-Shot Prompt Selection",
                _make_action(
                    "AI: One-Shot Prompt Selection",
                    lambda: self._handle_ai_action("One-Shot Prompt Selection", "", self._command_bar_selection_text()),
                ),
            )
        )

        for group in AI_ACTION_GROUPS:
            for action in group.actions:
                label = f"{base_label} / {group.title} / {action.title}"
                entries.append(
                    (
                        label,
                        _make_action(
                            label,
                            lambda checked=False, a=action: self._handle_ai_action(
                                a.title, a.prompt, self._command_bar_selection_text()
                            ),
                        ),
                    )
                )
        return entries

    def _collect_menu_actions(self) -> list[tuple[str, QAction]]:
        entries: list[tuple[str, QAction]] = []

        def walk(menu: QMenu, path: list[str]) -> None:
            for action in menu.actions():
                if action.isSeparator():
                    continue
                if action.menu():
                    label = self._menu_text(action.text())
                    walk(action.menu(), path + ([label] if label else []))
                    continue
                label = self._menu_text(action.text())
                if not label:
                    continue
                entries.append((" / ".join(path + [label]), action))

        menu_roots = [menu for menu in getattr(self, "_menu_roots", []) if menu is not None]
        if menu_roots:
            for menu in menu_roots:
                top_label = self._menu_text(menu.title())
                walk(menu, [top_label] if top_label else [])
        else:
            for top_action in self.menuBar().actions():
                if not top_action.menu():
                    continue
                top_label = self._menu_text(top_action.text())
                walk(top_action.menu(), [top_label] if top_label else [])
        if config.load_enable_ai_chats():
            entries.extend(self._build_ai_command_actions())
        return entries

    def _show_command_bar(self, *, query: str = "", ai_text_override: Optional[str] = None) -> None:
        if not hasattr(self, "_command_bar"):
            return
        if ai_text_override is not None:
            self._command_bar_ai_text_override = ai_text_override
        entries = self._collect_menu_actions()
        self._command_bar.open(entries, anchor=QCursor.pos(), query=query)

    def _run_command_bar_action(self, action: QAction) -> None:
        if action.isEnabled():
            action.trigger()

    def _quick_capture_shortcut_conflicts(self, sequence: QKeySequence) -> list[str]:
        if sequence.isEmpty():
            return []
        conflicts = []
        seen = set()
        app = QApplication.instance()
        widgets = app.topLevelWidgets() if app is not None else [self]
        for widget in widgets:
            for action in widget.findChildren(QAction):
                if action is getattr(self, "_action_quick_capture", None):
                    continue
                for seq in action.shortcuts():
                    if not seq.isEmpty() and seq == sequence:
                        label = action.text().replace("&", "").strip() or "Unnamed action"
                        key = f"action:{label}"
                        if key not in seen:
                            conflicts.append(label)
                            seen.add(key)
                        break
            for sc in widget.findChildren(QShortcut):
                if sc.key() == sequence:
                    label = sc.objectName() or "Shortcut"
                    key = f"shortcut:{label}"
                    if key not in seen:
                        conflicts.append(label)
                        seen.add(key)
        return conflicts

    def _is_ambiguous_alt_letter(self, sequence: QKeySequence) -> bool:
        text = sequence.toString(QKeySequence.NativeText).strip()
        return bool(re.match(r"^Alt\+[A-Za-z]$", text))

    def _setup_quick_capture_shortcut(self, *, show_error: bool) -> None:
        if not hasattr(self, "_action_quick_capture"):
            return
        hotkey = config.load_quick_capture_app_hotkey()
        sequence = QKeySequence(hotkey)
        if not hotkey.strip() or sequence.isEmpty():
            self._action_quick_capture.setShortcut(QKeySequence())
            return
        if self._is_ambiguous_alt_letter(sequence):
            self._action_quick_capture.setShortcut(QKeySequence())
            if show_error:
                QMessageBox.warning(
                    self,
                    "Quick Capture Hotkey",
                    "Alt+letter shortcuts are reserved for menu access and can be ambiguous.\n"
                    "Please choose a different shortcut.",
                )
            return
        conflicts = self._quick_capture_shortcut_conflicts(sequence)
        if conflicts:
            self._action_quick_capture.setShortcut(QKeySequence())
            if show_error:
                conflict_list = ", ".join(conflicts[:5])
                extra = f" (+{len(conflicts) - 5} more)" if len(conflicts) > 5 else ""
                QMessageBox.warning(
                    self,
                    "Quick Capture Hotkey",
                    "That shortcut conflicts with another command and was not applied.\n"
                    f"Conflicts: {conflict_list}{extra}",
                )
            return
        self._action_quick_capture.setShortcut(sequence)
        self._action_quick_capture.setShortcutContext(Qt.ApplicationShortcut)
        if self._action_quick_capture not in self.actions():
            self.addAction(self._action_quick_capture)

    def _build_format_menu(self) -> None:
        """Add a Format menu that mirrors markdown styling shortcuts."""
        format_menu = self.menuBar().addMenu("&Format")
        self._format_menu = format_menu
        for label, shortcut, handler, description in self.editor.style_operations():
            action = QAction(label, self.editor)
            action.setShortcut(shortcut)
            action.setShortcutContext(Qt.WidgetWithChildrenShortcut)
            action.setStatusTip(description)
            action.triggered.connect(lambda checked=False, func=handler: self._invoke_editor_style(func))
            format_menu.addAction(action)

    def _invoke_editor_style(self, formatter: Callable[[], None]) -> None:
        """Focus the editor before applying a format operation."""
        self.editor.setFocus(Qt.ShortcutFocusReason)
        formatter()

    def _selected_text_for_search(self) -> str:
        cursor = self.editor.textCursor()
        if cursor.hasSelection():
            return cursor.selectedText().replace("\u2029", "\n")
        return ""

    def _show_find_bar(self, *, replace: bool, backwards: bool = False, seed: Optional[str] = None) -> None:
        query = seed if seed is not None else self._selected_text_for_search()
        query = self._sanitize_find_query(query)
        if not query:
            query = self.editor.last_search_query()
        query = self._sanitize_find_query(query)
        self.find_bar.show_bar(replace=replace, query=query or "", backwards=backwards)

    def _on_editor_find_requested(self, replace_mode: bool, backwards: bool, seed_query: str) -> None:
        self._show_find_bar(replace=replace_mode, backwards=backwards, seed=seed_query)

    def _on_find_next_requested(self, query: str, backwards: bool, case_sensitive: bool) -> None:
        search_query = query.strip() or self.editor.last_search_query() or self._selected_text_for_search()
        search_query = self._sanitize_find_query(search_query)
        if not search_query:
            self.statusBar().showMessage("Enter text to find.", 2000)
            self.find_bar.focus_query()
            return
        self.find_bar.query_edit.setText(search_query)
        self.editor.search_find_next(search_query, backwards=backwards, wrap=True, case_sensitive=case_sensitive)

    def _on_replace_requested(self, replacement: str) -> None:
        self.editor.search_replace_current(replacement)

    def _on_replace_all_requested(self, query: str, replacement: str, case_sensitive: bool) -> None:
        search_query = query.strip() or self.editor.last_search_query()
        if not search_query:
            self.statusBar().showMessage("Enter text to find.", 2000)
            self.find_bar.focus_query()
            return
        self.editor.search_replace_all(search_query, replacement, case_sensitive=case_sensitive)

    def startup(self, vault_hint: Optional[str] = None, force_select: bool = False) -> bool:
        """Handle initial vault selection before the window is shown."""
        if force_select:
            return self._select_vault(startup=True)
        default_vault = vault_hint or config.load_default_vault()
        if default_vault:
            kind, server_url, path = self._decode_vault_ref(default_vault)
            if kind == "remote" and server_url and path:
                verify_ssl = self._remote_verify_ssl(server_url)
                self._switch_api_base(server_url, is_remote=True, verify_tls=verify_ssl)
                if self._set_vault(path):
                    QTimer.singleShot(100, self._auto_load_initial_file)
                    return True
                return self._select_vault(startup=True)
        if default_vault:
            if self._set_vault(default_vault):
                QTimer.singleShot(100, self._auto_load_initial_file)
                QTimer.singleShot(500, self._maybe_prompt_crash_report)
                return True
            # Fall through to prompt for another vault if lock/bind failed
        started = self._select_vault(startup=True)
        if started:
            QTimer.singleShot(500, self._maybe_prompt_crash_report)
        return started

    def _get_faulthandler_log_path(self) -> Optional[Path]:
        env_path = os.getenv("STILLPOINT_FAULTHANDLER_LOG")
        if env_path:
            return Path(env_path)
        try:
            return Path(tempfile.gettempdir()) / "stillpoint-faulthandler.log"
        except Exception:
            return None

    def _maybe_prompt_crash_report(self) -> None:
        log_path = self._get_faulthandler_log_path()
        if not log_path or not log_path.exists():
            return
        try:
            stat = log_path.stat()
        except Exception:
            return
        if stat.st_size <= 0:
            return
        state_path = Path.home() / ".stillpoint" / "last-crash.json"
        last_mtime = None
        last_size = None
        try:
            if state_path.exists():
                data = json.loads(state_path.read_text(encoding="utf-8"))
                last_mtime = data.get("mtime")
                last_size = data.get("size")
        except Exception:
            pass
        if last_mtime == stat.st_mtime and last_size == stat.st_size:
            return
        try:
            content = log_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            content = ""
        if not content.strip():
            return
        tail = content[-4000:]
        exception_line = ""
        for line in reversed(tail.splitlines()):
            if "Fatal Python error" in line or "Segmentation fault" in line or "SIGSEGV" in line:
                exception_line = line.strip()
                break
        exception = exception_line or "Fatal crash detected (faulthandler log)"
        message = "StillPoint detected a previous crash. You can report it now."
        reported = self._alert_issue_report(message, exception, tail.strip())
        if reported:
            try:
                log_path.write_text("", encoding="utf-8")
            except Exception:
                pass
        try:
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps({"mtime": stat.st_mtime, "size": stat.st_size}),
                encoding="utf-8",
            )
        except Exception:
            pass

    # --- Vault actions -------------------------------------------------
    def _fetch_remote_vaults_with_status(self) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """Load configured remote vaults with individual connectivity status."""
        results: list[dict[str, str]] = []
        status: list[dict[str, str]] = []
        debug = log_enabled("remote_vaults")
        start_total = time.perf_counter()
        servers = config.load_remote_servers()
        if not servers:
            status.append({"kind": "remote_status", "level": "info", "message": "No remote servers configured."})
            return results, status
        
        for entry in servers:
            host = entry.get("host")
            port_raw = entry.get("port")
            if not host or not port_raw:
                continue
            # Ensure port is an integer for consistency
            try:
                port = int(port_raw)
            except (ValueError, TypeError):
                continue
            scheme = entry.get("scheme") or "http"
            
            base_url = f"{scheme}://{host}:{port}"
            verify_ssl = entry.get("verify_ssl", True)
            selected_vaults = entry.get("selected_vaults") or []
            try:
                connect_timeout = float(entry.get("connect_timeout_s", config.load_remote_connect_timeout(3.0)))
            except (TypeError, ValueError):
                connect_timeout = config.load_remote_connect_timeout(3.0)
            try:
                read_timeout = float(entry.get("read_timeout_s", config.load_remote_read_timeout(10.0)))
            except (TypeError, ValueError):
                read_timeout = config.load_remote_read_timeout(10.0)
            timeout = self._http_timeout(connect_timeout, read_timeout)
            
            # Skip servers with no configured vaults
            if not selected_vaults:
                continue
            
            # Get server admin password hash - check session cache first, then saved config
            server_key = f"{scheme}://{host}:{port}"
            server_password_hash = self._session_server_passwords.get(server_key)
            if debug:
                print(f"[RemoteVaults] Server {base_url}:")
                print(f"[RemoteVaults]   Session cache: {'<hash>' if server_password_hash else 'None'}")
            if not server_password_hash:
                server_password_hash = config.get_server_password_hash(host, port, scheme)
                if debug:
                    print(f"[RemoteVaults]   Config lookup: {'<hash>' if server_password_hash else 'None'}")
            
            # If this is the local embedded server, use the embedded password
            if not server_password_hash and base_url == self._local_api_base and self._embedded_server_admin_password:
                import hashlib
                server_password_hash = hashlib.sha256(self._embedded_server_admin_password.encode()).hexdigest()
            
            start_server = time.perf_counter()
            try:
                headers: dict[str, str] = {}
                if server_password_hash:
                    headers["X-Server-Admin-Password"] = server_password_hash
                resp = httpx.get(f"{base_url}/api/vaults", headers=headers, timeout=timeout, verify=verify_ssl)

                if resp.status_code == 401:
                    server_key = self._server_key_for_url(base_url)
                    auth_entry = config.load_remote_auth(server_key)
                    refresh_token = auth_entry.get("refresh_token")
                    if refresh_token:
                        refresh_resp = httpx.post(
                            f"{base_url}/auth/refresh",
                            headers={"Authorization": f"Bearer {refresh_token}"},
                            timeout=timeout,
                            verify=verify_ssl,
                        )
                        if refresh_resp.status_code == 200:
                            payload = refresh_resp.json()
                            access_token = payload.get("access_token")
                            new_refresh = payload.get("refresh_token") or refresh_token
                            if access_token:
                                config.save_remote_auth(
                                    server_key,
                                    new_refresh,
                                    username=auth_entry.get("username"),
                                )
                                headers = {"Authorization": f"Bearer {access_token}"}
                                if server_password_hash:
                                    headers["X-Server-Admin-Password"] = server_password_hash
                                resp = httpx.get(
                                    f"{base_url}/api/vaults",
                                    headers=headers,
                                    timeout=timeout,
                                    verify=verify_ssl,
                                )

                latency_ms = (time.perf_counter() - start_server) * 1000.0
                payload = resp.json() if resp.status_code == 200 else {}
                remote_vaults = payload.get("vaults", []) if isinstance(payload, dict) else []
                vault_map: dict[str, dict] = {}
                if isinstance(remote_vaults, list):
                    for item in remote_vaults:
                        if not isinstance(item, dict):
                            continue
                        item_path = str(item.get("path") or "").strip()
                        if item_path:
                            vault_map[item_path] = item

                for vault_path in selected_vaults:
                    vault_status = "ok"
                    error_message = None
                    vault_name = Path(vault_path).name
                    if resp.status_code == 401:
                        vault_status = "error"
                        error_message = "Authentication required"
                    elif resp.status_code == 403:
                        vault_status = "error"
                        error_message = f"HTTP 403 - Server password: {'yes' if server_password_hash else 'NO'}"
                    elif resp.status_code != 200:
                        vault_status = "error"
                        error_message = f"HTTP {resp.status_code}"
                    else:
                        vault_data = vault_map.get(vault_path)
                        if vault_data is None:
                            vault_status = "error"
                            error_message = "Vault not found on server"
                        else:
                            vault_name = str(vault_data.get("name") or vault_name)
                            if latency_ms >= 1500:
                                vault_status = "slow"
                    results.append(
                        {
                            "kind": "remote",
                            "name": vault_name,
                            "path": vault_path,
                            "server_url": base_url,
                            "verify_ssl": verify_ssl,
                            "id": f"remote::{base_url}::{vault_path}",
                            "status": vault_status,
                            "latency_ms": latency_ms,
                            **({"error": error_message} if error_message else {}),
                        }
                    )
                if resp.status_code >= 500:
                    self._set_remote_health_state(
                        "degraded",
                        f"{self._format_remote_host(base_url)} HTTP {resp.status_code}",
                        latency_ms=latency_ms,
                    )
                else:
                    self._record_remote_latency(latency_ms, context=f"{self._format_remote_host(base_url)} vault list")
                if debug:
                    print(
                        f"[RemoteVaults] {base_url} status={resp.status_code} vaults={len(selected_vaults)} "
                        f"dt={latency_ms:.1f}ms"
                    )
            except Exception as exc:
                latency_ms = (time.perf_counter() - start_server) * 1000.0
                self._set_remote_health_state(
                    "degraded",
                    f"{self._format_remote_host(base_url)} unreachable: {exc}",
                    latency_ms=latency_ms,
                )
                for vault_path in selected_vaults:
                    results.append(
                        {
                            "kind": "remote",
                            "name": Path(vault_path).name,
                            "path": vault_path,
                            "server_url": base_url,
                            "verify_ssl": verify_ssl,
                            "id": f"remote::{base_url}::{vault_path}",
                            "status": "error",
                            "error": str(exc),
                            "latency_ms": latency_ms,
                        }
                    )
                if debug:
                    print(
                        f"[RemoteVaults] {base_url} error={exc} vaults={len(selected_vaults)} "
                        f"dt={latency_ms:.1f}ms"
                    )
        
        if debug:
            print(
                f"[RemoteVaults] total servers={len(servers)} total_vaults={len(results)} "
                f"dt={(time.perf_counter()-start_total)*1000:.1f}ms"
            )
        return results, status

    def _fetch_remote_vaults(self) -> list[dict[str, str]]:
        """Load available vaults from configured remote servers."""
        vaults, _status = self._fetch_remote_vaults_with_status()
        return vaults

    def _build_local_vault_entries(self, seed_vault: Optional[str]) -> list[dict[str, str]]:
        local_vaults = config.load_known_vaults()
        if seed_vault and not any(v.get("path") == seed_vault for v in local_vaults):
            local_vaults.append({"name": Path(seed_vault).name, "path": seed_vault})
        for vault in local_vaults:
            vault.setdefault("kind", "local")
            vault["id"] = vault.get("path")
        return local_vaults

    def _decode_vault_ref(self, value: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """Return (kind, server_url, path) for a saved vault reference."""
        if not value:
            return None, None, None
        if value.startswith("remote::"):
            parts = value.split("::", 2)
            if len(parts) == 3:
                _, server_url, path = parts
                return "remote", server_url, path
        return "local", None, value

    def _encode_remote_ref(self, server_url: str, path: str) -> str:
        return f"remote::{server_url}::{path}"

    def _remote_server_config_for_url(self, server_url: str) -> Optional[dict]:
        server_key = self._server_key_for_url(server_url)
        for entry in config.load_remote_servers():
            host = entry.get("host")
            port = entry.get("port")
            scheme = entry.get("scheme") or "http"
            if not host or not port:
                continue
            candidate = f"{scheme}://{host}:{port}"
            if self._server_key_for_url(candidate) == server_key:
                return entry
        return None

    def _remote_verify_ssl(self, server_url: str) -> bool:
        entry = self._remote_server_config_for_url(server_url)
        if entry is not None:
            return bool(entry.get("verify_ssl", True))
        return True

    def _remote_timeout_settings_for_url(self, server_url: str) -> tuple[float, float]:
        entry = self._remote_server_config_for_url(server_url)
        default_connect = config.load_remote_connect_timeout(3.0)
        default_read = config.load_remote_read_timeout(10.0)
        if entry is None:
            return default_connect, default_read
        try:
            connect_timeout = float(entry.get("connect_timeout_s", default_connect))
        except (TypeError, ValueError):
            connect_timeout = default_connect
        try:
            read_timeout = float(entry.get("read_timeout_s", default_read))
        except (TypeError, ValueError):
            read_timeout = default_read
        if connect_timeout <= 0:
            connect_timeout = default_connect
        if read_timeout <= 0:
            read_timeout = default_read
        return connect_timeout, read_timeout

    @staticmethod
    def _http_timeout(connect_timeout: float, read_timeout: float) -> httpx.Timeout:
        return httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=read_timeout,
            pool=connect_timeout,
        )

    def _add_remote_server(self) -> Optional[list[dict[str, str]]]:
        """Prompt for a remote server and verify it before adding."""
        dlg = AddRemoteDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return None
        (
            host,
            port,
            use_https,
            no_verify,
            server_password,
            remember_server_password,
            connect_timeout_s,
            read_timeout_s,
        ) = dlg.values()
        scheme = "https" if use_https else "http"
        verify_ssl = not no_verify
        base_url = f"{scheme}://{host}:{port}"
        timeout = self._http_timeout(connect_timeout_s, read_timeout_s)
        
        # Hash server password if provided
        server_password_hash = None
        if server_password:
            import hashlib
            server_password_hash = hashlib.sha256(server_password.encode()).hexdigest()
        
        # If this is the local embedded server, use the embedded password
        if not server_password_hash and base_url == self._local_api_base and self._embedded_server_admin_password:
            import hashlib
            server_password_hash = hashlib.sha256(self._embedded_server_admin_password.encode()).hexdigest()
        
        try:
            # Prepare headers with server admin password for consistency
            # (health endpoint doesn't require it, but including it doesn't hurt)
            headers = {}
            if server_password_hash:
                headers["X-Server-Admin-Password"] = server_password_hash
            health_start = time.perf_counter()
            resp = httpx.get(f"{base_url}/api/health", headers=headers, timeout=timeout, verify=verify_ssl)
            self._record_remote_latency((time.perf_counter() - health_start) * 1000.0, context=f"{self._format_remote_host(base_url)} health")
            if resp.status_code != 200:
                raise RuntimeError(f"Health check failed (HTTP {resp.status_code})")
        except Exception as exc:
            self._set_remote_health_state("degraded", f"{self._format_remote_host(base_url)} health failed: {exc}")
            self._alert(f"Could not verify server {base_url}: {exc}")
            return None
        access_token = None
        selected_path = None
        try:
            # Prepare headers with server admin password if provided
            headers = {}
            if server_password_hash:
                headers["X-Server-Admin-Password"] = server_password_hash
            vaults_start = time.perf_counter()
            resp = httpx.get(f"{base_url}/api/vaults", headers=headers, timeout=timeout, verify=verify_ssl)
            self._record_remote_latency((time.perf_counter() - vaults_start) * 1000.0, context=f"{self._format_remote_host(base_url)} vault list")
            if resp.status_code == 403:
                self._alert("Server admin password is invalid or missing.")
                return None
            if resp.status_code == 401:
                if not self._prompt_remote_login_for_server(base_url, verify_ssl):
                    return None
                access_token = self._access_token
                auth_headers = {"Authorization": f"Bearer {access_token}"}
                if server_password_hash:
                    auth_headers["X-Server-Admin-Password"] = server_password_hash
                resp = httpx.get(f"{base_url}/api/vaults", headers=auth_headers, timeout=timeout, verify=verify_ssl)
            if resp.status_code != 200:
                raise RuntimeError(f"Failed to list vaults (HTTP {resp.status_code})")
            payload = resp.json()
            vaults = payload.get("vaults", [])
            if not isinstance(vaults, list):
                raise RuntimeError("Invalid vault list response")
            def _prompt_create_remote_vault() -> Optional[str]:
                nonlocal access_token
                name = None
                while not name:
                    name, ok = QInputDialog.getText(
                        self,
                        "Create Remote Vault",
                        "Enter a new vault name:",
                    )
                    if not ok:
                        return None
                    name = name.strip()
                    if not name:
                        QMessageBox.warning(self, "Missing Name", "Please enter a vault name.")
                username = None
                while not username:
                    username, ok = QInputDialog.getText(
                        self,
                        "Vault Login",
                        "Set a username for this vault:",
                    )
                    if not ok:
                        return None
                    username = username.strip()
                    if not username:
                        QMessageBox.warning(self, "Missing Username", "Please enter a username.")
                password = None
                while not password:
                    password, ok = QInputDialog.getText(
                        self,
                        "Vault Login",
                        "Set a password for this vault:",
                        QLineEdit.Password,
                    )
                    if not ok:
                        return None
                    password = password.strip()
                    if not password:
                        QMessageBox.warning(self, "Missing Password", "Please enter a password.")
                headers = {}
                if access_token:
                    headers["Authorization"] = f"Bearer {access_token}"
                if server_password_hash:
                    headers["X-Server-Admin-Password"] = server_password_hash
                create_resp = httpx.post(
                    f"{base_url}/api/vaults/create",
                    json={"name": name, "auth_username": username, "auth_password": password},
                    headers=headers,
                    timeout=timeout,
                    verify=verify_ssl,
                )
                if create_resp.status_code == 403:
                    raise RuntimeError("Server admin password required for vault creation")
                if create_resp.status_code == 401:
                    if not self._prompt_remote_login_for_server(base_url, verify_ssl):
                        return None
                    access_token = self._access_token
                    headers = {"Authorization": f"Bearer {access_token}"}
                    if server_password_hash:
                        headers["X-Server-Admin-Password"] = server_password_hash
                        create_resp = httpx.post(
                            f"{base_url}/api/vaults/create",
                            json={"name": name, "auth_username": username, "auth_password": password},
                            headers=headers,
                            timeout=timeout,
                            verify=verify_ssl,
                        )
                if create_resp.status_code != 200:
                    raise RuntimeError(f"Failed to create vault (HTTP {create_resp.status_code})")
                created = create_resp.json()
                selected = created.get("path")
                if not selected:
                    raise RuntimeError("Failed to create vault (missing path)")
                return selected

            if not vaults:
                selected_path = _prompt_create_remote_vault()
        except Exception as exc:
            self._set_remote_health_state("degraded", f"{self._format_remote_host(base_url)} vault load failed: {exc}")
            self._alert(f"Could not load vaults from {base_url}: {exc}")
            return None

        if not selected_path and vaults:
            select_dialog = RemoteVaultSelectDialog(vaults, parent=self)
            if select_dialog.exec() != QDialog.Accepted:
                return None
            if select_dialog.create_new():
                try:
                    selected_path = _prompt_create_remote_vault()
                except Exception as exc:
                    self._alert(f"Could not create vault on {base_url}: {exc}")
                    return None
            else:
                selected_path = select_dialog.selected_path()
            if not selected_path:
                return None
        if not selected_path:
            return None

        existing = None
        for entry in config.load_remote_servers():
            if (
                entry.get("host") == host
                and str(entry.get("port")) == str(port)
                and entry.get("scheme") == scheme
            ):
                existing = entry
                break
        selected_vaults = list(existing.get("selected_vaults", [])) if existing else []
        if selected_path not in selected_vaults:
            selected_vaults.append(selected_path)
        
        # Cache the password in the session so it's available even if "remember" is unchecked
        if server_password_hash:
            server_key = f"{scheme}://{host}:{port}"
            self._session_server_passwords[server_key] = server_password_hash
        
        # Save server with password hash if remember was checked
        saved_password_hash = server_password_hash if remember_server_password else None
        debug = log_enabled("remote_vaults")
        if debug:
            print(f"[AddRemote] Saving server: host={host} port={port} scheme={scheme}")
            print(f"[AddRemote]   remember_password={remember_server_password}")
            print(f"[AddRemote]   saved_password_hash={'<hash>' if saved_password_hash else 'None'}")
        config.add_remote_server(
            host,
            port,
            scheme=scheme,
            verify_ssl=verify_ssl,
            selected_vaults=selected_vaults,
            server_password_hash=saved_password_hash,
            connect_timeout_s=connect_timeout_s,
            read_timeout_s=read_timeout_s,
        )
        if debug:
            # Verify it was saved
            retrieved = config.get_server_password_hash(host, port, scheme)
            print(f"[AddRemote]   Verification: retrieved password={'<hash>' if retrieved else 'None'}")
        vaults = self._build_local_vault_entries(self.vault_root if not self._remote_mode else None)
        # Add the newly added/created remote vault to the list
        # Note: We already fetched the vault list earlier, so we don't need to fetch again
        # This avoids a 403 error if the password wasn't saved (remember_password unchecked)
        vault_entry = {
            "kind": "remote",
            "name": Path(selected_path).name if selected_path else "Unknown",
            "path": selected_path,
            "server_url": base_url,
            "verify_ssl": verify_ssl,
        }
        vaults.append(vault_entry)
        # Also add any other vaults from this server that were previously configured
        if existing and existing.get("selected_vaults"):
            for vault_path in existing.get("selected_vaults", []):
                if vault_path != selected_path:
                    vaults.append({
                        "kind": "remote",
                        "name": Path(vault_path).name,
                        "path": vault_path,
                        "server_url": base_url,
                        "verify_ssl": verify_ssl,
                    })
        return vaults

    def _select_vault(self, checked: bool | None = None, startup: bool = False, spawn_new_process: bool = False) -> bool:  # noqa: ARG002
        seed_vault = self.vault_root or config.load_last_vault()
        if self._remote_mode and self._server_url and (self._remote_vault_ref_path or self.vault_root):
            seed_vault = self._encode_remote_ref(self._server_url, self._remote_vault_ref_path or self.vault_root)
        kind, server_url, path = self._decode_vault_ref(seed_vault)
        seed_path = path if kind == "local" else None
        select_id = seed_path
        vaults = self._build_local_vault_entries(seed_path)
        dialog = OpenVaultDialog(
            self,
            current_vault=seed_path,
            vaults=vaults,
            select_id=select_id,
            on_add_remote=self._add_remote_server,
            on_load_remote=self._fetch_remote_vaults_with_status,
        )
        if dialog.exec() != QDialog.Accepted:
            return False
        selection = dialog.selected_vault()
        if not selection:
            return False
        if spawn_new_process or dialog.selected_vault_new_window():
            if selection.get("kind") == "remote":
                server_url = selection.get("server_url")
                path = selection.get("path")
                if server_url and path:
                    self._launch_remote_vault_process(server_url, path)
                    self.statusBar().showMessage("Opened remote vault in a new window.", 4000)
                else:
                    self._launch_new_window(select_vault=True)
                    self.statusBar().showMessage("Opened new window. Select the remote vault there.", 4000)
            else:
                self._launch_vault_process(selection["path"])
            return True
        if selection.get("kind") == "remote":
            server_url = selection.get("server_url")
            verify_ssl = selection.get("verify_ssl", True)
            if server_url:
                self._switch_api_base(server_url, is_remote=True, verify_tls=verify_ssl)
        else:
            self._switch_api_base(self._local_api_base, is_remote=False, verify_tls=True)
        if self._set_vault(selection["path"], vault_name=selection.get("name")):
            if selection.get("kind") == "homebase":
                try:
                    config.delete_known_vault(selection["path"])
                except Exception:
                    pass
                self._apply_homebase_profile(selection)
            self._update_user_management_ui()
            self._restore_recent_history()
            QTimer.singleShot(100, self._auto_load_initial_file)
            return True
        return False

    @staticmethod
    def _normalize_vault_path(path_value: Optional[str]) -> str:
        if not path_value:
            return ""
        try:
            return str(Path(path_value).expanduser().resolve())
        except Exception:
            return os.path.abspath(os.path.expanduser(str(path_value)))

    def _homebase_profile_for_path(self, local_path: Optional[str]) -> Optional[dict]:
        target = self._normalize_vault_path(local_path)
        if not target:
            return None
        try:
            for profile in config.load_homebase_vault_profiles():
                if self._normalize_vault_path(str(profile.get("path") or "")) == target:
                    return profile
        except Exception:
            return None
        return None

    def _apply_homebase_profile(self, profile: dict) -> None:
        try:
            self._ensure_config_active_vault_context()
            config.save_vault_remote_mode("homebase_remote")
            config.save_homebase_remote_url(str(profile.get("server_url") or "").strip())
            config.save_homebase_verify_ssl(bool(profile.get("verify_ssl", True)))
            config.save_homebase_vault_id(str(profile.get("vault_id") or "").strip())
            config.save_homebase_username(str(profile.get("username") or "").strip())
            config.save_homebase_auth_token(str(profile.get("access_token") or "").strip())
            config.save_homebase_refresh_token(str(profile.get("refresh_token") or "").strip())
            config.save_homebase_passphrase(str(profile.get("passphrase") or ""))
            config.save_homebase_auto_sync(bool(profile.get("auto_sync", True)))
            config.save_homebase_interval_seconds(int(profile.get("interval_seconds", 60)))
            config.save_homebase_push_debounce_seconds(int(profile.get("push_debounce_seconds", 3)))
            config.save_homebase_max_parallel_transfers(int(profile.get("max_parallel_transfers", 6)))
            self._homebase_user_info_loaded = False
            self._configure_homebase_sync_for_vault()
            self._apply_remote_mode_ui()
        except Exception as exc:
            self.statusBar().showMessage(f"Homebase profile apply failed: {exc}", 5000)

    def _open_vault_preferences(self) -> None:
        if not config.has_active_vault():
            self._alert("Open a vault first.")
            return
        self._ensure_config_active_vault_context()
        dialog = VaultPreferencesDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self._ensure_config_active_vault_context()
            self._apply_feature_overrides()
            self._apply_vault_read_only_pref()
            self._configure_homebase_sync_for_vault()

    def _reload_vault(self) -> None:
        if not self.vault_root:
            self._alert("Open a vault first.")
            return
        if self._remote_mode:
            server_url = self.api_base
            if server_url:
                self._launch_remote_vault_process(server_url, self._remote_vault_ref_path or self.vault_root)
        else:
            self._launch_vault_process(self.vault_root)
        self._close_vault_window()

    def _close_vault_window(self) -> None:
        for window in list(getattr(self, "_detached_panels", [])):
            try:
                window.close()
            except Exception:
                pass
        if getattr(self, "_detached_ai_chat_window", None):
            try:
                self._detached_ai_chat_window.close()
            except Exception:
                pass
        self.close()
        QTimer.singleShot(0, self._quit_if_last_window)

    def _quit_if_last_window(self) -> None:
        app = QApplication.instance()
        if not app:
            return
        for widget in app.topLevelWidgets():
            if widget.isVisible():
                return
        app.quit()

    def _launch_new_window(self, select_vault: bool = False) -> None:
        """Spawn a fresh StillPoint process so it gets its own API server and vault."""
        try:
            cmd = self._build_launch_command()
            if select_vault:
                cmd.append("--select-vault")
            if self.vault_root and not select_vault:
                cmd.extend(["--vault", self.vault_root])
            # Ask the new process to pick an ephemeral port to avoid clashes
            cmd.extend(["--port", "0"])
            subprocess.Popen(cmd, start_new_session=True)
            self.statusBar().showMessage("Launching new window...", 2000)
        except Exception as exc:  # pragma: no cover - UI path
            self._alert(f"Failed to launch new window: {exc}")

    def _launch_remote_vault_process(self, server_url: str, path: str) -> None:
        """Launch a new StillPoint process and open the given remote vault."""
        try:
            cmd = self._build_launch_command()
            cmd.extend(["--vault-ref", self._encode_remote_ref(server_url, path)])
            cmd.extend(["--port", "0"])
            subprocess.Popen(cmd, start_new_session=True)
            self.statusBar().showMessage("Opening remote vault in a new window...", 3000)
        except Exception as exc:
            self._alert(f"Failed to open remote vault in new window: {exc}")

    def _launch_vault_process(self, vault_path: str) -> None:
        """Launch a new StillPoint process targeting the given vault."""
        try:
            cmd = self._build_launch_command()
            cmd.extend(["--vault", vault_path])
            cmd.extend(["--port", "0"])
            subprocess.Popen(cmd, start_new_session=True)
            self.statusBar().showMessage(f"Opening {vault_path} in a new window...", 3000)
        except Exception as exc:
            self._alert(f"Failed to open vault in new window: {exc}")

    @staticmethod
    def _build_launch_command() -> list[str]:
        """Return the command to start a new StillPoint instance using the current runtime."""
        if getattr(sys, "frozen", False):
            # Packaged app: the executable already bootstraps StillPoint
            cmd = [sys.executable]
        else:
            # Dev/venv: use the same interpreter to launch the module
            cmd = [sys.executable, "-m", "sp.app.main"]
        return cmd

    def _find_help_vault_template(self) -> Optional[Path]:
        """Return the bundled help-vault directory if present."""
        candidates: list[Path] = []
        base = getattr(sys, "_MEIPASS", None)
        if base:
            candidates.append(Path(base) / "sp" / "help-vault")
            candidates.append(Path(base) / "_internal" / "sp" / "help-vault")
            candidates.append(Path(base) / "help-vault")
            candidates.append(Path(base) / "_internal" / "help-vault")
        try:
            exe_dir = Path(os.path.abspath(os.path.dirname(sys.argv[0])))
            candidates.append(exe_dir / "sp" / "help-vault")
            candidates.append(exe_dir / "_internal" / "sp" / "help-vault")
            candidates.append(exe_dir / "help-vault")
            candidates.append(exe_dir / "_internal" / "help-vault")
        except Exception:
            pass
        # Walk up from this file to locate the repo root (contains sp/help-vault)
        here = Path(__file__).resolve()
        for parent in here.parents:
            if (parent / "sp" / "help-vault").exists():
                candidates.append(parent / "sp" / "help-vault")
                break

        def _is_help_vault_root(root: Path) -> bool:
            return (
                (root / "help-vault.md").exists()
                or (root / "help-vault.txt").exists()
                or (root / "Welcome" / "Welcome.md").exists()
                or (root / "help-vault" / "help-vault.md").exists()
            )

        for cand in candidates:
            try:
                root = cand
                if root.name == "help-vault":
                    parent = root.parent
                    if (parent / "Welcome" / "Welcome.md").exists():
                        root = parent
                if _is_help_vault_root(root):
                    return root
            except Exception:
                continue
        return None

    def _ensure_user_help_vault(self) -> Path:
        """Ensure user help vault exists under ~/.stillpoint/help-vault."""
        src = self._find_help_vault_template()
        if src is None:
            raise RuntimeError("Bundled help vault is missing.")

        user_root = Path.home() / ".stillpoint" / "help-vault"
        user_root.parent.mkdir(parents=True, exist_ok=True)

        # Use existing user copy exactly as-is.
        if user_root.exists():
            return user_root

        # First-time seed: raw copy the bundled help vault, including its settings.
        shutil.copytree(src, user_root)
        return user_root

    def _open_help_documentation(self) -> None:
        """Open the built-in help vault in a new StillPoint window."""
        if not self._require_local_mode("Open help documentation"):
            return
        try:
            vault_path = self._ensure_user_help_vault()
            self._launch_vault_process(str(vault_path))
        except Exception as exc:  # pragma: no cover - UI path
            self._alert(f"Failed to open documentation: {exc}")

    def _set_help_vault_last_file(self, vault_root: Path, rel_path: str) -> bool:
        db_path = vault_root / ".stillpoint" / "settings.db"
        try:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS kv(key TEXT PRIMARY KEY, value TEXT)")
                conn.execute(
                    "REPLACE INTO kv(key, value) VALUES(?, ?)",
                    ("last_file", rel_path),
                )
                conn.commit()
            return True
        except Exception:
            return False

    def _open_help_keyboard_shortcuts(self) -> None:
        """Open the Keyboard Shortcuts page in the help vault."""
        if not self._require_local_mode("Open keyboard shortcuts"):
            return
        try:
            vault_path = self._ensure_user_help_vault()
            rel_path = "/Shortcuts/Shortcuts.md"
            if self.vault_root and Path(self.vault_root).resolve() == vault_path.resolve():
                self._open_file(rel_path)
                return
            self._set_help_vault_last_file(vault_path, rel_path)
            self._launch_vault_process(str(vault_path))
        except Exception as exc:  # pragma: no cover - UI path
            self._alert(f"Failed to open keyboard shortcuts: {exc}")

    def _create_vault(self) -> None:
        if not self._require_local_mode("Create a new vault"):
            return
        target_path = QFileDialog.getExistingDirectory(self, "Select Folder for Vault", str(Path.home()))
        if not target_path:
            return
        target = Path(target_path)
        try:
            # Check if folder is empty or ask for confirmation
            if target.exists():
                existing_items = list(target.iterdir())
                if existing_items:
                    reply = QMessageBox.question(
                        self,
                        "Use Existing Folder",
                        f"{target.name} is not empty. Create vault here anyway?",
                    )
                    if reply != QMessageBox.StandardButton.Yes:
                        return
            else:
                target.mkdir(parents=True)
            
            self._seed_vault(target)
        except OSError as exc:
            self._alert(f"Failed to create vault: {exc}")
            return
        self._set_vault(str(target), vault_name=target.name)

    def _seed_vault(self, root: Path) -> None:
        root_dir = root / root.name
        root_dir.mkdir(parents=True, exist_ok=True)
        root_page = root_dir / f"{root.name}{PAGE_SUFFIX}"
        if not root_page.exists():
            root_page.write_text(
                f"# {root.name}\n\nWelcome to your vault. Use the tree to add new pages.\n",
                encoding="utf-8",
            )

    def _ensure_vault_root_page(self) -> bool:
        """
        Ensure the vault root page is only created in the subfolder: /VaultRoot/VaultRoot/VaultRoot.md
        Never create /VaultRoot/VaultRoot.md (legacy).
        """
        if not self.vault_root or not self.vault_root_name:
            return False
        if self._remote_mode or self._read_only:
            return False
        root = Path(self.vault_root)
        new_root = root / self.vault_root_name / f"{self.vault_root_name}{PAGE_SUFFIX}"
        if new_root.exists():
            return False
        try:
            new_root.parent.mkdir(parents=True, exist_ok=True)
            new_root.write_text(
                f"# {self.vault_root_name}\n\nWelcome to your vault. Use the tree to add new pages.\n",
                encoding="utf-8",
            )
            return True
        except OSError:
            return False

    def _is_pid_active(self, pid: int, host: str) -> bool:
        """Best-effort check if a PID is alive on this host."""
        try:
            local_host = socket.gethostname()
        except Exception:
            local_host = ""
        if host and local_host and host.lower() != local_host.lower():
            return False
        try:
            os.kill(pid, 0)  # Does not terminate; raises if not permitted or missing
            return True
        except OSError as exc:
            # EPERM/EACCES mean the process likely exists but we lack permission (common on Windows)
            if exc.errno in (errno.EPERM, errno.EACCES):
                return True
            return False

    def _ensure_writable(self, action: str, *, interactive: bool = True) -> bool:
        """Guard write operations when the vault is opened read-only."""
        if self._read_only:
            if not interactive:
                if self._user_read_only and self._remote_mode:
                    self._alert("You do not have permisison to write to this vault.")
                elif self._user_read_only and self._is_homebase_mode_enabled():
                    self._alert("You do not have permission to write to this vault.")
                else:
                    self._alert(f"Vault is read-only because another StillPoint window holds the lock.\nCannot {action}.")
            return False
        return True

    def _read_only_status_message(self, fallback: str) -> str:
        if self._user_read_only and self._remote_mode:
            return "You do not have permisison to write to this vault."
        if self._user_read_only and self._is_homebase_mode_enabled():
            return "You do not have permission to write to this vault."
        return fallback

    def _apply_remote_user_permissions(self, *, can_write: bool, is_admin: bool) -> None:
        self._remote_user_can_write = bool(can_write)
        self._remote_user_is_admin = bool(is_admin)
        if self._remote_mode:
            self._user_read_only = not self._remote_user_can_write
            self._read_only = self._user_read_only
            self._apply_read_only_state()
        self._update_user_management_ui()

    def _apply_homebase_user_permissions(self, *, can_write: bool, is_admin: bool) -> None:
        self._homebase_user_can_write = bool(can_write)
        self._homebase_user_is_admin = bool(is_admin)
        if self._is_homebase_mode_enabled():
            self._user_read_only = not self._homebase_user_can_write
            self._read_only = self._user_read_only
            self._apply_read_only_state()
        self._update_user_management_ui()

    def _require_local_mode(self, action: str) -> bool:
        """Block local-only features when connected to a remote server."""
        if not self._remote_mode:
            return True
        self._alert(f"{action} is not available when connected to a remote server.")
        return False

    def _disable_remote_action(self, action: QAction, label: str) -> None:
        """Disable UI actions that are local-only in remote mode."""
        if not self._remote_mode:
            return
        action.setEnabled(False)
        action.setToolTip(f"{label} is not available when connected to a remote server.")

    def _apply_remote_mode_ui(self) -> None:
        """Toggle UI actions based on whether we're connected to a remote server."""
        guarded = [
            (self._action_new_vault, "Create a new vault"),
            (self._action_view_vault_disk, "View vault on disk"),
            (self._action_zim_import, "Import from Zim"),
            (self._action_webserver, "Start web server"),
        ]
        for action, label in guarded:
            if self._remote_mode:
                action.setEnabled(False)
                action.setToolTip(f"{label} is not available when connected to a remote server.")
            else:
                action.setEnabled(True)
                action.setToolTip(self._action_tooltips.get(action, label))
        
        # Reindex actions are now supported for both local and remote vaults
        self._action_rebuild_index.setEnabled(True)
        self._action_rebuild_index.setToolTip(self._action_tooltips.get(self._action_rebuild_index, "Rebuild vault index"))
        self._action_rebuild_search_index.setEnabled(True)
        self._action_rebuild_search_index.setToolTip(self._action_tooltips.get(self._action_rebuild_search_index, "Rebuild vault search index"))
        if self._remote_mode or self._is_homebase_mode_enabled():
            self._action_server_login.setVisible(True)
            self._action_server_logout.setVisible(True)
            self._action_server_login.setEnabled(True)
            self._action_server_logout.setEnabled(True)
            if self._remote_mode:
                self._action_server_login.setToolTip(self._action_tooltips.get(self._action_server_login, ""))
                self._action_server_logout.setToolTip(self._action_tooltips.get(self._action_server_logout, ""))
            else:
                self._action_server_login.setToolTip("Authenticate to the Homebase vault")
                self._action_server_logout.setToolTip("Clear stored Homebase credentials")
        else:
            self._action_server_login.setVisible(False)
            self._action_server_logout.setVisible(False)
            self._action_server_login.setEnabled(False)
            self._action_server_logout.setEnabled(False)
        self._update_user_management_ui()
        if hasattr(self, "_action_homebase_sync_now"):
            self._action_homebase_sync_now.setVisible(self._is_homebase_mode_enabled())
        if hasattr(self, "_action_homebase_reset_sync"):
            self._action_homebase_reset_sync.setVisible(self._is_homebase_mode_enabled())
        self._update_periodic_search_sync_timer()

    def _update_periodic_search_sync_timer(self) -> None:
        """Enable/disable periodic local search index sync based on mode and preferences."""
        self._search_sync.update_timer()

    def _update_user_management_ui(self) -> None:
        if not hasattr(self, "_action_manage_users"):
            return
        is_remote = bool(self._remote_mode)
        is_homebase = bool(self._is_homebase_mode_enabled())
        if is_homebase and not self._homebase_user_info_loaded:
            auth_token = str(config.load_homebase_auth_token() or "").strip()
            if auth_token and not self._homebase_user_info_refreshing:
                try:
                    self._refresh_homebase_user_info()
                except Exception:
                    pass
        if hasattr(self, "_remote_vault_menu"):
            try:
                self._remote_vault_menu.menuAction().setVisible(is_remote or is_homebase)
            except Exception:
                self._remote_vault_menu.setVisible(is_remote or is_homebase)
        if not is_remote and not is_homebase:
            self._action_manage_users.setVisible(False)
            self._action_reset_password.setVisible(False)
            return
        self._action_reset_password.setVisible(is_remote or is_homebase)
        if is_remote:
            self._action_manage_users.setVisible(bool(self._remote_user_is_admin))
            self._action_manage_users.setEnabled(bool(self._remote_user_is_admin))
        elif is_homebase:
            self._action_manage_users.setVisible(bool(self._homebase_user_is_admin))
            self._action_manage_users.setEnabled(bool(self._homebase_user_is_admin))

    @staticmethod
    def _format_remote_host(server_url: str, include_scheme: bool = False) -> str:
        parsed = urlparse(server_url or "")
        scheme = parsed.scheme or "http"
        host = parsed.hostname or server_url
        port = parsed.port
        is_standard = (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
        host_port = f"{host}:{port}" if port and not is_standard else host
        if include_scheme:
            return f"{scheme}://{host_port}"
        return host_port

    def _remote_connection_string(self) -> Optional[str]:
        if not self._remote_mode or not self.api_base:
            return None
        base = self._format_remote_host(self.api_base, include_scheme=True)
        path = self.vault_root or ""
        if path and not path.startswith("/"):
            path = f"/{path}"
        return f"{base}{path}" if base else None

    def _hide_remote_feedback(self) -> None:
        if hasattr(self, "_remote_feedback_label"):
            self._remote_feedback_label.hide()
            self._remote_feedback_label.setText("")
            self._remote_feedback_label.setToolTip("")

    def _show_remote_feedback(self, message: str, timeout_ms: int = 5000) -> None:
        if not hasattr(self, "_remote_feedback_label"):
            return
        text = str(message or "").strip()
        if not text:
            self._hide_remote_feedback()
            return
        self._remote_feedback_label.setText(text)
        self._remote_feedback_label.setToolTip(text)
        self._remote_feedback_label.show()
        self._remote_feedback_timer.start(max(1000, int(timeout_ms)))

    def _set_remote_health_state(self, state: str, message: str = "", latency_ms: Optional[float] = None) -> None:
        self._remote_health_state = state
        self._remote_health_message = str(message or "")
        self._remote_last_latency_ms = latency_ms
        if state == "slow":
            self._show_remote_feedback("Remote network is slow", timeout_ms=6000)
        elif state in ("degraded", "offline"):
            detail = self._remote_health_message or "Remote network issue"
            self._show_remote_feedback(detail, timeout_ms=7000)
        else:
            self._hide_remote_feedback()
        self._update_remote_status_badge()

    def _record_remote_latency(self, latency_ms: float, context: str = "") -> None:
        latency = float(latency_ms)
        context_label = context or "Remote request"
        if latency >= self._remote_degraded_threshold_ms:
            self._remote_slow_strikes = 0
            self._remote_timeout_strikes = 0
            self._set_remote_health_state("degraded", f"{context_label} took {latency/1000.0:.1f}s", latency_ms=latency)
        elif latency >= self._remote_slow_threshold_ms:
            self._remote_slow_strikes += 1
            self._remote_timeout_strikes = 0
            if self._remote_slow_strikes >= self._remote_slow_strikes_required:
                self._set_remote_health_state("slow", f"{context_label} took {latency/1000.0:.1f}s", latency_ms=latency)
            else:
                # One-off slow sample should not immediately mark the connection as slow.
                self._set_remote_health_state("healthy", latency_ms=latency)
        else:
            self._remote_slow_strikes = 0
            self._remote_timeout_strikes = 0
            self._set_remote_health_state("healthy", latency_ms=latency)

    def _on_right_panel_remote_request_observed(self, state: str, latency_ms: float, message: str) -> None:
        if QThread.currentThread() != self.thread():
            return
        if not self._remote_mode:
            return
        if state == "ok":
            self._record_remote_latency(latency_ms, context=message or "Task request")
            return
        msg = str(message or "")
        lowered = msg.lower()
        if "read timeout" in lowered or "timed out waiting" in lowered:
            self._remote_timeout_strikes += 1
            self._remote_slow_strikes = 0
            if self._remote_timeout_strikes >= self._remote_timeout_strikes_required:
                self._set_remote_health_state("slow", msg or "Remote task request timed out", latency_ms=latency_ms)
            return
        self._remote_slow_strikes = 0
        self._remote_timeout_strikes = 0
        self._set_remote_health_state("degraded", msg or "Remote task request failed", latency_ms=latency_ms)

    def _update_remote_status_badge(self) -> None:
        if not hasattr(self, "_remote_status_label"):
            return
        if self._remote_mode and self.vault_root:
            self._remote_status_label.setText("REMOTE")
            
            # Build detailed tooltip with connection and auth status
            tooltip_parts = []
            connection_str = self._remote_connection_string()
            if connection_str:
                tooltip_parts.append(f"Connected: {connection_str}")
            
            # Add authentication status and set badge color
            if self._access_token:
                tooltip_parts.append("Auth: ✓ Active (access token valid)")
            elif self._refresh_token:
                if self._remember_refresh:
                    tooltip_parts.append("Auth: ✓ Saved (refresh token available)")
                else:
                    tooltip_parts.append("Auth: Session only (refresh token)")
            else:
                tooltip_parts.append("Auth: ✗ Not authenticated")
            if self._remote_last_latency_ms is not None:
                tooltip_parts.append(f"Latency: {self._remote_last_latency_ms:.0f} ms")
            if self._remote_health_message:
                tooltip_parts.append(f"Network: {self._remote_health_message}")
            if self._remote_health_state == "degraded":
                badge_color = theme_value("main_window.remote_badge.degraded_bg", "#d32f2f")
            elif self._remote_health_state == "offline":
                badge_color = theme_value("main_window.remote_badge.offline_bg", "#6d4c41")
            elif self._remote_health_state == "slow":
                badge_color = theme_value("main_window.remote_badge.slow_bg", "#ed6c02")
            elif self._access_token or self._refresh_token:
                badge_color = theme_value("main_window.remote_badge.authenticated_bg", "#1e88e5")
            else:
                badge_color = theme_value("main_window.remote_badge.unauthenticated_bg", "#ff9800")
            
            # Add username if known
            if self._remote_username:
                tooltip_parts.append(f"User: {self._remote_username}")
            
            tooltip = "\n".join(tooltip_parts) if tooltip_parts else ""
            self._remote_status_label.setToolTip(tooltip)
            self._remote_status_label.setStyleSheet(
                self._badge_base_style
                + f" background-color: {badge_color}; margin-right: 6px; color: "
                f"{theme_value('main_window.remote_badge.text', '#ffffff')};"
            )
            self._remote_status_label.show()
        else:
            self._remote_status_label.setToolTip("")
            self._remote_status_label.hide()
            self._hide_remote_feedback()

    def _is_homebase_mode_enabled(self) -> bool:
        if self._remote_mode:
            return False
        try:
            return config.load_vault_remote_mode() == "homebase_remote"
        except Exception:
            return False

    def _shutdown_homebase_sync(self) -> None:
        if self._homebase_status_poll_timer:
            try:
                self._homebase_status_poll_timer.stop()
            except Exception:
                pass
        self._homebase_status_poll_timer = None
        if self._homebase_sync_engine:
            try:
                self._homebase_sync_engine.stop()
            except Exception:
                pass
        self._homebase_sync_engine = None
        self._homebase_conflict_seen_keys.clear()
        self._homebase_conflict_popup_open = False
        self._update_homebase_status_badge(None)
        self._update_homebase_sync_action_state()

    def _configure_homebase_sync_for_vault(self) -> None:
        self._shutdown_homebase_sync()
        if not self.vault_root or not self._is_homebase_mode_enabled():
            self._update_homebase_sync_action_state()
            return
        try:
            remote_url = config.load_homebase_remote_url().strip()
            passphrase = config.load_homebase_passphrase()
            if not remote_url or not passphrase:
                self._update_homebase_status_badge(
                    HomebaseSyncStatus(state="error", summary="Homebase not configured")
                )
                return
            cfg = HomebaseSyncConfig(
                vault_root=Path(self.vault_root),
                vault_id=config.load_homebase_vault_id() or config.ensure_homebase_vault_id(),
                device_id=config.load_homebase_device_id(),
                remote_url=remote_url,
                verify_ssl=config.load_homebase_verify_ssl(),
                auth_token=config.load_homebase_auth_token().strip(),
                local_ui_token=self._homebase_local_ui_token_for_url(remote_url),
                passphrase=passphrase,
                refresh_token=config.load_homebase_refresh_token().strip(),
                auto_sync=config.load_homebase_auto_sync(),
                interval_seconds=config.load_homebase_interval_seconds(),
                push_debounce_seconds=config.load_homebase_push_debounce_seconds(),
                max_parallel_transfers=config.load_homebase_max_parallel_transfers(),
                token_update_callback=self._store_homebase_tokens,
            )
            self._homebase_sync_engine = HomebaseSyncEngine(cfg)
            self._homebase_sync_engine.start()
            self._homebase_status_poll_timer = QTimer(self)
            self._homebase_status_poll_timer.setInterval(1000)
            self._homebase_status_poll_timer.timeout.connect(self._poll_homebase_status)
            self._homebase_status_poll_timer.start()
            if cfg.auto_sync:
                self._homebase_sync_engine.schedule_sync("vault open")
            self._poll_homebase_status()
            self._update_homebase_sync_action_state()
            self._refresh_homebase_user_info()
            self._update_user_management_ui()
        except Exception as exc:
            self._update_homebase_status_badge(
                HomebaseSyncStatus(state="error", summary=f"Homebase error: {exc}")
            )
            self._update_homebase_sync_action_state()

    def _poll_homebase_status(self) -> None:
        status = self._homebase_sync_engine.get_status() if self._homebase_sync_engine else None
        if self._homebase_status_clears_unsynced_marker(status):
            self._homebase_has_unsynced_local_changes = False
            self._homebase_unsynced_marked_at = None
        self._update_homebase_status_badge(status)
        self._maybe_show_homebase_conflict_popup(status)
        if self._homebase_sync_engine:
            try:
                updates = self._homebase_sync_engine.consume_remote_updates()
            except Exception:
                updates = []
            if updates:
                self._on_homebase_remote_updates(updates)
        if self._homebase_pending_reload_path and self._is_editor_idle_for_remote_reload():
            pending = self._homebase_pending_reload_path
            self._homebase_pending_reload_path = None
            if pending and self.current_path == pending:
                self._open_file(
                    pending,
                    add_to_history=False,
                    force=True,
                    restore_history_cursor=True,
                    sync_calendar=False,
                )
        self._update_homebase_sync_action_state()

    def _mark_homebase_unsynced_local_change(self) -> None:
        if not self._is_homebase_mode_enabled():
            return
        self._homebase_has_unsynced_local_changes = True
        self._homebase_unsynced_marked_at = datetime.now(timezone.utc)
        status = self._homebase_sync_engine.get_status() if self._homebase_sync_engine else None
        self._update_homebase_status_badge(status)

    @staticmethod
    def _parse_homebase_sync_ts(value: Optional[str]) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            # Engine format is usually "...Z"; normalize for fromisoformat.
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except Exception:
            return None

    def _homebase_status_clears_unsynced_marker(self, status: Optional[HomebaseSyncStatus]) -> bool:
        if not status or not self._homebase_has_unsynced_local_changes:
            return False
        if status.conflicts != 0 or status.pending:
            return False
        if status.state not in {"idle", "hibernated"}:
            return False
        mark = self._homebase_unsynced_marked_at
        if mark is None:
            return True
        synced_at = self._parse_homebase_sync_ts(status.last_sync_at)
        if synced_at is None:
            return False
        # last_sync_at is second-granular; allow a small skew window.
        return (synced_at + timedelta(seconds=1)) >= mark

    def _is_editor_idle_for_remote_reload(self) -> bool:
        if not self.current_path:
            return False
        if self._merge_dialog_open:
            return False
        if self._dirty_flag:
            return False
        try:
            if self.editor.document().isModified():
                return False
        except Exception:
            pass
        try:
            if self.autosave_timer.isActive():
                return False
        except Exception:
            pass
        return True

    def _on_homebase_remote_updates(self, updated_paths: list[str]) -> None:
        if not updated_paths or not self.vault_root or self._remote_mode:
            return
        normalized: list[str] = []
        seen: set[str] = set()
        for rel in updated_paths:
            rel_path = str(rel or "").strip().replace("\\", "/").lstrip("/")
            if not rel_path or rel_path.startswith(".stillpoint/"):
                continue
            abs_path = Path(self.vault_root) / rel_path
            page_path = "/" + abs_path.relative_to(self.vault_root).as_posix()
            if page_path not in seen:
                seen.add(page_path)
                normalized.append(page_path)
        if not normalized:
            return
        try:
            self._ensure_config_active_vault_context()
            config.bump_tree_version()
            config.bump_sync_revision()
        except Exception:
            pass
        self._refresh_tree()
        if self.current_path and self.current_path in seen:
            if self._is_editor_idle_for_remote_reload():
                self._open_file(
                    self.current_path,
                    add_to_history=False,
                    force=True,
                    restore_history_cursor=True,
                    sync_calendar=False,
                )
            else:
                self._homebase_pending_reload_path = self.current_path
                self.statusBar().showMessage(
                    "Remote sync updated this page; reload deferred until editor is idle.",
                    4000,
                )

    def _homebase_conflict_key(self, entry: dict[str, Any]) -> str:
        return "|".join(
            [
                str(entry.get("path") or "").strip(),
                str(entry.get("conflict_copy_path") or "").strip(),
                str(entry.get("ts") or "").strip(),
            ]
        )

    def _format_homebase_conflict_ts(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "Unknown"
        try:
            if text.endswith("Z"):
                dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            else:
                dt = datetime.fromisoformat(text)
            return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return text

    def _format_homebase_conflict_mtime(self, value: Any) -> str:
        try:
            sec = int(value)
            if sec <= 0:
                return "n/a"
            return datetime.fromtimestamp(sec, tz=timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        except Exception:
            return "n/a"

    def _maybe_show_homebase_conflict_popup(self, status: Optional[HomebaseSyncStatus]) -> None:
        if not status or status.conflicts <= 0:
            return
        if not self._homebase_sync_engine or not self._is_homebase_mode_enabled():
            return
        if self._homebase_conflict_popup_open:
            return
        try:
            conflicts = self._homebase_sync_engine.list_conflicts(limit=200)
        except Exception:
            return
        if not conflicts:
            return
        unseen = [c for c in conflicts if self._homebase_conflict_key(c) not in self._homebase_conflict_seen_keys]
        if not unseen:
            return
        self._show_homebase_conflicts_popup(conflicts)

    def _show_homebase_conflicts_popup(self, conflicts: list[dict[str, Any]]) -> None:
        if not conflicts:
            return
        for entry in conflicts:
            self._homebase_conflict_seen_keys.add(self._homebase_conflict_key(entry))
        dialog = QDialog(self)
        dialog.setWindowTitle("Homebase Sync Conflicts")
        dialog.resize(860, 460)
        layout = QVBoxLayout(dialog)
        info = QLabel("Homebase detected file conflicts. Select a file and open the diff viewer to merge changes.")
        layout.addWidget(info)
        list_widget = QListWidget(dialog)
        detail_label = QLabel("")
        detail_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        detail_label.setWordWrap(True)
        detail_label.setStyleSheet(
            f"padding: 8px; border: 1px solid {theme_value('main_window.splitter.handle', '#444')};"
        )
        for entry in conflicts:
            path = str(entry.get("path") or "").strip().replace("\\", "/").lstrip("/")
            item_text = (
                f"/{path}  |  "
                f"remote device: {entry.get('remote_device_id', 'unknown')}  |  "
                f"detected: {self._format_homebase_conflict_ts(entry.get('ts'))}"
            )
            item = QListWidgetItem(item_text)
            item.setData(Qt.UserRole, entry)
            list_widget.addItem(item)
        layout.addWidget(list_widget, 1)
        layout.addWidget(detail_label)
        buttons = QHBoxLayout()
        open_diff_btn = QPushButton("Open Diff Viewer", dialog)
        close_btn = QPushButton("Close", dialog)
        buttons.addStretch(1)
        buttons.addWidget(open_diff_btn)
        buttons.addWidget(close_btn)
        layout.addLayout(buttons)

        def _update_detail() -> None:
            item = list_widget.currentItem()
            if item is None:
                detail_label.setText("")
                open_diff_btn.setEnabled(False)
                return
            entry = item.data(Qt.UserRole) or {}
            path = str(entry.get("path") or "").strip().replace("\\", "/").lstrip("/")
            conflict_copy = str(entry.get("conflict_copy_path") or "").strip().replace("\\", "/").lstrip("/")
            detail_label.setText(
                "\n".join(
                    [
                        f"Path: /{path}",
                        f"Conflict copy: /{conflict_copy}",
                        f"Reason: {entry.get('reason', 'unknown')}",
                        f"Remote device: {entry.get('remote_device_id', 'unknown')}",
                        f"Detected: {self._format_homebase_conflict_ts(entry.get('ts'))}",
                        f"Local mtime: {self._format_homebase_conflict_mtime(entry.get('local_mtime'))}",
                        f"Remote mtime: {self._format_homebase_conflict_mtime(entry.get('remote_mtime'))}",
                    ]
                )
            )
            open_diff_btn.setEnabled(True)

        def _open_diff() -> None:
            item = list_widget.currentItem()
            if item is None:
                return
            entry = item.data(Qt.UserRole) or {}
            if self._resolve_homebase_conflict_with_diff(entry):
                row = list_widget.row(item)
                list_widget.takeItem(row)
                if list_widget.count() == 0:
                    dialog.accept()
                else:
                    list_widget.setCurrentRow(max(0, row - 1))

        list_widget.currentItemChanged.connect(lambda current, previous: _update_detail())
        open_diff_btn.clicked.connect(_open_diff)
        close_btn.clicked.connect(dialog.accept)
        if list_widget.count():
            list_widget.setCurrentRow(0)
        self._homebase_conflict_popup_open = True
        try:
            dialog.exec()
        finally:
            self._homebase_conflict_popup_open = False

    def _resolve_homebase_conflict_with_diff(self, entry: dict[str, Any]) -> bool:
        if not self.vault_root:
            return False
        path_rel = str(entry.get("path") or "").strip().replace("\\", "/").lstrip("/")
        conflict_rel = str(entry.get("conflict_copy_path") or "").strip().replace("\\", "/").lstrip("/")
        if not path_rel or not conflict_rel:
            QMessageBox.warning(self, "Homebase Conflict", "Missing conflict path data.")
            return False
        local_path = (Path(self.vault_root) / path_rel).resolve()
        conflict_path = (Path(self.vault_root) / conflict_rel).resolve()
        try:
            root = Path(self.vault_root).resolve()
            local_path.relative_to(root)
            conflict_path.relative_to(root)
        except Exception:
            QMessageBox.warning(self, "Homebase Conflict", "Conflict path is outside vault root.")
            return False
        if not conflict_path.exists():
            if self._homebase_sync_engine:
                try:
                    self._homebase_sync_engine.resolve_conflict_entry(conflict_rel, resolution="missing-conflict-copy")
                except Exception:
                    pass
            QMessageBox.information(self, "Homebase Conflict", "Conflict copy no longer exists. Marked as resolved.")
            return True
        if not local_path.exists():
            local_text = ""
        else:
            try:
                local_text = local_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                QMessageBox.warning(self, "Homebase Conflict", f"Local file is not UTF-8 text: /{path_rel}")
                return False
            except Exception as exc:
                QMessageBox.warning(self, "Homebase Conflict", f"Failed to read local file: {exc}")
                return False
        try:
            conflict_text = conflict_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            QMessageBox.warning(self, "Homebase Conflict", f"Conflict copy is not UTF-8 text: /{conflict_rel}")
            return False
        except Exception as exc:
            QMessageBox.warning(self, "Homebase Conflict", f"Failed to read conflict copy: {exc}")
            return False
        page_path = "/" + path_rel
        merge_dialog = MergeConflictDialog(local_text, conflict_text, page_path, parent=self)
        if merge_dialog.exec() != QDialog.Accepted:
            return False
        merged_text = merge_dialog.merged_text()
        if merged_text is None:
            return False
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            local_path.write_text(merged_text, encoding="utf-8")
            if conflict_path.exists():
                conflict_path.unlink()
            if self._homebase_sync_engine:
                self._homebase_sync_engine.resolve_conflict_entry(conflict_rel, resolution="merged")
            self._ensure_config_active_vault_context()
            config.bump_tree_version()
            config.bump_sync_revision()
            indexer.index_page(page_path, merged_text)
            self.right_panel.refresh_tasks()
            if self.current_path == page_path and self._is_editor_idle_for_remote_reload():
                self._open_file(
                    page_path,
                    add_to_history=False,
                    force=True,
                    restore_history_cursor=True,
                    sync_calendar=False,
                )
            else:
                self._refresh_tree()
            self._schedule_homebase_sync("conflict resolved")
            self.statusBar().showMessage(f"Resolved Homebase conflict for {page_path}", 5000)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Homebase Conflict", f"Failed to apply merged file: {exc}")
            return False

    def _update_homebase_status_badge(self, status: Optional[HomebaseSyncStatus]) -> None:
        if not hasattr(self, "_homebase_status_label"):
            return
        if not status or not self._is_homebase_mode_enabled():
            self._homebase_status_label.hide()
            self._homebase_status_label.setToolTip("")
            return
        auto_sync_enabled = bool(
            self._homebase_sync_engine and getattr(self._homebase_sync_engine.cfg, "auto_sync", False)
        )
        state = status.state
        has_pending_work = bool(
            status.pending
            or state == "syncing"
            or int(getattr(status, "pending_uploads", 0) or 0) > 0
            or int(getattr(status, "pending_downloads", 0) or 0) > 0
            or bool(getattr(self, "_dirty_flag", False))
            or bool(getattr(self, "_homebase_has_unsynced_local_changes", False))
        )
        star = "*" if has_pending_work else ""
        text = f"HOMEBASE{star}"
        summary_lower = str(status.summary or "").lower()
        last_error_lower = str(status.last_error or "").lower()
        auth_error = (
            "unauthor" in summary_lower
            or "not authenticated" in summary_lower
            or "auth error" in summary_lower
            or "401" in last_error_lower
        )
        if status.conflicts > 0:
            text = f"HOMEBASE ({status.conflicts}){star}"
            bg = theme_value("main_window.homebase_badge.conflict_bg", "#d32f2f")
        elif auth_error:
            text = f"HOMEBASE AUTH{star}"
            bg = theme_value("main_window.homebase_badge.auth_bg", "#d32f2f")
        elif state == "syncing" or status.pending:
            bg = theme_value("main_window.homebase_badge.syncing_bg", "#1565c0")
        elif state == "hibernated" or not auto_sync_enabled:
            bg = theme_value("main_window.homebase_badge.idle_bg", "#757575")
        else:
            bg = theme_value("main_window.homebase_badge.ready_bg", "#2e7d32")
        tooltip = status.summary
        if status.last_sync_at:
            tooltip += f"\nLast sync: {status.last_sync_at}"
        if status.last_error:
            tooltip += f"\nLast error: {status.last_error}"
        self._homebase_status_label.setText(text)
        self._homebase_status_label.setToolTip(tooltip)
        self._homebase_status_label.setStyleSheet(
            self._badge_base_style
            + f" background-color: {bg}; margin-right: 6px; color: "
            f"{theme_value('main_window.homebase_badge.text', '#ffffff')};"
        )
        self._homebase_status_label.show()

    def _persist_homebase_sync_settings_to_profile(
        self,
        auto_sync: bool,
        interval_seconds: int,
        push_debounce_seconds: int,
        max_parallel_transfers: int,
    ) -> None:
        if not self.vault_root:
            return
        current_path = self._normalize_vault_path(self.vault_root)
        if not current_path:
            return
        current_server = config.load_homebase_remote_url().strip()
        current_vault_id = (config.load_homebase_vault_id() or "").strip()
        profiles = config.load_homebase_vault_profiles()
        updated = False
        for profile in profiles:
            if not isinstance(profile, dict):
                continue
            profile_path = self._normalize_vault_path(str(profile.get("path") or ""))
            if profile_path != current_path:
                continue
            if current_server and str(profile.get("server_url") or "").strip() != current_server:
                continue
            if current_vault_id and str(profile.get("vault_id") or "").strip() != current_vault_id:
                continue
            profile["auto_sync"] = bool(auto_sync)
            profile["interval_seconds"] = int(interval_seconds)
            profile["push_debounce_seconds"] = int(push_debounce_seconds)
            profile["max_parallel_transfers"] = int(max_parallel_transfers)
            updated = True
            break
        if updated:
            config.save_homebase_vault_profiles(profiles)

    def _schedule_homebase_sync(self, reason: str) -> None:
        if self._homebase_sync_engine:
            if not getattr(self._homebase_sync_engine.cfg, "auto_sync", False):
                return
            try:
                self._homebase_sync_engine.schedule_sync(reason)
            except Exception:
                pass

    def _trigger_homebase_sync_now(self, reason: str = "manual") -> None:
        if not self._homebase_sync_engine:
            self.statusBar().showMessage("Homebase sync is not configured for this vault.", 4000)
            return
        try:
            self._homebase_sync_engine.sync_now(reason)
            self.statusBar().showMessage("Homebase sync requested.", 2500)
        except Exception as exc:
            self.statusBar().showMessage(f"Homebase sync request failed: {exc}", 5000)

    def _reset_homebase_sync_state_server_authoritative(self) -> None:
        if not self.vault_root or not self._is_homebase_mode_enabled():
            self.statusBar().showMessage("Homebase sync is not configured for this vault.", 4000)
            return
        confirm = QMessageBox.question(
            self,
            "Reset Homebase Sync State",
            "This will clear local sync state/conflicts and overwrite local files "
            "with the current server snapshot where paths match.\n\nContinue?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return
        try:
            self._shutdown_homebase_sync()
            self._ensure_config_active_vault_context()
            remote_url = config.load_homebase_remote_url().strip()
            passphrase = config.load_homebase_passphrase()
            if not remote_url or not passphrase:
                QMessageBox.warning(self, "Homebase", "Homebase is not configured for this vault.")
                return
            cfg = HomebaseSyncConfig(
                vault_root=Path(self.vault_root),
                vault_id=config.load_homebase_vault_id() or config.ensure_homebase_vault_id(),
                device_id=config.load_homebase_device_id(),
                remote_url=remote_url,
                verify_ssl=config.load_homebase_verify_ssl(),
                auth_token=config.load_homebase_auth_token().strip(),
                local_ui_token=self._homebase_local_ui_token_for_url(remote_url),
                passphrase=passphrase,
                refresh_token=config.load_homebase_refresh_token().strip(),
                auto_sync=config.load_homebase_auto_sync(),
                interval_seconds=config.load_homebase_interval_seconds(),
                push_debounce_seconds=config.load_homebase_push_debounce_seconds(),
                max_parallel_transfers=config.load_homebase_max_parallel_transfers(),
                token_update_callback=self._store_homebase_tokens,
            )
            reset_engine = HomebaseSyncEngine(cfg)
            reset_engine.reset_to_server_authoritative()
            self._configure_homebase_sync_for_vault()
            if self._homebase_sync_engine:
                self._homebase_sync_engine.sync_now("post-reset")
            self.statusBar().showMessage("Homebase sync state reset complete (server authoritative).", 5000)
        except Exception as exc:
            self._configure_homebase_sync_for_vault()
            QMessageBox.critical(self, "Homebase Reset Failed", str(exc))

    def _update_homebase_sync_action_state(self) -> None:
        action = getattr(self, "_action_homebase_sync_now", None)
        reset_action = getattr(self, "_action_homebase_reset_sync", None)
        if action is None and reset_action is None:
            return
        enabled = bool(self._homebase_sync_engine) and self._is_homebase_mode_enabled()
        if action is not None:
            action.setEnabled(enabled)
            if enabled:
                action.setToolTip(self._action_tooltips.get(action, "Run Homebase sync immediately"))
            else:
                action.setToolTip("Available when Homebase Remote mode is enabled for this vault.")
        if reset_action is not None:
            reset_action.setEnabled(enabled)
            if enabled:
                reset_action.setToolTip(
                    self._action_tooltips.get(
                        reset_action,
                        "Discard local sync state/conflicts and re-seed local files from server",
                    )
                )
            else:
                reset_action.setToolTip("Available when Homebase Remote mode is enabled for this vault.")

    def _show_homebase_sync_summary(self) -> None:
        if not self._is_homebase_mode_enabled():
            return
        status = self._homebase_sync_engine.get_status() if self._homebase_sync_engine else None
        if not status:
            QMessageBox.information(
                self,
                "Homebase Sync",
                "Homebase sync is not configured for this vault.",
            )
            return
        try:
            self._ensure_config_active_vault_context()
            auto_sync = bool(config.load_homebase_auto_sync())
            interval_seconds = int(config.load_homebase_interval_seconds())
            push_debounce_seconds = int(config.load_homebase_push_debounce_seconds())
            max_parallel_transfers = int(config.load_homebase_max_parallel_transfers())
        except Exception:
            auto_sync = True
            interval_seconds = 60
            push_debounce_seconds = 3
            max_parallel_transfers = 6

        vault_id = ""
        try:
            vault_id = str(config.load_homebase_vault_id() or "").strip()
        except Exception:
            vault_id = ""

        dialog = QDialog(self)
        dialog.setWindowTitle("Homebase Sync")
        dialog.setModal(True)
        dialog.resize(560, 420)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        header = QHBoxLayout()
        title = QLabel("Homebase sync status")
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        header.addWidget(title)
        header.addStretch(1)
        layout.addLayout(header)

        info_box = QFrame()
        info_box.setFrameShape(QFrame.StyledPanel)
        info_layout = QGridLayout(info_box)
        info_layout.setContentsMargins(12, 12, 12, 12)
        info_layout.setHorizontalSpacing(10)
        info_layout.setVerticalSpacing(6)

        row = 0
        if vault_id:
            info_layout.addWidget(QLabel("Vault ID:"), row, 0, Qt.AlignTop)
            vault_label = QLabel(vault_id)
            vault_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info_layout.addWidget(vault_label, row, 1)
            row += 1

        info_layout.addWidget(QLabel("State:"), row, 0, Qt.AlignTop)
        info_layout.addWidget(QLabel(status.state), row, 1)
        row += 1
        info_layout.addWidget(QLabel("Summary:"), row, 0, Qt.AlignTop)
        summary_label = QLabel(status.summary)
        summary_label.setWordWrap(True)
        summary_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        info_layout.addWidget(summary_label, row, 1)
        row += 1
        info_layout.addWidget(QLabel("Conflicts:"), row, 0, Qt.AlignTop)
        info_layout.addWidget(QLabel(str(status.conflicts)), row, 1)
        row += 1
        info_layout.addWidget(QLabel("Pending:"), row, 0, Qt.AlignTop)
        info_layout.addWidget(QLabel("Yes" if status.pending else "No"), row, 1)
        row += 1
        if status.last_sync_at:
            info_layout.addWidget(QLabel("Last Sync:"), row, 0, Qt.AlignTop)
            info_layout.addWidget(QLabel(status.last_sync_at), row, 1)
            row += 1
        if status.last_error:
            info_layout.addWidget(QLabel("Last Error:"), row, 0, Qt.AlignTop)
            error_label = QLabel(status.last_error)
            error_label.setWordWrap(True)
            error_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            info_layout.addWidget(error_label, row, 1)

        layout.addWidget(info_box)

        settings_box = QFrame()
        settings_box.setFrameShape(QFrame.StyledPanel)
        settings_layout = QFormLayout(settings_box)
        settings_layout.setContentsMargins(12, 12, 12, 12)
        settings_layout.setSpacing(8)

        auto_sync_cb = QCheckBox("Enable auto sync")
        auto_sync_cb.setChecked(auto_sync)
        settings_layout.addRow("Auto Sync:", auto_sync_cb)

        interval_spin = QSpinBox()
        interval_spin.setRange(5, 86400)
        interval_spin.setValue(max(5, interval_seconds))
        interval_spin.setSuffix(" s")
        settings_layout.addRow("Interval:", interval_spin)

        debounce_spin = QSpinBox()
        debounce_spin.setRange(1, 300)
        debounce_spin.setValue(max(1, push_debounce_seconds))
        debounce_spin.setSuffix(" s")
        settings_layout.addRow("Push Debounce:", debounce_spin)

        max_parallel_spin = QSpinBox()
        max_parallel_spin.setRange(1, 64)
        max_parallel_spin.setValue(max(1, max_parallel_transfers))
        settings_layout.addRow("Max Parallel Transfers:", max_parallel_spin)

        layout.addWidget(settings_box)

        def _toggle_interval_enabled() -> None:
            interval_spin.setEnabled(bool(auto_sync_cb.isChecked()))

        auto_sync_cb.toggled.connect(_toggle_interval_enabled)
        _toggle_interval_enabled()

        button_row = QHBoxLayout()
        save_settings_btn = QPushButton("Save Settings")
        sync_now_btn = QPushButton("Sync Now")
        reset_auth_btn = QPushButton("Reset Auth")
        conflicts_btn = None
        if status.conflicts > 0 and self._homebase_sync_engine:
            conflicts_btn = QPushButton(f"View Conflicts ({status.conflicts})")
            button_row.addWidget(conflicts_btn)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(dialog.accept)

        button_row.addWidget(save_settings_btn)
        button_row.addWidget(sync_now_btn)
        button_row.addWidget(reset_auth_btn)
        button_row.addStretch(1)
        button_row.addWidget(close_btn)
        layout.addLayout(button_row)

        def _save_settings() -> None:
            try:
                self._ensure_config_active_vault_context()
                new_auto_sync = bool(auto_sync_cb.isChecked())
                new_interval = int(interval_spin.value())
                new_debounce = int(debounce_spin.value())
                new_parallel = int(max_parallel_spin.value())
                config.save_homebase_auto_sync(new_auto_sync)
                config.save_homebase_interval_seconds(new_interval)
                config.save_homebase_push_debounce_seconds(new_debounce)
                config.save_homebase_max_parallel_transfers(new_parallel)
                self._persist_homebase_sync_settings_to_profile(
                    auto_sync=new_auto_sync,
                    interval_seconds=new_interval,
                    push_debounce_seconds=new_debounce,
                    max_parallel_transfers=new_parallel,
                )
                self._configure_homebase_sync_for_vault()
                self.statusBar().showMessage("Homebase sync settings updated.", 4000)
            except Exception as exc:
                QMessageBox.critical(dialog, "Homebase Sync", f"Failed to save settings: {exc}")

        def _view_conflicts() -> None:
            try:
                conflicts = self._homebase_sync_engine.list_conflicts(limit=200) if self._homebase_sync_engine else []
            except Exception:
                conflicts = []
            self._show_homebase_conflicts_popup(conflicts)

        save_settings_btn.clicked.connect(_save_settings)
        sync_now_btn.clicked.connect(lambda: self._trigger_homebase_sync_now("badge"))
        reset_auth_btn.clicked.connect(self._reset_homebase_auth)
        if conflicts_btn is not None:
            conflicts_btn.clicked.connect(_view_conflicts)

        dialog.exec()

    def _reset_homebase_auth(self) -> None:
        if not self.vault_root or not self._is_homebase_mode_enabled():
            self.statusBar().showMessage("Homebase sync is not configured for this vault.", 4000)
            return
        try:
            self._ensure_config_active_vault_context()
            remote_url = config.load_homebase_remote_url().strip().rstrip("/")
            vault_id = (config.load_homebase_vault_id() or "").strip()
            if not remote_url or not vault_id:
                QMessageBox.warning(self, "Homebase Auth", "Missing Homebase server URL or vault ID.")
                return
            default_username = config.load_homebase_username().strip()
            username, ok = QInputDialog.getText(
                self,
                "Reset Homebase Auth",
                "Username:",
                QLineEdit.Normal,
                default_username,
            )
            if not ok or not str(username).strip():
                return
            password, ok = QInputDialog.getText(
                self,
                "Reset Homebase Auth",
                "Password:",
                QLineEdit.Password,
            )
            if not ok or not password:
                return
            headers: dict[str, str] = {}
            local_ui_token = self._homebase_local_ui_token_for_url(remote_url)
            if local_ui_token:
                headers["x-local-ui-token"] = local_ui_token
            url = f"{remote_url}/v1/homebase/bootstrap/connect"
            resp = httpx.post(
                url,
                json={"vault_id": vault_id, "username": str(username).strip(), "password": password},
                headers=headers,
                timeout=20.0,
                verify=config.load_homebase_verify_ssl(),
            )
            resp.raise_for_status()
            payload = resp.json()
            access_token = str(payload.get("access_token") or "").strip()
            refresh_token = str(payload.get("refresh_token") or "").strip()
            if not access_token or not refresh_token:
                raise ValueError("Server did not return access/refresh tokens")
            config.save_homebase_username(str(username).strip())
            self._store_homebase_tokens(access_token, refresh_token)
            self._homebase_user_info_loaded = False
            self._configure_homebase_sync_for_vault()
            self._refresh_homebase_user_info()
            if self._homebase_sync_engine:
                self._homebase_sync_engine.sync_now("auth reset")
                self._poll_homebase_status()
            self.statusBar().showMessage("Homebase auth reset complete.", 5000)
        except Exception as exc:
            QMessageBox.critical(self, "Homebase Auth Reset Failed", str(exc))

    def _on_local_attachment_changed(self, reason: str) -> None:
        self._mark_homebase_unsynced_local_change()
        self._schedule_homebase_sync(reason or "attachment write")

    def _homebase_local_ui_token_for_url(self, remote_url: str) -> str:
        token = (self._local_auth_token or "").strip()
        if not token:
            return ""
        try:
            target = urlparse(remote_url)
            local = urlparse(self._local_api_base)
            target_host = (target.hostname or "").strip().lower()
            if target_host not in {"127.0.0.1", "localhost", "::1"}:
                return ""
            target_port = target.port or (443 if (target.scheme or "http") == "https" else 80)
            local_port = local.port or (443 if (local.scheme or "http") == "https" else 80)
            if target_port != local_port:
                return ""
            return token
        except Exception:
            return ""

    def _store_homebase_tokens(self, access_token: str, refresh_token: str) -> None:
        try:
            self._ensure_config_active_vault_context()
            config.save_homebase_auth_token(access_token)
            config.save_homebase_refresh_token(refresh_token)
            # Keep global Homebase profile tokens in sync so reopening from
            # Homebase Vaults does not reapply stale/expired tokens.
            if self.vault_root:
                current_path = self._normalize_vault_path(self.vault_root)
                current_server = config.load_homebase_remote_url().strip()
                current_vault_id = (config.load_homebase_vault_id() or "").strip()
                profiles = config.load_homebase_vault_profiles()
                updated = False
                for profile in profiles:
                    if not isinstance(profile, dict):
                        continue
                    profile_path = self._normalize_vault_path(str(profile.get("path") or ""))
                    if profile_path != current_path:
                        continue
                    if current_server and str(profile.get("server_url") or "").strip() != current_server:
                        continue
                    if current_vault_id and str(profile.get("vault_id") or "").strip() != current_vault_id:
                        continue
                    profile["access_token"] = access_token
                    profile["refresh_token"] = refresh_token
                    updated = True
                    break
                if updated:
                    config.save_homebase_vault_profiles(profiles)
        except Exception:
            pass

    def _build_http_client(self, base_url: str, is_remote: bool, local_auth_token: Optional[str], request_hooks) -> httpx.Client:
        timeout: float | httpx.Timeout = 10.0
        if is_remote:
            self._load_remote_auth()
            auth = RemoteTokenAuth(self._get_access_token, self._attempt_refresh)
            headers = {}
            connect_timeout, read_timeout = self._remote_timeout_settings_for_url(base_url)
            timeout = self._http_timeout(connect_timeout, read_timeout)
            
            # Add server admin password header for remote servers
            try:
                from urllib.parse import urlparse
                parsed = urlparse(base_url)
                host = parsed.hostname
                port = parsed.port or (443 if parsed.scheme == "https" else 80)
                scheme = parsed.scheme
                
                if host:
                    server_password_hash = config.get_server_password_hash(host, port, scheme)
                    
                    # If this is the local embedded server, use the embedded password
                    if not server_password_hash and base_url == self._local_api_base and self._embedded_server_admin_password:
                        import hashlib
                        server_password_hash = hashlib.sha256(self._embedded_server_admin_password.encode()).hexdigest()
                    
                    if server_password_hash:
                        headers["X-Server-Admin-Password"] = server_password_hash
            except Exception:
                pass
            
            headers = headers if headers else None
        else:
            auth = None
            headers = {"X-Local-UI-Token": local_auth_token} if local_auth_token else None
        return httpx.Client(
            base_url=base_url,
            timeout=timeout,
            event_hooks={"request": [request_hooks[0]], "response": [request_hooks[1]]},
            headers=headers,
            verify=self._verify_tls,
            auth=auth,
        )

    def _rebuild_http_client(self) -> None:
        """Rebuild the HTTP client with current auth state (useful after re-login)."""
        if not hasattr(self, 'http'):
            return
        try:
            self.http.close()
        except Exception:
            pass
        def _log_request(request):
            request.extensions["sp_request_started_at"] = time.perf_counter()
            try:
                path = request.url.raw_path.decode("utf-8") if hasattr(request.url, "raw_path") else request.url.path
            except Exception:
                path = str(request.url)
            _log_api_client(f"{_ANSI_BLUE}[API] {request.method} {path}{_ANSI_RESET}")

        def _log_response(response):
            started = response.request.extensions.get("sp_request_started_at")
            on_ui_thread = QThread.currentThread() == self.thread()
            if isinstance(started, (int, float)) and self._remote_mode and on_ui_thread:
                latency_ms = (time.perf_counter() - float(started)) * 1000.0
                if response.status_code >= 500:
                    self._set_remote_health_state(
                        "degraded",
                        f"{response.request.method} {response.request.url.path} -> HTTP {response.status_code}",
                        latency_ms=latency_ms,
                    )
                else:
                    self._record_remote_latency(
                        latency_ms,
                        context=f"{response.request.method} {response.request.url.path}",
                    )
            try:
                path = response.request.url.raw_path.decode("utf-8") if hasattr(response.request.url, "raw_path") else response.request.url.path
            except Exception:
                path = str(response.request.url)
            _log_api_client(f"{_ANSI_BLUE}[API] {response.status_code} {path}{_ANSI_RESET}")

        self.http = self._build_http_client(
            base_url=self.api_base,
            is_remote=self._remote_mode,
            local_auth_token=self._local_auth_token,
            request_hooks=(_log_request, _log_response),
        )
        # Update references in other components
        try:
            self.right_panel.set_http_client(
                self.http,
                api_base=self.api_base,
                remote_mode=self._remote_mode,
                auth_prompt=self._prompt_remote_login if self._remote_mode else None,
            )
        except Exception:
            pass
        try:
            if self.search_tab:
                self.search_tab.set_http_client(self.http)
                self.search_tab.set_remote_mode(self._remote_mode)
        except Exception:
            pass

    def _switch_api_base(self, base_url: str, is_remote: bool, verify_tls: Optional[bool] = None) -> None:
        """Swap the active API base URL and rebuild the HTTP client."""
        self.api_base = base_url.rstrip("/")
        self._remote_mode = is_remote
        self._server_url = self.api_base if is_remote else None
        self._remote_cache_root = None
        if verify_tls is not None:
            self._verify_tls = bool(verify_tls)
        self._access_token = None
        self._refresh_token = None
        self._remember_refresh = False
        self._remote_username = None
        self._remote_health_state = "unknown"
        self._remote_health_message = ""
        self._remote_last_latency_ms = None
        self._remote_slow_strikes = 0
        self._remote_timeout_strikes = 0
        self._rebuild_http_client()
        self._apply_remote_mode_ui()
        try:
            self.right_panel.set_http_client(
                self.http,
                api_base=self.api_base,
                remote_mode=self._remote_mode,
                auth_prompt=self._prompt_remote_login if self._remote_mode else None,
            )
        except Exception:
            pass
        try:
            if self.search_tab:
                self.search_tab.set_http_client(self.http)
                self.search_tab.set_remote_mode(self._remote_mode)
        except Exception:
            pass
        try:
            self._refresh_editor_context(self.current_path)
        except Exception:
            pass
        if hasattr(self, "_remote_vault_menu"):
            show_menu = self._remote_mode or self._is_homebase_mode_enabled()
            try:
                self._remote_vault_menu.menuAction().setVisible(show_menu)
            except Exception:
                self._remote_vault_menu.setVisible(show_menu)
        
        # Check if remote vault needs indexing
        if is_remote:
            QTimer.singleShot(500, self._check_remote_vault_index)

    def _refresh_editor_context(self, path: Optional[str]) -> None:
        self.editor.set_context(self.vault_root, path)
        self.editor.set_remote_context(
            remote_mode=self._remote_mode,
            api_base=self.api_base if self._remote_mode else None,
            cache_root=self._ensure_remote_cache_root() if self._remote_mode else None,
            http_client=self.http if self._remote_mode else None,
            auth_prompt=self._prompt_remote_login if self._remote_mode else None,
        )

    def _ensure_remote_cache_root(self) -> Path:
        """Create a local cache root for remote vault metadata."""
        if self._remote_cache_root is not None:
            return self._remote_cache_root
        from urllib.parse import urlparse

        parsed = urlparse(self.api_base)
        host = parsed.hostname or "remote"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        key = f"{parsed.scheme}://{host}:{port}"
        digest = hashlib.sha1(key.encode("ascii", errors="ignore")).hexdigest()[:12]
        cache_root = Path.home() / ".stillpoint" / "remote" / f"{host}-{port}-{digest}"
        cache_root.mkdir(parents=True, exist_ok=True)
        self._remote_cache_root = cache_root
        return cache_root

    def _remote_vault_cache_root(self, vault_path: str) -> Path:
        """Create a per-remote-vault cache directory for local metadata."""
        cache_root = self._ensure_remote_cache_root()
        base_name = Path(vault_path).name or "vault"
        digest = hashlib.sha1(vault_path.encode("utf-8", errors="ignore")).hexdigest()[:12]
        return cache_root / "vaults" / f"{base_name}-{digest}"

    def _check_remote_vault_index(self) -> None:
        """Check if remote vault has an index and prompt to build if empty."""
        if not self._remote_mode:
            return
        
        try:
            # Check if database has any pages
            resp = self.http.get("/api/pages/search", params={"q": "", "limit": 1})
            if resp.status_code != 200:
                return
            
            data = resp.json()
            pages = data.get("pages", [])
            
            # If no pages found, prompt to build index
            if not pages:
                reply = QMessageBox.question(
                    self,
                    "Remote Vault Index Empty",
                    "The remote vault's page index is empty.\n\n"
                    "Would you like to build the vault index now?\n"
                    "(This will scan all pages and populate the database)",
                    QMessageBox.Yes | QMessageBox.No,
                    QMessageBox.Yes
                )
                
                if reply == QMessageBox.Yes:
                    self._rebuild_vault_index_from_disk()
        except Exception as e:
            print(f"[RemoteVault] Failed to check index status: {e}")

    def _local_vault_root(self) -> Optional[str]:
        """Return a local path to use for per-vault metadata storage."""
        if not self.vault_root:
            return None
        if self._remote_mode:
            ref_path = self._remote_vault_ref_path or self.vault_root
            return str(self._remote_vault_cache_root(ref_path))
        return self.vault_root

    def _remote_server_key(self) -> str:
        """Normalize the server URL into a stable config key."""
        return self._server_key_for_url(self.api_base)

    @staticmethod
    def _server_key_for_url(url: str) -> str:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        scheme = parsed.scheme or "http"
        host = parsed.hostname or url
        port = parsed.port or (443 if scheme == "https" else 80)
        return f"{scheme}://{host}:{port}"

    def _load_remote_auth(self) -> None:
        """Load stored refresh token for the remote server."""
        entry = config.load_remote_auth(self._remote_server_key())
        token = entry.get("refresh_token")
        if token:
            self._refresh_token = token
            self._remember_refresh = True
        username = entry.get("username")
        if isinstance(username, str) and username:
            self._remote_username = username

    def _set_auth_tokens(self, access: str, refresh: str, remember: bool, username: Optional[str]) -> None:
        self._access_token = access
        self._refresh_token = refresh
        self._remember_refresh = remember
        if remember:
            config.save_remote_auth(self._remote_server_key(), refresh, username=username)
        else:
            config.save_remote_auth(self._remote_server_key(), None, username=None)

    def _clear_auth_tokens(self) -> None:
        self._access_token = None
        self._refresh_token = None
        self._remember_refresh = False
        config.save_remote_auth(self._remote_server_key(), None, username=None)

    def _get_access_token(self) -> Optional[str]:
        return self._access_token

    def _attempt_refresh(self) -> bool:
        """Try to refresh the access token using the stored refresh token."""
        if not self._refresh_token:
            return False
        try:
            resp = httpx.post(
                f"{self.api_base}/auth/refresh",
                headers={"Authorization": f"Bearer {self._refresh_token}"},
                timeout=10.0,
                verify=self._verify_tls,
            )
            if resp.status_code != 200:
                if resp.status_code == 401:
                    self._clear_auth_tokens()
                return False
            payload = resp.json()
            access = payload.get("access_token")
            refresh = payload.get("refresh_token") or self._refresh_token
            if not access or not refresh:
                return False
            self._set_auth_tokens(access, refresh, self._remember_refresh, self._remote_username)
            return True
        except Exception:
            return False

    def _refresh_remote_user_info(self) -> None:
        if not self._remote_mode:
            return
        try:
            resp = self.http.get("/auth/me")
        except Exception:
            return
        if resp.status_code != 200:
            return
        try:
            info = resp.json()
        except Exception:
            return
        username = str(info.get("username") or "").strip()
        if username:
            self._remote_username = username
        role = str(info.get("role") or "").strip().lower()
        perm = str(info.get("perm") or "").strip().lower()
        is_admin = bool(info.get("is_admin") or role == "admin")
        can_write = info.get("can_write")
        if can_write is None:
            can_write = role == "admin" or perm in ("read_write", "read+write", "write", "readwrite")
        self._apply_remote_user_permissions(can_write=bool(can_write), is_admin=bool(is_admin))

    def _homebase_request(self, method: str, path: str, payload: Optional[dict] = None) -> httpx.Response:
        base_url = config.load_homebase_remote_url().strip().rstrip("/")
        vault_id = (config.load_homebase_vault_id() or "").strip()
        if not base_url or not vault_id:
            raise RuntimeError("Homebase server is not configured.")
        auth_token = str(config.load_homebase_auth_token() or "").strip()
        refresh_token = str(config.load_homebase_refresh_token() or "").strip()
        verify_ssl = config.load_homebase_verify_ssl()
        local_ui_token = self._homebase_local_ui_token_for_url(base_url)
        headers: dict[str, str] = {}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        if local_ui_token:
            headers["x-local-ui-token"] = local_ui_token
        url = f"{base_url}/v1/homebase/{vault_id}{path}"
        resp = httpx.request(method, url, headers=headers, json=payload, timeout=15.0, verify=verify_ssl)
        if resp.status_code != 401 or not refresh_token:
            return resp
        try:
            refresh_resp = httpx.post(
                f"{base_url}/v1/homebase/bootstrap/refresh",
                json={"vault_id": vault_id, "refresh_token": refresh_token},
                timeout=15.0,
                verify=verify_ssl,
            )
            if refresh_resp.status_code != 200:
                return resp
            data = refresh_resp.json()
            access = str(data.get("access_token") or "").strip()
            refreshed = str(data.get("refresh_token") or "").strip()
            if not access or not refreshed:
                return resp
            self._store_homebase_tokens(access, refreshed)
            headers["Authorization"] = f"Bearer {access}"
            resp = httpx.request(method, url, headers=headers, json=payload, timeout=15.0, verify=verify_ssl)
        except Exception:
            return resp
        return resp

    def _refresh_homebase_user_info(self) -> None:
        if not self._is_homebase_mode_enabled():
            return
        if self._homebase_user_info_refreshing:
            return
        self._homebase_user_info_refreshing = True
        try:
            resp = self._homebase_request("GET", "/auth/me")
            if resp.status_code != 200:
                return
            try:
                info = resp.json()
            except Exception:
                return
            role = str(info.get("role") or "").strip().lower()
            perm = str(info.get("perm") or "").strip().lower()
            is_admin = bool(info.get("role") == "admin" or info.get("is_admin"))
            can_write = info.get("can_write")
            if can_write is None:
                can_write = role == "admin" or perm in ("read_write", "read+write", "write", "readwrite")
            self._apply_homebase_user_permissions(can_write=bool(can_write), is_admin=bool(is_admin))
            self._homebase_user_info_loaded = True
            self._update_user_management_ui()
        finally:
            self._homebase_user_info_refreshing = False

    def _setup_remote_auth(self, username: str, password: str, remember: bool) -> bool:
        """Setup authentication for a vault that doesn't have it configured yet."""
        try:
            resp = httpx.post(
                f"{self.api_base}/auth/setup",
                json={"username": username, "password": password},
                timeout=10.0,
                verify=self._verify_tls,
            )
            if resp.status_code != 200:
                detail = None
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        detail = data.get("detail") or data.get("message")
                except Exception:
                    pass
                raise RuntimeError(detail or f"HTTP {resp.status_code}")
            payload = resp.json()
            access = payload.get("access_token")
            refresh = payload.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError("Missing tokens in response")
            self._remote_username = username
            self._set_auth_tokens(access, refresh, remember, username)
            # Rebuild the HTTP client so it uses the new auth tokens
            self._rebuild_http_client()
            self._refresh_remote_user_info()
            self.statusBar().showMessage("Vault authentication configured.", 3000)
            return True
        except Exception as exc:
            self._alert(f"Setup failed: {exc}")
            return False

    def _login_remote(self, username: str, password: str, remember: bool) -> bool:
        try:
            resp = httpx.post(
                f"{self.api_base}/auth/login",
                json={"username": username, "password": password},
                timeout=10.0,
                verify=self._verify_tls,
            )
            if resp.status_code != 200:
                detail = None
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        detail = data.get("detail") or data.get("message")
                except Exception:
                    pass
                if detail and "Server password" in detail:
                    if self._prompt_remote_password_change(username, password, remember):
                        return True
                raise RuntimeError(detail or f"HTTP {resp.status_code}")
            payload = resp.json()
            access = payload.get("access_token")
            refresh = payload.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError("Missing tokens in response")
            self._remote_username = username
            self._set_auth_tokens(access, refresh, remember, username)
            # Rebuild the HTTP client so it uses the new auth tokens
            self._rebuild_http_client()
            self._refresh_remote_user_info()
            self._update_remote_status_badge()
            self.statusBar().showMessage("Remote vault authentication successful.", 3000)
            return True
        except Exception as exc:
            self._alert(f"Login failed: {exc}")
            self.statusBar().showMessage(f"Remote vault login failed: {exc}", 5000)
            return False

    def _change_remote_password(self, username: str, old_password: str, new_password: str, remember: bool) -> bool:
        try:
            resp = httpx.post(
                f"{self.api_base}/auth/change",
                json={
                    "username": username,
                    "old_password": old_password,
                    "new_password": new_password,
                },
                timeout=10.0,
                verify=self._verify_tls,
            )
            if resp.status_code != 200:
                detail = None
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        detail = data.get("detail") or data.get("message")
                except Exception:
                    pass
                raise RuntimeError(detail or f"HTTP {resp.status_code}")
            payload = resp.json()
            access = payload.get("access_token")
            refresh = payload.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError("Missing tokens in response")
            self._remote_username = username
            self._set_auth_tokens(access, refresh, remember, username)
            self._rebuild_http_client()
            self._refresh_remote_user_info()
            self._update_remote_status_badge()
            self.statusBar().showMessage("Vault password updated.", 3000)
            return True
        except Exception as exc:
            self._alert(f"Password update failed: {exc}")
            return False

    def _prompt_remote_password_change(self, username: str, old_password: str, remember_default: bool) -> bool:
        dlg = RemoteChangePasswordDialog(
            self,
            username=username,
            old_password=old_password,
            remember_default=remember_default,
        )
        if dlg.exec() != QDialog.Accepted:
            return False
        new_username, old_pw, new_pw, remember = dlg.values()
        return self._change_remote_password(new_username, old_pw, new_pw, remember)

    def _prompt_reset_password(self) -> None:
        if not self._remote_mode:
            self._alert("Password reset is only available for remote vaults.")
            return
        remember_default = self._remember_refresh or bool(self._refresh_token)
        dlg = RemoteChangePasswordDialog(
            self,
            username=self._remote_username or "",
            old_password="",
            remember_default=remember_default,
        )
        dlg.setWindowTitle("Reset Vault Password")
        try:
            dlg.username_edit.setReadOnly(True)
        except Exception:
            pass
        if dlg.exec() != QDialog.Accepted:
            return
        username, old_pw, new_pw, remember = dlg.values()
        self._change_remote_password(username, old_pw, new_pw, remember)

    def _prompt_homebase_reset_password(self) -> None:
        if not self._is_homebase_mode_enabled():
            self._alert("Password reset is only available for Homebase vaults.")
            return
        dlg = RemoteChangePasswordDialog(
            self,
            username=config.load_homebase_username().strip(),
            old_password="",
            remember_default=True,
        )
        dlg.setWindowTitle("Reset Homebase Password")
        try:
            dlg.username_edit.setReadOnly(True)
        except Exception:
            pass
        if dlg.exec() != QDialog.Accepted:
            return
        username, old_pw, new_pw, _remember = dlg.values()
        self._change_homebase_password(old_pw, new_pw)

    def _change_homebase_password(self, old_password: str, new_password: str) -> None:
        try:
            resp = self._homebase_request(
                "POST",
                "/auth/change",
                payload={"old_password": old_password, "new_password": new_password},
            )
            if resp.status_code != 200:
                detail = None
                try:
                    data = resp.json()
                    if isinstance(data, dict):
                        detail = data.get("detail") or data.get("message")
                except Exception:
                    pass
                raise RuntimeError(detail or f"HTTP {resp.status_code}")
            self.statusBar().showMessage("Homebase password updated.", 3000)
        except Exception as exc:
            self._alert(f"Password update failed: {exc}")

    def _homebase_login(self) -> None:
        if not self._is_homebase_mode_enabled():
            self._alert("Homebase sync is not configured for this vault.")
            return
        self._reset_homebase_auth()

    def _homebase_logout(self) -> None:
        try:
            config.save_homebase_auth_token("")
            config.save_homebase_refresh_token("")
            self._homebase_user_is_admin = False
            self._homebase_user_can_write = True
            self._homebase_user_info_loaded = False
            self._configure_homebase_sync_for_vault()
            self.statusBar().showMessage("Homebase credentials cleared.", 3000)
        except Exception as exc:
            self._alert(f"Failed to clear Homebase credentials: {exc}")

    def _handle_remote_vault_login(self) -> None:
        if self._remote_mode:
            self._prompt_remote_login()
            return
        if self._is_homebase_mode_enabled():
            self._homebase_login()
            return
        self._alert("Remote vault login is only available when a remote or Homebase vault is active.")

    def _handle_remote_vault_logout(self) -> None:
        if self._remote_mode:
            self._logout_remote()
            return
        if self._is_homebase_mode_enabled():
            self._homebase_logout()
            return
        self._alert("Remote vault logout is only available when a remote or Homebase vault is active.")

    def _handle_remote_vault_reset_password(self) -> None:
        if self._remote_mode:
            self._prompt_reset_password()
            return
        if self._is_homebase_mode_enabled():
            self._prompt_homebase_reset_password()
            return
        self._alert("Password reset is only available for remote or Homebase vaults.")

    def _fetch_remote_users(self) -> list[dict]:
        resp = self.http.get("/auth/users")
        if resp.status_code == 404:
            raise RuntimeError("User management is not supported by this server. Update the remote server and try again.")
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")
        payload = resp.json()
        users = payload.get("users") if isinstance(payload, dict) else None
        return users if isinstance(users, list) else []

    def _create_remote_user(self, username: str, password: str, role: str, perm: str) -> None:
        resp = self.http.post(
            "/auth/users",
            json={"username": username, "password": password, "role": role, "perm": perm},
        )
        if resp.status_code == 404:
            raise RuntimeError("User management is not supported by this server. Update the remote server and try again.")
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")

    def _edit_remote_user(self, username: str, new_username: str, password: str, role: str, perm: str) -> None:
        payload: dict[str, str] = {"role": role, "perm": perm}
        if new_username and new_username != username:
            payload["username"] = new_username
        if password:
            payload["password"] = password
        resp = self.http.patch(f"/auth/users/{quote(username)}", json=payload)
        if resp.status_code == 404:
            raise RuntimeError("User management is not supported by this server. Update the remote server and try again.")
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")

    def _delete_remote_user(self, username: str) -> None:
        resp = self.http.delete(f"/auth/users/{quote(username)}")
        if resp.status_code == 404:
            raise RuntimeError("User management is not supported by this server. Update the remote server and try again.")
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")

    def _fetch_homebase_users(self) -> list[dict]:
        resp = self._homebase_request("GET", "/users")
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")
        payload = resp.json()
        users = payload.get("users") if isinstance(payload, dict) else None
        return users if isinstance(users, list) else []

    def _create_homebase_user(self, username: str, password: str, role: str, perm: str) -> None:
        resp = self._homebase_request(
            "POST",
            "/users",
            payload={"username": username, "password": password, "role": role, "perm": perm},
        )
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")

    def _edit_homebase_user(self, username: str, new_username: str, password: str, role: str, perm: str) -> None:
        payload: dict[str, str] = {"role": role, "perm": perm}
        if new_username and new_username != username:
            payload["username"] = new_username
        if password:
            payload["password"] = password
        resp = self._homebase_request("PATCH", f"/users/{quote(username)}", payload=payload)
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")

    def _delete_homebase_user(self, username: str) -> None:
        resp = self._homebase_request("DELETE", f"/users/{quote(username)}")
        if resp.status_code != 200:
            detail = None
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
            except Exception:
                pass
            raise RuntimeError(detail or f"HTTP {resp.status_code}")

    def _open_user_management(self) -> None:
        if self._remote_mode:
            dialog = ManageUsersDialog(
                self,
                fetch_users=self._fetch_remote_users,
                create_user=self._create_remote_user,
                edit_user=self._edit_remote_user,
                delete_user=self._delete_remote_user,
                title="Manage Users (Remote Vault)",
            )
            dialog.exec()
            return
        if self._is_homebase_mode_enabled():
            dialog = ManageUsersDialog(
                self,
                fetch_users=self._fetch_homebase_users,
                create_user=self._create_homebase_user,
                edit_user=self._edit_homebase_user,
                delete_user=self._delete_homebase_user,
                title="Manage Users (Homebase)",
            )
            dialog.exec()
            return
        self._alert("User management is only available for remote vaults.")

    def _prompt_remote_login(self) -> bool:
        """Prompt for vault credentials, calling setup or login based on auth status."""
        if not self._remote_mode:
            return False
        
        # Check if auth is already configured for this vault
        auth_configured = False
        try:
            resp = self.http.get("/auth/status")
            if resp.status_code == 200:
                payload = resp.json()
                auth_configured = payload.get("configured", False)
        except Exception:
            pass
        
        remember_default = self._remember_refresh or bool(self._refresh_token)
        if auth_configured:
            # Auth exists, prompt for login
            dlg = RemoteLoginDialog(self, username=self._remote_username or "", remember_default=remember_default)
            if dlg.exec() != QDialog.Accepted:
                return False
            username, password, remember = dlg.credentials()
            return self._login_remote(username, password, remember)
        else:
            # Auth doesn't exist, prompt for setup
            dlg = RemoteLoginDialog(self, username=self._remote_username or "", remember_default=remember_default)
            dlg.setWindowTitle("Setup Vault Authentication")
            if dlg.exec() != QDialog.Accepted:
                return False
            username, password, remember = dlg.credentials()
            return self._setup_remote_auth(username, password, remember)

    def _prompt_remote_login_for_server(self, base_url: str, verify_ssl: bool) -> bool:
        dlg = RemoteLoginDialog(self, username=self._remote_username or "", remember_default=True)
        if dlg.exec() != QDialog.Accepted:
            return False
        username, password, remember = dlg.credentials()
        try:
            resp = httpx.post(
                f"{base_url}/auth/login",
                json={"username": username, "password": password},
                timeout=10.0,
                verify=verify_ssl,
            )
            if resp.status_code != 200:
                raise RuntimeError(f"HTTP {resp.status_code}")
            payload = resp.json()
            access = payload.get("access_token")
            refresh = payload.get("refresh_token")
            if not access or not refresh:
                raise RuntimeError("Missing tokens in response")
            self._access_token = access
            self._refresh_token = refresh
            self._remote_username = username
            if remember:
                server_key = self._server_key_for_url(base_url)
                config.save_remote_auth(server_key, refresh, username=username)
            return True
        except Exception as exc:
            self._alert(f"Login failed: {exc}")
            return False

    def _ensure_remote_auth_for_vault(self) -> bool:
        """Prompt for remote login when the selected vault is protected."""
        if not self._remote_mode:
            return True
        try:
            resp = self.http.get("/auth/status")
        except httpx.HTTPError:
            return True
        if resp.status_code == 401:
            if not self._prompt_remote_login():
                return False
            try:
                resp = self.http.get("/auth/status")
            except httpx.HTTPError:
                return True
        if resp.status_code != 200:
            return True
        payload = resp.json()
        if not payload.get("enabled") or not payload.get("configured"):
            return True
        if self._access_token:
            self._refresh_remote_user_info()
            return True
        if self._refresh_token and self._attempt_refresh():
            self._refresh_remote_user_info()
            return True
        ok = self._prompt_remote_login()
        if ok:
            self._refresh_remote_user_info()
        return ok

    def _logout_remote(self) -> None:
        if not self._remote_mode:
            return
        self._clear_auth_tokens()
        self._remote_user_is_admin = False
        self._remote_user_can_write = True
        self._user_read_only = False
        self._read_only = False
        self._apply_read_only_state()
        self._update_user_management_ui()
        self._update_remote_status_badge()
        self.statusBar().showMessage("Remote vault credentials cleared.", 3000)

    def _check_and_acquire_vault_lock(self, directory: str, prefer_read_only: bool = False) -> bool:
        """Create a simple lockfile in the vault; prompt if locked or forced read-only."""
        self._read_only = False
        root = Path(directory)
        is_help_vault = root.name == "help-vault"
        lock_path = root / ".stillpoint" / "stillpoint.lock"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        existing: Optional[dict] = None
        if lock_path.exists():
            try:
                existing = json.loads(lock_path.read_text(encoding="utf-8"))
            except Exception:
                existing = {"raw": lock_path.read_text(errors="ignore")}
        if existing:
            pid = existing.get("pid")
            host = existing.get("host")
            active = False
            if isinstance(pid, int) and isinstance(host, str):
                active = self._is_pid_active(pid, host)
            owner_text = f"{host or '?'} (pid {pid})"
            if active:
                # Skip warning dialog for help-vault
                if not is_help_vault:
                    msg = QMessageBox(self)
                    msg.setWindowTitle("Read-Only Vault")
                    msg.setIcon(QMessageBox.Warning)
                    info = f" (owner: {owner_text})"
                    msg.setText("Database is read only via settings or due to another instance" + info + ".\n\nOpen in read-only mode?")
                    readonly_btn = msg.addButton("Open Read-Only", QMessageBox.AcceptRole)
                    cancel_btn = msg.addButton(QMessageBox.Cancel)
                    msg.setDefaultButton(readonly_btn)
                    msg.exec()
                    if msg.clickedButton() is not readonly_btn:
                        return False
                self._read_only = True
                # Do not take over the lock file
                self._vault_lock_path = None
                self._vault_lock_owner = None
                self._apply_read_only_state()
                return True
            else:
                # Stale lock; remove it
                try:
                    lock_path.unlink()
                except Exception:
                    pass
        if prefer_read_only:
            # Show the same warning even when forced by settings (skip for help-vault)
            if not is_help_vault:
                msg = QMessageBox(self)
                msg.setWindowTitle("Read-Only Vault")
                msg.setIcon(QMessageBox.Warning)
                msg.setText("Database is read only via settings or due to another instance.\n\nOpen in read-only mode?")
                readonly_btn = msg.addButton("Open Read-Only", QMessageBox.AcceptRole)
                cancel_btn = msg.addButton(QMessageBox.Cancel)
                msg.setDefaultButton(readonly_btn)
                msg.exec()
                if msg.clickedButton() is not readonly_btn:
                    return False
            self._read_only = True
            self._vault_lock_path = None
            self._vault_lock_owner = None
            self._apply_read_only_state()
            return True
        owner = {"pid": os.getpid(), "host": socket.gethostname(), "ts": time.time()}
        try:
            lock_path.write_text(json.dumps(owner), encoding="utf-8")
            self._vault_lock_path = lock_path
            self._vault_lock_owner = owner
        except Exception:
            # If we cannot write the lock, continue but warn the user
            self.statusBar().showMessage("Warning: could not write vault lock.", 5000)
        self._apply_read_only_state()
        return True

    def _release_vault_lock(self, reset_read_only: bool = True) -> None:
        """Release the lock if we own it."""
        if not self._vault_lock_path:
            return
        path = self._vault_lock_path
        owner = self._vault_lock_owner or {}
        if path.exists():
            try:
                current = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                current = {}
            if (
                current.get("pid") == owner.get("pid")
                and current.get("host") == owner.get("host")
            ):
                try:
                    path.unlink()
                except Exception:
                    pass
        self._vault_lock_path = None
        self._vault_lock_owner = None
        if reset_read_only:
            self._read_only = False
        self._apply_read_only_state()

    def _apply_vault_read_only_pref(self) -> None:
        """Toggle read-only mode immediately based on the per-vault preference."""
        if not self.vault_root:
            return
        self._ensure_config_active_vault_context()
        try:
            desired_read_only = config.load_vault_force_read_only()
        except Exception:
            desired_read_only = False
        if desired_read_only:
            if not self._read_only:
                # Drop any lock we hold and switch to read-only
                self._release_vault_lock(reset_read_only=False)
                self._read_only = True
                self._apply_read_only_state()
            return
        # Preference allows writes; try to acquire lock if currently read-only
        if self._read_only:
            if self._check_and_acquire_vault_lock(self.vault_root):
                pass
            else:
                # Failed to acquire lock (likely held elsewhere); stay read-only
                self._read_only = True
                self._apply_read_only_state()

    def _apply_feature_overrides(self) -> None:
        self._ensure_config_active_vault_context()
        new_tasks = config.load_feature_tasks_enabled()
        new_calendar = config.load_feature_calendar_enabled()
        new_link_navigator = config.load_feature_link_navigator_enabled()
        new_tags = config.load_feature_tags_enabled()
        new_remember_cursor_position = config.load_feature_remember_cursor_position_enabled()
        new_ai = config.load_enable_ai_chats()
        changed = (
            new_tasks != self._feature_tasks_enabled
            or new_calendar != self._feature_calendar_enabled
            or new_link_navigator != self._feature_link_navigator_enabled
            or new_tags != self._feature_tags_enabled
        )
        self._feature_tasks_enabled = new_tasks
        self._feature_calendar_enabled = new_calendar
        self._feature_link_navigator_enabled = new_link_navigator
        self._feature_tags_enabled = new_tags
        self._feature_remember_cursor_position_enabled = new_remember_cursor_position
        self.right_panel.set_feature_flags(
            enable_tasks=new_tasks,
            enable_calendar=new_calendar,
            enable_link_navigator=new_link_navigator,
        )
        self.right_panel.set_ai_enabled(new_ai)
        self.editor.set_ai_actions_enabled(new_ai)
        if new_tags and self.tags_tab is None:
            self.tags_tab = TagsTab(http_client=self.http)
            self.tags_tab.pageNavigationRequested.connect(self._on_search_result_selected)
            self.tags_tab.pageNavigationWithEditorFocusRequested.connect(self._on_search_result_selected_with_editor_focus)
            self.left_tab_widget.insertTab(1, self.tags_tab, "Tags")
            if self._nav_filter_path:
                try:
                    self.tags_tab.set_navigation_filter(
                        self._nav_filter_path,
                        path_to_colon(self._nav_filter_path),
                        self._clear_nav_filter,
                    )
                except Exception:
                    pass
        elif not new_tags and self.tags_tab is not None:
            idx = self.left_tab_widget.indexOf(self.tags_tab)
            if idx != -1:
                self.left_tab_widget.removeTab(idx)
            self.tags_tab.deleteLater()
            self.tags_tab = None
            if self.left_tab_widget.currentWidget() is None:
                self.left_tab_widget.setCurrentIndex(0)
        if changed:
            self._refresh_right_minibar_tabs()
            self._refresh_left_minibar_tabs()
        self._apply_calendar_action_visibility()

    def _ensure_config_active_vault_context(self) -> None:
        """Make config reads resolve against this window's active vault."""
        if not self.vault_root:
            return
        try:
            if self._remote_mode:
                cache_root = self._remote_vault_cache_root(self._remote_vault_ref_path or self.vault_root)
                cache_root.mkdir(parents=True, exist_ok=True)
                config.set_active_vault(str(cache_root))
            else:
                config.set_active_vault(self.vault_root)
        except Exception:
            return

    def _apply_calendar_action_visibility(self) -> None:
        visible = bool(self._feature_calendar_enabled)
        for action_name in (
            "_toolbar_today_action",
            "_action_go_today",
            "_action_go_calendar",
            "_action_calendar_window",
        ):
            action = getattr(self, action_name, None)
            if action is not None:
                action.setVisible(visible)

    def _set_vault(self, directory: str, vault_name: Optional[str] = None) -> bool:
        self.editor._push_paint_block()
        try:
            self._homebase_has_unsynced_local_changes = False
            self._homebase_unsynced_marked_at = None
            self._shutdown_homebase_sync()
            remote_ref_path = directory if self._remote_mode else None
            # Persist current history before switching away
            self._persist_recent_history()
            # Release any existing lock before switching vaults
            self._release_vault_lock()
            # Close any previous vault DB connection
            config.set_active_vault(None)
            # Persist history before clearing
            self._persist_recent_history()
            prefer_read_only = False
            if self._remote_mode:
                try:
                    cache_root = self._remote_vault_cache_root(remote_ref_path or directory)
                    cache_root.mkdir(parents=True, exist_ok=True)
                    config.set_active_vault(str(cache_root))
                    prefer_read_only = config.load_vault_force_read_only()
                except Exception:
                    prefer_read_only = False
            else:
                try:
                    config.set_active_vault(directory)
                    prefer_read_only = config.load_vault_force_read_only()
                except Exception:
                    prefer_read_only = False
            try:
                self._apply_feature_overrides()
            except Exception:
                pass
            try:
                ai_root = directory
                if self._remote_mode:
                    ai_root = str(self._remote_vault_cache_root(directory))
                self._ai_chat_store = AIChatStore(vault_root=ai_root)
                if self._ai_badge_icon is None:
                    ai_path = self._find_asset("ai.svg")
                    self._ai_badge_icon = self._load_icon(
                        ai_path,
                        theme_color("main_window.ai_badge.icon", "#4A90E2"),
                        size=14,
                    )
            except Exception:
                self._ai_chat_store = None
            if self._remote_mode:
                self._read_only = prefer_read_only
                self._vault_lock_path = None
                self._vault_lock_owner = None
                self._apply_read_only_state()
            else:
                if not self._check_and_acquire_vault_lock(directory, prefer_read_only=prefer_read_only):
                    return False
            self.right_panel.clear_tasks()
            try:
                resp = self.http.post("/api/vault/select", json={"path": directory})
                if resp.status_code == 401 and self._remote_mode:
                    if self._prompt_remote_login():
                        resp = self.http.post("/api/vault/select", json={"path": directory})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                self._alert_api_error(exc, "Failed to set vault")
                self._release_vault_lock()
                return False
            self.vault_root = resp.json().get("root")
            self._remote_vault_ref_path = remote_ref_path if self._remote_mode else None
            self.vault_root_name = Path(self.vault_root).name if self.vault_root else None
            if self._remote_mode:
                self._undo_cache_path = None
                self._undo_cache = {"schema_version": 1, "pages": {}, "order": []}
            else:
                self._init_persisted_undo_cache_for_vault()
            if self._remote_mode:
                if not self._ensure_remote_auth_for_vault():
                    self.statusBar().showMessage("Login required to access this vault.", 4000)
                    return False
            else:
                self._ensure_vault_root_page()
            self._update_remote_status_badge()
            index_dir_missing = False
            if self.vault_root and not self._remote_mode:
                index_dir = Path(self.vault_root) / ".stillpoint"
                if not index_dir.exists():
                    reply = QMessageBox.question(
                        self,
                        "No Vault Detected",
                        "No Vault Detected, Create new Index?",
                        QMessageBox.Yes | QMessageBox.No,
                        QMessageBox.Yes,
                    )
                    if reply != QMessageBox.Yes:
                        self.statusBar().showMessage("Vault open cancelled (no index).", 4000)
                        self.vault_root = None
                        self.vault_root_name = None
                        return False
                    index_dir_missing = True
            if self.vault_root:
                homebase_profile = None
                if not self._remote_mode:
                    homebase_profile = self._homebase_profile_for_path(self.vault_root)
                    if not homebase_profile:
                        homebase_profile = self._homebase_profile_for_path(directory)
                # Set active vault for both local and remote modes
                # For remote vaults, we cache the index locally so we still need the DB connection
                if self._remote_mode:
                    # For remote vaults, scope local metadata by selected remote vault path.
                    cache_root = self._remote_vault_cache_root(self._remote_vault_ref_path or directory)
                    cache_root.mkdir(parents=True, exist_ok=True)
                    config.set_active_vault(str(cache_root))
                else:
                    # For local vaults, use the vault directory itself
                    config.set_active_vault(self.vault_root)
                
                if self._remote_mode:
                    config.save_last_vault(
                        self._encode_remote_ref(self.api_base, self._remote_vault_ref_path or directory)
                    )
                else:
                    config.save_last_vault(self.vault_root)
                    if homebase_profile:
                        config.delete_known_vault(self.vault_root)
                    else:
                        display_name = vault_name or Path(self.vault_root).name
                        config.remember_vault(self.vault_root, display_name)
                if homebase_profile:
                    self._apply_homebase_profile(homebase_profile)
                else:
                    self._configure_homebase_sync_for_vault()
                try:
                    self.refresh_tree_button.setEnabled(True)
                except Exception:
                    pass
                try:
                    self.journal_tree_button.setEnabled(True)
                except Exception:
                    pass
                try:
                    self._show_journal_in_nav = config.load_show_journal()
                    blocker = QSignalBlocker(self.journal_tree_button)
                    self.journal_tree_button.setChecked(self._show_journal_in_nav)
                    del blocker
                except Exception:
                    pass
                try:
                    self.link_update_mode = config.load_link_update_mode()
                except Exception:
                    self.link_update_mode = "reindex"
                try:
                    self.update_links_on_index = config.load_update_links_on_index()
                except Exception:
                    self.update_links_on_index = True
                # Restore recent history (including cursor positions) for this vault
                self._restore_recent_history()
                try:
                    if config.load_vault_force_read_only():
                        # Respect per-vault read-only preference; release any lock we took.
                        self._release_vault_lock(reset_read_only=False)
                        self._read_only = True
                        self._apply_read_only_state()
                        # Intentionally no warning/toast; this is a user preference.
                except Exception:
                    pass
                # Respect globally persisted editor font size (not per-vault)
                self.font_size = config.load_global_editor_font_size(self.font_size)
                self.editor.set_font_point_size(self.font_size)
                self._refresh_editor_context(None)
                self._suspend_dirty_tracking = True
                try:
                    self.editor.set_markdown("")
                finally:
                    self._suspend_dirty_tracking = False
                    self._dirty_flag = False
                self._vi_initial_page_loaded = False
                if self._vi_enabled:
                    self._vi_enable_pending = True
                    self.editor.set_vi_mode_enabled(False)
                self.current_path = None
                self.right_panel.set_current_page(None, None)
                self.statusBar().showMessage(f"Vault: {self.vault_root}")
                self._update_window_title()
                self._restore_nav_filter_state()
                self._populate_vault_tree()

                # Check if index is empty and rebuild if needed
                needs_index = index_dir_missing or config.is_vault_index_empty()
                if needs_index:
                    self._reindex_vault(show_progress=True)

                self._load_bookmarks()
                if self.vault_root:
                    self.right_panel.set_vault_root(self._local_vault_root())
                    if self._remote_mode:
                        try:
                            self.right_panel.attachments_panel.set_remote_vault_root(self.vault_root)
                        except Exception:
                            pass

                # Restore window geometry and splitter positions
                self._restore_geometry()
                
                # Register this process's window for tray menu (cross-process)
                self._register_process_window()
                self._update_periodic_search_sync_timer()
                
                return True
        finally:
            try:
                self.editor._pop_paint_block()
            except Exception:
                pass

    def _add_bookmark(self) -> None:
        """Toggle the current page bookmark state."""
        self._toggle_bookmark_for_path(self.current_path)

    def _toggle_bookmark_for_path(self, path: Optional[str]) -> None:
        if not path:
            self.statusBar().showMessage("No page open to bookmark", 3000)
            return
        if path in self.bookmarks:
            self._remove_bookmark(path)
            return
        self.bookmarks.insert(0, path)
        config.save_bookmarks(self.bookmarks)
        self._refresh_bookmark_buttons()
        page_name = Path(path).stem
        self.statusBar().showMessage(f"Bookmarked: {page_name}", 3000)

    def _refresh_tree(self) -> None:
        """Manual refresh of the vault tree from the API.

        For local/homebase vaults, this also bumps tree version to invalidate
        server-side tree cache after out-of-band filesystem edits.
        """
        if not self._remote_mode:
            try:
                config.bump_tree_version()
            except Exception:
                pass
        # Clear all tree caches to force fresh reload
        self._tree_cache.clear()
        self._tree_path_version.clear()
        self._populate_vault_tree()
        if self._is_homebase_mode_enabled():
            self._trigger_homebase_sync_now("tree refresh")

    def _toggle_show_journal_in_nav(self, checked: bool) -> None:
        self._set_show_journal_in_nav(checked)

    def _is_journal_path(self, path: Optional[str]) -> bool:
        if not path:
            return False
        norm = path.strip().lower()
        if norm in ("journal", "/journal"):
            return True
        return norm.startswith("/journal/")

    def _is_journal_node(self, name: Optional[str], path: Optional[str]) -> bool:
        if name and name.strip().lower() == "journal":
            return True
        return self._is_journal_path(path)

    def _set_show_journal_in_nav(self, show: bool, select_path: Optional[str] = None) -> None:
        self._show_journal_in_nav = bool(show)
        try:
            blocker = QSignalBlocker(self.journal_tree_button)
            self.journal_tree_button.setChecked(self._show_journal_in_nav)
            del blocker
        except Exception:
            pass
        try:
            config.save_show_journal(self._show_journal_in_nav)
        except Exception:
            pass
        if not self._show_journal_in_nav and self._is_journal_path(self._nav_filter_path):
            self._nav_filter_path = None
            try:
                config.save_nav_filter_path(None)
            except Exception:
                pass
            self._sync_nav_filter_to_panels(None)
            self._apply_nav_filter_style()
        
        # When enabling journal view, set Journal page as pending selection
        if self._show_journal_in_nav and not select_path:
            select_path = "/Journal"
        
        if select_path:
            self._pending_selection = select_path
        
        # Clear all tree caches and expanded paths to force clean reload
        try:
            self._tree_cache.clear()
            self._tree_path_version.clear()
            # Clear expanded paths that might have stale "loading..." nodes
            self._expanded_paths.clear()
        except Exception:
            pass
        self._populate_vault_tree()
    
    def _load_bookmarks(self) -> None:
        """Load bookmarks from config and refresh display."""
        if not config.has_active_vault():
            return
        self.bookmarks = config.load_bookmarks()
        self._refresh_bookmark_buttons()

    def _restore_nav_filter_state(self) -> None:
        """Load the persisted navigation filter and apply it without rebuilding the tree."""
        try:
            persisted = config.load_nav_filter_path()
        except Exception:
            persisted = None
        if not persisted:
            self._nav_filter_path = None
            return
        normalized = self._file_path_to_folder(persisted if persisted.startswith("/") else f"/{persisted}")
        if self._is_journal_path(normalized) and not self._show_journal_in_nav:
            self._nav_filter_path = None
            try:
                config.save_nav_filter_path(None)
            except Exception:
                pass
            return
        self._nav_filter_path = normalized or "/"
        self._sync_nav_filter_to_panels(self._nav_filter_path)

    def _save_geometry(self) -> None:
        """Save window geometry and splitter positions."""
        if not config.has_active_vault():
            return
        
        # Save window geometry (size and position)
        geometry = self.saveGeometry().toBase64().data().decode('ascii')
        config.save_window_geometry(geometry)
        if _DETAILED_LOGGING:
            print(f"[Geometry] Saved window geometry: {len(geometry)} chars")
        # Persist history on close/resize save
        self._persist_recent_history()
        
        # Save main splitter state (tree vs editor+right panel)
        splitter_state = self.main_splitter.saveState().toBase64().data().decode('ascii')
        config.save_splitter_state(splitter_state)
        if _DETAILED_LOGGING:
            print(f"[Geometry] Saved main splitter state: {len(splitter_state)} chars")
        
        # Save editor splitter state (editor vs right panel)
        editor_splitter_state = self.editor_split.saveState().toBase64().data().decode('ascii')
        config.save_editor_splitter_state(editor_splitter_state)
        if _DETAILED_LOGGING:
            print(f"[Geometry] Saved editor splitter state: {len(editor_splitter_state)} chars")
        # Save panel visibility
        try:
            left_visible = self._is_left_panel_expanded()
            right_visible = self._is_right_panel_expanded()
            config.save_panel_visibility(left_visible, right_visible)
        except Exception:
            pass

    def _restore_geometry(self) -> None:
        """Restore window geometry and splitter positions."""
        if not config.has_active_vault():
            if _DETAILED_LOGGING:
                print("[Geometry] No active vault, skipping restore")
            return
        
        # Restore window geometry
        geometry_str = config.load_window_geometry()
        if geometry_str:
            if _DETAILED_LOGGING:
                print(f"[Geometry] Restoring window geometry: {len(geometry_str)} chars")
            from PySide6.QtCore import QByteArray
            geometry = QByteArray.fromBase64(geometry_str.encode('ascii'))
            result = self.restoreGeometry(geometry)
            if _DETAILED_LOGGING:
                print(f"[Geometry] Window geometry restore result: {result}")
        else:
            if _DETAILED_LOGGING:
                print("[Geometry] No saved window geometry found")
        
        # Restore main splitter state
        splitter_state_str = config.load_splitter_state()
        if splitter_state_str:
            if _DETAILED_LOGGING:
                print(f"[Geometry] Restoring main splitter state: {len(splitter_state_str)} chars")
            from PySide6.QtCore import QByteArray
            splitter_state = QByteArray.fromBase64(splitter_state_str.encode('ascii'))
            result = self.main_splitter.restoreState(splitter_state)
            if _DETAILED_LOGGING:
                print(f"[Geometry] Main splitter restore result: {result}")
        else:
            if _DETAILED_LOGGING:
                print("[Geometry] No saved main splitter state found")
        
        # Restore editor splitter state
        editor_splitter_state_str = config.load_editor_splitter_state()
        if editor_splitter_state_str:
            if _DETAILED_LOGGING:
                print(f"[Geometry] Restoring editor splitter state: {len(editor_splitter_state_str)} chars")
            from PySide6.QtCore import QByteArray
            editor_splitter_state = QByteArray.fromBase64(editor_splitter_state_str.encode('ascii'))
            result = self.editor_split.restoreState(editor_splitter_state)
            if _DETAILED_LOGGING:
                print(f"[Geometry] Editor splitter restore result: {result}")
        else:
            if _DETAILED_LOGGING:
                print("[Geometry] No saved editor splitter state found")
        
        # Restore panel visibility (overrides splitter sizes if hidden)
        vis = {}
        try:
            vis = config.load_panel_visibility() or {}
        except Exception:
            vis = {}
        left_visible = vis.get("left", True)
        right_visible = vis.get("right", True)
        try:
            if not left_visible:
                self._set_left_panel_collapsed(True)
            if not right_visible:
                self._set_right_panel_collapsed(True)
        except Exception:
            pass

    def _reset_view_layout(self) -> None:
        """Reset window geometry and splitter positions to defaults."""
        try:
            conn = config._get_conn()
            if conn:
                conn.execute(
                    "DELETE FROM kv WHERE key IN ('window_geometry','splitter_state','editor_splitter_state','panel_visibility')"
                )
                conn.commit()
        except Exception:
            pass
        # Apply sane default sizes and window state
        try:
            self.showNormal()
        except Exception:
            pass
        try:
            self.resize(1100, 720)
        except Exception:
            pass
        try:
            self.main_splitter.setSizes([240, max(500, self.width() - 260)])
            self.editor_split.setSizes([760, 320])
        except Exception:
            pass
        try:
            self._set_left_panel_collapsed(False)
            self._set_right_panel_collapsed(False)
        except Exception:
            pass
        self.statusBar().showMessage("View layout reset to defaults", 4000)

    def _on_splitter_moved(self, pos: int, index: int) -> None:
        """Save splitter positions when moved (debounced)."""
        self.geometry_save_timer.start()
        try:
            if self.right_panel:
                self.right_panel.notify_right_panel_resized()
        except Exception:
            pass

    def _refresh_bookmark_buttons(self) -> None:
        """Refresh the bookmark buttons in the toolbar."""
        self._clear_bookmark_drag_state()
        try:
            self.bookmark_scroll_area.horizontalScrollBar().setValue(0)
        except Exception:
            pass
        # Clear existing buttons
        for btn in list(self.bookmark_buttons.values()):
            self.bookmark_layout.removeWidget(btn)
            btn.deleteLater()
        self.bookmark_buttons.clear()
        
        # Add buttons for each bookmark
        for bookmark_path in self.bookmarks:
            # Extract leaf node name (page name)
            page_name = Path(bookmark_path).stem
            
            # Create button with drag-and-hold reorder support
            btn = BookmarkChickletButton(bookmark_path, page_name)
            btn.setToolTip(path_to_colon(bookmark_path) or bookmark_path)
            btn.clicked.connect(lambda checked=False, p=bookmark_path: self._open_bookmark(p))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(
                lambda pos, p=bookmark_path, b=btn: self._show_bookmark_context_menu(pos, p, b)
            )
            btn.dragStartRequested.connect(self._on_bookmark_drag_start)
            btn.dragMoveRequested.connect(self._on_bookmark_drag_move)
            btn.dragEndRequested.connect(self._on_bookmark_drag_end)
            
            # Store button in dict for later removal
            self.bookmark_buttons[bookmark_path] = btn
            
            # Add to layout
            self.bookmark_layout.addWidget(btn)
        self._update_bookmark_filter_highlights()
        self._update_bookmark_strip_width()
        self._sync_bookmark_scroll_range()
        self._update_bookmark_scroll_buttons()
        QTimer.singleShot(0, self._update_bookmark_strip_width)
        QTimer.singleShot(0, self._sync_bookmark_scroll_range)
        QTimer.singleShot(0, self._update_bookmark_scroll_buttons)

    def _scroll_bookmarks(self, delta: int) -> None:
        bar = self.bookmark_scroll_area.horizontalScrollBar()
        if bar.maximum() <= 0:
            if not getattr(self, "_bookmark_scroll_retry", False):
                self._bookmark_scroll_retry = True
                self._update_bookmark_strip_width()
                QTimer.singleShot(0, self._sync_bookmark_scroll_range)
                QTimer.singleShot(0, lambda: self._scroll_bookmarks(delta))
            return
        self._bookmark_scroll_retry = False
        step = bar.pageStep()
        if step <= 0:
            step = abs(delta)
        bar.setValue(max(0, min(bar.maximum(), bar.value() + (step if delta > 0 else -step))))
        self._update_bookmark_scroll_buttons()

    def _bookmark_matches_nav_filter(self, bookmark_path: str) -> bool:
        filter_path = getattr(self, "_nav_filter_path", None)
        if not filter_path or filter_path == "/":
            return False
        try:
            normalized_filter = self._normalize_tree_path(filter_path)
        except Exception:
            normalized_filter = filter_path.rstrip("/") or "/"
        try:
            bookmark_folder = self._file_path_to_folder(
                bookmark_path if bookmark_path.startswith("/") else f"/{bookmark_path}"
            )
        except Exception:
            bookmark_folder = bookmark_path
        try:
            normalized_bookmark = self._normalize_tree_path(bookmark_folder)
        except Exception:
            normalized_bookmark = bookmark_folder.rstrip("/") or "/"
        return normalized_bookmark == normalized_filter

    def _update_bookmark_filter_highlights(self) -> None:
        if not getattr(self, "bookmark_buttons", None):
            return
        for path, btn in self.bookmark_buttons.items():
            self._apply_bookmark_button_style(btn, path)

    def _apply_bookmark_button_style(self, btn: QPushButton, bookmark_path: str) -> None:
        is_active = bool(self.current_path and bookmark_path == self.current_path)
        is_filtered = self._bookmark_matches_nav_filter(bookmark_path)
        active_border = theme_value("main_window.bookmark.active_border", theme_value("main_window.focus_border.default", "#4A90E2"))
        active_bg = theme_value("main_window.bookmark.active_bg", "palette(highlight)")
        active_text = theme_value("main_window.bookmark.active_text", "palette(highlighted-text)")
        filtered_border = theme_value("main_window.bookmark.filtered_border", "#D9534F")
        filtered_bg = theme_value("main_window.bookmark.filtered_bg", filtered_border)
        filtered_text = theme_value("main_window.bookmark.filtered_text", "#ffffff")
        normal_border = theme_value("main_window.bookmark.normal_border", "#555555")
        border_color = filtered_border if is_filtered else (active_border if is_active else normal_border)
        style = (
            f"QPushButton {{ border: 1px solid {border_color}; "
            "padding: 2px 6px; border-radius: 3px;"
        )
        if is_filtered:
            style += f" background: {filtered_bg}; color: {filtered_text};"
        elif is_active:
            style += f" background: {active_bg}; color: {active_text};"
        style += " }"
        btn.setStyleSheet(style)

    def _apply_history_button_style(self, btn: QPushButton, history_path: str) -> None:
        is_active = bool(self.current_path and history_path == self.current_path)
        normal_border = theme_value("main_window.history.button_border", "#555555")
        active_border = theme_value("main_window.history.active_border", theme_value("main_window.focus_border.default", "#4A90E2"))
        active_bg = theme_value("main_window.history.active_bg", "palette(highlight)")
        active_text = theme_value("main_window.history.active_text", "palette(highlighted-text)")
        border_color = active_border if is_active else normal_border
        style = (
            f"QPushButton {{ border: 1px solid {border_color}; "
            "padding: 2px 6px; border-radius: 3px;"
        )
        if is_active:
            style += f" background: {active_bg}; color: {active_text};"
        style += " }"
        btn.setStyleSheet(style)

    def _update_active_page_chicklets(self) -> None:
        for path, btn in self.bookmark_buttons.items():
            self._apply_bookmark_button_style(btn, path)
        for btn in self.history_buttons:
            history_path = str(btn.property("history_path") or "")
            if history_path:
                self._apply_history_button_style(btn, history_path)

    def _update_bookmark_scroll_buttons(self) -> None:
        if not getattr(self, "bookmark_scroll_area", None):
            return
        bar = self.bookmark_scroll_area.horizontalScrollBar()
        content_width = 0
        try:
            content_width = self.bookmark_strip.sizeHint().width()
        except Exception:
            content_width = self.bookmark_strip.width()
        try:
            available_width = self.bookmark_scroll_area.viewport().width()
        except Exception:
            available_width = self.bookmark_scroll_area.width()
        has_overflow = content_width > max(0, available_width)
        self.bookmark_scroll_left.setVisible(has_overflow)
        self.bookmark_scroll_right.setVisible(has_overflow)
        if not has_overflow:
            bar.setValue(0)
            self.bookmark_scroll_left.setEnabled(False)
            self.bookmark_scroll_right.setEnabled(False)
            return
        self.bookmark_scroll_left.setEnabled(bar.value() > 0)
        self.bookmark_scroll_right.setEnabled(bar.value() < bar.maximum())

    def _bookmark_content_width(self) -> int:
        if not getattr(self, "bookmark_layout", None):
            return 0
        spacing = self.bookmark_layout.spacing()
        total = 0
        count = 0
        for btn in self.bookmark_buttons.values():
            try:
                total += btn.sizeHint().width()
                count += 1
            except Exception:
                continue
        if count > 1:
            total += spacing * (count - 1)
        return total

    def _update_bookmark_strip_width(self) -> None:
        if not getattr(self, "bookmark_strip", None):
            return
        try:
            width = max(1, self._bookmark_content_width())
            self.bookmark_strip.setMinimumWidth(width)
            self.bookmark_strip.setMaximumWidth(16777215)
            height = getattr(self, "_toolbar_height", None)
            if height:
                self.bookmark_strip.setFixedHeight(height)
                self.bookmark_strip.resize(width, height)
            else:
                self.bookmark_strip.resize(width, self.bookmark_strip.sizeHint().height())
            self.bookmark_strip.updateGeometry()
        except Exception:
            pass

    def _sync_bookmark_scroll_range(self) -> None:
        if not getattr(self, "bookmark_scroll_area", None):
            return
        bar = self.bookmark_scroll_area.horizontalScrollBar()
        content_width = self._bookmark_content_width()
        viewport_width = self.bookmark_scroll_area.viewport().width()
        max_range = max(0, content_width - viewport_width)
        bar.setRange(0, max_range)
        bar.setPageStep(max(0, viewport_width))
        if bar.value() > max_range:
            bar.setValue(max_range)

    def _ensure_bookmark_drag_divider(self) -> QFrame:
        if self._bookmark_drag_divider is None:
            divider = QFrame(self.bookmark_strip)
            divider.setFrameShape(QFrame.VLine)
            divider.setLineWidth(2)
            divider.setMidLineWidth(0)
            divider.setFixedWidth(3)
            divider.setStyleSheet(
                "QFrame { background: "
                f"{theme_value('main_window.bookmark.drag_divider', theme_value('main_window.focus_border.default', '#4A90E2'))}; "
                "border: none; margin: 2px 0px; }"
            )
            self._bookmark_drag_divider = divider
        return self._bookmark_drag_divider

    def _clear_bookmark_drag_state(self) -> None:
        self._bookmark_drag_source_path = None
        self._bookmark_drag_insert_index = None
        divider = self._bookmark_drag_divider
        if divider is not None:
            try:
                self.bookmark_layout.removeWidget(divider)
            except Exception:
                pass
            divider.hide()
        try:
            self.bookmark_strip.unsetCursor()
        except Exception:
            pass

    def _bookmark_drag_insert_pos(self, global_pos: QPoint) -> int:
        try:
            local = self.bookmark_strip.mapFromGlobal(global_pos)
            x = local.x()
        except Exception:
            return len(self.bookmarks)
        for idx, path in enumerate(self.bookmarks):
            btn = self.bookmark_buttons.get(path)
            if not btn or not btn.isVisible():
                continue
            mid_x = btn.geometry().x() + (btn.geometry().width() // 2)
            if x < mid_x:
                return idx
        return len(self.bookmarks)

    def _auto_scroll_bookmark_strip_during_drag(self, global_pos: QPoint) -> None:
        try:
            viewport_pos = self.bookmark_scroll_area.viewport().mapFromGlobal(global_pos)
        except Exception:
            return
        width = self.bookmark_scroll_area.viewport().width()
        if width <= 0:
            return
        bar = self.bookmark_scroll_area.horizontalScrollBar()
        if bar.maximum() <= 0:
            return
        edge_threshold = 28
        if viewport_pos.x() < edge_threshold:
            bar.setValue(max(0, bar.value() - 20))
        elif viewport_pos.x() > width - edge_threshold:
            bar.setValue(min(bar.maximum(), bar.value() + 20))
        self._update_bookmark_scroll_buttons()

    def _update_bookmark_drag_divider(self, global_pos: QPoint) -> None:
        if not self._bookmark_drag_source_path:
            return
        self._auto_scroll_bookmark_strip_during_drag(global_pos)
        insert_index = self._bookmark_drag_insert_pos(global_pos)
        self._bookmark_drag_insert_index = insert_index
        divider = self._ensure_bookmark_drag_divider()
        try:
            self.bookmark_layout.removeWidget(divider)
        except Exception:
            pass
        self.bookmark_layout.insertWidget(insert_index, divider)
        divider.show()

    def _on_bookmark_drag_start(self, bookmark_path: str) -> None:
        if bookmark_path not in self.bookmarks:
            return
        self._bookmark_drag_source_path = bookmark_path
        self._bookmark_drag_insert_index = self.bookmarks.index(bookmark_path)
        try:
            self.bookmark_strip.setCursor(Qt.ClosedHandCursor)
        except Exception:
            pass
        self._update_bookmark_drag_divider(QCursor.pos())

    def _on_bookmark_drag_move(self, bookmark_path: str, global_pos: QPoint) -> None:
        if bookmark_path != self._bookmark_drag_source_path:
            return
        self._update_bookmark_drag_divider(global_pos)

    def _on_bookmark_drag_end(self, bookmark_path: str, global_pos: QPoint) -> None:
        if bookmark_path != self._bookmark_drag_source_path:
            self._clear_bookmark_drag_state()
            return
        self._update_bookmark_drag_divider(global_pos)
        source_path = self._bookmark_drag_source_path
        insert_index = self._bookmark_drag_insert_index
        self._clear_bookmark_drag_state()
        if not source_path or insert_index is None or source_path not in self.bookmarks:
            return
        src_index = self.bookmarks.index(source_path)
        final_index = max(0, min(insert_index, len(self.bookmarks)))
        if final_index > src_index:
            final_index -= 1
        if final_index == src_index:
            return
        self.bookmarks.pop(src_index)
        self.bookmarks.insert(final_index, source_path)
        config.save_bookmarks(self.bookmarks)
        self._refresh_bookmark_buttons()
        page_name = Path(source_path).stem
        self.statusBar().showMessage(f"Reordered bookmark: {page_name}", 2000)

    def _refresh_history_buttons(self) -> None:
        """Refresh the history buttons in the toolbar (last 10 pages visited)."""
        # Clear existing buttons
        for btn in self.history_buttons:
            self.history_layout.removeWidget(btn)
            btn.deleteLater()
        self.history_buttons.clear()
        
        # Get last 25 items from history (most recent last)
        recent_history = self.page_history[-18:] if len(self.page_history) > 18 else self.page_history[:]

        # Remove calendar pages (e.g., /Journal/) from recent history
        filtered_history = [
            p
            for p in recent_history
            if self._is_history_path_allowed(p) and "/Journal/" not in p and "/journal/" not in p
        ]

        # Remove duplicates while preserving order (keep most recent occurrence)
        seen = set()
        unique_history = []
        for page_path in reversed(filtered_history):
            if page_path not in seen:
                seen.add(page_path)
                unique_history.append(page_path)
        unique_history.reverse()  # Restore original order (oldest to newest)

        # Add buttons for each history item
        for page_path in unique_history:
            # Extract page name
            page_name = Path(page_path).stem

            # Create button with border styling
            btn = QPushButton(page_name)
            btn.setProperty("history_path", page_path)
            self._apply_history_button_style(btn, page_path)
            btn.setToolTip(path_to_colon(page_path) or page_path)
            btn.clicked.connect(lambda checked=False, p=page_path: self._open_history_page(p))
            btn.setContextMenuPolicy(Qt.CustomContextMenu)
            btn.customContextMenuRequested.connect(lambda pos, p=page_path, b=btn: self._show_history_context_menu(pos, p, b))

            # Store button
            self.history_buttons.append(btn)

            # Add to layout
            self.history_layout.addWidget(btn)
        self._update_active_page_chicklets()

    def _open_history_page(self, page_path: str) -> None:
        """Open a page from history without updating tree selection."""
        self._remember_history_cursor()
        self._open_file(page_path, add_to_history=False, restore_history_cursor=True)  # Don't add to history again

    def _show_history_context_menu(self, pos: QPoint, page_path: str, button: QWidget) -> None:
        """Show context menu for a history button."""
        menu = QMenu(self)
        open_win = menu.addAction("Open in Editor Window")
        open_win.triggered.connect(lambda: self._open_page_editor_window(page_path))
        global_pos = button.mapToGlobal(pos)
        menu.exec(global_pos)

    def _auto_load_initial_file(self) -> None:
        """Auto-load the last opened file or vault home page on startup."""
        if not self.vault_root or not self.vault_root_name:
            return
        
        # Try to load the last opened file
        last_file = config.load_last_file()
        if last_file:
            # Verify the file still exists
            try:
                if self._remote_mode:
                    # For remote vaults, check via API
                    if self._page_exists(last_file):
                        self._open_file(last_file)
                        return
                else:
                    # For local vaults, check filesystem
                    abs_path = Path(self.vault_root) / last_file.lstrip("/")
                    if abs_path.exists():
                        self._open_file(last_file)
                        return
            except Exception:
                pass
        
        # Fall back to vault home page, but for remote vaults, check if it exists first
        if self._remote_mode:
            home_path = self._home_page_path()
            if home_path and self._page_exists(home_path):
                try:
                    self._open_file(home_path)
                except Exception:
                    # Silently fail for remote vaults - the vault might be empty
                    pass
        else:
            # For local vaults, always try to go home (will create if needed)
            self._go_home()
    
    def _page_exists(self, rel_path: str) -> bool:
        if not rel_path:
            return False
        if self.vault_root and not self._remote_mode:
            return (Path(self.vault_root) / rel_path.lstrip("/")).exists()
        try:
            return config.page_exists(rel_path)
        except Exception:
            return False

    def _vault_root_page_path(self) -> Optional[str]:
        if not self.vault_root_name:
            return None
        return f"/{self.vault_root_name}/{self.vault_root_name}{PAGE_SUFFIX}"

    def _normalize_root_page_path(self, path: str) -> str:
        if not path or not self.vault_root_name:
            return path
        cleaned = path.strip()
        if not cleaned:
            return cleaned
        if not cleaned.startswith("/"):
            cleaned = "/" + cleaned.lstrip("/")
        root_file = f"/{self.vault_root_name}{PAGE_SUFFIX}"
        root_folder_file = f"/{self.vault_root_name}/{self.vault_root_name}{PAGE_SUFFIX}"
        if cleaned.rstrip("/") in (f"/{self.vault_root_name}", root_file):
            return root_folder_file
        return cleaned

    def _home_page_path(self) -> Optional[str]:
        if not self.vault_root_name:
            return None
        home_path = None
        try:
            home_path = config.get_home_page_path()
        except Exception:
            home_path = None
        if home_path and self._page_exists(home_path):
            return self._normalize_root_page_path(home_path)
        try:
            candidate = self._first_tree_page_path()
            if candidate:
                return self._normalize_root_page_path(candidate)
        except Exception:
            pass
        return self._vault_root_page_path()

    def _first_tree_page_path(self) -> Optional[str]:
        model = self.tree_model
        if not model:
            return None
        root = model.invisibleRootItem()

        def scan(item: QStandardItem) -> Optional[str]:
            if item.data(PATH_ROLE) == FILTER_BANNER:
                return None
            candidate = item.data(OPEN_ROLE) or item.data(PATH_ROLE)
            if candidate and candidate != "/":
                return str(candidate)
            for row in range(item.rowCount()):
                child = item.child(row)
                found = scan(child)
                if found:
                    return found
            return None

        for row in range(root.rowCount()):
            child = root.child(row)
            found = scan(child)
            if found:
                return found
        return None

    def _go_home(self) -> None:
        """Navigate to the vault's home page (display position 0)."""
        if not self.vault_root or not self.vault_root_name:
            self.statusBar().showMessage("No vault selected", 3000)
            return

        # If navigation is filtered, go to the top of the filtered tree instead.
        if self._nav_filter_path and self._nav_filter_path != "/":
            filtered_path = None
            try:
                root_item = self._find_item(self.tree_model.invisibleRootItem(), self._nav_filter_path)
                if root_item:
                    filtered_path = root_item.data(OPEN_ROLE) or root_item.data(PATH_ROLE)
            except Exception:
                filtered_path = None
            if not filtered_path:
                filtered_path = self._first_tree_page_path()
            if not filtered_path:
                display = path_to_colon(self._nav_filter_path) or self._nav_filter_path
                self.statusBar().showMessage(f"No pages under filter: {display}", 3000)
                return
            self.tree_view.clearSelection()
            self._open_file(filtered_path)
            display = path_to_colon(filtered_path) or self.vault_root_name
            self.statusBar().showMessage(f"Filtered Home: {display}", 2000)
            return

        home_path = self._home_page_path()
        if not home_path:
            self.statusBar().showMessage("No home page found", 3000)
            return

        # Clear tree selection
        self.tree_view.clearSelection()

        # Open the home page
        self._open_file(home_path)
        display = path_to_colon(home_path) or self.vault_root_name
        self.statusBar().showMessage(f"Home: {display}", 2000)

    def _open_bookmark(self, path: str) -> None:
        """Open a bookmarked page."""
        self._open_file(path)

    def _show_bookmark_context_menu(self, pos: QPoint, bookmark_path: str, button: QWidget) -> None:
        """Show context menu for bookmark with Remove option."""
        menu = QMenu(self)
        open_win = menu.addAction("Open in Editor Window")
        open_win.triggered.connect(lambda: self._open_page_editor_window(bookmark_path))
        search_action = menu.addAction("Search From Here...")
        search_action.triggered.connect(lambda: self._search_from_folder(bookmark_path))
        filter_action = menu.addAction("Filter nav from here")
        filter_action.triggered.connect(lambda: self._set_nav_filter(bookmark_path))
        menu.addSeparator()
        remove_action = menu.addAction("Remove")
        remove_action.triggered.connect(lambda: self._remove_bookmark(bookmark_path))
        
        # Show menu at global position relative to button
        global_pos = button.mapToGlobal(pos)
        menu.exec(global_pos)

    def _remove_bookmark(self, path: str) -> None:
        """Remove a bookmark from the list."""
        if path in self.bookmarks:
            self.bookmarks.remove(path)
            config.save_bookmarks(self.bookmarks)
            self._refresh_bookmark_buttons()
            
            page_name = Path(path).stem
            self.statusBar().showMessage(f"Removed bookmark: {page_name}", 3000)

    def _set_nav_filter(self, path: str) -> None:
        """Enable tree filter for the given folder path."""
        if not path:
            return
        normalized = self._file_path_to_folder(path if path.startswith("/") else f"/{path}")
        if self._is_journal_path(normalized) and not self._show_journal_in_nav:
            logNav(f"_set_nav_filter: ignoring Journal path {normalized}")
            self._nav_filter_path = None
            try:
                config.save_nav_filter_path(None)
            except Exception:
                pass
            self._apply_nav_filter_style()
            return
        self._nav_filter_path = normalized or "/"
        logNav(f"_set_nav_filter: filtered to {self._nav_filter_path}")
        try:
            config.save_nav_filter_path(self._nav_filter_path)
        except Exception:
            pass
        self._sync_nav_filter_to_panels(self._nav_filter_path)
        self._populate_vault_tree()
        try:
            self.tree_view.expandToDepth(1)
        except Exception:
            pass
        self._apply_nav_filter_style()

    def _clear_nav_filter(self) -> None:
        """Disable tree filter and restore full view."""
        if not self._nav_filter_path:
            # Still collapse on escape even if no filter is active
            self.tree_view.collapseAll()
            return
        logNav(f"_clear_nav_filter: restoring full tree view")
        self._nav_filter_path = None
        try:
            config.save_nav_filter_path(None)
        except Exception:
            pass
        self._sync_nav_filter_to_panels(None)
        self._populate_vault_tree()
        self.tree_view.collapseAll()
        self._apply_nav_filter_style()

    def _apply_nav_filter_style(self) -> None:
        """Refresh focus borders to reflect filter state."""
        self._apply_focus_borders()
        self._update_filter_indicator()
        self._update_bookmark_filter_highlights()

    def _sync_nav_filter_to_panels(self, filter_path: Optional[str]) -> None:
        filter_label = path_to_colon(filter_path) if filter_path else None
        try:
            if self.right_panel.task_panel:
                self.right_panel.task_panel.set_navigation_filter(filter_path, refresh=False)
        except Exception:
            pass
        try:
            if self.right_panel.link_panel:
                self.right_panel.link_panel.set_navigation_filter(filter_path, refresh=False)
        except Exception:
            pass
        try:
            if self.search_tab:
                self.search_tab.set_navigation_filter(filter_path, filter_label, self._clear_nav_filter)
        except Exception:
            pass
        try:
            if self.tags_tab and hasattr(self.tags_tab, "set_navigation_filter"):
                self.tags_tab.set_navigation_filter(filter_path, filter_label, self._clear_nav_filter)
        except Exception:
            pass
        for panel in list(getattr(self, "_detached_link_panels", [])):
            try:
                panel.set_navigation_filter(filter_path, refresh=False)
            except Exception:
                pass
        self._sync_detached_task_filters(filter_path)

    def _sync_detached_task_filters(self, filter_path: Optional[str]) -> None:
        """Ensure detached task windows stay in sync with navigation filtering."""
        for window in list(getattr(self, "_detached_panels", [])):
            if window.windowTitle() != "Tasks":
                continue
            panel = window.centralWidget()
            if not hasattr(panel, "set_navigation_filter"):
                continue
            try:
                panel.set_navigation_filter(filter_path, refresh=False)
            except Exception:
                pass

    def _sanitize_find_query(self, text: Optional[str]) -> str:
        """Strip control/sentinel characters from seeded find queries."""
        if not text:
            return ""
        cleaned = text.replace("\u2029", "\n")
        try:
            cleaned = re.sub(r"[\x00-\x1F\x7F]", "", cleaned)
            cleaned = re.sub(r"[\uE000-\uF8FF]", "", cleaned)  # strip private-use sentinels (e.g., headings)
        except Exception:
            pass
        return cleaned.strip()

    def _resolve_template_path(self, name: str, fallback: str) -> Path:
        """Return a template path by stem, falling back if missing."""
        templates_root = Path(__file__).parent.parent.parent / "templates"
        user_templates = Path.home() / ".stillpoint" / "templates"
        candidates = [
            user_templates / f"{(name or '').strip()}.txt",
            templates_root / f"{(name or '').strip()}.txt",
            user_templates / f"{fallback}.txt",
            templates_root / f"{fallback}.txt",
        ]
        for cand in candidates:
            if cand.exists():
                return cand
        return templates_root / f"{fallback}.txt"

    def _cursor_at_position(self, pos: int) -> QTextCursor:
        """Return a cursor clamped to the document length."""
        cursor = self.editor.textCursor()
        try:
            length = cursor.document().characterCount()
        except Exception:
            length = len(self.editor.toPlainText())
        safe_max = max(0, length - 1)
        cursor.setPosition(max(0, min(pos, safe_max)))
        return cursor

    def _show_heading_picker_popup(self, global_pos, prefer_above: bool = False) -> None:
        """Show a filterable heading picker near the cursor (vi 't')."""
        headings = self._toc_headings or []
        if not headings:
            return
        # Dispose any existing picker
        if hasattr(self, "_heading_picker") and self._heading_picker:
            try:
                self._heading_picker.close()
            except Exception:
                pass
        # Pause autosave while picker is active to avoid API writes on focus shuffle
        self._heading_picker_active = True
        self._heading_picker_autosave_active = self.autosave_timer.isActive()
        self.autosave_timer.stop()
        popup = QWidget(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setStyleSheet(
            "QWidget { background: "
            f"{theme_value('main_window.picker_popup.bg', 'rgba(32,32,32,240)')}; "
            "border: 1px solid "
            f"{theme_value('main_window.picker_popup.border', '#666666')}; "
            "border-radius: 6px; }"
            "QLineEdit { border: 1px solid "
            f"{theme_value('main_window.picker_popup.input_border', '#777777')}; "
            "border-radius: 4px; padding: 4px 6px; }"
            "QListWidget { background: transparent; color: "
            f"{theme_value('main_window.picker_popup.list_text', '#f5f5f5')}; "
            "border: none; }}"
            "QListWidget::item { padding: 4px 6px; }"
            "QListWidget::item:selected { background: "
            f"{theme_value('main_window.picker_popup.list_selected_bg', 'rgba(90,161,255,80)')}; }}"
        )
        layout = QVBoxLayout(popup)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        filter_edit = QLineEdit(popup)
        filter_edit.setPlaceholderText("Filter headings…")
        list_widget = QListWidget(popup)
        layout.addWidget(filter_edit)
        layout.addWidget(list_widget, 1)

        def populate(query: str = "") -> None:
            list_widget.clear()
            needle = query.lower().strip()
            for h in headings:
                title = h.get("title") or "(heading)"
                if needle and needle not in title.lower():
                    continue
                line = h.get("line", 1)
                level = max(1, min(5, int(h.get("level", 1))))
                indent = "    " * (level - 1)
                item = QListWidgetItem(f"{indent}{title}  (line {line})")
                item.setData(Qt.UserRole, h)
                list_widget.addItem(item)
            if list_widget.count():
                list_widget.setCurrentRow(0)

        def finish_picker() -> None:
            self._heading_picker_active = False
            if getattr(self, "_heading_picker_autosave_active", False) and not self._read_only:
                try:
                    self.autosave_timer.start()
                except Exception:
                    pass
            self._heading_picker_autosave_active = False

        def activate_current() -> None:
            item = list_widget.currentItem()
            if not item:
                finish_picker()
                popup.close()
                return
            data = item.data(Qt.UserRole) or {}
            try:
                pos = int(data.get("position", 0))
            except Exception:
                pos = 0
            cursor = self._cursor_at_position(max(0, pos))
            self._animate_or_flash_to_cursor(cursor)
            finish_picker()
            popup.close()
            QTimer.singleShot(0, lambda: self.editor.setFocus(Qt.OtherFocusReason))

        filter_edit.textChanged.connect(populate)
        list_widget.itemDoubleClicked.connect(lambda *_: activate_current())
        list_widget.itemActivated.connect(lambda *_: activate_current())
        popup.destroyed.connect(lambda *_: finish_picker())

        editor_ref = self.editor

        class _PickerFilter(QObject):
            def eventFilter(self, obj, ev):  # type: ignore[override]
                if ev.type() == QEvent.KeyPress:
                    if ev.key() in (Qt.Key_Return, Qt.Key_Enter):
                        activate_current()
                        return True
                    if ev.key() == Qt.Key_J and ev.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                        row = list_widget.currentRow()
                        if list_widget.count():
                            list_widget.setCurrentRow(min(list_widget.count() - 1, row + 1))
                        return True
                    if ev.key() == Qt.Key_K and ev.modifiers() == (Qt.ControlModifier | Qt.ShiftModifier):
                        row = list_widget.currentRow()
                        if list_widget.count():
                            list_widget.setCurrentRow(max(0, row - 1))
                        return True
                    if ev.key() == Qt.Key_Escape:
                        finish_picker()
                        popup.close()
                        if editor_ref:
                            QTimer.singleShot(0, lambda: editor_ref.setFocus(Qt.OtherFocusReason))
                        return True
                return False

        filt = _PickerFilter(popup)
        filter_edit.installEventFilter(filt)
        list_widget.installEventFilter(filt)
        populate("")

        # Position near cursor, above or below based on preference and space
        popup.resize(360, min(320, max(160, list_widget.sizeHintForRow(0) * min(8, list_widget.count()) + 64)))
        screen = QApplication.primaryScreen().availableGeometry()
        size = popup.size()
        x = max(screen.x(), min(global_pos.x(), screen.x() + screen.width() - size.width()))
        if prefer_above:
            y = global_pos.y() - size.height() - 8
            if y < screen.y():
                y = global_pos.y() + 12
        else:
            y = global_pos.y() + 12
            if y + size.height() > screen.y() + screen.height():
                y = global_pos.y() - size.height() - 8
        popup.move(x, y)
        popup.show()
        popup.raise_()
        filter_edit.setFocus()
        self._heading_picker = popup

    def _save_panel_visibility(self) -> None:
        """Persist current left/right panel visibility to config."""
        try:
            left_visible = self._is_left_panel_expanded()
            right_visible = self._is_right_panel_expanded()
            config.save_panel_visibility(left_visible, right_visible)
        except Exception:
            pass

    def _save_expanded_state(self) -> None:
        """Save currently expanded tree paths."""
        model = self.tree_model
        if not model:
            return
        
        before_count = len(self._expanded_paths)
        
        def walk_tree(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                if self.tree_view.isExpanded(idx):
                    item = model.itemFromIndex(idx)
                    if item:
                        path = self._normalize_tree_path(item.data(PATH_ROLE))
                        if path:
                            self._expanded_paths.add(path)
                walk_tree(idx)
        
        walk_tree(QModelIndex())
        logNav(f"_save_expanded_state: saved {len(self._expanded_paths)} paths (was {before_count})")

    def _restore_expanded_state(self) -> None:
        """Restore previously expanded tree paths, expanding parents before children."""
        if not self._expanded_paths:
            return
        
        model = self.tree_model
        if not model:
            return
        
        # Build a map of all items by path
        path_to_index = {}
        
        def build_map(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                item = model.itemFromIndex(idx)
                if item:
                    path = self._normalize_tree_path(item.data(PATH_ROLE))
                    if path:
                        path_to_index[path] = idx
                build_map(idx)
        
        build_map(QModelIndex())
        
        # Sort paths by depth (parent folders before children) to ensure proper expansion order
        sorted_paths = sorted(self._expanded_paths, key=lambda p: p.count('/'))
        
        restored_count = 0
        
        for path in sorted_paths:
            idx = path_to_index.get(path)
            if idx and idx.isValid():
                if not self.tree_view.isExpanded(idx):
                    # Don't block signals - let expand trigger child loading
                    self.tree_view.expand(idx)
                    restored_count += 1
        
        if restored_count > 0:
            logNav(f"_restore_expanded_state: restored {restored_count} of {len(self._expanded_paths)} paths")

    def _count_folders_in_vault(self) -> int:
        """Count total number of folders in vault for lazy loading decision."""
        try:
            resp = self.http.get("/api/vault/stats")
            resp.raise_for_status()
            data = resp.json()
            count = data.get("folder_count", 0)
            _log_navigation(f"{_ANSI_BLUE}[TREE] Folder count: {count}{_ANSI_RESET}")
            return count
        except Exception as exc:
            _log_navigation(f"{_ANSI_BLUE}[TREE] Failed to get folder count: {exc}{_ANSI_RESET}")
            return 0

    def _populate_vault_tree(self) -> None:
        self._cancel_inline_editor()
        if not self.vault_root:
            return
        # Prevent overlapping resets that can confuse the model/view
        if self._tree_refresh_in_progress:
            self._pending_tree_refresh = True
            return
        self._tree_refresh_in_progress = True
        
        # Decide lazy vs full loading based on vault size
        folder_count = self._count_folders_in_vault()
        self._use_lazy_loading = folder_count >= TREE_LAZY_LOAD_THRESHOLD
        _log_navigation(
            f"{_ANSI_BLUE}[TREE] Vault has {folder_count} folders, using {'LAZY' if self._use_lazy_loading else 'FULL'} loading{_ANSI_RESET}"
        )
        
        nav_root = self._nav_filter_path or "/"
        fetch_path = "/" if (self._is_journal_path(nav_root) and not self._show_journal_in_nav) else nav_root
        selection_model = self.tree_view.selectionModel()
        selection_blocker = QSignalBlocker(selection_model) if selection_model else None
        self.tree_view.setUpdatesEnabled(False)
        try:
            try:
                # Use recursive loading for small vaults, lazy for large
                recursive_param = "false" if self._use_lazy_loading else "true"
                _log_navigation(f"{_ANSI_BLUE}[TREE] Fetching tree with recursive={recursive_param}{_ANSI_RESET}")
                resp = self.http.get(
                    "/api/vault/tree",
                    params={
                        "path": fetch_path,
                        "recursive": recursive_param,
                        "include_journal": "true" if self._show_journal_in_nav else "false",
                    },
                )
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                self._alert_api_error(exc, "Failed to load vault tree")
                return
            payload = resp.json()
            data = payload.get("tree", [])
            try:
                old_version = self._tree_version
                self._tree_version = int(payload.get("version", self._tree_version) or 0)
                logNav(f"_populate_vault_tree: version {old_version} -> {self._tree_version} (path={fetch_path})")
            except Exception:
                pass
            self._tree_cache.clear()
            try:
                self._tree_path_version[fetch_path] = self._tree_version
                logNav(f"_populate_vault_tree: cached path {fetch_path} at version {self._tree_version}")
            except Exception:
                pass

            # Clear existing items and rebuild
            self.tree_model.clear()
            self.tree_model.setHorizontalHeaderLabels(["Vault"])
            seen_paths: set[str] = set()

            # No synthetic root - just show actual folders directly
            if self._nav_filter_path:
                banner = QStandardItem("Filtered")
                font = banner.font()
                font.setBold(True)
                banner.setFont(font)
                banner.setEditable(False)
                banner.setForeground(QBrush(theme_color("main_window.banner.text", "#ffffff")))
                banner.setBackground(QBrush(theme_color("main_window.banner.bg", "#c62828")))
                display_path = path_to_colon(self._nav_filter_path) or self._nav_filter_path
                if display_path:
                    banner.setToolTip(f"{display_path} (click to clear)")
                banner.setData(FILTER_BANNER, PATH_ROLE)
                self.tree_model.invisibleRootItem().appendRow(banner)
    
            self._full_tree_data = data
            filtered_data = data
            if self._nav_filter_path and (self._show_journal_in_nav or not self._is_journal_path(self._nav_filter_path)):
                filtered_data = self._filter_tree_data(data, self._nav_filter_path)

            for node in filtered_data:
                # Show actual folders directly at root level
                if node.get("path") == "/":
                    self._cache_children(node)
                    for child in node.get("children", []):
                        if not self._show_journal_in_nav and self._is_journal_node(child.get("name"), child.get("path")):
                            continue
                        self._add_tree_node(self.tree_model.invisibleRootItem(), child, seen_paths)
                else:
                    if not self._show_journal_in_nav and self._is_journal_node(node.get("name"), node.get("path")):
                        continue
                    self._cache_children(node)
                    self._add_tree_node(self.tree_model.invisibleRootItem(), node, seen_paths)
        finally:
            self._tree_refresh_in_progress = False
            if self._pending_tree_refresh:
                self._pending_tree_refresh = False
                QTimer.singleShot(0, self._populate_vault_tree)
            self.tree_view.setUpdatesEnabled(True)
            if selection_blocker:
                del selection_blocker
        
        # Restore previously expanded paths
        self._restore_expanded_state()
        
        if self._pending_selection:
            # Defer selection to next event loop iteration to ensure tree is fully rendered
            selection_path = self._pending_selection
            self._pending_selection = None
            QTimer.singleShot(0, lambda: self._deferred_select_tree_path(selection_path))
        self.right_panel.refresh_tasks()
        self.right_panel.refresh_calendar()
        if self.tags_tab:
            self.tags_tab.refresh_tags()
        self._apply_nav_filter_style()

    def _add_tree_node(self, parent: QStandardItem, node: dict, seen: Optional[set[str]] = None) -> QStandardItem:
        item = QStandardItem(node.get("name") or "")
        folder_path = node.get("path")
        open_path = node.get("open_path")
        children = node.get("children") or []
        has_children = node.get("has_children")
        if has_children is None:
            has_children = bool(children)
        key = open_path or folder_path
        if seen is not None and key:
            if key in seen:
                return item
            seen.add(key)
        item.setData(folder_path, PATH_ROLE)
        item.setData(bool(has_children), TYPE_ROLE)
        item.setData(open_path, OPEN_ROLE)
        item.setIcon(QIcon())
        item.setEditable(False)
        # Only enable drag, not drop on items - drops are handled by the view's dropEvent
        item.setFlags(item.flags() | Qt.ItemIsDragEnabled)
        
        # Check if this is a virtual (unsaved) page
        if open_path and open_path in self.virtual_pages:
            font = item.font()
            font.setItalic(True)
            item.setFont(font)
        
        parent.appendRow(item)
        if children:
            for child in children:
                self._add_tree_node(item, child, seen)
        elif has_children:
            # placeholder to show the expand arrow; real children loaded on demand
            placeholder = QStandardItem("loading…")
            placeholder.setEnabled(False)
            item.appendRow(placeholder)
        return item

    def _filter_tree_data(self, nodes: list[dict], prefix: str) -> list[dict]:
        """Return a pruned copy of the vault tree limited to prefix and its descendants."""
        result: list[dict] = []
        for node in nodes:
            path = node.get("path") or ""
            children = node.get("children", [])
            filtered_children = self._filter_tree_data(children, prefix)
            if prefix == "/":
                include_as_node = True
            else:
                include_as_node = path and path.startswith(prefix)
            if include_as_node:
                clone = dict(node)
                clone["children"] = filtered_children
                result.append(clone)
            elif filtered_children:
                result.extend(filtered_children)
        return result

    def _cache_children(self, node: dict) -> None:
        path = self._normalize_tree_path(node.get("path"))
        children = node.get("children") or []
        has_children = bool(node.get("has_children")) or bool(children)
        # Only cache populated children; if we know it has children but none are loaded yet, skip cache entry
        if path and children:
            self._tree_cache[path] = list(children)
        for child in children:
            self._cache_children(child)

    @staticmethod
    def _normalize_tree_path(path: Optional[str]) -> str:
        if not path or path == "/":
            return "/"
        return path.rstrip("/") or "/"

    def _on_tree_expanded(self, index: QModelIndex) -> None:
        """Lazy-load children when a node is expanded."""
        item = self.tree_model.itemFromIndex(index)
        if not item:
            return
        path = self._normalize_tree_path(item.data(PATH_ROLE))
        if not path:
            return
        # Save expansion state
        if path:
            self._expanded_paths.add(path)
            self._debug(f"[EXPAND] Added to expanded paths: {path} (total: {len(self._expanded_paths)})")
        
        # If already populated and not just a placeholder, skip child loading
        if path in self._tree_cache and item.rowCount() > 0 and not (
            item.rowCount() == 1 and not item.child(0).isEnabled()
        ):
            return
        self._load_children_for_path(item, path)

    def _on_tree_collapsed(self, index: QModelIndex) -> None:
        """Remove collapsed paths from expansion state."""
        item = self.tree_model.itemFromIndex(index)
        if not item:
            return
        path = self._normalize_tree_path(item.data(PATH_ROLE))
        if path:
            self._expanded_paths.discard(path)
            self._suppress_nav_sync_path = path
            self._debug(f"[COLLAPSE] Removed from expanded paths: {path} (total: {len(self._expanded_paths)})")

    def _load_children_for_path(self, item: QStandardItem, path: str) -> None:
        """Fetch children for a path (cached, then API) and populate the node."""
        # Skip lazy loading if full tree was loaded
        if not self._use_lazy_loading:
            _log_navigation(f"{_ANSI_BLUE}[TREE] _load_children_for_path: skipping (full tree already loaded){_ANSI_RESET}")
            return
            
        if not self._show_journal_in_nav and self._is_journal_path(path):
            logNav(f"_load_children_for_path: skipping Journal path {path}")
            item.removeRows(0, item.rowCount())
            return
        norm_path = self._normalize_tree_path(path)
        children = self._tree_cache.get(norm_path)
        cached_ver = self._tree_path_version.get(norm_path)
        has_children_flag = bool(item.data(TYPE_ROLE))
        if children is None or cached_ver != self._tree_version or (not children and has_children_flag):
            reason = "not cached" if children is None else "version mismatch" if cached_ver != self._tree_version else "empty but has children"
            logNav(f"_load_children_for_path: fetching {norm_path} ({reason})")
            
            # Show status and busy cursor
            self.statusBar().showMessage(f"Loading folder tree...")
            QApplication.setOverrideCursor(Qt.WaitCursor)
            
            try:
                resp = self.http.get(
                    "/api/vault/tree",
                    params={
                        "path": norm_path,
                        "recursive": "false",
                        "include_journal": "true" if self._show_journal_in_nav else "false",
                    },
                )
                resp.raise_for_status()
                payload = resp.json()
                try:
                    old_version = self._tree_version
                    self._tree_version = int(payload.get("version", self._tree_version) or 0)
                    if old_version != self._tree_version:
                        logNav(f"_load_children_for_path: version bump {old_version} -> {self._tree_version}")
                except Exception:
                    pass
                tree = payload.get("tree") or []
                if tree:
                    children = tree[0].get("children") or []
                    if children:
                        self._tree_cache[norm_path] = list(children)
                        try:
                            self._tree_path_version[norm_path] = self._tree_version
                            logNav(f"_load_children_for_path: cached {norm_path} with {len(children)} children at version {self._tree_version}")
                        except Exception:
                            pass
            except httpx.HTTPError as e:
                logNav(f"_load_children_for_path: API error for {norm_path}: {e}")
                return
            finally:
                # Clear status and cursor
                QApplication.restoreOverrideCursor()
                self.statusBar().clearMessage()
                
        if children is None:
            return
        # Clear placeholders
        item.removeRows(0, item.rowCount())
        seen: set[str] = set()
        for child in children:
            self._add_tree_node(item, child, seen)
        
        # Restore expanded state for newly added children
        for row in range(item.rowCount()):
            child_index = item.child(row).index()
            child_path = self._normalize_tree_path(item.child(row).data(PATH_ROLE))
            if child_path and child_path in self._expanded_paths:
                self.tree_view.expand(child_index)

    def _ensure_tree_path_loaded(self, target_path: str) -> None:
        """Ensure the tree has loaded nodes along the target path."""
        if not target_path:
            return
        if not self._use_lazy_loading:
            if not self._find_item(self.tree_model.invisibleRootItem(), target_path):
                if getattr(self, "_selection_retry_path", None) != target_path:
                    self._selection_retry_path = target_path
                    self._pending_selection = target_path
                    self._populate_vault_tree()
            return
        folder_path = self._file_path_to_folder(target_path) or "/"
        parts = [p for p in folder_path.strip("/").split("/") if p]
        current_path = "/"
        # Prefer the active filter root or synthetic root if present
        root_lookup = self._nav_filter_path or "/"
        root_item = (
            self._find_item(self.tree_model.invisibleRootItem(), root_lookup)
            or self._find_item(self.tree_model.invisibleRootItem(), "/")
            or self.tree_model.invisibleRootItem()
        )
        if self._nav_filter_path and self._nav_filter_path != "/":
            current_path = self._nav_filter_path
            prefix_parts = [p for p in current_path.strip("/").split("/") if p]
            # Skip already-included parts in traversal
            parts = parts[len(prefix_parts):]
        parent_item = root_item
        # Load root children
        self._load_children_for_path(parent_item, current_path)
        try:
            self.tree_view.expand(parent_item.index())
        except Exception:
            pass
        for part in parts:
            next_path = f"{current_path.rstrip('/')}/{part}" if current_path != "/" else f"/{part}"
            self._load_children_for_path(parent_item, current_path)
            child = self._find_item(parent_item, next_path)
            if not child:
                # Attempt global search as fallback
                child = self._find_item(self.tree_model.invisibleRootItem(), next_path)
            if not child:
                break
            parent_item = child
            current_path = next_path
            try:
                self.tree_view.expand(parent_item.index())
            except Exception:
                pass
        # Finally load the folder containing the file so the file entry is present
        self._load_children_for_path(parent_item, current_path)

    def _on_selection_changed(self, current: QModelIndex, previous: QModelIndex) -> None:
        self._debug(f"[UI] tree change: {self._describe_index(current)}")
        if self.tree_view.is_dragging() or (QApplication.mouseButtons() & Qt.LeftButton):
            return
        had_tree_focus = self.tree_view.hasFocus()
        restore_tree_focus = (self._tree_arrow_focus_pending or had_tree_focus) and not self._tree_enter_focus
        # One-shot flag: consume after evaluating
        self._tree_arrow_focus_pending = False
        if self._tree_keyboard_nav and had_tree_focus:
            # Arrow-key navigation should not open pages; consume the flag and stop.
            self._tree_keyboard_nav = False
            return
        self._tree_keyboard_nav = False
        if self._skip_next_selection_open:
            self._skip_next_selection_open = False
            return
        if previous.isValid():
            prev_target = previous.data(OPEN_ROLE) or previous.data(PATH_ROLE)
            if prev_target and prev_target == self.current_path:
                # Check if leaving an unsaved virtual page
                if self.current_path in self.virtual_pages:
                    self._cleanup_virtual_page_if_unchanged(self.current_path)
        if not current.isValid():
            self._debug("Tree selection cleared (no valid index).")
            return
        # If we're programmatically changing selection (history/hierarchy nav), don't auto-open here
        if self._suspend_selection_open:
            self._debug("Selection change suppressed (programmatic nav).")
            return
        open_target = current.data(OPEN_ROLE) or current.data(PATH_ROLE)
        if open_target == FILTER_BANNER:
            return
        # Skip selection changes for folders - let rowClicked handle page opening
        is_folder = current.data(TYPE_ROLE)
        if is_folder:
            self._debug("Tree selection skipped: folder expand/collapse only.")
            return
        self._debug(f"Tree selection target resolved to: {open_target!r}")
        if not open_target:
            self._debug("Tree selection skipped: no open target.")
            return
        if open_target == self.current_path:
            self._debug("Tree selection skipped: already editing this path.")
            return
        try:
            self._open_file(open_target)
            if restore_tree_focus:
                self.tree_view.setFocus(Qt.OtherFocusReason)
                self._apply_focus_borders()
        except Exception as exc:
            self._debug(f"Tree selection crash while opening {open_target!r}: {exc!r}")
            raise

    def _open_file(
        self,
        path: str,
        retry: bool = False,
        add_to_history: bool = True,
        force: bool = False,
        cursor_at_end: bool = False,
        restore_history_cursor: bool = False,
        sync_calendar: bool = True,
    ) -> None:
        if path:
            path = self._normalize_root_page_path(path)
        if not path or (path == self.current_path and not force):
            return
        if getattr(self, "_mode_window_pending", False) or getattr(self, "_mode_window", None):
            # Defer while a mode overlay is opening/closing to avoid scene clears during teardown.
            QTimer.singleShot(
                100,
                lambda p=path, r=retry, a=add_to_history, f=force, c=cursor_at_end, rh=restore_history_cursor, sc=sync_calendar: self._open_file(p, r, a, f, c, rh, sc),
            )
            return
        # Remember current cursor before switching pages
        self._remember_history_cursor()
        tracer = PageLoadLogger(path) if PAGE_LOGGING_ENABLED else None
        # Save current page if dirty before switching
        if self.current_path and path != self.current_path:
            self._save_dirty_page(reason="page switch")
        
        # Clean up current page if it's an unchanged virtual page
        if self.current_path and self.current_path in self.virtual_pages:
            self._cleanup_virtual_page_if_unchanged(self.current_path)
        
        self.autosave_timer.stop()
        if tracer:
            tracer.mark("api read start")
        
        # Add to page history (unless we're navigating through history)
        if add_to_history and self._is_history_path_allowed(path) and path != self.current_path:
            # Remove any forward history when opening a new page
            if self.history_index < len(self.page_history) - 1:
                self.page_history = self.page_history[:self.history_index + 1]
            # Add new page if not duplicate of last
            if not self.page_history or self.page_history[-1] != path:
                self.page_history.append(path)
                self.history_index = len(self.page_history) - 1
                if log_enabled("navigation"):
                    print(f"[HISTORY] Added to history: {path}, history_index={self.history_index}, total={len(self.page_history)}")
                # Refresh history buttons
                self._refresh_history_buttons()
        
        try:
            resp = self.http.post("/api/file/read", json={"path": path})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            print(f"[UI] Failed to read page {path}: status={exc.response.status_code if exc.response else 'unknown'} body={exc.response.text if exc.response else ''}", file=sys.stderr)
            detail = exc.response.text if exc.response else str(exc)
            if tracer:
                tracer.mark(f"api read failed ({detail})")
            self._alert(f"Reason: {detail}")
            return
        except httpx.HTTPError as exc:
            print(f"[UI] Failed to read page {path}: {exc}", file=sys.stderr)
            if tracer:
                tracer.mark(f"api read failed ({exc})")
            self._alert_api_error(exc, f"Failed to open {path}")
            return
        payload = resp.json()
        content = payload.get("content", "")
        rev = payload.get("rev")
        mtime_ns = payload.get("mtime_ns")
        if path:
            self._page_revisions[path] = {"rev": rev, "mtime_ns": mtime_ns}
        if log_enabled("editor_markdown"):
            print(f"[DEBUG load] Loaded from API: {len(content)} chars, ends_with_newline={content.endswith('\\n')}, last_20_chars={repr(content[-20:])}")
        if tracer:
            try:
                content_len = len(content.encode("utf-8"))
            except Exception:
                content_len = len(content or "")
            tracer.mark(f"api read complete bytes={content_len}")
        self._refresh_editor_context(path)
        if tracer:
            tracer.mark("editor context set")
        # Hand logger to the editor so rendering steps are captured
        try:
            self.editor.set_page_load_logger(tracer)
        except Exception:
            pass
        self.current_path = path
        self._update_active_page_chicklets()
        self._suspend_autosave = True
        self._suspend_cursor_history = True
        self._suspend_dirty_tracking = True
        try:
            self.editor.set_markdown(content)
        finally:
            self._suspend_dirty_tracking = False
            self._suspend_autosave = False
        if tracer:
            tracer.mark("editor content applied")
        # Mark buffer clean for dirty tracking
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        self._dirty_flag = False
        self._last_saved_content = content
        self._update_dirty_indicator()
        self._capture_undo_snapshot(path, content, source="load")
        self._schedule_homebase_sync("page load")
        updated = indexer.index_page(path, content)
        if updated:
            self.right_panel.refresh_tasks()
        if tracer:
            tracer.mark(f"index refresh {'+ tasks' if updated else '(no task changes)'}")
        # Keep Link Navigator in sync when a page is opened or reloaded
        self.right_panel.refresh_links(path)
        self._refresh_detached_link_panels(path)
        if tracer:
            tracer.mark("right panel links refreshed")
        # Persist panel visibility when a page is opened (captures programmatic restores)
        self._save_panel_visibility()
        move_cursor_to_end = cursor_at_end or self._should_focus_hr_tail(content)
        remember_cursor_positions = bool(self._feature_remember_cursor_position_enabled)
        if not remember_cursor_positions:
            move_cursor_to_end = False
        restored_history_cursor = False
        final_cursor_pos = None
        def _restore_scroll_position(value: int | None) -> None:
            if value is None:
                return
            try:
                scroll_bar = self.editor.verticalScrollBar()
                if scroll_bar:
                    scroll_bar.setValue(max(0, min(int(value), scroll_bar.maximum())))
            except Exception:
                pass

        if not remember_cursor_positions and self._template_cursor_position >= 0:
            self._template_cursor_position = -1
        
        # Check if we have a template cursor position for this newly created page
        if remember_cursor_positions and self._template_cursor_position >= 0:
            template_pos = self._template_cursor_position
            self._template_cursor_position = -1  # Reset for next page creation
            cursor = self.editor.textCursor()
            content_len = len(self.editor.toPlainText())
            cursor.setPosition(min(template_pos, content_len))
            self.editor.setTextCursor(cursor)
            self._scroll_cursor_to_top_quarter(cursor, animate=False, flash=False)
            move_cursor_to_end = False
            restored_history_cursor = True
            final_cursor_pos = cursor.position()
        
        if remember_cursor_positions and restore_history_cursor:
            saved_pos = self._history_cursor_positions.get(path)
            saved_scroll = self._history_scroll_positions.get(path)
            if saved_pos is not None or saved_scroll is not None:
                cursor = self.editor.textCursor()
                if saved_pos is not None:
                    cursor.setPosition(min(saved_pos, len(self.editor.toPlainText())))
                    self.editor.setTextCursor(cursor)
                if saved_scroll is not None:
                    _restore_scroll_position(saved_scroll)
                elif saved_pos is not None:
                    self._scroll_cursor_to_top_quarter(cursor, animate=False, flash=False)
                restored_history_cursor = True
                move_cursor_to_end = False
                final_cursor_pos = cursor.position()
        # If no explicit restore request, prefer any remembered cursor for this path
        if remember_cursor_positions and not restored_history_cursor:
            saved_pos = self._history_cursor_positions.get(path)
            saved_scroll = self._history_scroll_positions.get(path)
            if saved_pos is not None or saved_scroll is not None:
                cursor = self.editor.textCursor()
                if saved_pos is not None:
                    cursor.setPosition(min(saved_pos, len(self.editor.toPlainText())))
                    self.editor.setTextCursor(cursor)
                if saved_scroll is not None:
                    _restore_scroll_position(saved_scroll)
                elif saved_pos is not None:
                    self._scroll_cursor_to_top_quarter(cursor, animate=False, flash=False)
                restored_history_cursor = True
                move_cursor_to_end = False
                final_cursor_pos = cursor.position()
        if move_cursor_to_end:
            cursor = self.editor.textCursor()
            display_length = len(self.editor.toPlainText())
            cursor.setPosition(display_length)
            self.editor.setTextCursor(cursor)
            final_cursor_pos = cursor.position()
        elif not restored_history_cursor:
            self.editor.moveCursor(QTextCursor.Start)
            final_cursor_pos = self.editor.textCursor().position()
        self._suspend_cursor_history = False
        if remember_cursor_positions and final_cursor_pos is not None:
            self._history_cursor_positions[path] = final_cursor_pos
        if remember_cursor_positions:
            try:
                scroll_bar = self.editor.verticalScrollBar()
                if scroll_bar:
                    self._history_scroll_positions[path] = scroll_bar.value()
            except Exception:
                pass
        # Always show editing status; vi-mode banner is separate
        display_path = path_to_colon(path) or path
        if hasattr(self, "toc_widget"):
            root_base = ensure_root_colon_link(display_path) if display_path else ""
            self.toc_widget.set_base_path(root_base)
            self.editor.refresh_heading_outline()
        self.statusBar().showMessage(f"Editing {display_path}")
        self._update_window_title()
        
        # Automatically sync the nav tree to highlight the active page
        self._sync_nav_tree_to_active_page()
        
        # Save the last opened file
        if config.has_active_vault():
            config.save_last_file(path)
            # Refresh read-only badge if preference or lock state changed mid-session
            self._update_dirty_indicator()
        
        # Update calendar if this is a journal page.
        if sync_calendar:
            self._update_calendar_for_journal_page(path)
        
        # Update attachments panel with current page
        from pathlib import Path
        if path:
            full_path = Path(self.vault_root) / path.lstrip("/")
            has_chat = self.right_panel.set_current_page(full_path, path, sync_calendar=sync_calendar)
            self.editor.set_ai_chat_available(has_chat, active=self.right_panel.is_active_chat_for_page(path))
        else:
            self.right_panel.set_current_page(None, None, sync_calendar=sync_calendar)
            self.editor.set_ai_chat_available(False)
        self._mark_initial_page_loaded()
        if tracer:
            tracer.end("ready for edit")
            # Set up a defensive stack dump if the Qt loop does not resume quickly.
            faulthandler.cancel_dump_traceback_later()
            faulthandler.dump_traceback_later(5.0, repeat=False)
            loop_start = time.perf_counter()
            QTimer.singleShot(
                0,
                lambda: (
                    faulthandler.cancel_dump_traceback_later(),
                    tracer.mark(
                        f"qt loop resumed post-open delay={(time.perf_counter() - loop_start)*1000:.1f}ms"
                    ),
                ),
            )

    def _if_match_headers(self, path: str) -> Optional[dict[str, str]]:
        if not self._remote_mode:
            return None
        info = self._page_revisions.get(path)
        if not info:
            return None
        mtime_ns = info.get("mtime_ns")
        if mtime_ns is not None:
            try:
                mtime_val = int(mtime_ns)
            except (TypeError, ValueError):
                mtime_val = None
            if mtime_val is not None:
                return {"If-Match": f"mtime:{mtime_val}"}
        rev = info.get("rev")
        if rev is not None:
            try:
                rev_val = int(rev)
            except (TypeError, ValueError):
                rev_val = None
            if rev_val is not None:
                return {"If-Match": f"rev:{rev_val}"}
        return None

    def _update_page_revision(self, path: str, payload: dict) -> None:
        rev = payload.get("rev") if isinstance(payload, dict) else None
        mtime_ns = payload.get("mtime_ns") if isinstance(payload, dict) else None
        if rev is None and mtime_ns is None:
            return
        try:
            rev_val = int(rev) if rev is not None else None
        except (TypeError, ValueError):
            rev_val = None
        try:
            mtime_val = int(mtime_ns) if mtime_ns is not None else None
        except (TypeError, ValueError):
            mtime_val = None
        self._page_revisions[path] = {"rev": rev_val, "mtime_ns": mtime_val}

    def _extract_conflict_payload(self, resp: httpx.Response) -> Optional[dict]:
        try:
            data = resp.json()
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        detail = data.get("detail")
        if isinstance(detail, dict) and "current_content" in detail:
            return detail
        return None

    def _finalize_save(self, path: str, content: str, resp_payload: dict, message: str) -> None:
        previous_saved_content = self._last_saved_content
        was_virtual = path in self.virtual_pages
        content_changed = previous_saved_content is None or content != previous_saved_content
        if content_changed or was_virtual:
            self._mark_homebase_unsynced_local_change()
        if config.has_active_vault():
            indexer.index_page(path, content)
            self.right_panel.refresh_tasks()
            self.right_panel.refresh_links(path)
        self._last_saved_content = content
        self._update_page_revision(path, resp_payload)
        try:
            if self._feature_remember_cursor_position_enabled:
                self._history_cursor_positions[path] = self.editor.textCursor().position()
            self._persist_recent_history()
        except Exception:
            pass
        try:
            self.editor.document().setModified(False)
        except Exception:
            pass
        self._dirty_flag = False
        self._update_dirty_indicator()
        self._capture_undo_snapshot(path, content, source="save")

        if was_virtual:
            self.virtual_pages.discard(path)
            self.virtual_page_original_content.pop(path, None)
            self._populate_vault_tree()
            self.right_panel.refresh_calendar()

        self.autosave_timer.stop()
        display_path = path_to_colon(path) if path else ""
        self.statusBar().showMessage(f"{message} {display_path}", 2000 if "Auto" in message else 4000)
        self._schedule_homebase_sync("page save")

    def _init_persisted_undo_cache_for_vault(self) -> None:
        if not self.vault_root:
            self._undo_cache_path = None
            self._undo_cache = {"schema_version": 1, "pages": {}, "order": []}
            return
        cache_path = Path(self.vault_root) / ".stillpoint" / "undo_cache.json"
        self._undo_cache_path = cache_path
        try:
            if cache_path.exists():
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                pages = payload.get("pages") if isinstance(payload, dict) else {}
                order = payload.get("order") if isinstance(payload, dict) else []
                self._undo_cache = {
                    "schema_version": 1,
                    "pages": pages if isinstance(pages, dict) else {},
                    "order": order if isinstance(order, list) else [],
                }
            else:
                self._undo_cache = {"schema_version": 1, "pages": {}, "order": []}
        except Exception:
            self._undo_cache = {"schema_version": 1, "pages": {}, "order": []}

    def _save_persisted_undo_cache(self) -> None:
        if not self._undo_cache_path:
            return
        try:
            self._undo_cache_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._undo_cache_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(self._undo_cache, sort_keys=True, separators=(",", ":")), encoding="utf-8")
            tmp.replace(self._undo_cache_path)
        except Exception:
            return

    def _capture_undo_snapshot(self, path: Optional[str], content: str, source: str = "edit") -> None:
        if self._undo_cache_replaying:
            return
        page_path = str(path or "").strip()
        if not page_path or not self._undo_cache_path:
            return
        pages = self._undo_cache.setdefault("pages", {})
        order = self._undo_cache.setdefault("order", [])
        if not isinstance(pages, dict) or not isinstance(order, list):
            self._undo_cache = {"schema_version": 1, "pages": {}, "order": []}
            pages = self._undo_cache["pages"]
            order = self._undo_cache["order"]
        entry = pages.get(page_path)
        if not isinstance(entry, dict):
            entry = {"states": [], "cursor": -1}
            pages[page_path] = entry
        states = entry.setdefault("states", [])
        if not isinstance(states, list):
            states = []
            entry["states"] = states
        current_cursor = int(entry.get("cursor", len(states) - 1))
        if states and 0 <= current_cursor < len(states) - 1:
            del states[current_cursor + 1 :]
        if states and isinstance(states[-1], dict) and str(states[-1].get("content", "")) == content:
            entry["cursor"] = len(states) - 1
        else:
            cursor_pos = 0
            try:
                cursor_pos = int(self.editor.textCursor().position())
            except Exception:
                cursor_pos = 0
            states.append(
                {
                    "content": content,
                    "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "source": source,
                    "cursor_pos": cursor_pos,
                }
            )
            if len(states) > self._undo_cache_states_limit:
                trim = len(states) - self._undo_cache_states_limit
                del states[:trim]
            entry["cursor"] = len(states) - 1
        if page_path in order:
            order.remove(page_path)
        order.append(page_path)
        while len(order) > self._undo_cache_pages_limit:
            evict = order.pop(0)
            pages.pop(evict, None)
        self._save_persisted_undo_cache()

    def _apply_persisted_snapshot(self, page_path: str, target_index: int) -> bool:
        pages = self._undo_cache.get("pages")
        if not isinstance(pages, dict):
            return False
        entry = pages.get(page_path)
        if not isinstance(entry, dict):
            return False
        states = entry.get("states")
        if not isinstance(states, list) or target_index < 0 or target_index >= len(states):
            return False
        state = states[target_index]
        if not isinstance(state, dict):
            return False
        content = str(state.get("content", ""))
        cursor_pos = int(state.get("cursor_pos", 0) or 0)
        self._undo_cache_replaying = True
        try:
            self._suspend_autosave = True
            self._suspend_dirty_tracking = True
            try:
                self.editor.replace_markdown_in_place(content)
            finally:
                self._suspend_dirty_tracking = False
                self._suspend_autosave = False
            try:
                cursor = self.editor.textCursor()
                doc_len = len(self.editor.toPlainText())
                cursor.setPosition(max(0, min(cursor_pos, doc_len)))
                self.editor.setTextCursor(cursor)
            except Exception:
                pass
            entry["cursor"] = target_index
            self._dirty_flag = self._last_saved_content is None or content != self._last_saved_content
            self._update_dirty_indicator()
            self._save_persisted_undo_cache()
            return True
        finally:
            self._undo_cache_replaying = False

    def _persisted_undo_fallback(self) -> bool:
        page_path = str(self.current_path or "").strip()
        if not page_path:
            return False
        pages = self._undo_cache.get("pages")
        if not isinstance(pages, dict):
            return False
        entry = pages.get(page_path)
        if not isinstance(entry, dict):
            return False
        states = entry.get("states")
        if not isinstance(states, list) or not states:
            return False
        current = int(entry.get("cursor", len(states) - 1))
        target = current - 1
        if target < 0:
            return False
        if not self._apply_persisted_snapshot(page_path, target):
            return False
        self.statusBar().showMessage(f"Undo (snapshot {target + 1}/{len(states)})", 2500)
        return True

    def _persisted_redo_fallback(self) -> bool:
        page_path = str(self.current_path or "").strip()
        if not page_path:
            return False
        pages = self._undo_cache.get("pages")
        if not isinstance(pages, dict):
            return False
        entry = pages.get(page_path)
        if not isinstance(entry, dict):
            return False
        states = entry.get("states")
        if not isinstance(states, list) or not states:
            return False
        current = int(entry.get("cursor", len(states) - 1))
        target = current + 1
        if target >= len(states):
            return False
        if not self._apply_persisted_snapshot(page_path, target):
            return False
        self.statusBar().showMessage(f"Redo (snapshot {target + 1}/{len(states)})", 2500)
        return True

    def _accept_noop_conflict(self, path: str, content: str, conflict: dict, auto: bool) -> bool:
        remote_content = conflict.get("current_content", "")
        if remote_content != content:
            return False
        print("[Conflict] 409 received, no changes; accepting remote revision.")
        payload = {
            "rev": conflict.get("current_rev"),
            "mtime_ns": conflict.get("current_mtime_ns"),
        }
        message = "Auto-saved" if auto else "Saved"
        self._finalize_save(path, content, payload, message)
        return True

    def _resolve_conflict_and_save(
        self,
        path: str,
        local_content: str,
        conflict: dict,
        auto: bool,
        reason: str = "conflict merge",
    ) -> bool:
        if self._merge_dialog_open:
            return False
        remote_content = conflict.get("current_content", "")
        dialog = MergeConflictDialog(local_content, remote_content, path, parent=self)
        self._merge_dialog_open = True
        try:
            if dialog.exec() != QDialog.Accepted:
                return False
            merged = dialog.merged_text()
            if merged is None:
                return False
        finally:
            self._merge_dialog_open = False
        headers = None
        current_rev = conflict.get("current_rev")
        current_mtime = conflict.get("current_mtime_ns")
        if current_mtime is not None:
            try:
                headers = {"If-Match": f"mtime:{int(current_mtime)}"}
            except (TypeError, ValueError):
                headers = None
        elif current_rev is not None:
            try:
                headers = {"If-Match": f"rev:{int(current_rev)}"}
            except (TypeError, ValueError):
                headers = None
        self._log_write(reason, path, merged, auto=auto)
        try:
            resp = self.http.post("/api/file/write", json={"path": path, "content": merged}, headers=headers)
            if resp.status_code == 401 and self._remote_mode:
                if self._prompt_remote_login():
                    resp = self.http.post("/api/file/write", json={"path": path, "content": merged}, headers=headers)
            if resp.status_code == 409:
                conflict_payload = self._extract_conflict_payload(resp)
                if conflict_payload and self._accept_noop_conflict(path, merged, conflict_payload, auto):
                    return True
                if not auto:
                    self._alert("Save failed: the server changed again. Please retry.")
                return False
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            if not auto:
                self._alert_api_error(exc, f"Failed to save {path}")
            return False
        self._suspend_autosave = True
        self._suspend_dirty_tracking = True
        try:
            self.editor.replace_markdown_in_place(merged)
        finally:
            self._suspend_dirty_tracking = False
            self._suspend_autosave = False
        message = "Auto-saved" if auto else "Merged and saved"
        self._finalize_save(path, merged, resp.json(), message)
        return True

    def _save_current_file(self, auto: bool = False, reason: str = "save") -> None:
        if getattr(self, "_heading_picker_active", False):
            # Skip saves triggered while the heading picker popup is active (vi 't')
            return
        if self._merge_dialog_open:
            return
        if self._suspend_autosave:
            self._debug("Autosave suppressed (suspend flag set).")
            return
        if auto and self._read_only:
            # In read-only mode, silently skip autosaves/background saves
            return
        
        # Skip autosave if content hasn't changed
        if auto:
            # Check Qt's built-in modified flag first (most reliable)
            try:
                if not self.editor.document().isModified():
                    self._debug(f"Skipping autosave (reason={reason}): document not modified")
                    return
            except Exception:
                pass
            
            # Fallback to content comparison
            current_content = self.editor.to_markdown()
            if self._last_saved_content is not None and current_content == self._last_saved_content:
                self._debug(f"Skipping autosave (reason={reason}): content unchanged")
                return
        
        # Autosave should silently skip when read-only; explicit Ctrl+S should warn.
        if not self._ensure_writable("save changes", interactive=not auto):
            return
        if not self.current_path:
            if not auto:
                self._alert("No file selected to save.")
            return
        editor_path = self.editor.current_relative_path()
        if editor_path and self.current_path and editor_path != self.current_path:
            self._debug(
                f"Autosave skipped due to path mismatch editor={editor_path} window={self.current_path}"
            )
            return
        
        # Check if this is a virtual page with unchanged content
        if self.current_path in self.virtual_pages:
            current_content = self.editor.to_markdown()
            original_content = self.virtual_page_original_content.get(self.current_path)
            
            # If content hasn't changed from the template, don't save
            if original_content is not None and current_content == original_content:
                self._debug(f"Virtual page {self.current_path} unchanged from template, skipping save.")
                # Still stop the timer to prevent repeated attempts
                self.autosave_timer.stop()
                self._last_saved_content = current_content
                try:
                    self.editor.document().setModified(False)
                except Exception:
                    pass
                self._update_dirty_indicator()
                return
            
            # Content has changed, ensure folders exist before saving
            folder_path = self._file_path_to_folder(self.current_path)
            if not self._ensure_page_folder(folder_path, allow_existing=True):
                if not auto:
                    self._alert(f"Failed to create folder for {self.current_path}")
                return
        
        saved_cursor_pos = None
        saved_anchor_pos = None
        saved_scroll_pos = None
        try:
            editor_cursor = self.editor.textCursor()
            saved_cursor_pos = editor_cursor.position()
            saved_anchor_pos = editor_cursor.anchor()
            saved_scroll_pos = self.editor.verticalScrollBar().value()
        except Exception:
            pass

        def _restore_editor_view() -> None:
            if saved_cursor_pos is None or saved_anchor_pos is None:
                return
            doc_len = len(self.editor.toPlainText())
            anchor = max(0, min(saved_anchor_pos, doc_len))
            pos = max(0, min(saved_cursor_pos, doc_len))
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(anchor)
            cursor.setPosition(
                pos,
                QTextCursor.KeepAnchor if anchor != pos else QTextCursor.MoveAnchor,
            )
            self.editor.setTextCursor(cursor)
            if saved_scroll_pos is not None:
                self.editor.verticalScrollBar().setValue(saved_scroll_pos)

        payload_content = self.editor.to_markdown()
        if log_enabled("editor_markdown"):
            print(f"[DEBUG save] to_markdown() returned {len(payload_content)} chars, ends_with_newline={payload_content.endswith('\\n')}, last_20_chars={repr(payload_content[-20:])}")
        
        payload = {"path": self.current_path, "content": payload_content}
        headers = self._if_match_headers(self.current_path)
        self._log_write(reason, self.current_path, payload_content, auto=auto)
        try:
            resp = self.http.post("/api/file/write", json=payload, headers=headers)
            if resp.status_code == 401 and self._remote_mode:
                if self._prompt_remote_login():
                    resp = self.http.post("/api/file/write", json=payload, headers=headers)
            if resp.status_code == 409:
                conflict_payload = self._extract_conflict_payload(resp)
                if conflict_payload:
                    if self._accept_noop_conflict(self.current_path, payload_content, conflict_payload, auto):
                        return
                    self._resolve_conflict_and_save(
                        self.current_path,
                        payload_content,
                        conflict_payload,
                        auto,
                        reason=f"{reason} (conflict merge)",
                    )
                    return
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            if not auto:
                self._alert_api_error(exc, f"Failed to save {self.current_path}")
            return

        _restore_editor_view()
        message = "Auto-saved" if auto else "Saved"
        self._finalize_save(self.current_path, payload_content, resp.json(), message)
        # Refresh any popup editors on the same page
        try:
            for win in list(getattr(self, "_page_windows", [])):
                if getattr(win, "_source_path", None) == self.current_path:
                    win._load_content()
        except Exception:
            pass
        
    def _append_text_to_page_from_editor(self, dest_path: str, markdown_text: str) -> bool:
        """Append text to the end of dest_path using the HTTP API.

        Returns True on success so the editor can replace the selection with a link.
        """
        if not dest_path or not markdown_text:
            return False
        if self._read_only:
            self._alert("Read-only mode: cannot move text.")
            return False
        if not self._ensure_writable("move text", interactive=True):
            return False
        if self.current_path and dest_path == self.current_path:
            self._alert("Destination page is the current page.")
            return False
        created_new = False
        try:
            resp = self.http.post("/api/file/read", json={"path": dest_path})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            status = getattr(exc.response, "status_code", None)
            if status != 404:
                self._alert_api_error(exc, f"Failed to read {dest_path}")
                return False
            if not self._ensure_writable("create new page"):
                return False
            page_folder = self._file_path_to_folder(dest_path)
            if not page_folder:
                self._alert("Invalid destination path.")
                return False
            try:
                create_resp = self.http.post("/api/path/create", json={"path": page_folder, "is_dir": True})
                create_resp.raise_for_status()
                created_new = True
            except httpx.HTTPError as create_exc:
                self._alert_api_error(create_exc, f"Failed to create {page_folder}")
                return False
            if not self._remote_mode:
                try:
                    page_name = Path(dest_path).stem
                    self._apply_new_page_template(dest_path, page_name)
                except Exception:
                    pass
            try:
                resp = self.http.post("/api/file/read", json={"path": dest_path})
                resp.raise_for_status()
            except httpx.HTTPError as reread_exc:
                self._alert_api_error(reread_exc, f"Failed to read {dest_path}")
                return False
        content = resp.json().get("content", "")
        if not self._move_attachments_for_moved_text(markdown_text, dest_path):
            return False

        snippet = markdown_text.replace("\u2029", "\n").rstrip("\n")
        if content:
            if not content.endswith("\n"):
                content += "\n"
            if not content.endswith("\n\n"):
                content += "\n"
            new_content = content + snippet + "\n"
        else:
            new_content = snippet + "\n"

        self._log_write("append text", dest_path, new_content, auto=None)
        try:
            write_resp = self.http.post("/api/file/write", json={"path": dest_path, "content": new_content})
            write_resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to write {dest_path}")
            return False

        if config.has_active_vault():
            try:
                indexer.index_page(dest_path, new_content)
            except Exception:
                pass
            try:
                self.right_panel.refresh_tasks()
                self.right_panel.refresh_links(dest_path)
                self._refresh_detached_link_panels(dest_path)
            except Exception:
                pass
        if created_new:
            self._populate_vault_tree()
        return True

    def _move_attachments_for_moved_text(self, markdown_text: str, dest_path: str) -> bool:
        if not markdown_text or not self.current_path or not dest_path:
            return True
        source_dir = Path(self.current_path).parent
        dest_dir = Path(dest_path).parent
        if source_dir == dest_dir:
            return True
        refs = self._collect_attachment_refs(markdown_text)
        if not refs:
            return True
        for ref in refs:
            name = self._normalize_attachment_ref(ref)
            if not name:
                continue
            source_path = f"/{(source_dir / name).as_posix()}"
            if not self._move_attachment_path(source_path, dest_path, name):
                return False
        return True

    def _collect_attachment_refs(self, markdown_text: str) -> list[str]:
        refs: set[str] = set()
        for match in re.finditer(r"!\[[^\]]*\]\((?P<path>[^)\s]+)\)(?:\{width=\d+\})?", markdown_text):
            refs.add(match.group("path"))
        for match in re.finditer(
            r"\[[^\]]+\]\((?P<path>(?:\./)?[^)\s]+\.[A-Za-z0-9]{1,8})\)",
            markdown_text,
        ):
            refs.add(match.group("path"))
        return list(refs)

    def _normalize_attachment_ref(self, path: str) -> Optional[str]:
        raw = (path or "").strip()
        if not raw:
            return None
        if raw.startswith(("http://", "https://", "data:", "#", ":")):
            return None
        if raw.startswith("file://"):
            raw = raw[7:]
        raw = raw.split("#", 1)[0].split("?", 1)[0]
        if raw.startswith("./"):
            raw = raw[2:]
        raw = raw.replace("\\", "/")
        if not raw or "/" in raw:
            return None
        return raw

    def _move_attachment_path(self, source_path: str, dest_page: str, filename: str) -> bool:
        data: bytes | None = None
        if not self._remote_mode:
            if not self.vault_root:
                return False
            try:
                src_abs = (Path(self.vault_root) / source_path.lstrip("/")).resolve()
                data = src_abs.read_bytes()
            except Exception as exc:
                self._alert(f"Failed to read attachment {source_path}: {exc}")
                return False
        else:
            try:
                resp = self.http.get("/api/file/raw", params={"path": source_path})
                if resp.status_code == 401 and self._remote_mode and self._prompt_remote_login():
                    resp = self.http.get("/api/file/raw", params={"path": source_path})
                resp.raise_for_status()
                data = resp.content
            except httpx.HTTPError as exc:
                self._alert_api_error(exc, f"Failed to fetch attachment {source_path}")
                return False
        if data is None:
            return False
        try:
            attach_resp = self.http.post(
                "/files/attach",
                data={"page_path": dest_page},
                files={"files": (filename, data, "application/octet-stream")},
            )
            if attach_resp.status_code == 401 and self._remote_mode and self._prompt_remote_login():
                attach_resp = self.http.post(
                    "/files/attach",
                    data={"page_path": dest_page},
                    files={"files": (filename, data, "application/octet-stream")},
                )
            attach_resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to attach {filename} to {dest_page}")
            return False
        try:
            delete_resp = self.http.post("/files/delete", json={"paths": [source_path]})
            if delete_resp.status_code == 401 and self._remote_mode and self._prompt_remote_login():
                delete_resp = self.http.post("/files/delete", json={"paths": [source_path]})
            delete_resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to remove attachment {source_path}")
            return False
        return True

    def _is_editor_dirty(self) -> bool:
        """Return True if the buffer differs from last saved content."""
        if not self.current_path:
            return False
        dirty = bool(getattr(self, "_dirty_flag", False))
        if not dirty:
            return False
        if not self._homebase_sync_engine:
            return True
        try:
            current_content = self.editor.to_markdown()
            if self._last_saved_content is not None and current_content == self._last_saved_content:
                self._dirty_flag = False
                return False
        except Exception:
            pass
        return True

    def _save_dirty_page(self, reason: str = "dirty page") -> None:
        """Save the current page if there are unsaved edits."""
        if getattr(self, "_heading_picker_active", False):
            return
        if self._read_only:
            return
        if self._is_editor_dirty():
            self._save_current_file(auto=True, reason=reason)
            return
        # If Qt reports clean but we still think dirty, ensure badge reflects it
        if getattr(self, "_dirty_flag", False):
            self._update_dirty_indicator()

    def _open_journal_today(self) -> None:
        if not self.vault_root:
            self._alert("Select a vault before creating journal entries.")
            return
        # Build day template string from templates/JournalDay.txt with substitution
        day_template = ""
        template_cursor_pos = -1
        try:
            templates_root = Path(__file__).parent.parent.parent / "templates"
            preferred_day = config.load_default_journal_template()
            day_tpl = self._resolve_template_path(preferred_day, fallback="JournalDay")
            if day_tpl.exists():
                from datetime import datetime
                now = datetime.now()
                raw = day_tpl.read_text(encoding="utf-8")
                print(f"[Template] Loaded journal template: {day_tpl}")
                
                # Use proper template variable processing to handle cursor and all variables
                vars_map = {
                    "{{YYYY}}": f"{now:%Y}",
                    "{{Month}}": now.strftime("%B"),
                    "{{MM}}": f"{now:%m}",
                    "{{DOW}}": now.strftime("%A"),
                    "{{dd}}": f"{now:%d}",
                    "{{DayDateYear}}": now.strftime("%A %d %B %Y"),
                }
                
                # Process QOTD if template uses it
                if "{{QOTD}}" in raw:
                    vars_map["{{QOTD}}"] = self._get_qotd()
                
                # Find cursor position in original template
                if "{{cursor}}" in raw:
                    template_cursor_pos = raw.find("{{cursor}}")
                
                # Replace all variables EXCEPT {{cursor}} first
                result = raw
                for k, v in vars_map.items():
                    if k != "{{cursor}}":
                        # If this replacement happens before cursor position, adjust cursor_pos
                        if template_cursor_pos >= 0:
                            before_cursor = result[:template_cursor_pos]
                            count = before_cursor.count(k)
                            if count > 0:
                                len_diff = len(v) - len(k)
                                template_cursor_pos += count * len_diff
                        result = result.replace(k, v)
                
                # Remove cursor tag
                day_template = result.replace("{{cursor}}", "")
        except Exception as e:
            print(f"[Template] Error processing journal template: {e}")
            day_template = ""

        try:
            resp = self.http.post("/api/journal/today", json={"template": day_template})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, "Failed to create journal entry")
            return
        payload = resp.json()
        path = payload.get("path")
        created = payload.get("created", False)
        if path:
            # Store template cursor position for use when opening
            if template_cursor_pos >= 0:
                self._template_cursor_position = template_cursor_pos
            
            if self._show_journal_in_nav:
                # Repopulate tree so newly created nested year/month/day nodes appear
                self._pending_selection = path
                self._populate_vault_tree()
            # Apply journal templates (year/month/day) if newly created
            # Skip template application for remote vaults - server handles this
            if not self._remote_mode:
                self._apply_journal_templates(path, allow_overwrite=created)
            # Open with cursor at template position or end for immediate typing
            self._debug(f"Journal shortcut: forcing reload for {path}")
            self._open_file(path, force=True, cursor_at_end=(template_cursor_pos < 0))
            self.statusBar().showMessage("Journal: today", 4000)
            # Ensure focus returns to editor (tree selection may have taken focus)
            self.editor.setFocus()
            self._apply_focus_borders()

    def _apply_journal_templates(self, day_file_path: str, allow_overwrite: bool = True) -> None:
        """Ensure year/month/day journal scaffolding exists and apply templates if allowed.

        day_file_path: relative file path like /Journal/2025/11/12/12.txt (from API)
        Templates: JournalYear.txt, JournalMonth.txt, JournalDay.txt
        Variables: {{YYYY}}, {{Month}}, {{DOW}}, {{dd}}
        allow_overwrite: when False, existing files are left untouched (no template writes)
        """
        if not self.vault_root:
            return
        from datetime import datetime
        now = datetime.now()
        year_str = f"{now:%Y}"
        month_num = f"{now:%m}"  # zero-padded numeric month (folder name)
        month_name = now.strftime("%B")  # English month name
        day_num = f"{now:%d}"  # zero-padded day
        dow_name = now.strftime("%A")

        vault_root = Path(self.vault_root)
        # Derive folders
        journal_root = vault_root / "Journal"
        year_dir = journal_root / year_str
        month_dir = year_dir / month_num
        day_dir = month_dir / day_num

        # Page files (name matches folder name)
        year_page = year_dir / f"{year_dir.name}{PAGE_SUFFIX}"
        month_page = month_dir / f"{month_dir.name}{PAGE_SUFFIX}"
        day_page = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"

        # Load templates (use preference for day)
        templates_root = Path(__file__).parent.parent.parent / "templates"
        year_tpl = templates_root / "JournalYear.txt"
        month_tpl = templates_root / "JournalMonth.txt"
        preferred_day = config.load_default_journal_template()
        day_tpl = self._resolve_template_path(preferred_day, fallback="JournalDay")

        vars_map = {
            "{{YYYY}}": year_str,
            "{{Month}}": month_name,
            "{{DOW}}": dow_name,
            "{{dd}}": day_num,
        }

        def render(template_path: Path) -> str:
            """Render template with proper cursor handling."""
            try:
                raw = template_path.read_text(encoding="utf-8")
            except Exception:
                return ""
            
            # Find cursor position before replacement
            cursor_pos = raw.find("{{cursor}}")
            
            # Replace all variables EXCEPT {{cursor}} first
            result = raw
            for k, v in vars_map.items():
                # If replacement happens before cursor position, adjust cursor_pos
                if cursor_pos >= 0:
                    before_cursor = result[:cursor_pos]
                    count = before_cursor.count(k)
                    if count > 0:
                        len_diff = len(v) - len(k)
                        cursor_pos += count * len_diff
                result = result.replace(k, v)
            
            # Remove cursor tag
            return result.replace("{{cursor}}", "")

        # Create missing directories
        year_dir.mkdir(parents=True, exist_ok=True)
        month_dir.mkdir(parents=True, exist_ok=True)
        day_dir.mkdir(parents=True, exist_ok=True)

        # Helper to decide if we overwrite (only when file absent or trivially small)
        def needs_write(path: Path) -> bool:
            if not path.exists():
                return True
            try:
                size = path.stat().st_size
            except OSError:
                return False
            return size < 20  # heuristic: very small stub header

        # Year
        if allow_overwrite and needs_write(year_page) and year_tpl.exists():
            content = render(year_tpl)
            if content:
                year_page.write_text(content, encoding="utf-8")
        # Month
        if allow_overwrite and needs_write(month_page) and month_tpl.exists():
            content = render(month_tpl)
            if content:
                month_page.write_text(content, encoding="utf-8")
        # Day
        def needs_day_write(path: Path) -> bool:
            if not path.exists():
                return True
            try:
                text = path.read_text(encoding="utf-8")
            except Exception:
                return False
            stripped = text.strip()
            # Consider it a stub if very short OR only header lines (<=3 lines)
            if not stripped:
                return True
            lines = [ln for ln in stripped.splitlines() if ln.strip()]
            if len(lines) <= 3 and len(stripped) < 160:
                return True
            return False
        if allow_overwrite and needs_day_write(day_page) and day_tpl.exists():
            content = render(day_tpl)
            if content:
                day_page.write_text(content, encoding="utf-8")
        # Always perform a substitution pass on existing day page if placeholders remain
        if allow_overwrite:
            try:
                existing = day_page.read_text(encoding="utf-8")
                if any(token in existing for token in ("{{YYYY}}","{{Month}}","{{DOW}}","{{dd}}")):
                    replaced = existing
                    for k,v in vars_map.items():
                        replaced = replaced.replace(k, v)
                    if replaced != existing:
                        day_page.write_text(replaced, encoding="utf-8")
            except Exception:
                pass

    def _on_image_saved(self, filename: str) -> None:
        self.statusBar().showMessage(f"Image pasted as {filename}", 5000)
        # Refresh attachments panel to show the new image
        self.right_panel.refresh_attachments()
        self._schedule_homebase_sync("image saved")

    def _on_editor_focus_lost(self) -> None:
        """Handle editor focus loss - save if not moving to right panel."""
        # Note: We do NOT exit vi insert mode here because focus can be lost
        # when alt-tabbing or clicking outside the app. Vi mode should only
        # exit on explicit navigation within the app (handled in navigation methods).
        # Check where focus is going
        new_focus = QApplication.focusWidget()
        # Skip autosave when focus is moving into the one-shot overlay.
        try:
            overlay = getattr(self, "_one_shot_overlay", None)
            if overlay and overlay.isVisible() and (new_focus is overlay or overlay.isAncestorOf(new_focus)):
                self._remember_history_cursor()
                return
        except Exception:
            pass
        # Only skip save if focus is moving to another in-app panel
        if new_focus and (
            new_focus is self.right_panel
            or self.right_panel.isAncestorOf(new_focus)
            or new_focus is self.left_panel_container
            or self.left_panel_container.isAncestorOf(new_focus)
            or (self.find_bar and (new_focus is self.find_bar or self.find_bar.isAncestorOf(new_focus)))
        ):
            self._remember_history_cursor()
        else:
            self._remember_history_cursor()
            self._save_current_file(auto=True, reason="focus lost")

    def _find_asset(self, name: str) -> Optional[Path]:
        """Locate an asset in development or PyInstaller layouts."""
        rel = os.path.join("assets", name)
        candidates: list[Path] = []
        base = getattr(sys, "_MEIPASS", None)
        if base:
            candidates.append(Path(base) / rel)
            candidates.append(Path(base) / "_internal" / rel)
        try:
            exe_dir = Path(os.path.abspath(os.path.dirname(sys.argv[0])))
            candidates.append(exe_dir / rel)
            candidates.append(exe_dir / "_internal" / rel)
        except Exception:
            pass
        pkg_root = Path(__file__).resolve().parent.parent
        candidates.append(pkg_root / rel)
        candidates.append(pkg_root / "sp" / rel)
        try:
            source_root = Path(__file__).resolve().parents[2]
            candidates.append(source_root / rel)
            candidates.append(source_root.parent / rel)
        except Exception:
            pass
        for cand in candidates:
            if cand.exists():
                return cand
        return None

    def _load_icon(self, path: Optional[Path], color: QColor | Qt.GlobalColor | None = None, size: int = 16) -> Optional[QIcon]:
        """Load an icon from disk and optionally tint it to a given color."""
        if path is None:
            return None
        abs_path = path.resolve()
        if not abs_path.exists():
            return None
        icon = QIcon(str(abs_path))
        if color is None:
            return icon
        pm = icon.pixmap(size, size)
        if pm.isNull():
            return icon  # Fall back to untinted icon if SVG can't rasterize
        colored = QPixmap(pm.size())
        colored.fill(Qt.transparent)
        painter = QPainter(colored)
        painter.drawPixmap(0, 0, pm)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(colored.rect(), color)
        painter.end()
        return QIcon(colored)

    def _main_icon_color(self) -> QColor:
        """Return an icon color with contrast against the current app palette."""
        pal = QApplication.instance().palette() if QApplication.instance() else None
        if pal is None:
            return theme_color("main_window.icon.on_dark", "#ffffff")
        # Use the window background for global toolbar/nav icon contrast.
        bg = pal.color(QPalette.Window)
        if bg.lightness() < 128:
            return theme_color("main_window.icon.on_dark", "#ffffff")
        return theme_color("main_window.icon.on_light", "#000000")

    def _badge_icon(self, base: QIcon) -> QIcon:
        """Return a copy of base icon with an AI badge overlay (bottom-right)."""
        if not self._ai_badge_icon or base.isNull():
            return base
        pm = base.pixmap(24, 24)
        if pm.isNull():
            return base
        badge_pm = self._ai_badge_icon.pixmap(12, 12)
        result = QPixmap(pm.size())
        result.fill(Qt.transparent)
        painter = QPainter(result)
        painter.drawPixmap(0, 0, pm)
        x = pm.width() - badge_pm.width() - 1
        y = pm.height() - badge_pm.height() - 1
        painter.drawPixmap(x, y, badge_pm)
        painter.end()
        return QIcon(result)

    def _collapse_tree_to_root(self) -> None:
        """Collapse the navigation tree to top-level folders."""
        self.tree_view.collapseAll()
    
    def _open_search_tab(self) -> None:
        """Switch to the Search tab and focus the search field."""
        self._ensure_left_panel_visible()
        self.left_tab_widget.setCurrentIndex(self.left_tab_widget.indexOf(self.search_tab))
        self.search_tab.focus_search()

    def _search_from_folder(self, path: str) -> None:
        """Open Search tab scoped to the selected folder path and focus query input."""
        if not path:
            return
        normalized = self._file_path_to_folder(path if path.startswith("/") else f"/{path}") or "/"
        try:
            self.search_tab.current_subtree = normalized
            self.search_tab.subtree_entry.setText(path_to_colon(normalized))
            self.search_tab.clear_subtree_button.setEnabled(True)
        except Exception:
            pass
        self._open_search_tab()
    
    def _is_search_index_populated(self) -> bool:
        """Check if the full-text search index has any content."""
        if self._remote_mode:
            # For remote vaults, use the dedicated status endpoint
            try:
                resp = self.http.get("/api/search/status")
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("populated", False)
                return False
            except Exception as e:
                print(f"[Search] Failed to check remote search index: {e}")
                return False
        else:
            # For local vaults, check the database directly
            db_path = config._vault_db_path()
            if not db_path:
                return False
            try:
                import sqlite3
                conn = sqlite3.connect(db_path, check_same_thread=False)
                cursor = conn.execute("SELECT COUNT(*) FROM pages_search_index")
                count = cursor.fetchone()[0]
                conn.close()
                return count > 0
            except Exception:
                return False
    
    def _search_across_vault(self) -> None:
        """Open search across vault dialog, prompting to build index if needed."""
        if not self.vault_root:
            self._alert("Select a vault before searching.")
            return
        
        # Check if index is populated
        if not self._is_search_index_populated():
            reply = QMessageBox.question(
                self,
                "Search Index Required",
                "Search requires a search index. Create one now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                # Trigger search index rebuild
                if self._remote_mode:
                    # Start remote reindex with search rebuild, then open search tab when done
                    self._reindex_remote_vault(rebuild_search=True, on_complete=self._open_search_tab)
                else:
                    # Local search index rebuild (opens search tab when done)
                    self._rebuild_vault_search_index_for_search()
            else:
                return
        else:
            # Index exists, open search tab
            self._open_search_tab()
    
    def _rebuild_vault_search_index_for_search(self) -> None:
        """Rebuild search index without prompting, for use when opening search."""
        if not self.vault_root or not config.has_active_vault():
            return
        if not self._ensure_writable("rebuild the vault search index"):
            return
        
        db_path = config._vault_db_path()
        if not db_path:
            self._alert("No vault database found for search index.")
            return
        
        self.statusBar().showMessage("Rebuilding search index...", 0)
        
        root = Path(self.vault_root)
        txt_files = []
        for suffix in PAGE_SUFFIXES:
            for page_file in sorted(root.rglob(f"*{suffix}")):
                if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                    continue
                txt_files.append(page_file)

        progress = QProgressDialog("Indexing search...", None, 0, len(txt_files), self)
        progress.setWindowTitle("Search Index")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        import sqlite3

        conn = sqlite3.connect(db_path, check_same_thread=False)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pages_search_index (
                    id INTEGER PRIMARY KEY,
                    path TEXT NOT NULL UNIQUE,
                    mtime INTEGER NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_search_path ON pages_search_index(path)")
            try:
                conn.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS pages_search_fts USING fts5(content, content_rowid='id')"
                )
            except sqlite3.OperationalError as exc:
                self.statusBar().showMessage("Search index unavailable", 4000)
                self._alert(f"Search index unavailable: {exc}")
                return
            conn.execute("DELETE FROM pages_search_fts")
            conn.execute("DELETE FROM pages_search_index")
            conn.commit()

            for idx, txt_file in enumerate(txt_files, start=1):
                rel_path = txt_file.relative_to(root)
                path_str = f"/{rel_path.as_posix()}"
                try:
                    content = txt_file.read_text(encoding="utf-8")
                    mtime = int(txt_file.stat().st_mtime)
                    search_index.upsert_page(conn, path_str, mtime, content)
                except Exception:
                    continue
                progress.setValue(idx)
                QApplication.processEvents()
        finally:
            conn.close()
            progress.close()

        page_count = len(txt_files)
        self.statusBar().showMessage(f"Search index rebuilt: {page_count} pages", 3000)
        
        # Now open the search tab
        self._open_search_tab()

    def _on_search_result_selected(self, path: str, line: int, position: int = -1) -> None:
        """Handle navigation from search results to a specific page."""
        _log_search(f"[SearchNav] Navigating to {path}, line {line}")
        prev_suppress = getattr(self.editor, "_suppress_focus_on_load", False)
        self.editor._suppress_focus_on_load = True
        try:
            self._open_file(path)
        finally:
            self.editor._suppress_focus_on_load = prev_suppress
        
        # Scroll to the line with flash animation if line number is provided
        if position is not None and position >= 0:
            _log_search(f"[SearchNav] Scheduling scroll to position {position}")
            QTimer.singleShot(50, lambda: self._scroll_to_position_with_flash(position))
        elif line > 0:
            _log_search(f"[SearchNav] Scheduling scroll to line {line}")
            QTimer.singleShot(50, lambda: self._scroll_to_line_with_flash(line))
        
        # Return focus to search results tree only if the user hasn't moved to the search box
        def _maybe_refocus_results() -> None:
            if not self.search_tab:
                return
            try:
                if self.search_tab.search_entry.hasFocus():
                    return
                if self.left_tab_widget.currentWidget() != self.search_tab:
                    return
                self.search_tab.results_tree.setFocus()
            except Exception:
                pass
        QTimer.singleShot(100, _maybe_refocus_results)
    
    def _on_search_result_selected_with_editor_focus(self, path: str, line: int, position: int = -1) -> None:
        """Handle navigation from search results with editor focus (Ctrl+Enter)."""
        self._open_file(path)
        
        # Scroll to the line with flash animation if line number is provided
        if position is not None and position >= 0:
            QTimer.singleShot(50, lambda: self._scroll_to_position_with_flash(position))
        elif line > 0:
            QTimer.singleShot(50, lambda: self._scroll_to_line_with_flash(line))
        
        # Focus editor instead of returning to search results
        QTimer.singleShot(100, lambda: self.editor.setFocus())
    
    def _scroll_to_line_with_flash(self, line: int) -> None:
        """Scroll to a specific line number and flash it."""
        _log_search(f"[SearchNav] _scroll_to_line_with_flash called with line {line}")
        if line <= 0:
            _log_search(f"[SearchNav] Line {line} is invalid, skipping")
            return
        
        # Create cursor at the specified line (1-indexed from search, but QTextDocument uses 0-indexed)
        doc = self.editor.document()
        total_lines = doc.blockCount()
        _log_search(f"[SearchNav] Document has {total_lines} lines, looking for line {line}")
        
        # Note: Our search returns 1-indexed line numbers from enumerate(lines, 1)
        # QTextDocument.findBlockByLineNumber expects 0-indexed
        # So line 1 from search -> block 0, line 2 -> block 1, etc.
        block = doc.findBlockByLineNumber(line - 1)
        if not block.isValid():
            # Fallback to line if that doesn't work
            block = doc.findBlockByLineNumber(line)
            if not block.isValid():
                _log_search(f"[SearchNav] Block at line {line} is not valid")
                return
        
        cursor = QTextCursor(block)
        cursor.movePosition(QTextCursor.StartOfBlock)
        
        _log_search(f"[SearchNav] Setting cursor to block {block.blockNumber()} (search line {line}) and animating")
        # Set the cursor position and scroll with animation and flash
        self.editor.setTextCursor(cursor)
        self._animate_or_flash_to_cursor(cursor)

    def _scroll_to_position_with_flash(self, position: int) -> None:
        """Scroll to a character offset and flash it."""
        _log_search(f"[SearchNav] _scroll_to_position_with_flash called with position {position}")
        doc = self.editor.document()
        if not doc:
            return
        max_pos = max(0, doc.characterCount() - 1)
        safe_pos = max(0, min(position, max_pos))
        cursor = QTextCursor(doc)
        cursor.setPosition(safe_pos)
        self.editor.setTextCursor(cursor)
        self._animate_or_flash_to_cursor(cursor)
    
    def _show_search_dialog(self) -> None:
        """Show Ctrl+Shift+F search dialog that populates the search tab."""
        if not config.has_active_vault():
            return
        
        # Check if search index is populated
        if not self._is_search_index_populated():
            reply = QMessageBox.question(
                self,
                "Search Index Required",
                "Search requires a search index. Create one now?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes,
            )
            if reply == QMessageBox.Yes:
                # Trigger search index rebuild
                if self._remote_mode:
                    # Start remote reindex with search rebuild, then open search tab when done
                    self._reindex_remote_vault(rebuild_search=True, on_complete=self._open_search_tab)
                else:
                    # Local search index rebuild (opens search tab when done)
                    self._rebuild_vault_search_index_for_search()
            return
        
        # Simple dialog to get search query
        dialog = QDialog(self)
        dialog.setWindowTitle("Search Across Vault")
        dialog.resize(500, 180)
        
        layout = QVBoxLayout()
        
        # Search term input
        layout.addWidget(QLabel("Search query:"))
        search_input = QLineEdit()
        search_input.setPlaceholderText("Enter search query (supports AND, OR, NOT, \"phrases\", #tags)")
        layout.addWidget(search_input)
        
        # Limit by path checkbox and input
        limit_checkbox = QCheckBox("Limit to page path:")
        limit_checkbox.setChecked(False)
        layout.addWidget(limit_checkbox)
        
        path_input = QLineEdit()
        path_input.setEnabled(False)
        # Display current path in colon form without suffix
        display_path = self.current_path or ""
        display_path = strip_page_suffix(display_path)
        if display_path:
            display_path = path_to_colon(display_path)
        path_input.setText(display_path)
        path_input.setPlaceholderText("(current page path)")
        layout.addWidget(path_input)
        
        limit_checkbox.toggled.connect(path_input.setEnabled)
        
        # Buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        
        dialog.setLayout(layout)
        search_input.setFocus()
        
        if dialog.exec() == QDialog.Accepted:
            query = search_input.text().strip()
            if query:
                # Switch to search tab and populate it
                self._open_search_tab()
                
                subtree = None
                if limit_checkbox.isChecked() and path_input.text().strip():
                    # Convert from colon form back to slash form for API
                    from .path_utils import colon_to_path
                    subtree = colon_to_path(path_input.text().strip())
                    if not subtree.endswith(tuple(PAGE_SUFFIXES)):
                        subtree = subtree + PAGE_SUFFIX
                
                self.search_tab.set_search_query(query, subtree)

    def _on_attachment_dropped(self, filename: str) -> None:
        """Force-save the current page after a dropped attachment inserts content."""
        self._save_current_file(auto=True, reason="attachment dropped")
        self.statusBar().showMessage(f"Saved after dropping {filename}", 3000)
        self._schedule_homebase_sync("attachment dropped")

    def _jump_to_page(self) -> None:
        if not config.has_active_vault():
            return
        
        filter_prefix = self._nav_filter_path
        filter_label = path_to_colon(filter_prefix) if filter_prefix else None
        dlg = JumpToPageDialog(
            self,
            filter_prefix=filter_prefix,
            filter_label=filter_label,
            clear_filter_cb=self._clear_nav_filter,
            http_client=self.http,
            remote_mode=self._remote_mode,
        )
        result = dlg.exec()
        
        if result == QDialog.Accepted:
            target = dlg.selected_path()
            if target:
                self._exit_vi_insert_on_activate()
                self._open_file(target)
        

    def _insert_link(self) -> None:
        """Open insert link dialog and insert selected link at cursor."""
        if not config.has_active_vault():
            return
        
        # Capture cursor position BEFORE saving (as integers, immune to cursor object changes)
        editor_cursor = self.editor.textCursor()
        saved_cursor_pos = editor_cursor.position()
        saved_anchor_pos = editor_cursor.anchor()
        if log_enabled("editor_markdown"):
            print(f"[DEBUG _insert_link] BEFORE save: pos={saved_cursor_pos}, anchor={saved_anchor_pos}, doc_len={len(self.editor.toPlainText())}")
        
        # Save current page before inserting link to ensure it's indexed
        # Note: Save may reset cursor, but we've already captured the position as integers
        if self.current_path:
            self._save_current_file(auto=True, reason="insert link")
        
        if log_enabled("editor_markdown"):
            print(f"[DEBUG _insert_link] AFTER save: cursor.pos={self.editor.textCursor().position()}, doc_len={len(self.editor.toPlainText())}")
        
        # Get selected text if any
        selection_range: tuple[int, int] | None = None
        selected_text = ""
        if editor_cursor.hasSelection():
            selection_range = (editor_cursor.selectionStart(), editor_cursor.selectionEnd())
            selected_text = editor_cursor.selectedText()
            # Clean up selected text - remove line breaks and paragraph separators
            # Qt returns paragraph separators as U+2029 which cause line breaks in links
            selected_text = selected_text.replace('\u2029', ' ').replace('\n', ' ').replace('\r', ' ').strip()

        def _restore_cursor() -> QTextCursor:
            """Restore the cursor/selection captured before opening the dialog."""
            doc_len = len(self.editor.toPlainText())
            anchor = max(0, min(saved_anchor_pos, doc_len))
            pos = max(0, min(saved_cursor_pos, doc_len))
            if log_enabled("editor_markdown"):
                print(f"[DEBUG _restore_cursor] doc_len={doc_len}, saved_anchor={saved_anchor_pos}, saved_pos={saved_cursor_pos}, clamped_anchor={anchor}, clamped_pos={pos}")
            cursor = QTextCursor(self.editor.document())
            cursor.setPosition(anchor)
            cursor.setPosition(
                pos,
                QTextCursor.KeepAnchor if anchor != pos else QTextCursor.MoveAnchor,
            )
            self.editor.setTextCursor(cursor)
            return cursor
        
        filter_prefix = self._nav_filter_path
        filter_label = path_to_colon(filter_prefix) if filter_prefix else None
        dlg = InsertLinkDialog(
            self,
            selected_text=selected_text,
            filter_prefix=filter_prefix,
            filter_label=filter_label,
            clear_filter_cb=self._clear_nav_filter,
            current_page_path=self.current_path,
        )
        self.editor.begin_dialog_block()
        try:
            result = dlg.exec()
        finally:
            self.editor.end_dialog_block()
            # Restore cursor/selection to the pre-dialog location
            restore_cursor = _restore_cursor()
            # Always restore focus to the editor after dialog closes
            QTimer.singleShot(0, self.editor.setFocus)

        inserted = False
        if result == QDialog.Accepted:
            # Ensure we're still at the pre-dialog caret before mutating text
            restore_cursor = _restore_cursor()
            colon_path = dlg.selected_colon_path()
            link_name = dlg.selected_link_name()
            should_create_new = dlg.should_create_new_page()
            created_via_insert = False
            if should_create_new and colon_path:
                resolved_target, created_via_insert = self._ensure_inline_link_target_page(
                    colon_path,
                    template_name="",
                )
                if resolved_target:
                    colon_path = resolved_target
            if colon_path:
                # If there was selected text, replace it with the link
                if selection_range:
                    doc_len = len(self.editor.toPlainText())
                    start = max(0, min(selection_range[0], doc_len))
                    end = max(0, min(selection_range[1], doc_len))
                    restore_cursor.setPosition(start)
                    restore_cursor.setPosition(end, QTextCursor.KeepAnchor)
                    restore_cursor.removeSelectedText()
                
                # Always set the cursor before inserting the link
                self.editor.setTextCursor(restore_cursor)
                label = None if should_create_new else (link_name or selected_text or colon_path)
                self.editor.insert_link(
                    colon_path,
                    label,
                    surround_with_spaces=selection_range is None,
                )
                if created_via_insert:
                    post_cursor = self.editor.textCursor()
                    text = self.editor.toPlainText()
                    pos = post_cursor.position()
                    if pos >= len(text) or text[pos] != " ":
                        post_cursor.insertText(" ")
                    else:
                        post_cursor.setPosition(pos + 1)
                    self.editor.setTextCursor(post_cursor)
                inserted = True

    def _ensure_inline_link_target_page(self, colon_path: str, *, template_name: str = "") -> tuple[str, bool]:
        """Ensure link target exists for inline/Ctrl-L create flows.

        Returns: (resolved_colon_path, created_new).
        """
        normalized_colon = ensure_root_colon_link(colon_path)
        target_file = self._normalize_editor_path(colon_to_path(normalized_colon, self.vault_root_name))
        target_file = self._resolve_case_insensitive_rel_path(target_file)
        page_name = Path(target_file).stem
        if not page_name:
            return normalized_colon, False

        existing_abs = Path(self.vault_root, target_file.lstrip("/")) if self.vault_root else None
        if existing_abs and existing_abs.exists():
            existing_colon = path_to_colon(target_file) or normalized_colon
            return ensure_root_colon_link(existing_colon), False

        if self._read_only:
            self.statusBar().showMessage(
                self._read_only_status_message("Cannot create new pages while vault is read-only."),
                5000,
            )
            return normalized_colon, False
        folder_path = self._file_path_to_folder(target_file)
        if not self._ensure_page_folder(folder_path, allow_existing=True):
            return normalized_colon, False

        if template_name and template_name.strip():
            template_path = self._resolve_template_path(template_name.strip(), fallback="Default")
            self._apply_template_from_path(target_file, page_name, str(template_path))
        else:
            self._apply_new_page_template(target_file, page_name)
        self._mark_homebase_unsynced_local_change()
        self._schedule_homebase_sync("page create")
        # Keep inline link creation from triggering navigation side effects in the tree.
        saved_pending_selection = self._pending_selection
        self._pending_selection = None
        saved_suspend = self._suspend_selection_open
        self._suspend_selection_open = True
        try:
            self._populate_vault_tree()
        finally:
            self._suspend_selection_open = saved_suspend
            self._pending_selection = saved_pending_selection

        created_colon = path_to_colon(target_file) or normalized_colon
        return ensure_root_colon_link(created_colon), True


    def _insert_date(self) -> None:
        """Show calendar/date dialog and insert selected date."""
        if not self.vault_root:
            self._alert("Select a vault before inserting dates.")
            return
        cursor = self.editor.textCursor()
        saved_cursor_pos = cursor.position()
        saved_anchor_pos = cursor.anchor()
        cursor_rect = self.editor.cursorRect()
        anchor = self.editor.viewport().mapToGlobal(cursor_rect.bottomRight() + QPoint(0, 4))
        dlg = DateInsertDialog(
            self,
            anchor_pos=anchor,
            accept_on_double_click=True,
            accept_on_enter=True,
            allow_nav_keys=True,
            use_vi_keys=bool(getattr(self, "_vi_enabled", False)),
            keep_edit_focus=True,
        )
        result = dlg.exec()
        # Restore cursor/selection to where the user triggered the dialog
        doc_len = len(self.editor.toPlainText())
        anchor_pos = max(0, min(saved_anchor_pos, doc_len))
        cursor_pos = max(0, min(saved_cursor_pos, doc_len))
        restore_cursor = QTextCursor(self.editor.document())
        restore_cursor.setPosition(anchor_pos)
        restore_cursor.setPosition(
            cursor_pos,
            QTextCursor.KeepAnchor if anchor_pos != cursor_pos else QTextCursor.MoveAnchor,
        )
        self.editor.setTextCursor(restore_cursor)
        if result == QDialog.Accepted:
            text = dlg.selected_date_text()
            if text:
                restore_cursor.insertText(text)
                self.editor.setTextCursor(restore_cursor)
                self.statusBar().showMessage(f"Inserted date: {text}", 3000)
        

    def _copy_current_page_link(self) -> None:
        """Copy link under cursor or current page's link to clipboard (Ctrl+Shift+L)."""
        if not self.current_path:
            self.statusBar().showMessage("No page open to copy", 3000)
            return
        # First try to copy the link under the cursor (includes slug links)
        copied = self.editor._copy_link_or_heading()
        if copied:
            self.statusBar().showMessage(f"Copied link: {copied}", 3000)
        else:
            # Fallback to copying current page
            copied = self.editor.copy_current_page_link()
            if copied:
                self.statusBar().showMessage(f"Copied link: {copied}", 3000)
            else:
                colon_path = path_to_colon(self.current_path)
                if colon_path:
                    rooted = ensure_root_colon_link(colon_path)
                    self.statusBar().showMessage(f"Copied link: {rooted}", 3000)

    def _on_link_copied(self, link_text: str) -> None:
        """Show status when links are copied via editor context menu."""
        if link_text:
            self.statusBar().showMessage(f"Copied link: {link_text}", 3000)

    def _show_new_page_dialog(
        self,
        parent_path: Optional[str] = None,
        *,
        insert_link_in_editor: bool = False,
    ) -> None:
        """Show dialog to create a new page with template selection (Ctrl+N)."""
        if not self.vault_root:
            self._alert("Select a vault before creating pages.")
            return
        if not self._ensure_writable("create new pages"):
            return
        
        # Determine the effective parent path (respecting filter)
        resolved_parent = parent_path if parent_path is not None else self._get_current_parent_path()
        filter_hint = None
        if self._nav_filter_path and self._nav_filter_path != "/":
            # Show filter hint if we're using the filtered path
            if parent_path is None or parent_path == self._nav_filter_path:
                filter_hint = path_to_colon(self._nav_filter_path)
        
        dlg = NewPageDialog(self, filter_hint=filter_hint)
        if dlg.exec() == QDialog.Accepted:
            page_name = dlg.get_page_name()
            if not page_name:
                self.statusBar().showMessage("Page name cannot be empty", 3000)
                return
            
            if "/" in page_name or ":" in page_name:
                self.statusBar().showMessage("Page name cannot contain '/' or ':'", 3000)
                return

            # Create the new page path (resolved_parent already determined above)
            target_path = self._join_paths(resolved_parent, page_name)
            
            try:
                # Create the page folder
                resp = self.http.post("/api/path/create", json={"path": target_path, "is_dir": True})
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                if exc.response is not None and exc.response.status_code == 409:
                    existing_file = self._folder_to_file_path(target_path)
                    colon_existing = path_to_colon(existing_file) if existing_file else None
                    if insert_link_in_editor and colon_existing and self.current_path:
                        self.editor.insert_link(colon_existing, surround_with_spaces=True)
                        cursor = self.editor.textCursor()
                        text = self.editor.toPlainText()
                        pos = cursor.position()
                        if pos >= len(text) or text[pos] != " ":
                            cursor.insertText(" ")
                        else:
                            cursor.setPosition(pos + 1)
                        self.editor.setTextCursor(cursor)
                        self.statusBar().showMessage("Page already exists here; inserted link", 4000)
                        return
                    if not insert_link_in_editor and existing_file:
                        self._open_file(existing_file, cursor_at_end=False, force=True)
                        self.editor.setFocus()
                        self.statusBar().showMessage("Opened existing page", 3000)
                        return
                    self.statusBar().showMessage("Page already exists here", 4000)
                else:
                    self._alert_api_error(exc, "Failed to create page")
                return
            except httpx.HTTPError as exc:
                self._alert_api_error(exc, "Failed to create page")
                return
            
            # Get the file path
            file_path = self._folder_to_file_path(target_path)
            if not file_path:
                return
            
            # Apply the selected template
            template_path = dlg.get_template_path()
            if template_path:
                self._apply_template_from_path(file_path, page_name, template_path)
            self._mark_homebase_unsynced_local_change()
            self._schedule_homebase_sync("page create")
            
            # Refresh tree without auto-opening/selecting the new page.
            saved_pending_selection = self._pending_selection
            self._pending_selection = None
            saved_suspend = self._suspend_selection_open
            self._suspend_selection_open = True
            try:
                self._populate_vault_tree()
            finally:
                self._suspend_selection_open = saved_suspend
                self._pending_selection = saved_pending_selection

            # Insert link where the cursor is and keep editing the current page.
            if insert_link_in_editor and self.current_path:
                colon_path = path_to_colon(file_path)
                if colon_path:
                    self.editor.insert_link(colon_path, surround_with_spaces=True)
                    cursor = self.editor.textCursor()
                    text = self.editor.toPlainText()
                    pos = cursor.position()
                    if pos >= len(text) or text[pos] != " ":
                        cursor.insertText(" ")
                        self.editor.setTextCursor(cursor)
                    else:
                        cursor.setPosition(pos + 1)
                        self.editor.setTextCursor(cursor)
                    self.statusBar().showMessage("Created page and inserted link", 4000)
                    return
                self.statusBar().showMessage("Created page", 3000)
                return

            # No active page to insert into, or insertion disabled.
            self.statusBar().showMessage("Created page", 3000)
            self._open_file(file_path, cursor_at_end=True, force=True)
            self.editor.setFocus()

    def _show_folder_template_dialog(self, parent_path: str = "/") -> None:
        """Show dialog to create pages from a folder template."""
        if not self.vault_root:
            self._alert("Select a vault before creating pages.")
            return
        if not self._ensure_writable("create new pages"):
            return
        
        dlg = FolderTemplateDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        
        template_path = dlg.get_template_path()
        folder_name = dlg.get_folder_name()
        
        if not template_path or not folder_name:
            return
        
        # Create target folder path
        target_folder_path = self._join_paths(parent_path, folder_name)
        
        # Create the folder
        try:
            resp = self.http.post("/api/path/create", json={"path": target_folder_path, "is_dir": True})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                self.statusBar().showMessage(f"Folder '{folder_name}' already exists", 4000)
            else:
                self._alert_api_error(exc, "Failed to create folder")
            return
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, "Failed to create folder")
            return
        
        # Get all .txt files from the template directory
        txt_files = sorted(template_path.glob("*.txt"))
        if not txt_files:
            self.statusBar().showMessage("No template files found", 3000)
            return
        
        # Create each page from the template
        first_page_file = None
        created_pages = []
        
        for template_file in txt_files:
            page_name = template_file.stem
            page_folder_path = self._join_paths(target_folder_path, page_name)
            
            # Create page folder
            try:
                resp = self.http.post("/api/path/create", json={"path": page_folder_path, "is_dir": True})
                resp.raise_for_status()
            except Exception:
                continue
            
            # Get the file path
            page_file_path = self._folder_to_file_path(page_folder_path)
            if not page_file_path:
                continue
            
            # Process template with {{FolderName}} variable
            try:
                template_content = template_file.read_text(encoding="utf-8")
                print(f"[FolderTemplate] Processing: {template_file.name}")
            except Exception:
                continue
            
            # Process template variables (includes {{FolderName}})
            content, cursor_pos = self._process_folder_template_variables(
                template_content,
                target_folder_path,
                folder_name,
                page_name,
            )
            
            # Write to the new page file
            abs_path = Path(self.vault_root) / page_file_path.lstrip("/")
            try:
                abs_path.write_text(content, encoding="utf-8")
                created_pages.append(page_name)
                if first_page_file is None:
                    first_page_file = page_file_path
            except Exception:
                pass
        
        # Refresh tree and open first page
        self._populate_vault_tree()
        
        if first_page_file:
            self._pending_selection = first_page_file
            self._open_file(first_page_file)
        
        # Show success message
        if created_pages:
            self._mark_homebase_unsynced_local_change()
            self._schedule_homebase_sync("page create")
            self.statusBar().showMessage(
                f"Created {len(created_pages)} pages in '{folder_name}'",
                5000
            )
        else:
            self.statusBar().showMessage("No pages were created", 3000)

    def _get_current_parent_path(self) -> str:
        """Get the parent path for creating new pages based on current selection."""
        # If navigation is filtered, use the filter path
        if self._nav_filter_path and self._nav_filter_path != "/":
            return self._nav_filter_path
        # If we have a current file open, use its parent
        if self.current_path:
            rel_current = Path(self.current_path.lstrip("/"))
            parent_folder = rel_current.parent
            if parent_folder.parts:
                # Remove the filename to get the folder
                return f"/{parent_folder.as_posix()}"
        return "/"

    def _open_preferences(self) -> None:
        """Open the preferences dialog."""
        dlg = PreferencesDialog(self)
        dlg.rebuildIndexRequested.connect(lambda: self._reindex_vault(show_progress=True))
        if dlg.exec() == QDialog.Accepted:
            self._apply_vi_preferences()
            self._apply_feature_overrides()
            self._refresh_right_minibar_tabs()
            ai_family = config.load_ai_chat_font_family()
            if self.right_panel.ai_chat_panel:
                self.right_panel.ai_chat_panel.set_font_family(ai_family)
            if self._detached_ai_chat_panel:
                self._detached_ai_chat_panel.set_font_family(ai_family)
            # Apply vault read-only preference immediately
            self._apply_vault_read_only_pref()
            try:
                self.link_update_mode = config.load_link_update_mode()
            except Exception:
                self.link_update_mode = "reindex"
            try:
                self.update_links_on_index = config.load_update_links_on_index()
            except Exception:
                self.update_links_on_index = True
            self._setup_quick_capture_shortcut(show_error=True)
            self._update_periodic_search_sync_timer()
        try:
            self.editor.set_pygments_style(config.load_pygments_style("monokai"))
        except Exception:
            pass
        try:
            self._main_soft_scroll_enabled = config.load_enable_main_soft_scroll()
        except Exception:
            self._main_soft_scroll_enabled = True
        self._setup_tray_icon()
        self._register_quick_capture_hook()

    def _setup_tray_icon(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            return
        if not self._acquire_tray_lock():
            if getattr(self, "_tray_retry_timer", None) is None:
                self._tray_retry_timer = QTimer(self)
                self._tray_retry_timer.setInterval(2000)
                self._tray_retry_timer.timeout.connect(self._setup_tray_icon)
                self._tray_retry_timer.start()
            return
        if getattr(self, "_tray_retry_timer", None) is not None:
            self._tray_retry_timer.stop()
            self._tray_retry_timer = None
        app = QApplication.instance()
        if app is not None:
            tray_owner = getattr(app, "_stillpoint_tray_owner", None)
            for widget in app.topLevelWidgets():
                if (
                    isinstance(widget, MainWindow)
                    and widget is not self
                    and getattr(widget, "_tray_icon", None) is not None
                ):
                    if tray_owner is None:
                        app._stillpoint_tray_owner = widget
                    return
            if tray_owner is not None and tray_owner is not self:
                return
        if not config.load_tray_icon_enabled():
            if getattr(self, "_tray_icon", None):
                self._tray_icon.hide()
                self._tray_icon.deleteLater()
                self._tray_icon = None
                self._tray_menu = None
                if app is not None and getattr(app, "_stillpoint_tray_owner", None) is self:
                    app._stillpoint_tray_owner = None
                self._release_tray_lock()
            return
        if getattr(self, "_tray_icon", None):
            return
        from sp.app.main import get_app_icon

        tray_icon = QSystemTrayIcon(get_app_icon(), self)
        menu = QMenu()
        
        # Static menu items at top
        action_open_vault_new = menu.addAction("Open Vault in New Window...")
        action_open_vault_new.triggered.connect(lambda: self._select_vault(spawn_new_process=True))
        
        # Dynamic vault list (populated on menu show)
        menu.addSeparator()
        menu.aboutToShow.connect(lambda: self._update_tray_vault_list(menu))
        
        # Static menu items at bottom
        menu.addSeparator()
        action_quit = menu.addAction("Quit")
        action_quit.triggered.connect(self._quit_from_tray)
        
        tray_icon.setContextMenu(menu)
        tray_icon.activated.connect(self._on_tray_activated)
        tray_icon.show()
        self._tray_icon = tray_icon
        self._tray_menu = menu
        if app is not None:
            app._stillpoint_tray_owner = self

    def _show_from_tray(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()

    def _update_tray_vault_list(self, menu: QMenu) -> None:
        """Dynamically update the vault list in the tray menu."""
        # Find the separators
        actions = menu.actions()
        sep_indices = [i for i, action in enumerate(actions) if action.isSeparator()]
        if len(sep_indices) < 2:
            return
        
        # Remove existing vault items (between the two separators)
        for i in range(sep_indices[1] - 1, sep_indices[0], -1):
            if i < len(actions) and not actions[i].isSeparator():
                menu.removeAction(actions[i])
        
        # Get all open vaults from all processes
        vaults = self._get_all_process_vaults()
        if not vaults:
            return
        
        # Sort by vault name
        vaults.sort(key=lambda x: x['vault_name'].lower())
        
        # Refresh actions list after removals
        actions = menu.actions()
        sep_indices = [i for i, action in enumerate(actions) if action.isSeparator()]
        if len(sep_indices) < 2:
            return
        
        # Insert vault actions
        second_sep = actions[sep_indices[1]]
        current_pid = os.getpid()
        
        for vault_info in vaults:
            vault_name = vault_info['vault_name']
            vault_pid = vault_info['pid']
            
            action = QAction(f"Show {vault_name}", menu)
            if vault_pid == current_pid:
                # Same process - use normal focus
                action.triggered.connect(self._show_from_tray)
            else:
                # Different process - use cross-process activation
                action.triggered.connect(lambda checked=False, pid=vault_pid: self._activate_process_window(pid))
            
            menu.insertAction(second_sep, action)

    def _get_all_process_vaults(self) -> list[dict]:
        """Get all open vaults from all StillPoint processes."""
        vaults = []
        try:
            windows_dir = Path.home() / ".stillpoint" / "windows"
            if not windows_dir.exists():
                return vaults
            
            # Read all pid files
            for pid_file in windows_dir.glob("*.json"):
                try:
                    data = json.loads(pid_file.read_text(encoding="utf-8"))
                    pid = data.get('pid')
                    vault_name = data.get('vault_name')
                    vault_path = data.get('vault_path')
                    
                    if pid and vault_name and vault_path:
                        # Check if process is still alive
                        if self._is_process_alive(pid):
                            vaults.append({
                                'pid': pid,
                                'vault_name': vault_name,
                                'vault_path': vault_path
                            })
                        else:
                            # Clean up stale file
                            pid_file.unlink()
                except Exception:
                    pass
        except Exception:
            pass
        
        return vaults

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process with given PID is alive."""
        try:
            if sys.platform == "win32":
                import ctypes
                kernel32 = ctypes.windll.kernel32
                PROCESS_QUERY_INFORMATION = 0x0400
                handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
                if handle:
                    kernel32.CloseHandle(handle)
                    return True
                return False
            else:
                # Unix-like systems
                os.kill(pid, 0)
                return True
        except (OSError, AttributeError):
            return False

    def _activate_process_window(self, pid: int) -> None:
        """Activate the window of another StillPoint process."""
        try:
            if sys.platform == "win32":
                self._activate_window_windows(pid)
            elif sys.platform == "darwin":
                self._activate_window_macos(pid)
            else:
                self._activate_window_linux(pid)
        except Exception as exc:
            print(f"Failed to activate window for PID {pid}: {exc}", file=sys.stderr)

    def _activate_window_windows(self, pid: int) -> None:
        """Activate window on Windows using win32 APIs."""
        try:
            import ctypes
            from ctypes import wintypes
            
            user32 = ctypes.windll.user32
            
            # Find window by process ID
            def enum_windows_callback(hwnd, lparam):
                if user32.IsWindowVisible(hwnd):
                    process_id = wintypes.DWORD()
                    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(process_id))
                    if process_id.value == pid:
                        # Found the window, activate it
                        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
                        user32.SetForegroundWindow(hwnd)
                        return False  # Stop enumeration
                return True
            
            EnumWindowsProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
            user32.EnumWindows(EnumWindowsProc(enum_windows_callback), 0)
        except Exception:
            pass

    def _activate_window_macos(self, pid: int) -> None:
        """Activate window on macOS using AppleScript."""
        try:
            script = f'tell application "System Events" to set frontmost of (first process whose unix id is {pid}) to true'
            subprocess.run(["osascript", "-e", script], check=False, capture_output=True)
        except Exception:
            pass

    def _activate_window_linux(self, pid: int) -> None:
        """Activate window on Linux using wmctrl or xdotool."""
        try:
            # Try wmctrl first
            result = subprocess.run(
                ["wmctrl", "-lp"], 
                capture_output=True, 
                text=True, 
                check=False
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split(None, 3)
                    if len(parts) >= 3 and parts[2] == str(pid):
                        window_id = parts[0]
                        subprocess.run(["wmctrl", "-ia", window_id], check=False)
                        return
            
            # Try xdotool as fallback
            result = subprocess.run(
                ["xdotool", "search", "--pid", str(pid)],
                capture_output=True,
                text=True,
                check=False
            )
            if result.returncode == 0 and result.stdout.strip():
                window_id = result.stdout.strip().split()[0]
                subprocess.run(["xdotool", "windowactivate", window_id], check=False)
        except Exception:
            pass

    def _register_process_window(self) -> None:
        """Register this process's window in shared state."""
        if not self.vault_root:
            return
        
        try:
            windows_dir = Path.home() / ".stillpoint" / "windows"
            windows_dir.mkdir(parents=True, exist_ok=True)
            
            # Clean up stale files on first registration
            if not hasattr(self, '_process_registered'):
                self._cleanup_stale_process_files()
            
            pid_file = windows_dir / f"{os.getpid()}.json"
            data = {
                'pid': os.getpid(),
                'vault_name': Path(self.vault_root).name,
                'vault_path': str(self.vault_root)
            }
            pid_file.write_text(json.dumps(data), encoding="utf-8")
            self._process_registered = True
        except Exception:
            pass

    def _unregister_process_window(self) -> None:
        """Unregister this process's window from shared state."""
        try:
            windows_dir = Path.home() / ".stillpoint" / "windows"
            pid_file = windows_dir / f"{os.getpid()}.json"
            if pid_file.exists():
                pid_file.unlink()
        except Exception:
            pass

    def _cleanup_stale_process_files(self) -> None:
        """Clean up pid files for processes that are no longer running."""
        try:
            windows_dir = Path.home() / ".stillpoint" / "windows"
            if not windows_dir.exists():
                return
            
            for pid_file in windows_dir.glob("*.json"):
                try:
                    data = json.loads(pid_file.read_text(encoding="utf-8"))
                    pid = data.get('pid')
                    if pid and not self._is_process_alive(pid):
                        pid_file.unlink()
                except Exception:
                    # If we can't read it, remove it
                    try:
                        pid_file.unlink()
                    except Exception:
                        pass
        except Exception:
            pass

    def _quit_from_tray(self) -> None:
        self._allow_tray_exit = True
        QApplication.instance().quit()

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._show_from_tray()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        if (
            config.load_tray_icon_enabled()
            and config.load_minimize_to_tray_enabled()
            and getattr(self, "_tray_icon", None)
            and not getattr(self, "_allow_tray_exit", False)
        ):
            event.ignore()
            self.hide()
            return
        
        super().closeEvent(event)


    def _show_quick_capture_overlay(self) -> None:
        target = self._resolve_quick_capture_target()
        if not target:
            print("[QuickCapture] UI overlay aborted: no capture target resolved.")
            self._show_quick_capture_unavailable()
            return
        print(f"[QuickCapture] UI overlay target resolved: {target}")

        def _on_capture(text: str, attachments: list[dict], _vault_path: Optional[str]) -> None:
            self._submit_quick_capture(text, target, attachments)

        subtitle = self._quick_capture_subtitle(target)
        overlay = QuickCaptureOverlay(parent=self, on_capture=_on_capture, subtitle=subtitle)
        overlay.adjustSize()
        geo = self.frameGeometry()
        pos = geo.center() - overlay.rect().center()
        pos.setY(pos.y() - int(geo.height() * 0.1))
        overlay.move(pos)
        overlay.show()
        overlay.raise_()
        overlay.activateWindow()
        overlay.input.setFocus()

    def _resolve_quick_capture_target(self) -> Optional[dict[str, Optional[str]]]:
        # Quick capture works with both local and remote vaults (remote is API-based)
        home_vault = config.load_quick_capture_vault()
        vault_path = home_vault or self.vault_root
        
        if not vault_path:
            return None
        
        # For remote vaults, we can skip local filesystem checks since it's API-based
        if self._remote_mode and not home_vault:
            # Capturing to current remote vault
            if self._read_only:
                return None
            return {"vault_path": vault_path, "page_mode": config.load_quick_capture_page_mode(), "page_ref": config.load_quick_capture_custom_page() if config.load_quick_capture_page_mode() == "custom" else None}
        
        # For local vaults or explicit home vault, do filesystem checks
        vault_root = Path(vault_path).expanduser()
        if not vault_root.exists():
            return None
        if self.vault_root and Path(self.vault_root).expanduser().resolve() == vault_root.resolve() and self._read_only:
            return None
        if not os.access(vault_root, os.W_OK):
            return None
        page_mode = config.load_quick_capture_page_mode()
        page_ref = config.load_quick_capture_custom_page() if page_mode == "custom" else None
        if page_mode == "custom" and not page_ref:
            return None
        return {"vault_path": str(vault_root), "page_mode": page_mode, "page_ref": page_ref}

    def _show_quick_capture_unavailable(self) -> None:
        msg = QMessageBox(self)
        msg.setWindowTitle("Quick Capture")
        msg.setText(
            "Quick Capture isn't ready yet.\n"
            "Choose a home vault in Settings to enable it."
        )
        msg.setIcon(QMessageBox.Information)
        msg.setStandardButtons(QMessageBox.NoButton)
        open_settings = msg.addButton("Open Settings", QMessageBox.AcceptRole)
        msg.exec()
        if msg.clickedButton() == open_settings:
            self._open_preferences()

    def _submit_quick_capture(self, text: str, target: dict[str, Optional[str]], attachments: Optional[list[dict]] = None) -> None:
        payload = {
            "vault_path": None if self._remote_mode else target.get("vault_path"),
            "page_mode": target.get("page_mode"),
            "page_ref": target.get("page_ref"),
            "text": text,
        }
        attachments = attachments or []
        if attachments and not self._remote_mode:
            try:
                from sp.app import quickcapture as qc
                from sp.server.adapters import files
                vault_root = Path(target.get("vault_path") or "")
                page_mode = target.get("page_mode") or "today"
                page_ref = target.get("page_ref")
                if page_mode == "today":
                    target_file, _created = files.ensure_journal_today(vault_root, template=None)
                    rel_path = f"/{target_file.relative_to(vault_root).as_posix()}"
                else:
                    rel_path = qc._resolve_custom_page_ref(page_ref or "")
                content = files.read_file(vault_root, rel_path)
                from datetime import datetime
                now = datetime.now()
                if rel_path.startswith("/Journal/"):
                    timestamp = now.strftime("%I:%M %p").lower()
                else:
                    timestamp = f"{now:%Y-%m-%d}: {now.strftime('%I:%M%p').lower()}"
                saved_images = qc._persist_attachments(vault_root, rel_path, attachments)
                entry_lines = qc._build_quick_capture_entry(text, timestamp, saved_images)
                updated = qc._append_quick_capture_section(content, entry_lines)
                files.write_file(vault_root, rel_path, updated)
                self.statusBar().showMessage("Quick Capture saved.", 3000)
                return
            except Exception:
                self.statusBar().showMessage("Quick Capture failed.", 4000)
                return
        try:
            resp = self.http.post("/api/quick-capture", json=payload)
            resp.raise_for_status()
        except Exception:
            self.statusBar().showMessage("Quick Capture failed.", 4000)

    def _quick_capture_subtitle(self, target: dict[str, Optional[str]]) -> str:
        vault_path = target.get("vault_path") or ""
        vault_name = Path(vault_path).name or vault_path
        page_mode = target.get("page_mode") or "today"
        page_ref = target.get("page_ref") or ""
        page_label = "Today's Journal" if page_mode == "today" else page_ref
        if config.load_quick_capture_vault():
            return f"Dropping to {vault_name}: {page_label}"
        current_name = Path(self.vault_root).name if self.vault_root else vault_name
        return f"Dropping to: {current_name}: {page_label} | Today's Journal"
        try:
            self._main_soft_scroll_lines = config.load_main_soft_scroll_lines(5)
        except Exception:
            self._main_soft_scroll_lines = 5
        self._apply_application_fonts_immediate()
        # If AI chat panel exists, refresh its server/model selections immediately
        try:
            if self.right_panel.ai_chat_panel:
                # Refresh server dropdown (this will respect saved default server)
                try:
                    self.right_panel.ai_chat_panel._refresh_server_dropdown()
                except Exception:
                    pass
                # Refresh model dropdown and apply default model
                try:
                    self.right_panel.ai_chat_panel._refresh_model_dropdown(initial=True)
                except Exception:
                    pass
        except Exception:
            pass

    def _apply_application_fonts_immediate(self) -> None:
        """Apply application/editor/AI chat fonts immediately after preferences change."""
        app = QApplication.instance()
        try:
            app_family = config.load_application_font()
            app_size = config.load_application_font_size()
        except Exception:
            app_family = None
            app_size = None
        if app:
            try:
                font = app.font()
                if app_family:
                    font.setFamily(app_family)
                if app_size:
                    font.setPointSize(max(6, app_size))
                app.setFont(font)
            except Exception:
                pass
        # Preserve editor font size; only update AI chat size relative to current editor size
        base_ai_font = max(6, (self.font_size or 14) - 2)
        ai_font_size = config.load_ai_chat_font_size(base_ai_font)
        self.right_panel.set_font_size(ai_font_size)

    def _open_task_from_panel(self, path: str, line: int, *, preserve_calendar_state: bool = False) -> None:
        if log_enabled("ui_state"):
            print(f"[MAIN_WINDOW] _open_task_from_panel called: {path}:{line}, current_path={self.current_path}")
        # Remember which widget had focus (should be task tree)
        focused_widget = self.focusWidget()
        if log_enabled("ui_state"):
            print(f"[MAIN_WINDOW] Focus before: {focused_widget}")
        # Detect activation source (keyboard vs mouse) from sender
        activation_source = None
        sender = self.sender()
        try:
            if hasattr(sender, "consume_activation_source"):
                activation_source = sender.consume_activation_source()
            elif hasattr(sender, "task_panel") and hasattr(sender.task_panel, "consume_activation_source"):
                activation_source = sender.task_panel.consume_activation_source()
        except Exception:
            activation_source = None
        
        # Open the file and jump to the task line
        if path != self.current_path:
            self._open_file(path, sync_calendar=not preserve_calendar_state)
        self._goto_line(line, select_line=True)
        
        # Keyboard activation: move focus to editor; mouse: restore task focus
        if activation_source == "keyboard":
            try:
                self._exit_vi_insert_on_activate()
            except Exception:
                pass
            try:
                self.editor.setFocus(Qt.OtherFocusReason)
            except Exception:
                pass
        elif focused_widget and "Task" in focused_widget.__class__.__name__:
            focused_widget.setFocus()
            if log_enabled("ui_state"):
                print(f"[MAIN_WINDOW] Focus restored to: {focused_widget}")

    def _open_task_from_calendar_panel(self, path: str, line: int) -> None:
        """Open task target page without altering calendar selection/filter state."""
        self._open_task_from_panel(path, line, preserve_calendar_state=True)

    def _normalize_task_date_paths(self, paths: list[str]) -> set[str]:
        normalized: set[str] = set()
        for raw in paths or []:
            text = str(raw or "").strip()
            if not text:
                continue
            try:
                norm = self._normalize_editor_path(text)
            except Exception:
                norm = text if text.startswith("/") else f"/{text.lstrip('/')}"
            if norm:
                normalized.add(norm)
        return normalized

    def _on_task_dates_will_apply(self, paths: list[str]) -> None:
        targets = self._normalize_task_date_paths(paths)
        if not targets:
            return
        if self.current_path in targets:
            try:
                if self._dirty_flag or self.editor.document().isModified():
                    self._save_current_file(auto=True, reason="task date pre-apply")
            except Exception:
                pass
        for win in list(getattr(self, "_page_windows", [])):
            try:
                src = self._normalize_editor_path(str(getattr(win, "_source_path", "") or ""))
            except Exception:
                continue
            if src not in targets:
                continue
            try:
                if bool(win._is_dirty()):
                    win._save_current_file(auto=True, reason="task date pre-apply")
            except Exception:
                pass

    def _on_task_dates_applied(self, paths: list[str]) -> None:
        targets = self._normalize_task_date_paths(paths)
        if not targets:
            return
        if self.current_path in targets:
            can_reload = True
            try:
                can_reload = not (self._dirty_flag or self.editor.document().isModified())
            except Exception:
                can_reload = not self._dirty_flag
            if can_reload:
                self._open_file(
                    self.current_path,
                    add_to_history=False,
                    force=True,
                    restore_history_cursor=True,
                    sync_calendar=False,
                )
            else:
                self.statusBar().showMessage(
                    "Task date updated on current page; reload skipped (unsaved edits).",
                    4000,
                )
        for win in list(getattr(self, "_page_windows", [])):
            try:
                src = self._normalize_editor_path(str(getattr(win, "_source_path", "") or ""))
            except Exception:
                continue
            if src not in targets:
                continue
            try:
                if bool(win._is_dirty()):
                    continue
            except Exception:
                pass
            try:
                win._load_content()
            except Exception:
                pass

    def _open_link_from_panel(self, path: str) -> None:
        if not path:
            return
        # Support fragment anchors in panel links (e.g. /Journal/2025/.../15.txt#slug)
        base, anchor = self._split_link_anchor(path)
        path = self._normalize_editor_path(base)
        # Special case: if the path matches the vault root name or is the vault root folder, open the main page
        if self.vault_root_name:
            # Accept /VaultRoot, VaultRoot, /VaultRoot/, or /VaultRoot/VaultRoot.md as vault root
            normalized = path.strip().strip("/")
            if (
                normalized == self.vault_root_name
                or normalized == f"{self.vault_root_name}{PAGE_SUFFIX.strip()}"
                or normalized == f"{self.vault_root_name}{PAGE_SUFFIX}"
                or normalized == f"{self.vault_root_name}/{self.vault_root_name}{PAGE_SUFFIX}"
            ):
                main_page = self._vault_root_page_path()
                if main_page:
                    self._open_file(main_page)
                    self.right_panel.focus_link_tab(main_page)
                self._apply_navigation_focus("navigator")
                return
        self._open_file(path)
        # Scroll to anchor if provided
        try:
            slug = self._anchor_slug(anchor)
            self._scroll_to_anchor_slug(slug)
        except Exception:
            pass
        self.right_panel.focus_link_tab(path)
        self._apply_navigation_focus("navigator")

    def _open_calendar_page(self, path: str) -> None:
        """Open a page from the Calendar tab without changing tabs."""
        if not path:
            return
        # Handle possible anchor fragment in calendar links
        base, anchor = self._split_link_anchor(path)
        norm = self._normalize_editor_path(base)
        # Preserve calendar selection/filter context while reviewing linked pages.
        self._open_file(norm, sync_calendar=False)
        try:
            slug = self._anchor_slug(anchor)
            self._scroll_to_anchor_slug(slug)
        except Exception:
            pass
        # Keep the Calendar tab active and return focus to its tree
        try:
            self.right_panel.tabs.setCurrentWidget(self.right_panel.calendar_panel)
            self.right_panel.calendar_panel.journal_tree.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def _refresh_detached_link_panels(self, path: Optional[str]) -> None:
        """Keep detached Link Navigator windows in sync with the current page."""
        if not self._detached_link_panels:
            return
        if not path or not config.has_active_vault():
            for panel in list(self._detached_link_panels):
                panel.set_page(None)
            return
        norm = self._normalize_editor_path(path)
        for panel in list(self._detached_link_panels):
            try:
                panel.set_page(norm)
            except Exception:
                pass


    # --- Detached panel windows -------------------------------------------------

    def _register_detached_panel(self, window: QMainWindow) -> None:
        """Keep a reference to detached panels to prevent GC, and remove on close."""
        self._detached_panels.append(window)
        window.destroyed.connect(
            lambda: self._detached_panels.remove(window) if window in self._detached_panels else None
        )
    
    def _prepare_top_level_window(self, window: QMainWindow) -> None:
        """Ensure detached windows are true top-level (Alt+Tab visible)."""
        try:
            window.setParent(None)
            window.setWindowFlag(Qt.Window, True)
            window.setWindowFlag(Qt.Tool, False)
            window.setAttribute(Qt.WA_NativeWindow, True)
            window.setWindowModality(Qt.NonModal)
            # Set window icon explicitly (especially important on Windows)
            from sp.app.main import get_app_icon
            window.setWindowIcon(get_app_icon())
        except Exception:
            pass

    def _open_task_panel_window(self) -> None:
        if not self._feature_tasks_enabled:
            return
        if not config.has_active_vault():
            self._alert("Open a vault first.")
            return
        panel = TaskPanel(font_size_key="task_font_size_detached")
        panel.set_vault_root(self._local_vault_root() or "")
        panel.set_filter_clear_enabled(False)
        try:
            panel.set_navigation_filter(self._nav_filter_path, refresh=False)
        except Exception:
            pass
        panel.refresh()
        panel.taskActivated.connect(self._open_task_from_panel)
        panel.taskDatesWillApply.connect(self._on_task_dates_will_apply)
        panel.taskDatesApplied.connect(self._on_task_dates_applied)
        window = QMainWindow(None)
        self._prepare_top_level_window(window)
        window.setWindowTitle("Tasks")
        window.setCentralWidget(panel)
        window.resize(720, 640)
        self._apply_geometry_persistence(window, "task_panel_window")
        window.show()
        self._register_detached_panel(window)
    
    def _open_calendar_panel_window(self) -> None:
        if not self._feature_calendar_enabled:
            return
        if not config.has_active_vault():
            self._alert("Open a vault first.")
            return
        panel = CalendarPanel(
            font_size_key="calendar_font_size_detached",
            http_client=self.http,
            api_base=self.api_base,
        )
        try:
            panel.set_base_font_size(self.font_size)
        except Exception:
            pass
        panel.set_vault_root(self._local_vault_root() or "")
        panel.dateActivated.connect(self.right_panel.calendar_panel.dateActivated.emit)
        panel.pageActivated.connect(self._open_calendar_page)
        panel.taskActivated.connect(self._open_task_from_calendar_panel)
        panel.taskDatesWillApply.connect(self._on_task_dates_will_apply)
        panel.taskDatesApplied.connect(self._on_task_dates_applied)
        panel.openInWindowRequested.connect(self._open_page_editor_window)
        panel.remoteRequestObserved.connect(self._on_right_panel_remote_request_observed, Qt.QueuedConnection)
        panel.set_remote_mode(bool(self._remote_mode))
        window = QMainWindow(None)
        self._prepare_top_level_window(window)
        window.setWindowTitle("Calendar")
        window.setCentralWidget(panel)
        window.resize(760, 680)
        class _CalendarResizeHook(QObject):
            def __init__(self, target: CalendarPanel) -> None:
                super().__init__()
                self._target = target

            def eventFilter(self, obj, event):  # type: ignore[override]
                if event.type() == QEvent.Resize:
                    try:
                        self._target.update_calendar_layout()
                    except Exception:
                        pass
                return False

        hook = _CalendarResizeHook(panel)
        window.installEventFilter(hook)
        window._calendar_resize_hook = hook  # type: ignore[attr-defined]
        self._apply_geometry_persistence(window, "calendar_panel_window")
        window.show()
        self._register_detached_panel(window)

    def _open_link_panel_window(self) -> None:
        if not self._feature_link_navigator_enabled:
            return
        if not config.has_active_vault():
            self._alert("Open a vault first.")
            return
        panel = LinkNavigatorPanel()
        current = self.current_path
        try:
            panel.reload_mode_from_config()
            panel.reload_layout_from_config()
        except Exception:
            pass
        if current:
            panel.set_page(self._normalize_editor_path(current))
        panel.pageActivated.connect(self._open_link_from_panel)
        panel.openInWindowRequested.connect(self._open_page_editor_window)
        window = QMainWindow(None)
        self._prepare_top_level_window(window)
        window.setWindowTitle("Link Navigator")
        window.setCentralWidget(panel)
        window.resize(760, 680)
        self._apply_geometry_persistence(window, "link_navigator_window")
        window.show()
        self._register_detached_panel(window)
        self._detached_link_panels.append(panel)
        window.destroyed.connect(lambda: self._remove_detached_link_panel(panel))

    def _open_ai_chat_window(self) -> None:
        if not config.load_enable_ai_chats():
            self._alert("Enable AI Chat in settings to use this window.")
            return
        
        # First check if there's a detached AI chat window
        if self._detached_ai_chat_window and self._detached_ai_chat_window.isVisible():
            try:
                self._detached_ai_chat_window.raise_()
                self._detached_ai_chat_window.activateWindow()
                if self._detached_ai_chat_panel:
                    self._detached_ai_chat_panel.focus_input()
            except Exception:
                pass
            return
        
        # Check if AI Chat tab exists in right panel
        if self.right_panel.ai_chat_panel:
            # Ensure right panel is visible
            self._ensure_right_panel_visible()
            
            # Focus the AI Chat tab
            self.right_panel.focus_ai_chat(self.current_path)
            return
        
        # No tabbed or detached window - create a new detached window
        panel = AIChatPanel(font_size=self.right_panel.get_ai_font_size(), api_client=self.http)
        panel.set_font_family(config.load_ai_chat_font_family())
        local_vault_root = self._local_vault_root()
        if local_vault_root:
            panel.set_vault_root(local_vault_root)
        if self.current_path:
            panel.open_chat_for_page(self._normalize_editor_path(self.current_path))
        panel.chatNavigateRequested.connect(self._on_ai_chat_navigate)
        panel.pageWritten.connect(self._on_ai_chat_page_written)
        window = QMainWindow(None)
        self._prepare_top_level_window(window)
        window.setWindowTitle("AI Chat")
        window.setCentralWidget(panel)
        window.resize(820, 720)
        self._apply_geometry_persistence(window, "ai_chat_window")
        window.show()
        self._detached_ai_chat_panel = panel
        self._detached_ai_chat_window = window
        window.destroyed.connect(lambda: self._clear_detached_ai_chat())
        self._register_detached_panel(window)

    def _remove_detached_link_panel(self, panel: LinkNavigatorPanel) -> None:
        if panel in self._detached_link_panels:
            self._detached_link_panels.remove(panel)

    def _clear_detached_ai_chat(self) -> None:
        self._detached_ai_chat_panel = None
        self._detached_ai_chat_window = None

    def _active_ai_chat_panel(self) -> Optional[AIChatPanel]:
        panel = self._detached_ai_chat_panel
        window = self._detached_ai_chat_window
        if panel and window and window.isVisible():
            return panel
        return None

    def _apply_geometry_persistence(self, window: QMainWindow, key: str) -> None:
        """Restore and persist window geometry for detached panels."""
        geom_b64 = config.load_dialog_geometry(key)
        if geom_b64:
            try:
                geometry = QByteArray.fromBase64(geom_b64.encode("ascii"))
                window.restoreGeometry(geometry)
            except Exception:
                pass

        class _GeometrySaver(QObject):
            def __init__(self, target: QMainWindow, name: str) -> None:
                super().__init__(target)
                self._target = target
                self._name = name
                self._timer = QTimer(self)
                self._timer.setSingleShot(True)
                self._timer.setInterval(200)
                self._timer.timeout.connect(self._save)
                target.installEventFilter(self)

            def eventFilter(self, obj, event):
                if obj is self._target and event.type() in (QEvent.Resize, QEvent.Move, QEvent.Close):
                    self._timer.start()
                return super().eventFilter(obj, event)

            def _save(self) -> None:
                try:
                    geom = (
                        self._target.saveGeometry().toBase64().data().decode("ascii")
                        if hasattr(self._target, "saveGeometry")
                        else None
                    )
                    if geom:
                        config.save_dialog_geometry(self._name, geom)
                except Exception:
                    pass

        saver = _GeometrySaver(window, key)
        window._geometry_saver = saver  # Keep reference

    def _open_page_editor_window(self, path: str) -> None:
        """Open a lightweight editor window for a single page (shared server)."""
        if not path or not self.vault_root:
            return
        rel_path = self._normalize_editor_path(path)
        try:
            window = PageEditorWindow(
                api_base=self.api_base,
                vault_root=self.vault_root,
                page_path=rel_path,
                read_only=self._read_only,
                open_in_main_callback=lambda target, **kw: self._open_link_in_context(target, **kw),
                local_auth_token=self._local_auth_token,
                remote_mode=self._remote_mode,
                auth_prompt=self._prompt_remote_login if self._remote_mode else None,
                http_headers=dict(self.http.headers),
                http_auth=self.http.auth,
                verify_tls=self._verify_tls,
                parent=None,
            )
            try:
                window.setWindowFlag(Qt.Window, True)
                window.setWindowFlag(Qt.Tool, False)
                window.setAttribute(Qt.WA_NativeWindow, True)
                window.setWindowModality(Qt.NonModal)
            except Exception:
                pass
            window.show()
            self._page_windows.append(window)
            window.destroyed.connect(lambda: self._page_windows.remove(window) if window in self._page_windows else None)
        except Exception as exc:
            self._alert(f"Failed to open editor window: {exc}")

    def _open_plantuml_editor(self, file_path) -> None:
        """Open a PlantUML editor window for the given .puml file."""
        if not file_path:
            return
        
        try:
            from .plantuml_editor_window import PlantUMLEditorWindow
            if isinstance(file_path, dict) and file_path.get("kind") == "remote":
                self._open_remote_plantuml_editor(file_path.get("path", ""), file_path.get("page_path"))
                return
            if log_enabled("diagrams"):
                print(f"[MainWindow] Opening PlantUML editor for: {file_path}")
            
            window = PlantUMLEditorWindow(str(file_path), parent=None)
            try:
                window.setWindowFlag(Qt.Window, True)
                window.setWindowFlag(Qt.Tool, False)
                window.setAttribute(Qt.WA_NativeWindow, True)
                window.setWindowModality(Qt.NonModal)
            except Exception:
                pass
            # Keep a strong reference so the window isn't GC'd immediately
            if not hasattr(self, "_plantuml_windows"):
                self._plantuml_windows: list[QMainWindow] = []
            self._plantuml_windows.append(window)
            try:
                window.destroyed.connect(lambda: self._plantuml_windows.remove(window) if window in self._plantuml_windows else None)
            except Exception:
                pass
            window.show()
        except Exception as exc:
            self._alert(f"Failed to open PlantUML editor: {exc}")

    def _open_remote_plantuml_editor(self, remote_path: str, page_key: Optional[str]) -> None:
        if not remote_path:
            return
        try:
            resp = self.http.get("/api/file/raw", params={"path": remote_path})
            if resp.status_code == 401 and self._remote_mode:
                if self._prompt_remote_login():
                    resp = self.http.get("/api/file/raw", params={"path": remote_path})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to load {remote_path}")
            return
        try:
            content = resp.content.decode("utf-8")
        except Exception as exc:
            self._alert(f"Failed to decode {remote_path}: {exc}")
            return
        cache_root = self._ensure_remote_cache_root()
        cache_path = (cache_root / "attachments" / remote_path.lstrip("/")).resolve()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            self._alert(f"Failed to cache remote file: {exc}")
            return

        def _save_remote(content_text: str):
            if not page_key:
                return False, "Missing page context for attachment save."
            try:
                write_resp = self.http.post(
                    "/files/attach",
                    data={"page_path": page_key},
                    files={"files": (Path(remote_path).name, content_text.encode("utf-8"), "text/plain")},
                )
                if write_resp.status_code == 401 and self._remote_mode:
                    if self._prompt_remote_login():
                        write_resp = self.http.post(
                            "/files/attach",
                            data={"page_path": page_key},
                            files={"files": (Path(remote_path).name, content_text.encode("utf-8"), "text/plain")},
                        )
                if write_resp.status_code == 409:
                    return False, "Save failed: server version changed. Reopen to merge."
                write_resp.raise_for_status()
            except httpx.HTTPError as exc:
                return False, str(exc)
            try:
                cache_path.write_text(content_text, encoding="utf-8")
            except Exception:
                pass
            return True, None

        try:
            from .plantuml_editor_window import PlantUMLEditorWindow
            window = PlantUMLEditorWindow(str(cache_path), parent=None, on_save=_save_remote)
            try:
                window.setWindowFlag(Qt.Window, True)
                window.setWindowFlag(Qt.Tool, False)
                window.setAttribute(Qt.WA_NativeWindow, True)
                window.setWindowModality(Qt.NonModal)
            except Exception:
                pass
            if not hasattr(self, "_plantuml_windows"):
                self._plantuml_windows: list[QMainWindow] = []
            self._plantuml_windows.append(window)
            try:
                window.destroyed.connect(lambda: self._plantuml_windows.remove(window) if window in self._plantuml_windows else None)
            except Exception:
                pass
            window.show()
        except Exception as exc:
            self._alert(f"Failed to open PlantUML editor: {exc}")

    def _open_mermaid_editor(self, file_path) -> None:
        """Open a Mermaid editor window for the given .mmd/.mermaid file."""
        if not file_path:
            return
        try:
            from .mermaid_editor_window import MermaidEditorWindow
            if isinstance(file_path, dict) and file_path.get("kind") == "remote":
                self._open_remote_mermaid_editor(file_path.get("path", ""), file_path.get("page_path"))
                return
            print(f"[MainWindow] Opening Mermaid editor for: {file_path}")

            window = MermaidEditorWindow(str(file_path), parent=None)
            try:
                window.setWindowFlag(Qt.Window, True)
                window.setWindowFlag(Qt.Tool, False)
                window.setAttribute(Qt.WA_NativeWindow, True)
                window.setWindowModality(Qt.NonModal)
            except Exception:
                pass
            if not hasattr(self, "_mermaid_windows"):
                self._mermaid_windows: list[QMainWindow] = []
            self._mermaid_windows.append(window)
            try:
                window.destroyed.connect(lambda: self._mermaid_windows.remove(window) if window in self._mermaid_windows else None)
            except Exception:
                pass
            window.show()
        except Exception as exc:
            self._alert(f"Failed to open Mermaid editor: {exc}")

    def _open_remote_mermaid_editor(self, remote_path: str, page_key: Optional[str]) -> None:
        if not remote_path:
            return
        try:
            resp = self.http.get("/api/file/raw", params={"path": remote_path})
            if resp.status_code == 401 and self._remote_mode:
                if self._prompt_remote_login():
                    resp = self.http.get("/api/file/raw", params={"path": remote_path})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to load {remote_path}")
            return
        try:
            content = resp.content.decode("utf-8")
        except Exception as exc:
            self._alert(f"Failed to decode {remote_path}: {exc}")
            return
        cache_root = self._ensure_remote_cache_root()
        cache_path = (cache_root / "attachments" / remote_path.lstrip("/")).resolve()
        try:
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            self._alert(f"Failed to cache remote file: {exc}")
            return

        def _save_remote(content_text: str):
            if not page_key:
                return False, "Missing page context for attachment save."
            try:
                write_resp = self.http.post(
                    "/files/attach",
                    data={"page_path": page_key},
                    files={"files": (Path(remote_path).name, content_text.encode("utf-8"), "text/plain")},
                )
                if write_resp.status_code == 401 and self._remote_mode:
                    if self._prompt_remote_login():
                        write_resp = self.http.post(
                            "/files/attach",
                            data={"page_path": page_key},
                            files={"files": (Path(remote_path).name, content_text.encode("utf-8"), "text/plain")},
                        )
                if write_resp.status_code == 409:
                    return False, "Save failed: server version changed. Reopen to merge."
                write_resp.raise_for_status()
            except httpx.HTTPError as exc:
                return False, str(exc)
            try:
                cache_path.write_text(content_text, encoding="utf-8")
            except Exception:
                pass
            return True, None

        try:
            from .mermaid_editor_window import MermaidEditorWindow
            window = MermaidEditorWindow(str(cache_path), parent=None, on_save=_save_remote)
            try:
                window.setWindowFlag(Qt.Window, True)
                window.setWindowFlag(Qt.Tool, False)
                window.setAttribute(Qt.WA_NativeWindow, True)
                window.setWindowModality(Qt.NonModal)
            except Exception:
                pass
            if not hasattr(self, "_mermaid_windows"):
                self._mermaid_windows: list[QMainWindow] = []
            self._mermaid_windows.append(window)
            try:
                window.destroyed.connect(lambda: self._mermaid_windows.remove(window) if window in self._mermaid_windows else None)
            except Exception:
                pass
            window.show()
        except Exception as exc:
            self._alert(f"Failed to open Mermaid editor: {exc}")

    def _toggle_mode_overlay(self, mode: str) -> None:
        """Toggle Focus/Audience mode full-screen overlay."""
        normalized = (mode or "").lower()
        if normalized not in {"focus", "audience"}:
            return
        if not hasattr(self, "_pending_mode_target"):
            self._pending_mode_target: str | None = None
        if getattr(self, "_mode_window_pending", False):
            return
        if self._mode_window:
            current_mode = getattr(self._mode_window, "mode", "")
            if current_mode != normalized:
                self._pending_mode_target = normalized
                self._mode_window_pending = True
                try:
                    self._mode_window.close()
                except Exception:
                    self._mode_window_pending = False
                return
            self._mode_window_pending = True
            try:
                self._mode_window.close()
            except Exception:
                self._mode_window_pending = False
            self._mode_window = None
            return
        # If a reload is pending from a prior close, process it before opening another overlay.
        if getattr(self, "_pending_reload_path", None) and not self._mode_window_pending:
            self._process_mode_pending()
            if self._mode_window_pending:
                return
        if not (self.current_path or self.editor.toPlainText().strip()):
            self.statusBar().showMessage("Open a page before entering Focus/Audience mode", 3000)
            return
        self._mode_window_pending = True
        # Remember cursor to seed overlay and restore later
        try:
            self._last_cursor_for_mode = int(self.editor.textCursor().position())
        except Exception:
            self._last_cursor_for_mode = 0
        settings = config.load_focus_mode_settings() if normalized == "focus" else config.load_audience_mode_settings()
        try:
            window = ModeWindow(
                normalized,
                self.editor,
                vault_root=self.vault_root,
                page_path=self.current_path,
                read_only=self._read_only,
                heading_provider=lambda: list(self._toc_headings or []),
                settings=settings,
                initial_cursor=getattr(self, "_last_cursor_for_mode", 0),
                parent=self,
            )
            window.closed.connect(self._on_mode_overlay_closed)
            try:
                window.ready.connect(self._on_mode_overlay_ready)
            except Exception:
                self._mode_window_pending = False
                self._process_mode_pending()
            self._mode_window = window
            window.show()
        except Exception as exc:
            self._alert(f"Unable to open {normalized.title()} mode: {exc}")
            self._mode_window_pending = False
            self._process_mode_pending()

    def _on_mode_overlay_ready(self) -> None:
        self._mode_window_pending = False
        self._process_mode_pending()

    def _on_mode_overlay_closed(self, mode: str, cursor_pos: int) -> None:
        """Reset state after an overlay window closes."""
        self._mode_window_pending = False
        self._mode_window = None
        pending_target = getattr(self, "_pending_mode_target", None)
        self._pending_mode_target = None
        if self.current_path:
            self._pending_reload_path = self.current_path
        self._restore_editor_width_constraints()
        try:
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, int(cursor_pos)))
            self.editor.setTextCursor(cursor)
        except Exception:
            pass
        # Force a reload to drop any lingering overlay styling (e.g., width wrap) while keeping cursor.
        if self.current_path:
            try:
                if self._feature_remember_cursor_position_enabled:
                    self._history_cursor_positions[self.current_path] = int(cursor_pos)
            except Exception:
                pass
            # Save current buffer if it is dirty before reloading.
            try:
                if self.editor.document().isModified() and not self._read_only:
                    self._save_current_file(auto=True, reason="pre-reload save")
            except Exception:
                pass
        QTimer.singleShot(0, lambda: self.editor.setFocus(Qt.ShortcutFocusReason))
        self._process_mode_pending(pending_target)

    def _process_mode_pending(self, pending_target: str | None = None) -> None:
        """Handle deferred reloads or mode switches once overlays are settled."""
        if self._mode_window_pending:
            return
        reload_path = getattr(self, "_pending_reload_path", None)
        self._pending_reload_path = None
        if reload_path:
            QTimer.singleShot(0, lambda p=reload_path: self._reload_page_preserve_cursor(p))
        if pending_target:
            QTimer.singleShot(0, lambda m=pending_target: self._toggle_mode_overlay(m))

    def _restore_editor_width_constraints(self) -> None:
        """Ensure the main editor isn't left with focus-mode width limits."""
        try:
            self.editor.setMaximumWidth(getattr(self, "_default_editor_max_width", 16777215))
            self.editor.setMinimumWidth(max(self.editor.minimumWidth(), 200))
            self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
            # Reset wrap to widget width in case overlay altered document options.
            try:
                self.editor.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
                opt = self.editor.document().defaultTextOption()
                opt.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
                self.editor.document().setDefaultTextOption(opt)
            except Exception:
                pass
            for widget in (self.editor, self.editor.parentWidget(), getattr(self, "editor_split", None), getattr(self, "main_splitter", None)):
                try:
                    if widget:
                        widget.updateGeometry()
                except Exception:
                    continue
        except Exception:
            pass

    def _scroll_cursor_top_quarter(self) -> None:
        """Keep cursor near the top quarter of the viewport when regaining focus."""
        sb = self.editor.verticalScrollBar()
        viewport = self.editor.viewport()
        if not sb or not viewport:
            return
        rect = self.editor.cursorRect()
        target = int(viewport.height() * 0.25)
        delta = rect.top() - target
        if delta:
            sb.setValue(max(sb.minimum(), min(sb.maximum(), sb.value() + delta)))

    def _build_minibar(self, labels: list[str], *, side: str) -> tuple[QWidget, QTabBar, QToolButton]:
        toggle = QToolButton()
        toggle.setAutoRaise(True)
        toggle.setToolButtonStyle(Qt.ToolButtonIconOnly)
        toggle.setFocusPolicy(Qt.NoFocus)
        toggle.setToolTip("Show sidebar")
        app = QApplication.instance()
        try:
            base_lightness = app.palette().color(QPalette.ColorRole.Base).lightness() if app else 0
        except Exception:
            base_lightness = 0
        is_light_palette = base_lightness >= 128
        pref = (config.load_theme_preference() or "").strip().lower()
        using_default_dark_theme = pref in {"", "default", "dark-theme", "dark-theme.json", "theme-config", "theme-config.json"}
        selected_bg_default = "#eef2f7" if is_light_palette else "#2b2b2b"
        selected_text_default = "#111827" if is_light_palette else "#ffffff"
        unselected_text_default = "#4b5563" if is_light_palette else "#c0c0c0"
        if is_light_palette and using_default_dark_theme:
            selected_bg = selected_bg_default
            selected_text = selected_text_default
            unselected_text = unselected_text_default
        else:
            selected_bg = theme_value("main_window.minibar.selected_bg", selected_bg_default)
            selected_text = theme_value("main_window.minibar.selected_text", selected_text_default)
            unselected_text = theme_value("main_window.minibar.unselected_text", unselected_text_default)
        bar = QTabBar()
        bar.setDocumentMode(True)
        bar.setExpanding(False)
        bar.setUsesScrollButtons(False)
        bar.setFocusPolicy(Qt.NoFocus)
        bar.setElideMode(Qt.ElideNone)
        bar.setShape(QTabBar.RoundedWest if side == "left" else QTabBar.RoundedEast)
        for label in labels:
            bar.addTab(label)
        bar.setStyleSheet(
            "QTabBar::tab { padding: 6px 10px; margin: 2px 0; }"
            "QTabBar::tab:selected { background: "
            f"{selected_bg}; "
            "color: "
            f"{selected_text}; }}"
            "QTabBar::tab:!selected { color: "
            f"{unselected_text}; }}"
        )
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignTop)
        layout.addWidget(toggle)
        layout.addWidget(bar)
        wrapper.setFixedWidth(self._minibar_width)
        return wrapper, bar, toggle

    def _show_right_minibar_context_menu(self, pos: QPoint) -> None:
        bar = self._right_minibar_bar
        if not bar:
            return
        index = bar.tabAt(pos)
        if index < 0:
            return
        widget = self.right_panel.tabs.widget(index)
        menu = QMenu(self)
        if self.right_panel.task_panel and widget == self.right_panel.task_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self._open_task_panel_window)
        elif self.right_panel.calendar_panel and widget == self.right_panel.calendar_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self._open_calendar_panel_window)
        elif self.right_panel.link_panel and widget == self.right_panel.link_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self._open_link_panel_window)
        elif widget == self.right_panel.ai_chat_panel:
            action = menu.addAction("Open in New Window")
            action.triggered.connect(self._open_ai_chat_window)
        else:
            return
        menu.exec(bar.mapToGlobal(pos))

    def _sidebar_toggle_icon(self, collapsed: bool) -> Optional[QIcon]:
        icon_name = "show-sidebar.svg" if collapsed else "hide-sidebar.svg"
        return self._load_icon(self._find_asset(icon_name), self._main_icon_color(), size=16)

    def _update_sidebar_toggle_icons(self) -> None:
        if not hasattr(self, "_left_panel_stack") or not hasattr(self, "_right_panel_stack"):
            return
        left_collapsed = not self._is_left_panel_expanded()
        right_collapsed = not self._is_right_panel_expanded()
        left_icon = self._sidebar_toggle_icon(left_collapsed)
        right_icon = self._sidebar_toggle_icon(not right_collapsed)
        if self._left_toggle_button and left_icon:
            self._left_toggle_button.setIcon(left_icon)
            self._left_toggle_button.setToolTip("Show sidebar" if left_collapsed else "Hide sidebar")
        if self._left_minibar_toggle and left_icon:
            self._left_minibar_toggle.setIcon(left_icon)
            self._left_minibar_toggle.setToolTip("Show sidebar" if left_collapsed else "Hide sidebar")
        if self._right_toggle_button and right_icon:
            self._right_toggle_button.setIcon(right_icon)
            self._right_toggle_button.setToolTip("Show sidebar" if right_collapsed else "Hide sidebar")
        if self._right_minibar_toggle and right_icon:
            self._right_minibar_toggle.setIcon(right_icon)
            self._right_minibar_toggle.setToolTip("Show sidebar" if right_collapsed else "Hide sidebar")

    def _right_minibar_labels(self) -> list[str]:
        labels: list[str] = []
        for i in range(self.right_panel.tabs.count()):
            label = self.right_panel.tabs.tabText(i) or ""
            if label.endswith(")") and "(" in label:
                label = label.rsplit("(", 1)[0].strip()
            labels.append(label or "Tab")
        return labels

    def _left_minibar_labels(self) -> list[str]:
        labels: list[str] = []
        for i in range(self.left_tab_widget.count()):
            label = self.left_tab_widget.tabText(i) or ""
            labels.append(label or "Tab")
        return labels

    def _sync_left_minibar_selection(self, index: int) -> None:
        if not self._left_minibar_bar:
            return
        blocker = QSignalBlocker(self._left_minibar_bar)
        self._left_minibar_bar.setCurrentIndex(index)
        del blocker

    def _sync_right_minibar_selection(self, index: int) -> None:
        if not self._right_minibar_bar:
            return
        blocker = QSignalBlocker(self._right_minibar_bar)
        self._right_minibar_bar.setCurrentIndex(index)
        del blocker

    def _refresh_right_minibar_tabs(self) -> None:
        if not self._right_minibar_bar:
            return
        blocker = QSignalBlocker(self._right_minibar_bar)
        while self._right_minibar_bar.count() > 0:
            self._right_minibar_bar.removeTab(0)
        for label in self._right_minibar_labels():
            self._right_minibar_bar.addTab(label)
        self._right_minibar_bar.setCurrentIndex(self.right_panel.tabs.currentIndex())
        del blocker

    def _refresh_left_minibar_tabs(self) -> None:
        if not self._left_minibar_bar:
            return
        blocker = QSignalBlocker(self._left_minibar_bar)
        while self._left_minibar_bar.count() > 0:
            self._left_minibar_bar.removeTab(0)
        for label in self._left_minibar_labels():
            self._left_minibar_bar.addTab(label)
        self._left_minibar_bar.setCurrentIndex(self.left_tab_widget.currentIndex())
        del blocker

    def _expand_left_from_minibar(self, index: int) -> None:
        self.left_tab_widget.setCurrentIndex(index)
        self._set_left_panel_collapsed(False)

    def _expand_right_from_minibar(self, index: int) -> None:
        if QApplication.mouseButtons() & Qt.RightButton:
            return
        self.right_panel.tabs.setCurrentIndex(index)
        self._set_right_panel_collapsed(False)

    def _is_left_panel_expanded(self) -> bool:
        return self._left_panel_stack.currentWidget() == self.left_tab_widget

    def _is_right_panel_expanded(self) -> bool:
        return self._right_panel_stack.currentWidget() == self.right_panel

    def _set_left_panel_collapsed(self, collapsed: bool) -> None:
        sizes = self.main_splitter.sizes()
        total = sum(sizes) or max(1, self.main_splitter.width())
        if collapsed:
            if self._is_left_panel_expanded():
                self._saved_left_width = sizes[0] if sizes else getattr(self, "_saved_left_width", 240)
            self._left_panel_stack.setCurrentWidget(self.left_minibar)
            self.left_panel_container.setFixedWidth(self._minibar_width)
            self.main_splitter.setSizes([self._minibar_width, max(1, total - self._minibar_width)])
        else:
            self._left_panel_stack.setCurrentWidget(self.left_tab_widget)
            self.left_panel_container.setMinimumWidth(self.left_tab_widget.minimumWidth())
            self.left_panel_container.setMaximumWidth(16777215)
            width = getattr(self, "_saved_left_width", 240)
            self.main_splitter.setSizes([width, max(1, total - width)])
        self._update_sidebar_toggle_icons()

    def _set_right_panel_collapsed(self, collapsed: bool) -> None:
        sizes = self.editor_split.sizes()
        total = sum(sizes) or max(1, self.editor_split.width())
        if collapsed:
            if self._is_right_panel_expanded():
                self._saved_right_width = sizes[1] if len(sizes) > 1 else getattr(self, "_saved_right_width", 360)
            self._right_panel_stack.setCurrentWidget(self.right_minibar)
            self.right_panel_container.setFixedWidth(self._minibar_width)
            self.editor_split.setSizes([max(1, total - self._minibar_width), self._minibar_width])
        else:
            self._right_panel_stack.setCurrentWidget(self.right_panel)
            self.right_panel_container.setMinimumWidth(self._minibar_width)
            self.right_panel_container.setMaximumWidth(16777215)
            width = getattr(self, "_saved_right_width", 360)
            self.editor_split.setSizes([max(1, total - width), max(0, width)])
        self._update_sidebar_toggle_icons()

    def _toggle_left_panel(self) -> None:
        """Show/hide the navigation (tree) panel."""
        self._set_left_panel_collapsed(self._is_left_panel_expanded())
        self._save_panel_visibility()

    def _toggle_right_panel(self) -> None:
        """Show/hide the right tabbed panel."""
        collapsing = self._is_right_panel_expanded()
        self._set_right_panel_collapsed(collapsing)
        if collapsing:
            self.editor.setFocus(Qt.OtherFocusReason)
        self._save_panel_visibility()

    def _ensure_left_panel_visible(self) -> None:
        """Ensure the left navigation panel is visible (used before showing search/tags)."""
        if not self._is_left_panel_expanded():
            self._set_left_panel_collapsed(False)
            self._save_panel_visibility()

    def _ensure_right_panel_visible(self) -> None:
        """Ensure the right panel is visible (used before showing link/AI panes)."""
        if not self._is_right_panel_expanded():
            self._set_right_panel_collapsed(False)
            self._save_panel_visibility()

    def _open_link_in_context(self, link: str, force: bool = False, refresh_only: bool = False) -> None:
        """Handle link activations from the editor (main or popup)."""
        if not link:
            return
        if link.strip() == "//":
            target = self._home_page_path() or self._vault_root_page_path()
            if target:
                if refresh_only and self.current_path == target:
                    self._reload_page_preserve_cursor(target)
                else:
                    self._open_file(target, force=force)
                return
        restore_vi_insert = False
        try:
            sender = self.sender()
            if sender is not None and hasattr(sender, "_vi_restore_after_link_activation"):
                restore_vi_insert = bool(getattr(sender, "_vi_restore_after_link_activation"))
                try:
                    setattr(sender, "_vi_restore_after_link_activation", False)
                except Exception:
                    pass
        except Exception:
            restore_vi_insert = False
        if not restore_vi_insert:
            self._exit_vi_insert_on_activate()
        if "\x00" in link:
            link = link.split("\x00", 1)[0]
        if link.startswith(("http://", "https://")):
            try:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
            except Exception:
                return
            QDesktopServices.openUrl(QUrl(link))
            return
        if self._is_attachment_link(link):
            if self._open_attachment_link(link):
                return
        if self._is_local_file_link(link):
            if self._open_local_file_link(link):
                return
        # Absolute vault-relative path (starts with /): open directly without CamelCase heuristics
        if link.startswith("/"):
            target = self._normalize_editor_path(link)
            if refresh_only and self.current_path == target:
                self._reload_page_preserve_cursor(target)
            elif not refresh_only:
                self._open_file(target, force=force)
            if restore_vi_insert:
                try:
                    self.editor._enter_vi_insert_mode()  # type: ignore[attr-defined]
                except Exception:
                    pass
            return
        # Otherwise treat as page link
        self._open_camel_link(link, focus_target="editor", refresh_only=refresh_only, force=force)
        if restore_vi_insert:
            try:
                self.editor._enter_vi_insert_mode()  # type: ignore[attr-defined]
            except Exception:
                pass

    def _is_local_file_link(self, link: str) -> bool:
        cleaned = (link or "").strip()
        if not cleaned:
            return False
        if cleaned.startswith(("http://", "https://")):
            return False
        if cleaned.startswith(("./", "../", "~/" )):
            return True
        if re.match(r"^[A-Za-z]:[\\/]", cleaned):
            return True
        if cleaned.startswith(("\\\\", "//")):
            return True
        return False

    def _open_local_file_link(self, link: str) -> bool:
        cleaned = (link or "").strip()
        if not cleaned:
            return False
        target_path: Optional[Path] = None
        if cleaned.startswith("~/"):
            target_path = Path(cleaned).expanduser()
        elif cleaned.startswith(("./", "../")):
            if self.vault_root and self.current_path:
                base_dir = Path(self.vault_root) / Path(self.current_path.lstrip("/")).parent
                target_path = (base_dir / cleaned).resolve()
            else:
                target_path = Path(cleaned).expanduser().resolve()
        elif re.match(r"^[A-Za-z]:[\\/]", cleaned) or cleaned.startswith(("\\\\", "//")):
            target_path = Path(cleaned).expanduser()
        if target_path is None:
            return False
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            ok = QDesktopServices.openUrl(QUrl.fromLocalFile(str(target_path)))
            if not ok:
                self.statusBar().showMessage(f"Failed to open file link: {cleaned}", 4000)
            return ok
        except Exception:
            try:
                self.statusBar().showMessage(f"Failed to open file link: {cleaned}", 4000)
            except Exception:
                pass
            return False
    
    def _open_journal_date(self, year: int, month: int, day: int) -> None:
        """Open or create journal entry for the selected date."""
        if not self.vault_root:
            self._alert("Select a vault before creating journal entries.")
            return
        
        # Format paths: Journal/YYYY/MM/DD/DD.txt
        month_str = f"{month:02d}"
        day_str = f"{day:02d}"
        
        # Build the file path
        rel_path = f"/Journal/{year}/{month_str}/{day_str}/{day_str}{PAGE_SUFFIX}"
        
        # Check if file already exists
        from pathlib import Path
        abs_path = Path(self.vault_root) / rel_path.lstrip("/")
        file_exists = abs_path.exists()
        
        if file_exists:
            # File exists, open it normally
            self._pending_selection = rel_path
            self._populate_vault_tree()
            self._open_file(rel_path)
        else:
            # File doesn't exist yet - open virtual page
            self._open_virtual_journal_page(rel_path, year, month, day)
        
        self.editor.setFocus()
        self._apply_focus_borders()
    
    def _open_virtual_journal_page(self, rel_path: str, year: int, month: int, day: int) -> None:
        """Open a virtual (not yet saved) journal page."""
        # Generate template content but don't save to disk yet
        from datetime import date, datetime
        target_date = date(year, month, day)
        
        preferred_day = config.load_default_journal_template()
        day_tpl = self._resolve_template_path(preferred_day, fallback="JournalDay")
        
        # Build date-specific variables
        vars_map = {
            "{{YYYY}}": f"{year}",
            "{{Month}}": target_date.strftime("%B"),
            "{{MM}}": f"{target_date.month:02d}",
            "{{DOW}}": target_date.strftime("%A"),
            "{{dd}}": f"{day:02d}",
            "{{DayDateYear}}": target_date.strftime("%A %d %B %Y"),
        }
        
        content = ""
        cursor_pos = -1
        if day_tpl.exists():
            try:
                raw = day_tpl.read_text(encoding="utf-8")
                print(f"[Template] Loaded journal template: {day_tpl}")
                
                # Process QOTD if template uses it
                if "{{QOTD}}" in raw:
                    vars_map["{{QOTD}}"] = self._get_qotd()
                
                # Find cursor position in original template
                if "{{cursor}}" in raw:
                    cursor_pos = raw.find("{{cursor}}")
                
                # Replace all variables EXCEPT {{cursor}} first
                content = raw
                for k, v in vars_map.items():
                    if k != "{{cursor}}":
                        # If this replacement happens before cursor position, adjust cursor_pos
                        if cursor_pos >= 0:
                            # Count occurrences before cursor position
                            before_cursor = content[:cursor_pos]
                            count = before_cursor.count(k)
                            if count > 0:
                                # Adjust cursor position by the length difference for each replacement
                                len_diff = len(v) - len(k)
                                cursor_pos += count * len_diff
                        content = content.replace(k, v)
                
                # Now remove cursor tag
                content = content.replace("{{cursor}}", "")
                
            except Exception:
                content = f"# {target_date.strftime('%A %d %B %Y')}\n\n"
        else:
            content = f"# {target_date.strftime('%A %d %B %Y')}\n\n"
        
        # Set up editor without saving to disk
        self._refresh_editor_context(rel_path)
        self.current_path = rel_path
        self._suspend_autosave = True
        self._suspend_dirty_tracking = True
        try:
            self.editor.set_markdown(content)
        finally:
            self._suspend_dirty_tracking = False
            self._suspend_autosave = False
        self._dirty_flag = True
        
        # Mark as virtual page and store original template content
        self.virtual_pages.add(rel_path)
        self.virtual_page_original_content[rel_path] = content
        self._mark_homebase_unsynced_local_change()
        
        # Move cursor to template position or end
        cursor = self.editor.textCursor()
        if cursor_pos >= 0:
            # Cursor position from template (before variable substitution)
            # Need to adjust for any variable replacements before cursor position
            cursor.setPosition(min(cursor_pos, len(self.editor.toPlainText())))
        else:
            # Default: move to end
            display_length = len(self.editor.toPlainText())
            cursor.setPosition(display_length)
        self.editor.setTextCursor(cursor)
        
        # Update UI
        display_path = path_to_colon(rel_path) or rel_path
        if hasattr(self, "toc_widget"):
            root_base = ensure_root_colon_link(display_path) if display_path else ""
            self.toc_widget.set_base_path(root_base)
            self.editor.refresh_heading_outline()
        self.statusBar().showMessage(f"Editing (unsaved) {display_path}")
        self._update_window_title()
        
        # Update calendar to show this date
        self._update_calendar_for_journal_page(rel_path)
        
        # Refresh tree to show italicized entry
        self._populate_vault_tree()
        
        # Update attachments panel (virtual pages may still have folders)
        if rel_path:
            full_path = Path(self.vault_root) / rel_path.lstrip("/")
            has_chat = self.right_panel.set_current_page(full_path, rel_path)
            self.editor.set_ai_chat_available(has_chat, active=self.right_panel.is_active_chat_for_page(rel_path))
        else:
            self.right_panel.set_current_page(None, None)
            self.editor.set_ai_chat_available(False)
    
    def _apply_journal_templates_for_date(self, day_file_path: str, year: int, month: int, day: int) -> None:
        """Apply journal templates for a specific date."""
        if not self.vault_root:
            return
        
        from datetime import date
        target_date = date(year, month, day)
        year_str = f"{year}"
        month_num = f"{month:02d}"
        month_name = target_date.strftime("%B")
        day_num = f"{day:02d}"
        dow_name = target_date.strftime("%A")
        
        vault_root = Path(self.vault_root)
        journal_root = vault_root / "Journal"
        year_dir = journal_root / year_str
        month_dir = year_dir / month_num
        day_dir = month_dir / day_num
        
        year_page = year_dir / f"{year_dir.name}{PAGE_SUFFIX}"
        month_page = month_dir / f"{month_dir.name}{PAGE_SUFFIX}"
        day_page = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"
        
        templates_root = Path(__file__).parent.parent.parent / "templates"
        year_tpl = templates_root / "JournalYear.txt"
        month_tpl = templates_root / "JournalMonth.txt"
        preferred_day = config.load_default_journal_template()
        day_tpl = self._resolve_template_path(preferred_day, fallback="JournalDay")
        
        vars_map = {
            "{{YYYY}}": year_str,
            "{{Month}}": month_name,
            "{{DOW}}": dow_name,
            "{{dd}}": day_num,
        }
        
        def render(template_path: Path) -> str:
            try:
                raw = template_path.read_text(encoding="utf-8")
            except Exception:
                return ""
            out = raw
            for k, v in vars_map.items():
                out = out.replace(k, v)
            return out
        
        year_dir.mkdir(parents=True, exist_ok=True)
        month_dir.mkdir(parents=True, exist_ok=True)
        day_dir.mkdir(parents=True, exist_ok=True)
        
        def needs_write(path: Path) -> bool:
            if not path.exists():
                return True
            try:
                size = path.stat().st_size
            except OSError:
                return False
            return size < 20
        
        if needs_write(year_page) and year_tpl.exists():
            content = render(year_tpl)
            if content:
                year_page.write_text(content, encoding="utf-8")
        
        if needs_write(month_page) and month_tpl.exists():
            content = render(month_tpl)
            if content:
                month_page.write_text(content, encoding="utf-8")
        
        if needs_write(day_page) and day_tpl.exists():
            content = render(day_tpl)
            if content:
                day_page.write_text(content, encoding="utf-8")
    
    def _cleanup_virtual_page_if_unchanged(self, path: str) -> None:
        """Remove virtual page tracking if it was never edited."""
        if path not in self.virtual_pages:
            return
        
        current_content = self.editor.to_markdown()
        original_content = self.virtual_page_original_content.get(path)
        
        # If content hasn't changed from template, clean up virtual tracking
        if original_content is not None and current_content == original_content:
            self.virtual_pages.discard(path)
            self.virtual_page_original_content.pop(path, None)
            self._debug(f"Cleaned up unchanged virtual page: {path}")
    
    def _extract_journal_date(self, path: str) -> Optional[tuple[int, int, int]]:
        """Extract year, month, day from a journal path like /Journal/2025/11/16/16.txt.
        
        Returns tuple of (year, month, day) or None if not a journal path.
        """
        if not path or not path.startswith("/Journal/"):
            return None
        
        try:
            # Split path: /Journal/YYYY/MM/DD/DD.txt
            parts = path.split("/")
            if len(parts) >= 5:  # ['', 'Journal', 'YYYY', 'MM', 'DD', ...]
                year = int(parts[2])
                month = int(parts[3])
                day = int(parts[4])
                return (year, month, day)
        except (ValueError, IndexError):
            pass
        
        return None
    
    def _update_calendar_for_journal_page(self, path: str) -> None:
        """Update calendar selection if opening a journal page."""
        date_tuple = self._extract_journal_date(path)
        if date_tuple:
            # Preserve multi-day review context only when the calendar tab is
            # actively visible in the expanded right panel.
            try:
                cal = getattr(self.right_panel, "calendar_panel", None)
                cal_visible = bool(cal) and self._is_right_panel_expanded() and (self.right_panel.tabs.currentWidget() == cal)
                multi_day_active = bool(cal and len(getattr(cal, "multi_selected_dates", set())) > 1)
                if cal_visible and multi_day_active:
                    return
            except Exception:
                pass
            year, month, day = date_tuple
            self.right_panel.set_calendar_date(year, month, day)
            # Also sync the journal tree navigation
            try:
                if hasattr(self.right_panel, 'calendar_panel') and self.right_panel.calendar_panel:
                    self.right_panel.calendar_panel.set_current_page(path)
            except Exception:
                pass

    def _on_link_hovered(self, link: str) -> None:
        """Update status bar when hovering over a link."""
        if link:
            self.statusBar().showMessage(f"Link: {link}")
        else:
            # Restore default status message
            if self.current_path:
                display_path = path_to_colon(self.current_path) or self.current_path
                self.statusBar().showMessage(f"Editing {display_path}")
            else:
                self.statusBar().showMessage("")

    def _get_editor_text_for_path(self, rel_path: Optional[str]) -> str:
        """Return live editor text when it matches the requested relative path."""
        if not rel_path:
            return ""
        try:
            target_norm = self._normalize_editor_path(rel_path)
        except Exception:
            target_norm = rel_path
        try:
            current_norm = self._normalize_editor_path(self.current_path) if self.current_path else None
        except Exception:
            current_norm = self.current_path
        if target_norm and current_norm and target_norm == current_norm:
            try:
                return self.editor.toPlainText()
            except Exception:
                return ""
        return ""
    
    def _normalize_editor_path(self, path: str) -> str:
        """Normalize incoming page refs (folder, colon, bare) to file path with leading slash."""
        if not path:
            return path
        cleaned = path.strip()
        if cleaned.startswith(":"):
            cleaned = colon_to_path(cleaned, self.vault_root_name) or cleaned
        if not cleaned.startswith("/"):
            cleaned = "/" + cleaned.lstrip("/")
        rel = Path(cleaned.lstrip("/"))
        if rel.suffix.lower() in PAGE_SUFFIXES:
            if rel.suffix.lower() == LEGACY_SUFFIX:
                cleaned = str(Path(cleaned).with_suffix(PAGE_SUFFIX))
        else:
            # Treat as folder; map to its page file
            file_path = self._folder_to_file_path(cleaned)
            if file_path:
                cleaned = file_path
        return self._normalize_root_page_path(cleaned)

    def _is_attachment_link(self, name: str) -> bool:
        cleaned = (name or "").strip()
        if not cleaned:
            return False
        if cleaned.startswith(("http://", "https://")):
            return False
        if ":" in cleaned:
            return False
        try:
            suffix = Path(cleaned.replace("\\", "/")).suffix
        except Exception:
            suffix = ""
        return bool(suffix) and suffix.lower() not in PAGE_SUFFIXES

    def _open_attachment_link(self, name: str) -> bool:
        if self._remote_mode:
            return self._open_remote_attachment_link(name)
        return self._open_local_attachment_link(name)

    def _open_local_attachment_link(self, name: str) -> bool:
        if not self.vault_root:
            return False
        if not self.current_path and not name.startswith("/"):
            return False
        rel_name = name[2:] if name.startswith("./") else name
        if name.startswith("/"):
            candidate = (Path(self.vault_root) / rel_name.lstrip("/")).resolve()
        else:
            rel_current = Path(self.current_path.lstrip("/")) if self.current_path else Path("/")
            page_folder = rel_current.parent
            candidate = (Path(self.vault_root) / page_folder / rel_name).resolve()
        if not candidate.exists() or not candidate.is_file():
            return False
        try:
            if candidate.suffix.lower() == ".puml":
                self._open_plantuml_editor(candidate)
                return True
            if candidate.suffix.lower() in {".mmd", ".mermaid"}:
                self._open_mermaid_editor(candidate)
                return True
        except Exception:
            pass
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(candidate)))
            return True
        except Exception:
            return False

    def _open_remote_attachment_link(self, name: str) -> bool:
        if not self._remote_mode or not self.api_base:
            return False
        if not self.current_path and not name.startswith("/"):
            return False
        rel_name = name[2:] if name.startswith("./") else name
        if name.startswith("/"):
            virtual_path = f"/{rel_name.lstrip('/')}"
        else:
            base_dir = Path(self.current_path).parent if self.current_path else Path("/")
            virtual_path = f"/{(base_dir / rel_name).as_posix()}"
        try:
            if Path(virtual_path).suffix.lower() == ".puml":
                self._open_plantuml_editor(
                    {
                        "kind": "remote",
                        "path": virtual_path,
                        "page_path": self.current_path,
                    }
                )
                return True
            if Path(virtual_path).suffix.lower() in {".mmd", ".mermaid"}:
                self._open_mermaid_editor(
                    {
                        "kind": "remote",
                        "path": virtual_path,
                        "page_path": self.current_path,
                    }
                )
                return True
        except Exception:
            pass
        cache_root = self._ensure_remote_cache_root()
        cache_path = (cache_root / "attachments" / virtual_path.lstrip("/")).resolve()
        if not cache_path.exists():
            if not self.http:
                return False
            try:
                resp = self.http.get("/api/file/raw", params={"path": virtual_path})
                if resp.status_code == 401 and self._remote_mode:
                    if not self._prompt_remote_login():
                        return False
                    resp = self.http.get("/api/file/raw", params={"path": virtual_path})
                resp.raise_for_status()
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_bytes(resp.content)
            except httpx.HTTPError as exc:
                self._alert_api_error(exc, f"Failed to load {virtual_path}")
                return False
            except OSError as exc:
                self._alert(f"Failed to cache remote file: {exc}")
                return False
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(cache_path)))
            return True
        except Exception:
            return False

    def _open_camel_link(self, name: str, focus_target: str | None = None, refresh_only: bool = False, force: bool = False) -> None:
        """Open a link - handles both CamelCase (relative), colon notation (absolute), and HTTP URLs."""
        # Handle HTTP/HTTPS links
        if name.startswith("http://") or name.startswith("https://"):
            try:
                from PySide6.QtGui import QDesktopServices
                from PySide6.QtCore import QUrl
                QDesktopServices.openUrl(QUrl(name))
                return
            except Exception as e:
                self._alert(f"Failed to open URL: {e}")
                return
        
        if not self.current_path:
            self._alert("Open a page before following links.")
            return
        
        # Save current page before following link to ensure it's indexed
        self._save_current_file(auto=True, reason="follow link")

        # Attachment file link: detect filename with extension (non .txt) and open via OS
        if self._is_attachment_link(name):
            if self._open_attachment_link(name):
                return

        target_name, anchor = self._split_link_anchor(name)
        anchor_slug = self._anchor_slug(anchor)
        
        # Check if this is a colon notation link (PageA:PageB:PageC or :VaultRoot)
        if ":" in target_name:
            # Special case: :VaultRoot means open the current home page
            if target_name.strip() == ":VaultRoot":
                main_page = self._home_page_path() or self._vault_root_page_path()
                if not main_page:
                    return
                created_root = False
                if not self._page_exists(main_page):
                    if self._read_only:
                        self.statusBar().showMessage(
                            self._read_only_status_message("Cannot create new pages while vault is read-only."),
                            5000,
                        )
                        return
                    folder_path = self._file_path_to_folder(main_page)
                    if not self._ensure_page_folder(folder_path, allow_existing=True):
                        return
                    self._apply_new_page_template(main_page, self.vault_root_name or "Home")
                    created_root = True
                if refresh_only and self.current_path == main_page:
                    self._reload_page_preserve_cursor(main_page)
                else:
                    self._open_file(main_page, cursor_at_end=created_root, force=force)
                    self._scroll_to_anchor_slug(anchor_slug)
                    self._apply_navigation_focus(focus_target)
                return
            # Special case: :<vault_root_name> maps to the vault root page
            vault_root_colon = f":{self.vault_root_name}" if self.vault_root_name else ""
            if vault_root_colon and target_name.strip() == vault_root_colon:
                main_page = self._vault_root_page_path()
                if not main_page:
                    return
                created_root = False
                if not self._page_exists(main_page):
                    if self._read_only:
                        self.statusBar().showMessage(
                            self._read_only_status_message("Cannot create new pages while vault is read-only."),
                            5000,
                        )
                        return
                    folder_path = self._file_path_to_folder(main_page)
                    if not self._ensure_page_folder(folder_path, allow_existing=True):
                        return
                    self._apply_new_page_template(main_page, self.vault_root_name or "Home")
                    created_root = True
                if refresh_only and self.current_path == main_page:
                    self._reload_page_preserve_cursor(main_page)
                else:
                    self._open_file(main_page, cursor_at_end=created_root, force=force)
                    self._scroll_to_anchor_slug(anchor_slug)
                    self._apply_navigation_focus(focus_target)
                return
            # Colon notation is absolute - convert directly to path
            # Prevent duplicate vault root in path (e.g., VaultRoot/VaultRoot.md)
            target_file = colon_to_path(target_name, self.vault_root_name)
            # If the resolved file is the vault root's main page, normalize to its canonical path
            vault_main_page = self._vault_root_page_path()
            if vault_main_page and target_file.replace("\\", "/").strip("/") in (
                self.vault_root_name + PAGE_SUFFIX if self.vault_root_name else "",
                vault_main_page.strip("/"),
            ):
                target_file = vault_main_page
            if not target_file:
                self._alert(f"Invalid link format: {name}")
                return
            target_file = self._resolve_case_insensitive_rel_path(target_file)
            folder_path = self._file_path_to_folder(target_file)
            # Check if file already exists before creating
            file_existed = self.vault_root and Path(self.vault_root, target_file.lstrip("/")).exists()
            if file_existed:
                is_new_page = False
            else:
                if self._read_only:
                    self.statusBar().showMessage(
                        self._read_only_status_message("Cannot create new pages while vault is read-only."),
                        5000,
                    )
                    return
                if not self._ensure_page_folder(folder_path, allow_existing=True):
                    return
                is_new_page = True
                page_name = target_name.split(":")[-1]  # Get last part for page name
                self._apply_new_page_template(target_file, page_name)
            self._pending_selection = target_file
            if refresh_only and self.current_path == target_file:
                self._reload_page_preserve_cursor(target_file)
            else:
                self._populate_vault_tree()
                self._open_file(target_file, cursor_at_end=is_new_page, force=force)
                self._scroll_to_anchor_slug(anchor_slug)
                self._apply_navigation_focus(focus_target)
        else:
            # CamelCase link is relative to current page
            # Special case: if the link target matches the vault root name, open the vault root page
            if target_name == self.vault_root_name:
                target_file = self._vault_root_page_path()
                if target_file:
                    self._open_file(target_file)
                    self._scroll_to_anchor_slug(anchor_slug)
                    self._apply_navigation_focus(focus_target)
                return
            rel_current = Path(self.current_path.lstrip("/"))
            parent_folder = rel_current.parent
            # Always create a subfolder named after the link, and place the file inside it
            if parent_folder.parts:
                file_path = f"/{parent_folder.as_posix()}/{target_name}/{target_name}{PAGE_SUFFIX}"
            else:
                file_path = f"/{target_name}/{target_name}{PAGE_SUFFIX}"
            target_file = self._resolve_case_insensitive_rel_path(file_path)
            folder_path = self._file_path_to_folder(target_file)
            # Check if file already exists before creating
            file_existed = self.vault_root and Path(self.vault_root, target_file.lstrip("/")).exists()
            if file_existed:
                is_new_page = False
            else:
                if self._read_only:
                    self.statusBar().showMessage(
                        self._read_only_status_message("Cannot create new pages while vault is read-only."),
                        5000,
                    )
                    return
                if not self._ensure_page_folder(folder_path, allow_existing=True):
                    return
                is_new_page = True
                self._apply_new_page_template(target_file, target_name)
            self._pending_selection = target_file
            if refresh_only and self.current_path == target_file:
                self._reload_page_preserve_cursor(target_file)
            else:
                self._populate_vault_tree()
                self._open_file(target_file, cursor_at_end=is_new_page, force=force)
                self._scroll_to_anchor_slug(anchor_slug)
                self._apply_navigation_focus(focus_target)

    def _adjust_font_size(self, delta: int) -> None:
        new_size = max(6, min(24, self.font_size + delta))
        fw = self.focusWidget()
        ai_focus = False
        if fw and self.right_panel.ai_chat_panel:
            if fw is self.right_panel.ai_chat_panel or self.right_panel.ai_chat_panel.isAncestorOf(fw):
                ai_focus = True
        if ai_focus:
            self.right_panel.set_font_size(new_size)
            config.save_ai_chat_font_size(new_size)
        else:
            if new_size == self.font_size:
                return
            self.font_size = new_size
            self.editor.set_font_point_size(self.font_size)
            self.right_panel.set_calendar_font_size(self.font_size)
            config.save_global_editor_font_size(self.font_size)

    def _apply_navigation_focus(self, focus_target: str | None) -> None:
        """Set focus after navigation based on source (editor vs link navigator)."""
        if focus_target == "navigator":
            if not self._feature_link_navigator_enabled:
                self.editor.setFocus()
                return
            # Ensure right panel is visible
            self._ensure_right_panel_visible()
            
            self.right_panel.focus_link_tab(self.current_path)
            try:
                if self.right_panel.link_panel:
                    self.right_panel.link_panel.graph_view.setFocus()
            except Exception:
                pass
        elif focus_target == "editor":
            self.editor.setFocus()

    def _focus_editor_from_tree(self) -> None:
        self._tree_enter_focus = True
        index = self.tree_view.currentIndex()
        target = index.data(OPEN_ROLE) or index.data(PATH_ROLE) if index.isValid() else None
        if target == FILTER_BANNER:
            self._clear_nav_filter()
            return
        if target and target != self.current_path:
            self._skip_next_selection_open = True
            self._open_file(target)
        self._focus_editor()
        QTimer.singleShot(0, lambda: setattr(self, "_tree_enter_focus", False))
        self._tree_keyboard_nav = False

    def _on_tree_row_clicked(self, index: QModelIndex) -> None:
        """Open and focus editor when a tree row is clicked."""
        target = index.data(OPEN_ROLE) or index.data(PATH_ROLE)
        if target == FILTER_BANNER:
            self._clear_nav_filter()
            return
        if target:
            if target != self.current_path:
                self._skip_next_selection_open = True
                self._open_file(target)
            self._focus_editor()

    def _focus_editor(self) -> None:
        self.editor.setFocus()

    def _focus_tasks_search(self) -> None:
        """Focus the Tasks tab search bar. If external task window exists, focus that instead."""
        if not self._feature_tasks_enabled:
            return
        # First check if there's an external task panel window
        for window in self._detached_panels:
            if window.windowTitle() == "Tasks" and window.isVisible():
                try:
                    # Bring external window to front and focus it
                    window.raise_()
                    window.activateWindow()
                    # Focus the search box in the external panel
                    central_widget = window.centralWidget()
                    if hasattr(central_widget, "focus_search"):
                        central_widget.focus_search()
                    elif hasattr(central_widget, "search"):
                        central_widget.search.setFocus(Qt.ShortcutFocusReason)
                    return
                except Exception:
                    pass
        
        # No external window - ensure right panel is visible if hidden
        self._ensure_right_panel_visible()
        
        # Switch to Tasks tab (this will trigger _focus_current_tab but that's OK)
        self.right_panel.tabs.setCurrentIndex(0)
        
        # Use QTimer to defer focus until tab switch completes and UI updates
        QTimer.singleShot(0, self._deferred_focus_tasks_search)
    
    def _deferred_focus_tasks_search(self) -> None:
        """Deferred helper to focus task search after tab switch completes."""
        try:
            # Get the search box directly
            search_box = getattr(self.right_panel.task_panel, "search", None)
            if search_box and search_box.isVisible():
                # Ensure editor doesn't have focus
                self.editor.clearFocus()
                # Set focus on search box
                search_box.setFocus(Qt.TabFocusReason)
                search_box.selectAll()
                # Process events to ensure focus is applied
                from PySide6.QtCore import QCoreApplication
                QCoreApplication.processEvents()
                # Schedule one more focus attempt after a delay to catch any focus-stealing
                QTimer.singleShot(100, lambda: self._force_search_focus(search_box))
        except Exception:
            pass
    
    def _force_search_focus(self, search_box) -> None:
        """Force focus to search box, called a short time after initial focus attempt."""
        try:
            if search_box and search_box.isVisible():
                search_box.setFocus(Qt.TabFocusReason)
                search_box.selectAll()
        except Exception:
            pass

    def _focus_calendar_tab(self) -> None:
        """Switch to Calendar tab and focus calendar widget."""
        if not self._feature_calendar_enabled:
            return
        # Ensure right panel is visible
        self._ensure_right_panel_visible()
        
        try:
            for i in range(self.right_panel.tabs.count()):
                if self.right_panel.tabs.widget(i) == self.right_panel.calendar_panel:
                    self.right_panel.tabs.setCurrentIndex(i)
                    try:
                        self.right_panel.calendar_panel.calendar.setFocus(Qt.ShortcutFocusReason)
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    def _focus_tags_tab(self) -> None:
        """Switch to Tags tab and focus the search bar."""
        if not self._feature_tags_enabled or not self.tags_tab:
            return
        # Ensure left panel is visible
        sizes = self.editor_split.sizes()
        if len(sizes) >= 2 and sizes[0] == 0:
            width = getattr(self, "_saved_left_width", 360)
            total = sum(sizes)
            self.editor_split.setSizes([width, max(1, total - width)])
        
        try:
            # Switch to Tags tab (index 1 in left_tab_widget: Vault=0, Tags=1, Search=2)
            for i in range(self.left_tab_widget.count()):
                if self.left_tab_widget.widget(i) == self.tags_tab:
                    self.left_tab_widget.setCurrentIndex(i)
                    # Use QTimer to defer focus until tab switch completes
                    QTimer.singleShot(0, self._deferred_focus_tags_search)
                    break
        except Exception:
            pass
    
    def _deferred_focus_tags_search(self) -> None:
        """Deferred helper to focus tags search after tab switch completes."""
        try:
            if self.tags_tab and hasattr(self.tags_tab, "focus_search"):
                self.tags_tab.focus_search()
        except Exception:
            pass

    def _focus_attachments_tab(self) -> None:
        """Switch to Attachments tab and focus."""
        # Ensure right panel is visible
        self._ensure_right_panel_visible()
        
        try:
            for i in range(self.right_panel.tabs.count()):
                if self.right_panel.tabs.widget(i) == self.right_panel.attachments_panel:
                    self.right_panel.tabs.setCurrentIndex(i)
                    try:
                        self.right_panel.attachments_panel.setFocus(Qt.ShortcutFocusReason)
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    def _mark_tree_arrow_nav(self) -> None:
        """Flag that tree navigation via arrow keys should keep focus on the tree."""
        self._tree_arrow_focus_pending = True
        self._tree_keyboard_nav = True

    # --- Focus toggle & visual indication ---------------------------
    def _toggle_focus_between_tree_and_editor(self) -> None:
        """Toggle focus between tree, editor, and right panel (Ctrl+Shift+Space) using MRU order."""
        current = self._focus_target_for_widget(self.focusWidget())
        if current in self._focus_recent:
            # Rotate MRU list so current moves to end, pick next
            self._focus_recent = [t for t in self._focus_recent if t != current] + [current]
        target = self._focus_recent[0] if self._focus_recent else "editor"
        self._set_focus_target(target)

    def _set_focus_target(self, target: str) -> None:
        """Move focus to target and update MRU list."""
        if target == "editor":
            self.editor.setFocus()
        elif target == "tree":
            self.tree_view.setFocus()
        elif target == "left":
            current_tab = self.left_tab_widget.currentWidget()
            if current_tab is self.search_tab:
                self.search_tab.focus_search()
            elif self.tags_tab and current_tab is self.tags_tab:
                current_tab.setFocus(Qt.ShortcutFocusReason)
            else:
                self.left_tab_widget.setFocus()
        elif target == "right":
            current_tab = self.right_panel.tabs.currentWidget()
            if current_tab:
                current_tab.setFocus()
            else:
                self.right_panel.setFocus()
        if target in self._focus_recent:
            self._focus_recent = [target] + [t for t in self._focus_recent if t != target]
        else:
            self._focus_recent.insert(0, target)
        self._apply_focus_borders()

    def _focus_target_for_widget(self, widget: Optional[QWidget]) -> Optional[str]:
        if not widget:
            return None
        if widget is self.editor or (self.editor and self.editor.isAncestorOf(widget)):
            return "editor"
        if widget is self.tree_view or self.tree_view.isAncestorOf(widget):
            return "tree"
        if widget is self.left_tab_widget or self.left_tab_widget.isAncestorOf(widget):
            return "left"
        if widget is self.right_panel or self.right_panel.isAncestorOf(widget):
            return "right"
        return None

    def _on_focus_changed(self, widget: Optional[QWidget]) -> None:
        if getattr(self, '_suppress_focus_borders', False):
            return
        target = self._focus_target_for_widget(widget)
        if target:
            if target in self._focus_recent:
                self._focus_recent = [target] + [t for t in self._focus_recent if t != target]
            else:
                self._focus_recent.insert(0, target)
        self._apply_focus_borders()

    def _apply_focus_borders(self) -> None:
        """Apply a subtle border around the widget that currently has focus."""
        try:
            if getattr(self, '_suppress_focus_borders', False):
                return
            focused = self.focusWidget()
            editor_has = focused is self.editor or (self.editor and self.editor.isAncestorOf(focused))
            tree_has = focused is self.tree_view or self.tree_view.isAncestorOf(focused)
            left_has = focused is self.left_tab_widget or self.left_tab_widget.isAncestorOf(focused)
            right_has = focused is self.right_panel or self.right_panel.isAncestorOf(focused)
        except RuntimeError:
            # Window is being deleted, silently ignore
            return
        except Exception:
            return
        # Styles: subtle border with accent color; remove when unfocused. Reset any filter tint to default background.
        focus_border = (
            theme_value("main_window.focus_border.filtered", "#D9534F")
            if getattr(self, "_nav_filter_path", None)
            else theme_value("main_window.focus_border.default", "#4A90E2")
        )
        editor_style = f"QTextEdit {{ border: 1px solid {focus_border}; border-radius:3px; }}" if editor_has else "QTextEdit { border: 1px solid transparent; }"
        is_light_theme = QApplication.palette().color(QPalette.Base).lightness() >= 150
        right_arrow_name = "right-arrow-dark.svg" if is_light_theme else "right-arrow.svg"
        down_arrow_name = "down-arrow-dark.svg" if is_light_theme else "down-arrow.svg"
        right_arrow_path = self._find_asset(right_arrow_name)
        down_arrow_path = self._find_asset(down_arrow_name)
        arrow_closed = (
            str(right_arrow_path).replace("\\", "/") if right_arrow_path else ""
        )
        arrow_open = (
            str(down_arrow_path).replace("\\", "/") if down_arrow_path else ""
        )
        tree_style = (
            "QTreeView { border: 1px solid transparent; background: palette(base); }"
            "QTreeView::item { padding: 2px 6px 2px 2px; border-bottom: 1px solid palette(midlight); }"
            "QTreeView::item:selected { background: palette(highlight); color: palette(highlighted-text); }"
            "QTreeView::item:hover { background: palette(alternate-base); }"
            "QTreeView::branch { width: 16px; height: 16px; }"
        )
        if arrow_closed and arrow_open:
            tree_style += (
                f'QTreeView::branch:has-children:closed {{ image: url("{arrow_closed}"); }}'
                f'QTreeView::branch:has-children:open {{ image: url("{arrow_open}"); }}'
            )
        left_style = f"QTabWidget::pane {{ border: 1px solid {focus_border}; border-radius:3px; }}" if left_has else ""
        right_style = f"QTabWidget::pane {{ border: 1px solid {focus_border}; border-radius:3px; }}" if right_has else ""
        # Preserve existing styles by appending (simple approach)
        try:
            self.editor.setStyleSheet(editor_style)
        except RuntimeError:
            pass  # Widget may have been deleted
        try:
            self.tree_view.setStyleSheet(tree_style)
        except RuntimeError:
            pass  # Widget may have been deleted
        try:
            if left_style:
                self.left_tab_widget.setStyleSheet(left_style)
            else:
                self.left_tab_widget.setStyleSheet("")
        except RuntimeError:
            pass  # Widget may have been deleted
        try:
            if right_style:
                self.right_panel.tabs.setStyleSheet(right_style)
            else:
                self.right_panel.tabs.setStyleSheet("")
        except RuntimeError:
            pass  # Widget may have been deleted

    def _goto_line(self, line: int, select_line: bool = False) -> None:
        # Convert line number (1-indexed) to block number (0-indexed)
        block_num = max(0, line - 1)
        doc = self.editor.document()
        block = doc.findBlockByNumber(block_num)
        
        if block.isValid():
            cursor = QTextCursor(block)
            # Move to start of block content (skip whitespace if selecting line)
            if select_line:
                cursor.select(QTextCursor.LineUnderCursor)
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()

    def _ensure_page_folder(self, folder_path: str, allow_existing: bool = False) -> bool:
        if not self._ensure_writable("create folders/pages"):
            return False
        payload = {"path": folder_path, "is_dir": True}
        try:
            resp = self.http.post("/api/path/create", json=payload)
            resp.raise_for_status()
            return True
        except httpx.HTTPStatusError as exc:
            if allow_existing and exc.response is not None and exc.response.status_code == 409:
                return True
            self._alert_api_error(exc, f"Failed to create page {folder_path}")
            return False
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to create page {folder_path}")
            return False

    def _folder_to_file_path(self, folder_path: str) -> Optional[str]:
        if not self.vault_root_name:
            return None
        cleaned = (folder_path or "/").strip()
        if cleaned in ("", "/"):
            return self._vault_root_page_path()
        rel = Path(cleaned.lstrip("/"))
        name = rel.name or self.vault_root_name
        rel_file = rel / f"{name}{PAGE_SUFFIX}"
        return f"/{rel_file.as_posix()}"

    def _resolve_case_insensitive_rel_path(self, rel_path: str) -> str:
        """Resolve a vault-relative path by ignoring case in existing filesystem entries."""
        if not self.vault_root:
            return rel_path
        cleaned = (rel_path or "").strip().lstrip("/")
        if not cleaned:
            return rel_path
        current = Path(self.vault_root)
        resolved: list[str] = []
        parts = cleaned.split("/")
        for part in parts:
            try:
                match = next((child.name for child in current.iterdir() if child.name.lower() == part.lower()), None)
            except OSError:
                match = None
            name = match or part
            resolved.append(name)
            current = current / name
        return "/" + "/".join(resolved)

    def _file_path_to_folder(self, file_path: str) -> str:
        """Convert file path like /PageA/PageB/PageC/PageC.md to folder path /PageA/PageB/PageC."""
        if not file_path or file_path == "/":
            return "/"
        # Remove the page file at the end
        path_obj = Path(file_path.lstrip("/"))
        if path_obj.suffix.lower() in PAGE_SUFFIXES:  # Suffix includes the dot
            return f"/{path_obj.parent.as_posix()}"
        return file_path

    def _expand_subtree(self, index: QModelIndex) -> None:
        """Recursively expand the given node and all descendants."""
        if not index.isValid():
            return
        stack = [index]
        while stack:
            idx = stack.pop()
            self.tree_view.expand(idx)
            model = idx.model()
            if not model:
                continue
            for row in range(model.rowCount(idx)):
                child = model.index(row, 0, idx)
                if child.isValid():
                    stack.append(child)

    def _collapse_subtree_recursive(self, index: QModelIndex) -> None:
        """Recursively collapse all descendants first, then collapse the given node."""
        if not index.isValid():
            return
        
        model = index.model()
        if not model:
            return
        
        # Recursively collapse all children first (depth-first, bottom-up)
        for row in range(model.rowCount(index)):
            child = model.index(row, 0, index)
            if child.isValid():
                self._collapse_subtree_recursive(child)
        
        # Finally collapse this node after all children are collapsed
        self.tree_view.collapse(index)

    def _collapse_other_folders(self, keep_path: str) -> None:
        """Collapse all folders except those in the path to keep_path."""
        if not keep_path:
            return
        
        # Build set of paths to keep expanded (the target and all its ancestors)
        keep_expanded = set()
        keep_expanded.add(self._normalize_tree_path(keep_path))
        
        # Add all parent paths
        parts = [p for p in keep_path.strip("/").split("/") if p]
        for i in range(len(parts)):
            parent_path = "/" + "/".join(parts[:i+1])
            keep_expanded.add(self._normalize_tree_path(parent_path))
        
        # Also keep the root expanded
        keep_expanded.add("/")
        
        # Collapse all paths not in keep_expanded
        paths_to_collapse = [p for p in list(self._expanded_paths) if p not in keep_expanded]
        
        # Collapse each one
        for path in paths_to_collapse:
            # Find the item and collapse it
            item = self._find_item(self.tree_model.invisibleRootItem(), path)
            if item:
                idx = item.index()
                if idx.isValid() and self.tree_view.isExpanded(idx):
                    self.tree_view.collapse(idx)
                    # The collapse will trigger _on_tree_collapsed which updates _expanded_paths

    # --- Tree context menu -------------------------------------------
    def _open_context_menu(self, pos: QPoint) -> None:
        if not self.vault_root:
            return
        index = self.tree_view.indexAt(pos)
        global_pos = self.tree_view.viewport().mapToGlobal(pos)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu::section {"
            " color: palette(disabled, text);"
            " font-size: 9px;"
            " letter-spacing: 1px;"
            " padding: 8px 16px 4px 16px;"
            " border-top: 1px solid palette(midlight);"
            " text-align: center;"
            " text-transform: uppercase;"
            " }"
        )

        def add_menu_section(title: str) -> None:
            menu.addSection(title)

        if index.isValid():
            path = index.data(PATH_ROLE) or "/"
            open_path = index.data(OPEN_ROLE)
            file_path = open_path or self._folder_to_file_path(path)
            add_menu_section("Create")
            page_template_action = menu.addAction("New Page...")
            page_template_action.triggered.connect(
                lambda checked=False, p=path: self._show_new_page_dialog(
                    parent_path=p,
                    insert_link_in_editor=False,
                )
            )
            folder_template_action = menu.addAction("New from Folder Template…")
            folder_template_action.triggered.connect(
                lambda checked=False, p=path: self._show_folder_template_dialog(p)
            )
            add_menu_section("Navigation & View")
            if file_path:
                copy_link_action = menu.addAction("Copy Link to this Location")
                copy_link_action.triggered.connect(
                    lambda checked=False, p=path, op=open_path: self._copy_tree_location_link(p, op)
                )
                search_from_here_action = menu.addAction("Search From Here...")
                search_from_here_action.triggered.connect(
                    lambda checked=False, p=path: self._search_from_folder(p)
                )
                toggle_bookmark_action = menu.addAction("Toggle Bookmark for this Page")
                toggle_bookmark_action.triggered.connect(
                    lambda checked=False, fp=file_path: self._toggle_bookmark_for_path(fp)
                )
            collapse_action = menu.addAction("Collapse")
            collapse_action.triggered.connect(
                lambda checked=False, idx=index: self._collapse_subtree_recursive(idx if idx.isValid() else QModelIndex())
            )
            expand_action = menu.addAction("Expand")
            expand_action.triggered.connect(
                lambda checked=False, idx=index: self._expand_subtree(idx if idx.isValid() else QModelIndex())
            )
            collapse_others_action = menu.addAction("Collapse other folders")
            collapse_others_action.triggered.connect(
                lambda checked=False, p=path: self._collapse_other_folders(p)
            )
            filter_action = menu.addAction("Filter to this subtree")
            filter_action.triggered.connect(lambda checked=False, p=path: self._set_nav_filter(p))
            if path:
                open_window_action = menu.addAction("Open in Editor Window")
                open_window_action.triggered.connect(lambda checked=False, p=path: self._open_page_editor_window(p))
            add_menu_section("Organize")
            if path != "/":
                rename_action = menu.addAction("Rename")
                rename_action.triggered.connect(
                    lambda checked=False, p=path, idx=index: self._start_inline_rename(p, self._parent_path(idx), global_pos, idx)
                )
                move_action = menu.addAction("Move…")
                move_action.triggered.connect(
                    lambda checked=False, p=path, idx=index: self._move_path_dialog(p, self._parent_path(idx))
                )
                delete_action = menu.addAction("Delete")
                delete_action.triggered.connect(
                    lambda checked=False, p=path, op=open_path: self._delete_path(p, op, global_pos)
                )
            if file_path:
                add_menu_section("File & Location")
                if not self._remote_mode:
                    view_src = menu.addAction("Edit Page Source")
                    view_src.triggered.connect(lambda checked=False, fp=file_path: self._view_page_source(fp))

                    # Open File Location
                    open_loc = menu.addAction("Open File Location")
                    open_loc.triggered.connect(lambda checked=False, fp=file_path: self._open_tree_file_location(fp))
                
                add_menu_section("Insights & Output")
                print_page_action = menu.addAction("Print Page…")
                print_page_action.triggered.connect(
                    lambda checked=False, fp=file_path: self._print_page_for_path(fp)
                )
                
                if self._feature_link_navigator_enabled:
                    backlinks_action = menu.addAction("Backlinks…")
                    backlinks_action.triggered.connect(
                        lambda checked=False, fp=file_path: self._show_link_navigator_for_path(fp)
                    )
                ai_chat_action = menu.addAction("AI Chat…")
                ai_chat_action.triggered.connect(lambda checked=False, fp=file_path: self._open_ai_chat_for_path(fp, create=True))
        else:
            # When clicking empty space, use filtered path if active, otherwise root
            default_parent = self._nav_filter_path if self._nav_filter_path and self._nav_filter_path != "/" else "/"
            menu.addAction(
                "New Page",
                lambda checked=False, p=default_parent: self._show_new_page_dialog(
                    parent_path=p,
                    insert_link_in_editor=False,
                ),
            )
        if menu.actions():
            menu.exec(global_pos)


    def _view_page_source(self, file_path: str) -> None:
        """Open the given page's txt file in the OS editor, show modal, and reload on OK."""
        if not self.vault_root:
            return
        try:
            from PySide6.QtGui import QDesktopServices
            from PySide6.QtCore import QUrl
        except Exception:
            return
        abs_path = str((Path(self.vault_root) / file_path.lstrip("/")).resolve())
        # Launch in default editor
        QDesktopServices.openUrl(QUrl.fromLocalFile(abs_path))
        # Block with modal until user confirms they're done
        dlg = QMessageBox(self)
        dlg.setWindowTitle("Edit Page Source")
        dlg.setText("File being edited outside of StillPoint.\nPress OK when finished.")
        dlg.setIcon(QMessageBox.Information)
        dlg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        dlg.setDefaultButton(QMessageBox.Ok)
        result = dlg.exec()
        if result == QMessageBox.Ok:
            # Reload and render page (force reload even if already current)
            self._open_file(file_path, force=True)
    
    def _open_tree_file_location(self, file_path: str) -> None:
        """Open the folder containing the given page file."""
        if not self.vault_root:
            return
        
        abs_path = (Path(self.vault_root) / file_path.lstrip("/")).resolve()
        folder_path = abs_path.parent
        opened = self._open_in_file_manager(folder_path)
        if not opened:
            self._alert(f"Could not open folder: {folder_path}")

    def _copy_tree_location_link(self, path: str, open_path: Optional[str]) -> None:
        """Copy a colon-style link for the selected tree item."""
        target = open_path or self._folder_to_file_path(path) or path
        colon = ""
        try:
            colon = path_to_colon(target)
        except Exception:
            colon = ""
        if not colon and path:
            try:
                colon = ensure_root_colon_link(path_to_colon(f"{path.rstrip('/')}/{Path(path).name}{PAGE_SUFFIX}"))
            except Exception:
                colon = ensure_root_colon_link(path.replace("/", ":"))
        colon = ensure_root_colon_link(colon)
        if not colon:
            self.statusBar().showMessage("Could not copy link for this item", 3000)
            return
        try:
            QApplication.clipboard().setText(colon)
            self.statusBar().showMessage(f"Copied link: {colon}", 3000)
        except Exception:
            self.statusBar().showMessage("Failed to copy link", 3000)

    def _show_link_navigator_for_path(self, file_path: Optional[str]) -> None:
        """Open the Link Navigator tab for the given page."""
        if not self._feature_link_navigator_enabled:
            return
        if not file_path:
            return
        normalized = self._normalize_editor_path(file_path)
        self._ensure_right_panel_visible()
        if normalized != self.current_path:
            try:
                self._open_file(normalized)
            except Exception:
                return
        self.right_panel.focus_link_tab(normalized)
        # Sync any detached link navigator windows to the same page
        for panel in list(getattr(self, "_detached_link_panels", [])):
            try:
                panel.set_page(normalized)
            except Exception:
                continue

    def _open_ai_chat_for_path(self, file_path: Optional[str], create: bool = False, *, focus_tab: bool = True) -> None:
        """Open (or create) the AI Chat session for the given page, optionally without shifting focus."""
        if not file_path:
            return
        detached = self._active_ai_chat_panel()
        if detached:
            if create:
                detached.open_chat_for_page(file_path)
            else:
                detached.set_current_page(file_path)
            try:
                if self._detached_ai_chat_window:
                    self._detached_ai_chat_window.raise_()
                    self._detached_ai_chat_window.activateWindow()
            except Exception:
                pass
            if focus_tab:
                detached.focus_input()
            return
        if not self.right_panel.ai_chat_panel:
            return
        self._ensure_right_panel_visible()
        if focus_tab:
            self.right_panel.focus_ai_chat(file_path, create=create)
            self.right_panel.focus_ai_chat_input()
        else:
            if create:
                self.right_panel.ai_chat_panel.open_chat_for_page(file_path)
                if file_path == self.current_path:
                    self.editor.set_ai_chat_available(True, active=True)
            else:
                self.right_panel.ai_chat_panel.set_current_page(file_path)

    def _focus_ai_chat_for_page(self, path: str) -> None:
        """Ensure AI tab shows the requested page and give the input focus."""
        target_path = path or self.current_path
        if not target_path:
            return
        detached = self._active_ai_chat_panel()
        if detached:
            detached.open_chat_for_page(target_path)
            detached.focus_input()
            try:
                if self._detached_ai_chat_window:
                    self._detached_ai_chat_window.raise_()
                    self._detached_ai_chat_window.activateWindow()
            except Exception:
                pass
            return
        if not self.right_panel.ai_chat_panel:
            return
        self._ensure_right_panel_visible()
        self.right_panel.focus_ai_chat(target_path, create=True)
        self.right_panel.focus_ai_chat_input()

    def _handle_ai_action(self, action: str, prompt: str, text: str) -> None:
        """Send selected text to AI chat with the chosen action."""
        # Special-case: One-Shot prompt — call the API directly and replace
        # the selected text inline with the LLM response (do not add to chat history).
        if action == "One-Shot Prompt Selection":
            # Perform one-shot inline replacement
            self._perform_one_shot_prompt(text)
            return
        if action == "Load Global Chat":
            if not config.load_enable_ai_chats() or not self.right_panel.ai_chat_panel:
                QMessageBox.information(self, "AI Chat", "Enable AI Chats in Preferences to use AI actions.")
                return
            detached = self._active_ai_chat_panel()
            if detached:
                detached.open_chat_for_page(None)
                detached.focus_input()
                try:
                    if self._detached_ai_chat_window:
                        self._detached_ai_chat_window.raise_()
                        self._detached_ai_chat_window.activateWindow()
                except Exception:
                    pass
                return
            self._ensure_right_panel_visible()
            self.right_panel.focus_ai_chat(None, create=True)
            self.right_panel.focus_ai_chat_input()
            return
        if action == "Send selection to Global Chat":
            if not config.load_enable_ai_chats() or not self.right_panel.ai_chat_panel:
                QMessageBox.information(self, "AI Chat", "Enable AI Chats in Preferences to use AI actions.")
                return
            detached = self._active_ai_chat_panel()
            if detached:
                detached.open_chat_for_page(None)
                detached.send_text_message(text)
                detached.focus_input()
                try:
                    if self._detached_ai_chat_window:
                        self._detached_ai_chat_window.raise_()
                        self._detached_ai_chat_window.activateWindow()
                except Exception:
                    pass
                return
            self._ensure_right_panel_visible()
            self.right_panel.focus_ai_chat(None, create=True)
            if not self.right_panel.send_text_to_chat(text):
                self.statusBar().showMessage("Enable AI chats to send text from the editor.", 4000)
            return
        if action == "Send selection to Page Chat":
            self._send_selection_to_ai_chat(text)
            return

        if not config.load_enable_ai_chats() or not self.right_panel.ai_chat_panel:
            QMessageBox.information(self, "AI Chat", "Enable AI Chats in Preferences to use AI actions.")
            return
        target_path = self.current_path
        detached = self._active_ai_chat_panel()
        if detached:
            if target_path:
                detached.open_chat_for_page(target_path)
            detached.send_action_message(action, prompt, text)
            detached.focus_input()
            try:
                if self._detached_ai_chat_window:
                    self._detached_ai_chat_window.raise_()
                    self._detached_ai_chat_window.activateWindow()
            except Exception:
                pass
            return
        self._ensure_right_panel_visible()
        if target_path:
            self.right_panel.focus_ai_chat(target_path, create=True)
        self.right_panel.send_ai_action(action, prompt, text)
        if target_path:
            self.editor.set_ai_chat_available(True, active=self.right_panel.is_active_chat_for_page(target_path))
        self.right_panel.focus_ai_chat_input()

    def _perform_one_shot_prompt(self, text: str) -> None:
        """Run the One-Shot prompt in an overlay and insert on Accept."""
        if not text or not text.strip():
            self.statusBar().showMessage("Select text to run One-Shot Prompt on.", 4000)
            return
        cursor = self.editor.textCursor()
        if not cursor.hasSelection():
            self.statusBar().showMessage("Select text to run One-Shot Prompt on.", 4000)
            return
        panel = self.right_panel.ai_chat_panel
        if not panel:
            self.statusBar().showMessage("AI Chat panel not available; enable AI Chats.", 4000)
            return

        try:
            from .ai_chat_panel import ServerManager
        except Exception:
            self.statusBar().showMessage("AI worker unavailable.", 4000)
            return
        try:
            from .one_shot_overlay import OneShotPromptOverlay
        except Exception:
            self.statusBar().showMessage("One-Shot overlay unavailable.", 4000)
            return

        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        try:
            server_mgr = ServerManager()
            if default_server_name:
                server_cfg = server_mgr.get_server(default_server_name)
                if server_cfg:
                    server_config = server_cfg
        except Exception:
            server_config = {}

        if not server_config:
            server_config = getattr(panel, "current_server", None) or {}

        try:
            default_model_name = config.load_default_ai_model()
        except Exception:
            default_model_name = None
        if default_model_name:
            model = default_model_name
        else:
            model = (server_config.get("default_model") if server_config else None) or (
                getattr(panel, "model_combo", None).currentText() if getattr(panel, "model_combo", None) else None
            ) or "gpt-3.5-turbo"

        doc = self.editor.document()
        start_pos = cursor.selectionStart()
        end_pos = cursor.selectionEnd()

        def _accept_insert(assistant_text: str) -> None:
            try:
                replace_cursor = QTextCursor(doc)
                replace_cursor.setPosition(start_pos)
                replace_cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
                replace_cursor.beginEditBlock()
                replace_cursor.removeSelectedText()
                replace_cursor.insertText(assistant_text)
                replace_cursor.endEditBlock()
                self.editor.setFocus()
            except Exception:
                pass

        system_prompt = _load_one_shot_prompt()
        overlay = OneShotPromptOverlay(
            parent=self,
            server_config=server_config,
            model=model,
            system_prompt=system_prompt,
            on_accept=_accept_insert,
        )
        try:
            self._one_shot_overlay = overlay
        except Exception:
            pass
        try:
            self.editor.push_focus_lost_suppression()
        except Exception:
            try:
                setattr(self.editor, "_suppress_focus_lost_once", True)
            except Exception:
                pass
        # Disable autosave while the one-shot overlay is open (focus shifts / timers
        # should not write the file during this workflow).
        prev_suspend_autosave = bool(getattr(self, "_suspend_autosave", False))
        self._suspend_autosave = True

        def _overlay_cleanup() -> None:
            try:
                self.editor.pop_focus_lost_suppression()
            except Exception:
                pass
            try:
                self._suspend_autosave = prev_suspend_autosave
            except Exception:
                pass
            try:
                setattr(self, "_one_shot_overlay", None)
            except Exception:
                pass

        try:
            overlay.finished.connect(lambda *_: _overlay_cleanup())
        except Exception:
            pass
        try:
            geo = self.geometry()
            overlay.move(geo.center() - overlay.rect().center())
        except Exception:
            pass
        overlay.open_with_selection(text)

    def _open_inline_ai_prompt(self, anchor: QPoint, insert_pos: int) -> None:
        if not config.load_enable_ai_chats():
            self.statusBar().showMessage("Enable AI Chats in Preferences to use inline prompts.", 4000)
            return
        if getattr(self, "_inline_ai_worker", None):
            self.statusBar().showMessage("Inline AI is already streaming.", 3000)
            return
        try:
            from .inline_ai_prompt import InlineAIPromptOverlay
        except Exception:
            self.statusBar().showMessage("Inline AI prompt unavailable.", 4000)
            return

        def _send(prompt: str) -> None:
            self._start_inline_ai_stream(prompt, insert_pos)

        overlay = InlineAIPromptOverlay(parent=self, on_send=_send, anchor=QPoint(anchor.x(), anchor.y() + 10))
        try:
            self._inline_ai_prompt_overlay = overlay
        except Exception:
            pass
        try:
            self.editor.push_focus_lost_suppression()
        except Exception:
            try:
                setattr(self.editor, "_suppress_focus_lost_once", True)
            except Exception:
                pass

        def _overlay_cleanup() -> None:
            try:
                self.editor.pop_focus_lost_suppression()
            except Exception:
                pass
            try:
                setattr(self, "_inline_ai_prompt_overlay", None)
            except Exception:
                pass

        try:
            overlay.finished.connect(lambda *_: _overlay_cleanup())
        except Exception:
            pass
        overlay.show()

    def _on_page_tag_inserted(self, tag: str) -> None:
        if self.tags_tab:
            self.tags_tab.add_tag(tag)

    def _start_inline_ai_stream(self, prompt: str, insert_pos: int) -> None:
        if not prompt.strip():
            return
        if getattr(self, "_inline_ai_worker", None):
            return
        # Remove /ai trigger text only when action is actually performed.
        try:
            doc = self.editor.document()
            raw_text = self.editor.toPlainText()
            if 0 <= insert_pos <= len(raw_text) and raw_text[insert_pos:insert_pos + 4] == "/ai ":
                tc = QTextCursor(doc)
                tc.setPosition(insert_pos)
                tc.setPosition(insert_pos + 4, QTextCursor.KeepAnchor)
                tc.removeSelectedText()
        except Exception:
            pass
        try:
            from .ai_chat_panel import ServerManager, ApiWorker
        except Exception:
            self.statusBar().showMessage("AI worker unavailable.", 4000)
            return

        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        try:
            server_mgr = ServerManager()
            if default_server_name:
                server_cfg = server_mgr.get_server(default_server_name)
                if server_cfg:
                    server_config = server_cfg
        except Exception:
            server_config = {}
        if not server_config:
            server_config = getattr(self.right_panel.ai_chat_panel, "current_server", None) or {}
        if not server_config:
            self.statusBar().showMessage("No AI server configured.", 4000)
            return

        try:
            default_model_name = config.load_default_ai_model()
        except Exception:
            default_model_name = None
        if default_model_name:
            model = default_model_name
        else:
            panel = self.right_panel.ai_chat_panel
            model = (server_config.get("default_model") if server_config else None) or (
                getattr(panel, "model_combo", None).currentText() if getattr(panel, "model_combo", None) else None
            ) or "gpt-3.5-turbo"

        system_prompt = _load_one_shot_prompt()
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt.strip()},
        ]

        doc = self.editor.document()
        cursor = QTextCursor(doc)
        cursor.setPosition(max(0, insert_pos))
        cursor.beginEditBlock()
        cursor.setKeepPositionOnInsert(False)
        self._inline_ai_stream_cursor = cursor
        self._inline_ai_stream_used = False
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
        except Exception:
            pass

        worker = ApiWorker(server_config, messages, model, stream=True, parent=self)
        worker.chunk.connect(self._append_inline_ai_chunk)
        worker.finished.connect(self._finalize_inline_ai_stream)
        worker.failed.connect(self._inline_ai_failed)
        self._inline_ai_worker = worker
        self.statusBar().showMessage("AI streaming...", 3000)
        worker.start()

    def _append_inline_ai_chunk(self, chunk: str) -> None:
        if not chunk:
            return
        cursor = getattr(self, "_inline_ai_stream_cursor", None)
        if cursor is None:
            return
        try:
            cursor.insertText(chunk)
            self._inline_ai_stream_used = True
        except Exception:
            pass

    def _finalize_inline_ai_stream(self, full: str) -> None:
        try:
            cursor = getattr(self, "_inline_ai_stream_cursor", None)
            used = getattr(self, "_inline_ai_stream_used", False)
            if cursor is not None and not used and full:
                cursor.insertText(full)
            if cursor is not None:
                try:
                    cursor.endEditBlock()
                except Exception:
                    pass
            if cursor is not None:
                try:
                    self.editor.setTextCursor(cursor)
                    self.editor.setFocus()
                except Exception:
                    pass
            self.statusBar().showMessage("Inline AI complete.", 2500)
        finally:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self._inline_ai_worker = None
            for attr in ("_inline_ai_stream_cursor", "_inline_ai_stream_used"):
                try:
                    delattr(self, attr)
                except Exception:
                    pass

    def _inline_ai_failed(self, err: str) -> None:
        self.statusBar().showMessage(f"Inline AI failed: {err}", 6000)
        try:
            cursor = getattr(self, "_inline_ai_stream_cursor", None)
            if cursor is not None:
                cursor.endEditBlock()
        except Exception:
            pass
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        self._inline_ai_worker = None
        for attr in ("_inline_ai_stream_cursor", "_inline_ai_stream_used"):
            try:
                delattr(self, attr)
            except Exception:
                pass

    def _append_one_shot_chunk(self, doc: QTextDocument, chunk: str) -> None:
        """Append streamed chunk into the one-shot buffer just before the footer."""
        try:
            footer_pos = getattr(self, "_one_shot_footer_pos", None)
            footer_len = getattr(self, "_one_shot_footer_len", None)
            if footer_pos is None or footer_len is None:
                return
            cursor = getattr(self, "_one_shot_stream_cursor", None)
            if cursor is None:
                cursor = QTextCursor(doc)
                cursor.beginEditBlock()
                self._one_shot_stream_cursor = cursor
            cursor.setPosition(footer_pos)
            cursor.insertText(chunk)
            self._one_shot_footer_pos = footer_pos + len(chunk)
            self._one_shot_stream_used = True
        except Exception:
            pass

    def _finalize_one_shot(self, doc: QTextDocument, full: str) -> None:
        """Finish the one-shot response: ensure content inserted, select, scroll."""
        try:
            start, _, orig = getattr(self, "_one_shot_range", (None, None, None))
            footer_pos = getattr(self, "_one_shot_footer_pos", None)
            footer_len = getattr(self, "_one_shot_footer_len", 0)
            editor = self.editor
            if start is None or footer_pos is None:
                self.statusBar().showMessage("One-Shot missing state; aborting.", 4000)
                return
            # If no chunks streamed, insert the full response now
            if not getattr(self, "_one_shot_stream_used", False) and full:
                cursor = QTextCursor(doc)
                cursor.beginEditBlock()
                cursor.setPosition(footer_pos)
                cursor.insertText(full)
                cursor.endEditBlock()
                footer_pos += len(full)
            stream_cursor = getattr(self, "_one_shot_stream_cursor", None)
            if stream_cursor is not None:
                try:
                    stream_cursor.endEditBlock()
                except Exception:
                    pass
            end_pos = footer_pos + footer_len
            final_cursor = QTextCursor(doc)
            final_cursor.setPosition(start)
            final_cursor.setPosition(end_pos, QTextCursor.KeepAnchor)
            editor.setTextCursor(final_cursor)
            editor.setFocus()
            try:
                self._scroll_cursor_to_top_quarter(final_cursor, animate=True, flash=False)
            except Exception:
                pass
            self.statusBar().showMessage("One-Shot complete.", 2500)
        except Exception as exc:
            self.statusBar().showMessage(f"One-Shot failed to apply response: {exc}", 4000)
        finally:
            try:
                QApplication.restoreOverrideCursor()
            except Exception:
                pass
            self._one_shot_worker = None
            for attr in (
                "_one_shot_range",
                "_one_shot_footer_pos",
                "_one_shot_footer_len",
                "_one_shot_stream_used",
                "_one_shot_stream_cursor",
            ):
                try:
                    delattr(self, attr)
                except Exception:
                    pass

    def _one_shot_failed(self, err: str) -> None:
        self.statusBar().showMessage(f"One-Shot failed: {err}", 6000)
        try:
            QApplication.restoreOverrideCursor()
        except Exception:
            pass
        try:
            stream_cursor = getattr(self, "_one_shot_stream_cursor", None)
            if stream_cursor is not None:
                stream_cursor.endEditBlock()
        except Exception:
            pass
        self._one_shot_worker = None
        for attr in (
            "_one_shot_range",
            "_one_shot_footer_pos",
            "_one_shot_footer_len",
            "_one_shot_stream_used",
            "_one_shot_stream_cursor",
        ):
            try:
                delattr(self, attr)
            except Exception:
                pass

    def _send_selection_to_ai_chat(self, text: str) -> None:
        if not text.strip():
            return
        target_path = self.current_path
        detached = self._active_ai_chat_panel()
        if detached:
            if target_path:
                detached.open_chat_for_page(target_path)
            detached.send_text_message(text)
            detached.focus_input()
            try:
                if self._detached_ai_chat_window:
                    self._detached_ai_chat_window.raise_()
                    self._detached_ai_chat_window.activateWindow()
            except Exception:
                pass
            return
        if not self.right_panel.ai_chat_panel:
            self.statusBar().showMessage("Enable AI chats to send text from the editor.", 4000)
            return
        self._ensure_right_panel_visible()
        if target_path:
            self.right_panel.focus_ai_chat(target_path, create=True)
            self.editor.set_ai_chat_available(True, active=self.right_panel.is_active_chat_for_page(target_path))
        else:
            if self.right_panel.ai_chat_index is not None:
                self.right_panel.tabs.setCurrentIndex(self.right_panel.ai_chat_index)
        if not self.right_panel.send_text_to_chat(text):
            self.statusBar().showMessage("Enable AI chats to send text from the editor.", 4000)

    def _on_ai_chat_navigate(self, chat_folder: Optional[str]) -> None:
        """Handle 'Go To Page' from AI chat by focusing the matching page in the editor."""
        if not chat_folder:
            return
        # Accept both folder paths and full page refs (may include anchors)
        base, anchor = self._split_link_anchor(chat_folder)
        file_path = self._normalize_editor_path(base or "/")
        # Stay on the current page if it already matches this chat's folder/file
        if self.current_path:
            try:
                current_folder = "/" + Path(self.current_path.lstrip("/")).parent.as_posix()
            except Exception:
                current_folder = None
            target_folder = "/" + Path(file_path.lstrip("/")).parent.as_posix() if file_path else None
            if current_folder == target_folder or self.current_path == file_path:
                # If an anchor was provided, attempt to scroll within current page
                if anchor and self.current_path == file_path:
                    self._scroll_to_anchor_slug(self._anchor_slug(anchor))
                self.editor.setFocus()
                self._apply_focus_borders()
                return
        if not file_path:
            return
        if self.current_path == file_path:
            self.editor.setFocus()
            self._apply_focus_borders()
            return
        # Keep AI Chat tab visible while navigating
        self.right_panel.focus_ai_chat(chat_folder)
        try:
            # Open base file then scroll to anchor if provided
            self._open_file(file_path, force=True)
            try:
                if anchor:
                    self._scroll_to_anchor_slug(self._anchor_slug(anchor))
            except Exception:
                pass
            self.editor.setFocus()
            self._apply_focus_borders()
        except Exception:
            return

    def _on_ai_chat_page_written(self, page_path: Optional[str]) -> None:
        """Refresh nav tree/search-adjacent panels when AI tools write a page."""
        if not page_path:
            return
        try:
            normalized = self._normalize_editor_path(str(page_path))
        except Exception:
            normalized = str(page_path)
        if normalized:
            self._pending_selection = normalized
        self._refresh_tree()

    def _on_ai_overlay_requested(self, text: str, anchor) -> None:
        """Open command bar focused on AI actions using chat panel context."""
        if not text:
            return
        self._show_command_bar(query="AI / ", ai_text_override=text)

    def _open_in_file_manager(self, path: Path) -> bool:
        """Try to open a file or folder in the OS file manager."""
        try:
            if not path.exists():
                return False
            url = QUrl.fromLocalFile(str(path))
            if QDesktopServices.openUrl(url):
                return True
            # Fallback per-OS
            if sys.platform.startswith("darwin"):
                result = subprocess.run(["open", str(path)], check=False)
                return result.returncode == 0
            if sys.platform.startswith("win"):
                result = subprocess.run(["explorer", str(path)], check=False)
                return result.returncode == 0
            # Assume Linux/Unix
            result = subprocess.run(["xdg-open", str(path)], check=False)
            return result.returncode == 0
        except Exception as exc:
            self._alert(f"Failed to open file manager: {exc}")
            return False

    def _reload_current_page(self) -> None:
        """Reload the current page from disk without altering history."""
        if not self.current_path:
            self.statusBar().showMessage("No page to reload", 2000)
            return
        self._save_dirty_page(reason="reload")
        self._remember_history_cursor()
        self._open_file(self.current_path, add_to_history=False, force=True, restore_history_cursor=True)
        self.statusBar().showMessage("Reloaded current page", 2000)

    def _start_inline_creation(
        self,
        parent_path: str,
        global_pos: QPoint,
        anchor_index: Optional[QModelIndex] = None,
    ) -> None:
        self._cancel_inline_editor()
        editor = InlineNameEdit(self.tree_view.viewport())
        editor.setPlaceholderText("Page name")
        editor.submitted.connect(lambda name: self._handle_inline_submit(parent_path, name))
        editor.cancelled.connect(self._inline_editor_cancelled)
        self._inline_editor = editor
        if anchor_index and anchor_index.isValid():
            rect = self.tree_view.visualRect(anchor_index)
            viewport_pos = rect.bottomLeft()
            viewport_pos.setY(viewport_pos.y() + 4)
        else:
            viewport_pos = self.tree_view.viewport().mapFromGlobal(global_pos)
        editor.move(viewport_pos)
        width = max(200, self.tree_view.viewport().width() - 40)
        editor.resize(width, editor.sizeHint().height())
        editor.show()
        editor.setFocus()
        try:
            editor.selectAll()
        except Exception:
            pass

    def _handle_inline_submit(self, parent_path: str, name: str) -> None:
        name = name.strip()
        if not name:
            self._cancel_inline_editor()
            return
        if not self._ensure_writable("create new pages"):
            self._cancel_inline_editor()
            return
        if "/" in name:
            self.statusBar().showMessage("Names cannot contain '/'", 4000)
            return
        target_path = self._join_paths(parent_path, name)
        try:
            resp = self.http.post("/api/path/create", json={"path": target_path, "is_dir": True})
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            if exc.response is not None and exc.response.status_code == 409:
                self.statusBar().showMessage("Name already exists", 4000)
            else:
                self._alert(f"Failed to create entry: {exc}")
            return
        except httpx.HTTPError as exc:
            self._alert(f"Failed to create entry: {exc}")
            return
        self._cancel_inline_editor()
        file_path = self._folder_to_file_path(target_path)
        if file_path:
            # Apply NewPage.txt template to the newly created page
            self._apply_new_page_template(file_path, name)
            self._pending_selection = file_path
            self._mark_homebase_unsynced_local_change()
            self._schedule_homebase_sync("page create")
        self._populate_vault_tree()

    def _trigger_tree_rename(self) -> None:
        """Start inline rename on the selected tree item (and select text)."""
        index = self.tree_view.currentIndex()
        if (not index or not index.isValid()) and self.current_path:
            self._select_tree_path(self.current_path)
            index = self.tree_view.currentIndex()
        if not index or not index.isValid():
            return
        path = index.data(PATH_ROLE)
        if not path or path == FILTER_BANNER:
            return
        parent_path = self._parent_path(index)
        rect = self.tree_view.visualRect(index)
        global_pos = self.tree_view.viewport().mapToGlobal(rect.topLeft())
        self.tree_view.setFocus(Qt.ShortcutFocusReason)
        self._start_inline_rename(path, parent_path, global_pos, anchor_index=index)

    def _start_inline_rename(
        self,
        path: str,
        parent_path: str,
        global_pos: QPoint,
        anchor_index: Optional[QModelIndex] = None,
    ) -> None:
        self._cancel_inline_editor()
        current_name = Path(path.rstrip("/")).name
        editor = InlineNameEdit(self.tree_view.viewport())
        editor.setText(current_name)
        editor.submitted.connect(lambda name: self._handle_inline_rename(parent_path, path, name))
        editor.cancelled.connect(self._inline_editor_cancelled)
        self._inline_editor = editor
        if anchor_index and anchor_index.isValid():
            rect = self.tree_view.visualRect(anchor_index)
            viewport_pos = rect.bottomLeft()
            viewport_pos.setY(viewport_pos.y() + 4)
        else:
            viewport_pos = self.tree_view.viewport().mapFromGlobal(global_pos)
        editor.move(viewport_pos)
        width = max(200, self.tree_view.viewport().width() - 40)
        editor.resize(width, editor.sizeHint().height())
        editor.show()
        editor.setFocus()
        try:
            QTimer.singleShot(0, editor.selectAll)
        except Exception:
            pass

    def _handle_inline_rename(self, parent_path: str, old_path: str, new_name: str) -> None:
        self._cancel_inline_editor()
        new_name = new_name.strip()
        if not new_name:
            return
        if "/" in new_name:
            self.statusBar().showMessage("Names cannot contain '/'", 4000)
            return
        dest_path = self._join_paths(parent_path, new_name)
        if dest_path == old_path:
            return
        if not self._ensure_writable("rename pages or folders"):
            return
        old_open_path = self._folder_to_file_path(old_path)
        if self.current_path and old_open_path and self.current_path == old_open_path:
            self._save_dirty_page(reason="pre-rename save")
        try:
            resp = self.http.post("/api/file/rename", json={"from": old_path, "to": dest_path})
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, f"Failed to rename {old_path}")
            return
        data = resp.json()
        self._apply_path_map(data.get("page_map") or {})
        self._register_link_path_map(data.get("page_map") or {})
        new_open_path = self._folder_to_file_path(dest_path)
        if new_open_path:
            self._pending_selection = new_open_path
            # Reload editor if this page was open so heading/title changes are reflected
            if self.current_path == new_open_path:
                self._open_file(new_open_path, force=True)
        self._populate_vault_tree()

    def _move_path_dialog(self, folder_path: str, current_parent: str) -> None:
        if not self._ensure_writable("move pages or folders"):
            return
        implied_target_path = "/"
        implied_target_label = "<Vault Root>"
        filter_path = (self._nav_filter_path or "").strip()
        if filter_path and filter_path != "/":
            implied_target_path = filter_path
            filter_name = Path(filter_path.rstrip("/")).name or (path_to_colon(filter_path) or "Filtered")
            implied_target_label = f"<{filter_name}>"
        dlg = JumpToPageDialog(
            self,
            filter_prefix=filter_path if (filter_path and filter_path != "/") else None,
            filter_label=path_to_colon(filter_path) if (filter_path and filter_path != "/") else None,
            allow_filter_removal=False,
            show_rewrite_links_checkbox=True,
            http_client=self.http,
            remote_mode=self._remote_mode,
            implied_target_path=implied_target_path,
            implied_target_label=implied_target_label,
        )
        dlg.setWindowTitle("Move To…")
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        target_path = dlg.selected_path()
        if not target_path:
            return
        rewrite_links = dlg.should_rewrite_links()
        parent_clean = self._file_path_to_folder(target_path) or "/"
        if not parent_clean.startswith("/"):
            parent_clean = f"/{parent_clean}"
        leaf = Path(folder_path.rstrip("/")).name
        dest_path = self._join_paths(parent_clean, leaf)
        if dest_path == folder_path:
            return
        
        from_display = path_to_colon(folder_path) or folder_path
        to_display = path_to_colon(dest_path) or dest_path
        
        # Show progress dialog for folder moves (folders don't end in .txt)
        # Pages end in .txt, folders do not
        is_folder = not folder_path.endswith('.txt')
        show_progress = is_folder  # Always show progress for folder moves
        
        progress = None
        if show_progress:
            progress = QProgressDialog(f"Moving {from_display}...", None, 0, 3, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setWindowTitle("Moving Folder")
            progress.setCancelButton(None)  # Can't cancel move operation
            progress.setValue(0)
            QApplication.processEvents()
        else:
            # For simple moves, just show status bar
            self.statusBar().showMessage(f"Moving {from_display} to {to_display}...")
            QApplication.processEvents()
        
        old_open_path = self._folder_to_file_path(folder_path)
        if self.current_path and old_open_path and self.current_path == old_open_path:
            self._save_dirty_page(reason="pre-move save")
        
        try:
            if progress:
                progress.setLabelText(f"Moving {from_display} via API...")
                progress.setValue(1)
                QApplication.processEvents()
            
            resp = self.http.post("/api/file/move", json={"from": folder_path, "to": dest_path, "rewrite_links": rewrite_links})
            resp.raise_for_status()
            
            if progress:
                progress.setLabelText("Reindexing links...")
                progress.setValue(2)
                QApplication.processEvents()
            
        except httpx.HTTPError as exc:
            if progress:
                progress.close()
            self._alert_api_error(exc, f"Failed to move {folder_path}")
            return
        
        self._handle_move_response(dest_path, resp.json(), progress)

    def _on_tree_move_requested(self, from_path: str, dest_path: str) -> None:
        if from_path == dest_path:
            return
        if not self._ensure_writable("move pages or folders"):
            return
        
        from_display = path_to_colon(from_path) or from_path
        to_display = path_to_colon(dest_path) or dest_path
        
        # Show progress dialog for folder moves (folders don't end in .txt)
        # Pages end in .txt, folders do not
        is_folder = not from_path.endswith('.txt')
        show_progress = is_folder  # Always show progress for folder moves
        
        progress = None
        if show_progress:
            progress = QProgressDialog(f"Moving {from_display}...", None, 0, 3, self)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.setWindowTitle("Moving Folder")
            progress.setCancelButton(None)  # Can't cancel move operation
            progress.setValue(0)
            QApplication.processEvents()
        else:
            # For simple moves, just show status bar
            self.statusBar().showMessage(f"Moving {from_display} to {to_display}...")
            QApplication.processEvents()
        
        old_open_path = self._folder_to_file_path(from_path)
        if self.current_path and old_open_path and self.current_path == old_open_path:
            self._save_dirty_page(reason="pre-move save")
        
        try:
            if progress:
                progress.setLabelText(f"Moving {from_display} via API...")
                progress.setValue(1)
                QApplication.processEvents()
            
            resp = self.http.post("/api/file/move", json={"from": from_path, "to": dest_path})
            resp.raise_for_status()
            
            if progress:
                progress.setLabelText("Reindexing links...")
                progress.setValue(2)
                QApplication.processEvents()
            
        except httpx.HTTPError as exc:
            if progress:
                progress.close()
            self._alert_api_error(exc, f"Failed to move {from_path}")
            return
        
        self._handle_move_response(dest_path, resp.json(), progress)
    
    def _on_tree_reorder_requested(self, parent_path: str, page_order: list) -> None:
        """Handle reordering pages within the same parent."""
        print(f"[UI] _on_tree_reorder_requested called: parent={parent_path}, count={len(page_order)}")
        if not self._ensure_writable("reorder pages"):
            self.statusBar().clearMessage()
            return
        try:
            print(f"[UI] Posting reorder to API: parent={parent_path}")
            resp = self.http.post("/api/tree/reorder", json={"parent_path": parent_path, "page_order": page_order})
            resp.raise_for_status()
            data = resp.json()
            print(f"[UI] Reorder API response: {data}")
            # Update tree version if returned
            if "version" in data:
                # Clear tree cache so next refresh will fetch updated order
                self._tree_cache.clear()
                # Move the row in the model directly instead of full refresh
                if hasattr(self.tree_view, "_pending_reorder"):
                    pending = self.tree_view._pending_reorder
                    parent_index = pending["parent_index"]
                    src_row = pending["src_row"]
                    dest_row = pending["dest_row"]
                    
                    # Get the parent item
                    if parent_index.isValid():
                        parent_item = self.tree_model.itemFromIndex(parent_index)
                    else:
                        parent_item = self.tree_model.invisibleRootItem()
                    
                    if parent_item and src_row != dest_row:
                        # Take the row from source position
                        row_items = parent_item.takeRow(src_row)
                        if row_items:
                            # Insert at destination position
                            parent_item.insertRow(dest_row, row_items)
                            # Select the moved item
                            new_index = self.tree_model.index(dest_row, 0, parent_index)
                            self.tree_view.setCurrentIndex(new_index)
                    
                    # Clean up
                    delattr(self.tree_view, "_pending_reorder")
            
            self.statusBar().showMessage("Items reordered", 2000)
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, "Failed to reorder items")
            self.statusBar().clearMessage()
            # Clean up on error
            if hasattr(self.tree_view, "_pending_reorder"):
                delattr(self.tree_view, "_pending_reorder")
    
    def _on_drag_status_changed(self, message: str) -> None:
        """Update status bar during drag operations."""
        if message:
            self.statusBar().showMessage(message)
        else:
            # Clear status message after drag
            if self.current_path:
                display_path = path_to_colon(self.current_path) or self.current_path
                self.statusBar().showMessage(f"Editing {display_path}")

    # --- Zim import --------------------------------------------------

    def _prompt_zim_source(self) -> Optional[Path]:
        dialog = QFileDialog(self, "Select Zim wiki folder or .txt file")
        dialog.setFileMode(QFileDialog.Directory)
        dialog.setOption(QFileDialog.ShowDirsOnly, True)
        dialog.setOption(QFileDialog.DontUseNativeDialog, True)
        dialog.setNameFilter("Zim wiki (*.txt);;All files (*)")
        if self.vault_root:
            dialog.setDirectory(self.vault_root)
        if dialog.exec() != QFileDialog.Accepted:
            return None
        files = dialog.selectedFiles()
        if not files:
            return None
        return Path(files[0])

    def _prompt_import_target_folder(self) -> Optional[str]:
        dlg = JumpToPageDialog(self, http_client=self.http, remote_mode=self._remote_mode)
        dlg.setWindowTitle("Import Target")
        result = dlg.exec()
        if result != QDialog.Accepted:
            return None
        target_path = dlg.selected_path()
        if not target_path:
            return None
        folder = self._file_path_to_folder(target_path)
        return folder or "/"

    def _import_zim_wiki(self) -> None:
        if not self._require_local_mode("Import a Zim wiki"):
            return
        if not self.vault_root or not config.has_active_vault():
            self._alert("Select a vault before importing.")
            return
        if not self._ensure_writable("import pages"):
            return
        source = self._prompt_zim_source()
        if not source:
            return
        target_folder = self._prompt_import_target_folder()
        if target_folder is None:
            return
        rename_map: dict[str, str] = {}
        rename_dlg = PageRenameDialog(self)
        if rename_dlg.exec() == QDialog.Accepted:
            rename_map = rename_dlg.mapping()
        try:
            pages, attachment_count = zim_import.plan_import(source, target_folder, rename_map or None)
        except Exception as exc:
            self._alert(f"Import failed: {exc}")
            return
        if not pages:
            self._alert("No .txt files found to import.")
            return

        def _short_name(name: str, limit: int = 40) -> str:
            clean = name or ""
            if len(clean) <= limit:
                return clean
            return clean[:limit] + "..."

        total_steps = len(pages) + attachment_count
        progress = QProgressDialog("Importing Zim wiki...", None, 0, max(1, total_steps), self)
        progress.setWindowTitle("Importing")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()

        steps_done = 0
        attachments_done = 0
        for idx, page in enumerate(pages, start=1):
            name = _short_name(page.rel_stem)
            progress.setLabelText(
                f"Pages {idx}/{len(pages)}, attachments {attachments_done}/{attachment_count} — {name}"
            )
            QApplication.processEvents()
            try:
                self._log_write("import page", page.dest_path, page.content, auto=None)
                resp = self.http.post("/api/file/write", json={"path": page.dest_path, "content": page.content})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                progress.close()
                self._alert_api_error(exc, f"Failed to import {page.rel_stem}")
                return
            steps_done += 1
            progress.setValue(steps_done)
            QApplication.processEvents()

            if page.attachments:
                progress.setLabelText(
                    f"Copying {len(page.attachments)} attachment(s) for {name} "
                    f"({attachments_done}/{attachment_count} done)"
                )
                QApplication.processEvents()
                files_payload = []
                open_files = []
                try:
                    for attachment in page.attachments:
                        fh = attachment.open("rb")
                        open_files.append(fh)
                        files_payload.append(("files", (attachment.name, fh, "application/octet-stream")))
                    resp = self.http.post("/files/attach", data={"page_path": page.dest_path}, files=files_payload)
                    resp.raise_for_status()
                except Exception as exc:
                    progress.close()
                    self._alert(f"Failed to copy attachments for {page.rel_stem}: {exc}")
                    for fh in open_files:
                        try:
                            fh.close()
                        except Exception:
                            pass
                    return
                finally:
                    for fh in open_files:
                        try:
                            fh.close()
                        except Exception:
                            pass
                steps_done += len(page.attachments)
                attachments_done += len(page.attachments)
                progress.setValue(steps_done)
                QApplication.processEvents()

        # Indicate tree refresh while it runs
        progress.setRange(0, total_steps + 1)
        progress.setLabelText("Updating tree…")
        QApplication.processEvents()
        progress.setValue(total_steps)
        QApplication.processEvents()
        self._populate_vault_tree()
        progress.setValue(total_steps + 1)
        progress.close()
        self.statusBar().showMessage(f"Imported {len(pages)} page(s) from Zim", 5000)
        QMessageBox.information(
            self,
            "Import complete",
            f"Import complete: imported {len(pages)} page(s) and {attachment_count} attachment(s).\n"
            "You probably need to reindex the vault.",
        )

    def _handle_move_response(self, dest_path: str, data: dict, progress=None) -> None:
        path_map = data.get("page_map") or {}
        self._apply_path_map(path_map)
        # Immediately rewrite backlinks if enabled
        if self.rewrite_backlinks_on_move and path_map:
            try:
                self._rewrite_links_on_disk_immediate(path_map)
            except Exception as exc:
                print(f"[UI] Failed to rewrite backlinks: {exc}")
        new_open_path = self._folder_to_file_path(dest_path)
        # If we were filtered to a subtree and the item moved outside it, clear the filter so it stays visible
        if self._nav_filter_path and dest_path and not dest_path.startswith(self._nav_filter_path):
            self._clear_nav_filter()
        
        # Clear cache to ensure fresh data on next expansion
        self._tree_cache.clear()
        self._tree_path_version.clear()
        
        # Set pending selection for the new location
        if new_open_path:
            self._pending_selection = new_open_path
            if self.current_path == new_open_path:
                self._open_file(new_open_path, force=True)
        
        if progress:
            progress.setLabelText("Refreshing tree view...")
            progress.setValue(3)
            QApplication.processEvents()
        
        # Repopulate tree - this is necessary to show the moved item in its new location
        self._populate_vault_tree()
        
        if progress:
            progress.close()
        
        self.statusBar().showMessage(f"Moved to {path_to_colon(dest_path) or dest_path}", 3000)

    def _apply_new_page_template(self, file_path: str, page_name: str) -> None:
        """Apply the preferred template to a newly created page."""
        template_name = "Default"
        try:
            template_name = config.load_default_page_template()
        except Exception:
            template_name = "Default"
        template_path = self._resolve_template_path(template_name, fallback="Default")
        self._apply_template_from_path(file_path, page_name, str(template_path))

    def _apply_template_from_path(self, file_path: str, page_name: str, template_path: str) -> None:
        """Apply a specific template file to a newly created page."""
        if not self.vault_root:
            return
        if not self._ensure_writable("apply templates or write pages"):
            return
        
        # Load template
        template_file = Path(template_path)
        if not template_file.exists():
            return
        
        try:
            template_content = template_file.read_text(encoding="utf-8")
            print(f"[Template] Loaded template: {template_file}")
        except Exception:
            return
        
        # Process template variables and extract cursor position
        content, cursor_pos = self._process_template_variables(template_content, page_name)
        
        # Store cursor position for use when opening the file
        self._template_cursor_position = cursor_pos
        
        # Write to the new page file
        abs_path = Path(self.vault_root) / file_path.lstrip("/")
        try:
            abs_path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _get_qotd(self) -> str:
        """Fetch a random quote of the day from feedburner."""
        try:
            return "Have a super awesome day!\n\t-- Rodney Norman"
        except Exception as e:
            if log_enabled("editor_markdown"):
                print(f"[DEBUG] Failed to fetch QOTD: {e}")
            return ""

    def _process_template_variables(self, template: str, page_name: str) -> tuple[str, int]:
        """Replace template variables with their values.
        
        Returns:
            Tuple of (processed_content, cursor_position)
            cursor_position is -1 if {{cursor}} tag not found
        """
        from datetime import datetime
        
        # Get current date in format: Tuesday 29 April 2025
        now = datetime.now()
        day_date_year = now.strftime("%A %d %B %Y")
        vault_name = self.vault_root_name or ""
        vault_slug = "_".join(vault_name.split()) if vault_name else ""
        vars_map = {
            "{{PageName}}": page_name,
            "{{PageSlug}}": "_".join(page_name.split()),
            "{{VaultName}}": vault_name,
            "{{VaultSlug}}": vault_slug,
            "{{DayDateYear}}": day_date_year,
            "{{YYYY}}": f"{now:%Y}",
            "{{Month}}": now.strftime("%B"),
            "{{MM}}": f"{now:%m}",
            "{{DOW}}": now.strftime("%A"),
            "{{dd}}": f"{now:%d}",
        }
        
        # Only fetch QOTD if template uses it
        if "{{QOTD}}" in template:
            vars_map["{{QOTD}}"] = self._get_qotd()
        
        # Find cursor position in original template
        cursor_pos = -1
        if "{{cursor}}" in template:
            cursor_pos = template.find("{{cursor}}")
        
        # Replace all variables EXCEPT {{cursor}} first
        result = template
        for k, v in vars_map.items():
            if k != "{{cursor}}":
                # If this replacement happens before cursor position, adjust cursor_pos
                if cursor_pos >= 0:
                    # Count occurrences before cursor position
                    before_cursor = result[:cursor_pos]
                    count = before_cursor.count(k)
                    if count > 0:
                        # Adjust cursor position by the length difference for each replacement
                        len_diff = len(v) - len(k)
                        cursor_pos += count * len_diff
                result = result.replace(k, v)
        
        # Now remove cursor tag
        result = result.replace("{{cursor}}", "")
        
        return result, cursor_pos

    def _process_folder_template_variables(self, template: str, folder_path: str, folder_name: str, page_name: str) -> tuple[str, int]:
        """Replace folder template variables with their values.
        
        Includes {{FolderName}} in addition to standard template variables.
        
        Returns:
            Tuple of (processed_content, cursor_position)
            cursor_position is -1 if {{cursor}} tag not found
        """
        from datetime import datetime
        
        # Get current date in format: Tuesday 29 April 2025
        now = datetime.now()
        day_date_year = now.strftime("%A %d %B %Y")
        vault_name = self.vault_root_name or ""
        vault_slug = "_".join(vault_name.split()) if vault_name else ""
        folder_path_clean = folder_path or ""
        if folder_path_clean and not folder_path_clean.startswith("/"):
            folder_path_clean = f"/{folder_path_clean}"
        folder_leaf = Path(folder_path_clean).name if folder_path_clean else folder_name
        folder_file_path = f"{folder_path_clean.rstrip('/')}/{folder_leaf}{PAGE_SUFFIX}" if folder_path_clean else ""
        folder_colon = path_to_colon(folder_file_path) if folder_file_path else ""
        vars_map = {
            "{{FolderName}}": folder_name,
            "{{FolderSlug}}": "_".join(folder_name.split()),
            "{{FolderPathSlug}}": folder_colon,
            "{{PageName}}": page_name,
            "{{PageSlug}}": "_".join(page_name.split()),
            "{{VaultName}}": vault_name,
            "{{VaultSlug}}": vault_slug,
            "{{DayDateYear}}": day_date_year,
            "{{YYYY}}": f"{now:%Y}",
            "{{Month}}": now.strftime("%B"),
            "{{MM}}": f"{now:%m}",
            "{{DOW}}": now.strftime("%A"),
            "{{dd}}": f"{now:%d}",
        }
        
        # Only fetch QOTD if template uses it
        if "{{QOTD}}" in template:
            vars_map["{{QOTD}}"] = self._get_qotd()
        
        # Find cursor position in original template
        cursor_pos = -1
        if "{{cursor}}" in template:
            cursor_pos = template.find("{{cursor}}")
        
        # Replace all variables EXCEPT {{cursor}} first
        result = template
        for k, v in vars_map.items():
            if k != "{{cursor}}":
                # If this replacement happens before cursor position, adjust cursor_pos
                if cursor_pos >= 0:
                    # Count occurrences before cursor position
                    before_cursor = result[:cursor_pos]
                    count = before_cursor.count(k)
                    if count > 0:
                        # Adjust cursor position by the length difference for each replacement
                        len_diff = len(v) - len(k)
                        cursor_pos += count * len_diff
                result = result.replace(k, v)
        
        # Now remove cursor tag
        result = result.replace("{{cursor}}", "")
        
        return result, cursor_pos

    def _inline_editor_cancelled(self) -> None:
        self._inline_editor = None

    def _cancel_inline_editor(self) -> None:
        if self._inline_editor:
            editor = self._inline_editor
            self._inline_editor = None
            try:
                editor.cancelled.disconnect(self._inline_editor_cancelled)
            except Exception:
                pass
            editor.deleteLater()

    def _delete_path(self, folder_path: str, open_path: Optional[str], global_pos: QPoint) -> None:
        try:
            if folder_path == "/":
                self.statusBar().showMessage("Cannot delete the root page.", 4000)
                return
            if not self._ensure_writable("delete pages or folders"):
                return
            # Remember a sensible sibling/parent to focus after deletion
            delete_index = self.tree_view.currentIndex()
            next_focus_path: Optional[str] = None
            if delete_index.isValid():
                # Prefer the visually previous item (regardless of hierarchy)
                prev_index = self.tree_view.indexAbove(delete_index)
                if prev_index.isValid():
                    next_focus_path = prev_index.data(OPEN_ROLE) or prev_index.data(PATH_ROLE)
                if not next_focus_path:
                    parent_index = delete_index.parent()
                    if parent_index.isValid():
                        next_focus_path = parent_index.data(OPEN_ROLE) or parent_index.data(PATH_ROLE)
            confirm = QMessageBox(self)
            confirm.setIcon(QMessageBox.Warning)
            confirm.setWindowTitle("Delete")
            warning = ""
            store = None
            target_folder = folder_path
            if folder_path.lower().endswith(tuple(PAGE_SUFFIXES)):
                target_folder = self._file_path_to_folder(folder_path)
            try:
                if self.right_panel.ai_chat_panel:
                    store = self.right_panel.ai_chat_panel.store  # type: ignore[attr-defined]
                if store and store.has_chats_under(target_folder):
                    warning = '<br><span style="color:red; font-weight:bold;">WARNING: this will delete any stored AI chats.</span>'
            except Exception:
                warning = ""
            confirm.setTextFormat(Qt.TextFormat.RichText)
            confirm.setText(f"Delete {folder_path}? This cannot be undone.{warning}")
            confirm.setStandardButtons(QMessageBox.Yes | QMessageBox.No)
            confirm.setDefaultButton(QMessageBox.No)
            confirm.adjustSize()
            confirm.move(global_pos - QPoint(confirm.width() // 2, confirm.height() // 2))
            result = confirm.exec()
            if result != QMessageBox.Yes:
                return
            deleting_current = bool(self.current_path and open_path and self.current_path == open_path)
            if deleting_current:
                try:
                    self.editor.unload_for_delete()
                except Exception:
                    pass
                self.current_path = None
                self._skip_next_selection_open = True
                self._pending_selection = next_focus_path or self._parent_path(self.tree_view.currentIndex())
            try:
                resp = self.http.post("/api/path/delete", json={"path": folder_path})
                resp.raise_for_status()
            except httpx.HTTPError as exc:
                self._alert_api_error(exc, f"Failed to delete {folder_path}")
                return
            
            # Remove deleted paths from history buffer
            self._remove_deleted_paths_from_history(folder_path)
            
            if store:
                try:
                    store.delete_chats_under(target_folder)  # type: ignore[attr-defined]
                except Exception:
                    pass
            selection_model = self.tree_view.selectionModel()
            if selection_model:
                blocker = QSignalBlocker(selection_model)
                try:
                    self.tree_view.clearSelection()
                    self.tree_view.setCurrentIndex(QModelIndex())
                finally:
                    del blocker
            
            # Re-focus parent after refresh to avoid dangling selection on the deleted item
            try:
                if not self._pending_selection:
                    parent_for_selection = Path(target_folder.lstrip("/")).parent.as_posix()
                    if parent_for_selection in ("", "."):
                        parent_for_selection = "/"
                    else:
                        parent_for_selection = f"/{parent_for_selection}"
                    self._pending_selection = parent_for_selection
            except Exception:
                if not self._pending_selection:
                    self._pending_selection = "/"
            self._skip_next_selection_open = True
            QTimer.singleShot(0, self._populate_vault_tree)
            self.right_panel.refresh_tasks()
            self.right_panel.refresh_calendar()
            self.right_panel.refresh_links(self.current_path)
        except Exception as exc:
            # Catch-all to keep the UI alive; surface error to the user.
            try:
                self._alert(f"Unexpected error while deleting {folder_path}: {exc}")
            except Exception:
                pass

    def _delete_current_page_from_editor(self, global_pos: QPoint) -> None:
        # Center dialog on editor instead of mouse position
        editor_center = self.editor.mapToGlobal(self.editor.rect().center())
        self._delete_current_page(editor_center)

    def _delete_current_page_from_menu(self) -> None:
        self._delete_current_page(None)

    def _delete_current_page(self, global_pos: Optional[QPoint]) -> None:
        if not self.current_path:
            self.statusBar().showMessage("No page to delete.", 3000)
            return
        if global_pos is None:
            global_pos = self.mapToGlobal(self.rect().center())
        folder_path = self._file_path_to_folder(self.current_path)
        self._delete_path(folder_path, self.current_path, global_pos)

    def _parent_path(self, index: QModelIndex) -> str:
        parent = index.parent()
        if parent.isValid():
            return parent.data(PATH_ROLE) or "/"
        return "/"

    def _join_paths(self, parent_path: str, name: str) -> str:
        parent = (parent_path or "/").rstrip("/")
        if parent in ("", "/"):
            return f"/{name}"
        return f"{parent}/{name}"

    def _deferred_select_tree_path(self, target_path: str) -> None:
        """Select tree path after deferring to next event loop iteration."""
        if not target_path:
            return
        if self._tree_refresh_in_progress:
            self._pending_selection = target_path
            return
        try:
            self._ensure_tree_path_loaded(target_path)
            if self._select_tree_path(target_path):
                self._selection_retry_path = None
                return
            if self._nav_filter_path:
                filter_root = self._nav_filter_path.rstrip("/") or "/"
                if not (target_path == filter_root or target_path.startswith(filter_root + "/")):
                    return
            if getattr(self, "_selection_retry_path", None) == target_path:
                return
            self._selection_retry_path = target_path
            self._pending_selection = target_path
            self._populate_vault_tree()
        except Exception as exc:
            logNav(f"Failed to select tree path {target_path}: {exc}")

    def _select_tree_path(self, target_path: str) -> bool:
        match = self._find_item(self.tree_model.invisibleRootItem(), target_path)
        if match:
            index = match.index()
            if index.isValid():
                self.tree_view.setCurrentIndex(index)
                self.tree_view.scrollTo(index)
                return True
        return False

    def _locate_current_page_in_tree(self) -> None:
        """Manually locate the current page in the navigator."""
        if not self.current_path:
            self.statusBar().showMessage("No page to locate", 3000)
            return
        if self._ensure_journal_visible_for_path(self.current_path):
            return
        self._ensure_tree_path_loaded(self.current_path)
        if not self._select_tree_path(self.current_path):
            self._pending_selection = self.current_path
            self._populate_vault_tree()

    def _ensure_journal_visible_for_path(self, path: str) -> bool:
        if not self._is_journal_path(path):
            return False
        if not self._show_journal_in_nav:
            self._set_show_journal_in_nav(True, select_path=path)
            return True
        if self._nav_filter_path and not self._is_journal_path(self._nav_filter_path):
            self._nav_filter_path = None
            try:
                config.save_nav_filter_path(None)
            except Exception:
                pass
            self._sync_nav_filter_to_panels(None)
            self._apply_nav_filter_style()
            self._pending_selection = path
            self._populate_vault_tree()
            return True
        return False

    def _sync_nav_tree_to_active_page(self) -> None:
        """Automatically sync the nav tree to highlight the currently active page in the editor.
        
        This is called when a file is opened via _open_file() to keep the tree selection
        in sync with the active editor page. It respects active filters and lazy-loads
        necessary parent nodes to make the page visible in the tree.
        """
        if not self.current_path:
            return
        suppress_path = (self._suppress_nav_sync_path or "").rstrip("/")
        if suppress_path and self.current_path.startswith(suppress_path + "/"):
            self._suppress_nav_sync_path = None
            return
        try:
            # Ensure all parent nodes are loaded so we can select the target
            self._ensure_tree_path_loaded(self.current_path)
            # Select and scroll to the page in the tree
            self._select_tree_path(self.current_path)
            logNav(f"_sync_nav_tree_to_active_page: selected {self.current_path}")
        except Exception as e:
            logNav(f"_sync_nav_tree_to_active_page: error syncing {self.current_path} ({e})")

    def _find_item(self, parent: QStandardItem, target: str) -> Optional[QStandardItem]:
        for row in range(parent.rowCount()):
            child = parent.child(row)
            child_path = child.data(PATH_ROLE)
            child_open = child.data(OPEN_ROLE)
            if target in (child_path, child_open):
                return child
            found = self._find_item(child, target)
            if found:
                return found
        return None

    def _apply_path_map(self, path_map: dict[str, str]) -> None:
        """Update local state (open page, history, bookmarks) after a rename/move."""
        if not path_map:
            return

        def _rewrite_list(items: list[str]) -> list[str]:
            return [path_map.get(item, item) for item in items]

        if self.current_path in path_map:
            new_current = path_map[self.current_path]
            self.current_path = new_current
            try:
                self._refresh_editor_context(new_current)
            except Exception:
                pass
            self._pending_selection = new_current

        self.page_history = _rewrite_list(self.page_history)
        self.bookmarks = _rewrite_list(self.bookmarks)
        self._history_cursor_positions = {path_map.get(k, k): v for k, v in self._history_cursor_positions.items()}
        if getattr(self, "virtual_pages", None) is not None:
            self.virtual_pages = {path_map.get(p, p) for p in self.virtual_pages}
        try:
            config.save_bookmarks(self.bookmarks)
        except Exception:
            pass
        self._refresh_bookmark_buttons()

    def _register_link_path_map(self, path_map: dict[str, str]) -> None:
        """Track link rewrite hints and trigger background actions based on preference."""
        if not path_map:
            return
        if self.link_update_mode == "none":
            return
        # Always stash path maps; reindexing is user-initiated unless index is missing.
        self._pending_link_path_maps.append(dict(path_map))

    def _trigger_background_reindex(self) -> None:
        """Schedule a background reindex; guard against overlapping calls."""
        if not self._pending_reindex_trigger:
            return
        self._pending_reindex_trigger = False
        print("[UI] Reindex requested (link update mode=reindex)")
        self._reindex_vault(show_progress=False)

    def _rewrite_links_on_disk_immediate(self, path_map: dict[str, str]) -> None:
        """Rewrite page links across the vault immediately after a move."""
        if not self.vault_root or not path_map:
            return
        try:
            resp = self.http.post("/api/vault/update-links", json={"path_map": path_map})
            resp.raise_for_status()
            data = resp.json()
            touched = data.get("touched") or []
            if touched:
                print(f"[UI] Rewrote backlinks in {len(touched)} file(s)")
                self.statusBar().showMessage(f"Updated backlinks in {len(touched)} file(s)", 3000)
        except httpx.HTTPError as exc:
            print(f"[UI] Failed to rewrite backlinks: {exc}")

    def _ensure_page_title(self, content: str, path: Optional[str]) -> str:
        """Ensure first non-empty line is a heading matching the leaf name if missing."""
        if not path:
            return content
        leaf = Path(path.rstrip("/")).stem
        lines = content.splitlines()
        first_idx = None
        for idx, line in enumerate(lines):
            if line.strip():
                first_idx = idx
                break
        if first_idx is None:
            return f"# {leaf}\n"
        first = lines[first_idx].lstrip()
        if first.startswith("#"):
            heading_text = first.lstrip("#").strip()
            # If heading already matches leaf, keep as-is; otherwise leave untouched
            if heading_text.lower() == leaf.lower():
                lines[first_idx] = f"# {leaf}"
            return "\n".join(lines)
        # Insert heading before first content line
        lines.insert(first_idx, f"# {leaf}")
        return "\n".join(lines)

    def _gather_indexes(self, leaves_only: bool) -> list[QModelIndex]:
        model = self.tree_model
        flat: list[QModelIndex] = []

        def recurse(parent_index: QModelIndex) -> None:
            rows = model.rowCount(parent_index)
            for row in range(rows):
                idx = model.index(row, 0, parent_index)
                is_dir = bool(idx.data(TYPE_ROLE))
                if not leaves_only or not is_dir:
                    flat.append(idx)
                recurse(idx)

        recurse(QModelIndex())
        return [idx for idx in flat if idx.isValid()]

    def _navigate_history_back(self) -> None:
        """Navigate to previous page in history (Alt+Left)."""
        if not self.page_history or self.history_index <= 0:
            return
        self._exit_vi_insert_on_activate()
        self._remember_history_cursor()
        self.history_index -= 1
        target_path = self.page_history[self.history_index]
        if log_enabled("navigation"):
            print(f"[HISTORY] Navigate back: index {self.history_index+1} -> {self.history_index}, opening: {target_path}")
        self._suspend_selection_open = True
        try:
            self._open_file(target_path, add_to_history=True, restore_history_cursor=True)
        finally:
            self._suspend_selection_open = False
        QTimer.singleShot(0, self.editor.setFocus)

    def _navigate_history_forward(self) -> None:
        """Navigate to next page in history (Alt+Right)."""
        if not self.page_history or self.history_index >= len(self.page_history) - 1:
            return
        self._exit_vi_insert_on_activate()
        self._remember_history_cursor()
        self.history_index += 1
        target_path = self.page_history[self.history_index]
        if log_enabled("navigation"):
            print(f"[HISTORY] Navigate forward: index {self.history_index-1} -> {self.history_index}, opening: {target_path}")
        self._suspend_selection_open = True
        try:
            self._open_file(target_path, add_to_history=True, restore_history_cursor=True)
        finally:
            self._suspend_selection_open = False
        QTimer.singleShot(0, self.editor.setFocus)
    
    def _reload_page_preserve_cursor(self, path: str) -> None:
        """Reload a page while keeping its last known cursor position."""
        if not self._feature_remember_cursor_position_enabled:
            self._open_file(path, add_to_history=False, force=True, restore_history_cursor=False)
            return
        saved_pos = self._history_cursor_positions.get(path)
        saved_scroll = self._history_scroll_positions.get(path)
        # Prefer the live cursor position if this tab is the one being reloaded
        if self.current_path == path:
            try:
                saved_pos = self.editor.textCursor().position()
                scroll_bar = self.editor.verticalScrollBar()
                if scroll_bar:
                    saved_scroll = scroll_bar.value()
            except Exception:
                pass
        self._remember_history_cursor()
        self._open_file(path, add_to_history=False, force=True, restore_history_cursor=True)
        if saved_pos is not None:
            cursor = self.editor.textCursor()
            cursor.setPosition(min(saved_pos, len(self.editor.toPlainText())))
            self.editor.setTextCursor(cursor)
        if saved_scroll is not None:
            try:
                scroll_bar = self.editor.verticalScrollBar()
                if scroll_bar:
                    scroll_bar.setValue(max(0, min(int(saved_scroll), scroll_bar.maximum())))
            except Exception:
                pass

    def _history_can_go_back(self) -> bool:
        """Return True if history has a previous entry to navigate to."""
        return bool(self.page_history) and self.history_index > 0

    def _history_can_go_forward(self) -> bool:
        """Return True if history has a forward entry."""
        return bool(self.page_history) and self.history_index < len(self.page_history) - 1

    def _on_editor_cursor_moved(self, position: int) -> None:
        """Persist last cursor position for the active page whenever it changes."""
        if not self.current_path:
            return
        if not self._feature_remember_cursor_position_enabled:
            return
        if self._suspend_cursor_history:
            return
        self._history_cursor_positions[self.current_path] = position
        if getattr(self, "_main_soft_scroll_enabled", True):
            self._soft_autoscroll_main()

    def _remember_history_cursor(self) -> None:
        """Remember the current cursor position for history restore."""
        if not self.current_path:
            return
        if not self._feature_remember_cursor_position_enabled:
            return
        try:
            pos = self.editor.textCursor().position()
            scroll_bar = self.editor.verticalScrollBar()
            scroll_pos = scroll_bar.value() if scroll_bar else None
        except Exception:
            return
        self._history_cursor_positions[self.current_path] = pos
        if scroll_pos is not None:
            self._history_scroll_positions[self.current_path] = scroll_pos

    def _should_focus_hr_tail(self, content: str) -> bool:
        """Return True if cursor should jump to trailing newline after a horizontal rule."""
        if not content:
            return False
        # Skip expensive work on very large files
        if len(content.encode("utf-8")) > 100_000:
            return False
        trimmed = content.rstrip("\n")
        if not trimmed:
            return False
        last_line = trimmed.splitlines()[-1]
        return last_line.strip() == "---"

    # --- History persistence & popup ---------------------------------

    def _handle_page_about_to_be_deleted(self, rel_path: str) -> None:
        """Handle page about to be deleted - unload editor if it's the current page."""
        if not self.current_path or not rel_path:
            return
        
        # Check if the page being deleted is currently open
        if self.current_path == rel_path or self.current_path.endswith(rel_path):
            try:
                self.editor.unload_for_delete()
            except Exception:
                pass
            self.current_path = None

    def _remove_deleted_paths_from_history(self, deleted_folder_path: str) -> None:
        """Remove deleted page(s) from history buffer and persist."""
        # Normalize the deleted path
        if deleted_folder_path.lower().endswith(tuple(PAGE_SUFFIXES)):
            deleted_folder_path = self._file_path_to_folder(deleted_folder_path)
        
        # Filter out any history entries that match or are subpaths of the deleted folder
        original_len = len(self.page_history)
        self.page_history = [
            path for path in self.page_history
            if not (path == deleted_folder_path or 
                    path.lower().endswith(tuple(PAGE_SUFFIXES)) and self._file_path_to_folder(path) == deleted_folder_path or
                    path.startswith(deleted_folder_path + "/") or
                    (path.lower().endswith(tuple(PAGE_SUFFIXES)) and self._file_path_to_folder(path).startswith(deleted_folder_path + "/")))
        ]
        
        # Remove cursor positions for deleted paths
        deleted_paths = [k for k in self._history_cursor_positions.keys() 
                        if k not in self.page_history]
        for path in deleted_paths:
            self._history_cursor_positions.pop(path, None)
            self._history_scroll_positions.pop(path, None)
        
        # Adjust history index if needed
        if self.history_index >= len(self.page_history):
            self.history_index = len(self.page_history) - 1
        if self.history_index < 0 and self.page_history:
            self.history_index = 0
        
        # Persist updated history if anything was removed
        if len(self.page_history) != original_len:
            self._persist_recent_history()
            self._refresh_history_buttons()

    def _persist_recent_history(self) -> None:
        """Persist recent history to the vault DB."""
        if not config.has_active_vault():
            return
        seen: set[str] = set()
        ordered: list[str] = []
        for path in self.page_history:
            if self._is_history_path_allowed(path) and path not in seen:
                seen.add(path)
                ordered.append(path)
        config.save_recent_history(ordered[-50:])
        # Persist cursor positions for the same set
        positions: dict[str, int] = {}
        if self._feature_remember_cursor_position_enabled:
            for path in ordered[-50:]:
                pos = self._history_cursor_positions.get(path)
                if isinstance(pos, int):
                    positions[path] = pos
        config.save_recent_history_positions(positions)

    def _restore_recent_history(self) -> None:
        """Restore recent history from the vault DB."""
        if not config.has_active_vault():
            return
        history = [p for p in config.load_recent_history() if self._is_history_path_allowed(p)]
        self.page_history = history[:50]
        self.history_index = len(self.page_history) - 1 if self.page_history else -1
        positions = config.load_recent_history_positions() if self._feature_remember_cursor_position_enabled else {}
        # Replace per-vault maps so entries from another vault cannot leak across switches.
        self._history_cursor_positions = {k: v for k, v in positions.items() if k in self.page_history}
        self._history_scroll_positions = {
            k: v for k, v in self._history_scroll_positions.items() if k in self.page_history
        }

    def _recent_history_candidates(self) -> list[str]:
        """Return MRU list (unique) for popup cycling."""
        seen: set[str] = set()
        result: list[str] = []
        for path in reversed(self.page_history):
            if (
                self._is_history_path_allowed(path)
                and path != self.current_path
                and path not in seen
            ):
                seen.add(path)
                result.append(path)
        return result

    @staticmethod
    def _is_history_path_allowed(path: Optional[str]) -> bool:
        if not path or not isinstance(path, str):
            return False
        return path != FILTER_BANNER

    def _heading_popup_candidates(self) -> list[dict]:
        """Return headings for current page (excluding horizontal rules)."""
        return [h for h in self._toc_headings if h and h.get("type") != "hr"]

    def _ensure_history_popup(self) -> None:
        if self._history_popup is None:
            popup = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
            popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            popup.setStyleSheet(
                "background: "
                f"{theme_value('main_window.history_popup.bg', 'rgba(30,30,30,220)')}; "
                "border: 1px solid "
                f"{theme_value('main_window.history_popup.border', '#888888')}; "
                "border-radius: 6px; padding: 8px;"
            )
            layout = QVBoxLayout(popup)
            layout.setContentsMargins(12, 8, 12, 8)
            self._history_popup_label = QLabel(popup)
            self._history_popup_label.setStyleSheet(
                "color: "
                f"{theme_value('main_window.history_popup.label_text', '#f5f5f5')}; "
                "font-weight: bold;"
            )
            layout.addWidget(self._history_popup_label)
            self._history_popup_list = QListWidget(popup)
            self._history_popup_list.setStyleSheet(
                "QListWidget { background: transparent; color: "
                f"{theme_value('main_window.history_popup.list_text', '#f5f5f5')}; "
                "border: none; }}"
                "QListWidget::item { padding: 4px 6px; }"
                "QListWidget::item:selected { background: "
                f"{theme_value('main_window.history_popup.list_selected_bg', 'rgba(255,255,255,40)')}; }}"
            )
            self._history_popup_list.viewport().installEventFilter(self)
            layout.addWidget(self._history_popup_list)
            self._history_popup = popup

    def _show_history_popup(self) -> None:
        self._ensure_history_popup()
        if not self._history_popup or not self._history_popup_label or not self._history_popup_list:
            return
        self._history_popup_list.clear()
        max_label = ""
        max_chars = 75
        if self._popup_mode == "history":
            for path in self._popup_items:
                display = self._recent_history_display_label(path)
                candidate = display[:max_chars]
                if len(candidate) > len(max_label):
                    max_label = candidate
                item = QListWidgetItem(self._elide_history_label(display, max_chars))
                item.setData(Qt.UserRole, path)
                self._history_popup_list.addItem(item)
            label = "Recent pages"
        elif self._popup_mode == "heading":
            for heading in self._popup_items:
                title = heading.get("title") or "(heading)"
                line = heading.get("line", 1)
                level = max(1, min(5, int(heading.get("level", 1))))
                indent = "    " * (level - 1)
                display = f"{indent}{title}  (line {line})"
                candidate = display[:max_chars]
                if len(candidate) > len(max_label):
                    max_label = candidate
                item = QListWidgetItem(self._elide_history_label(display, max_chars))
                self._history_popup_list.addItem(item)
            label = "Headings"
        else:
            return
        if 0 <= self._popup_index < self._history_popup_list.count():
            self._history_popup_list.setCurrentRow(self._popup_index)
        self._history_popup_label.setText(label)
        editor_rect = self.editor.rect()
        top_left = self.editor.mapToGlobal(editor_rect.topLeft())
        metrics = self._history_popup_list.fontMetrics()
        width_label = "M" * max_chars
        width_hint = metrics.horizontalAdvance(width_label) + 40
        popup_width = max(self._history_popup.sizeHint().width(), width_hint)
        popup_width = min(popup_width, editor_rect.width() - 40)
        # Make popup at least half the editor height for easier scanning
        min_height = int(editor_rect.height() * 0.5)
        popup_height = max(self._history_popup.sizeHint().height(), min_height)
        x = top_left.x() + editor_rect.width() // 2 - popup_width // 2
        y = top_left.y() + 24
        self._history_popup.resize(popup_width, popup_height)
        self._history_popup.move(x, y)
        self._history_popup.show()
        self._history_popup.raise_()

    def _cycle_popup(self, mode: str, reverse: bool = False) -> None:
        if mode == "history":
            self._exit_vi_insert_on_activate()
        if mode == "history":
            items = self._recent_history_candidates()
        elif mode == "heading":
            items = self._heading_popup_candidates()
        else:
            return
        if not items:
            return
        if self._popup_mode != mode:
            self._popup_items = items
            self._popup_mode = mode
            self._popup_index = 0
        else:
            self._popup_items = items
            if self._popup_index < 0 or self._popup_index >= len(items):
                self._popup_index = 0
            else:
                delta = -1 if reverse else 1
                self._popup_index = (self._popup_index + delta) % len(items)
        self._show_history_popup()

    def _activate_history_popup_selection(self) -> None:
        if not self._popup_items or self._popup_index < 0 or not self._popup_mode:
            self._hide_history_popup()
            return
        target = self._popup_items[self._popup_index]
        mode = self._popup_mode
        self._hide_history_popup()
        if mode == "history" and target:
            self._exit_vi_insert_on_activate()
            self._remember_history_cursor()
            # Ctrl+Tab should behave like true MRU switching: the selected page
            # becomes most-recent so alternating pages stays at the top.
            self._open_file(target, add_to_history=True, force=True, restore_history_cursor=True)
        elif mode == "heading" and target:
            try:
                pos = int(target.get("position", 0))
            except Exception:
                pos = 0
            if pos <= 0:
                try:
                    line = int(target.get("line", 1))
                except Exception:
                    line = 1
                block = self.editor.document().findBlockByNumber(max(0, line - 1))
                if block.isValid():
                    pos = block.position()
            cursor = self._cursor_at_position(max(0, pos))
            self._animate_or_flash_to_cursor(cursor)

    def _hide_history_popup(self) -> None:
        self._popup_items = []
        self._popup_index = -1
        self._popup_mode = None
        if self._history_popup:
            self._history_popup.hide()

    def _split_link_anchor(self, target: str) -> tuple[str, Optional[str]]:
        if "#" not in target:
            return target, None
        base, anchor = target.split("#", 1)
        return base or "", anchor or None

    def _anchor_slug(self, anchor: Optional[str]) -> Optional[str]:
        if not anchor:
            return None
        return heading_slug(anchor)

    def _scroll_to_anchor_slug(self, slug: Optional[str]) -> None:
        if not slug:
            return

        def jump() -> None:
            if not self.editor.jump_to_anchor(slug):
                self.statusBar().showMessage(f"Heading not found for anchor #{slug}", 4000)
                return
            cursor = self.editor.textCursor()
            self._animate_or_flash_to_cursor(cursor)

        QTimer.singleShot(0, jump)

    def _on_headings_changed(self, headings: list[dict]) -> None:
        self._toc_headings = list(headings or [])
        self._update_toc_visibility(force=True)

    def _update_toc_visibility(self, force: bool = False) -> None:
        """Show/hide/refresh the ToC based on headings and scrollability."""
        if not self.toc_widget:
            return
        # Check if TOC widget is enabled in preferences
        if not config.load_toc_widget_enabled():
            self.toc_widget.hide()
            return
        scrollbar = self.editor.verticalScrollBar()
        scrollable = scrollbar and scrollbar.maximum() > 0
        enough_headings = len(self._toc_headings) > 1
        should_show = scrollable and enough_headings
        if not should_show:
            self.toc_widget.hide()
            return
        if not self.toc_widget.isVisible() or force:
            self.toc_widget.set_headings(self._toc_headings)
            self.toc_widget.show()
            try:
                # Reset to idle opacity when showing
                self.toc_widget._opacity_effect.setOpacity(self.toc_widget._idle_opacity)
            except Exception:
                pass

    def _toc_jump_to_position(self, position: int) -> None:
        cursor = self._cursor_at_position(max(0, position))
        self._animate_or_flash_to_cursor(cursor)
        QTimer.singleShot(180, lambda: self.editor.setFocus(Qt.OtherFocusReason))

    def _queue_toc_jump(self, position: int, attempt: int = 0, flash: bool = True) -> None:
        if attempt > 5:
            return
        if getattr(self.editor, "_vi_paint_in_progress", False) or getattr(self.editor, "_suppress_paint_depth", 0):
            QTimer.singleShot(0, lambda: self._queue_toc_jump(position, attempt + 1, flash))
            return
        cursor = self._cursor_at_position(max(0, position))
        self._prepare_editor_jump()
        self._scroll_cursor_to_top_quarter(cursor, animate=False, flash=flash)
        QTimer.singleShot(0, self._restore_editor_after_jump)

    def _prepare_editor_jump(self) -> None:
        try:
            self.editor._push_paint_block()
        except Exception:
            pass
        try:
            self.editor.setUpdatesEnabled(False)
        except Exception:
            pass
        try:
            viewport = self.editor.viewport()
            if viewport:
                viewport.setUpdatesEnabled(False)
        except Exception:
            pass

    def _restore_editor_after_jump(self) -> None:
        try:
            viewport = self.editor.viewport()
            if viewport:
                viewport.setUpdatesEnabled(True)
        except Exception:
            pass
        try:
            self.editor.setUpdatesEnabled(True)
        except Exception:
            pass
        self._safe_pop_paint_block()

    def _safe_pop_paint_block(self) -> None:
        try:
            self.editor._pop_paint_block()
        except Exception:
            pass

    def _on_toc_collapsed_changed(self, collapsed: bool) -> None:
        config.save_toc_collapsed(collapsed)
        self._position_toc_widget()

    def _position_toc_widget(self) -> None:
        if not hasattr(self, "toc_widget") or not self.toc_widget:
            return
        viewport = self.editor.viewport()
        if viewport is None:
            return
        self._update_toc_visibility()
        margin = 12
        width = self.toc_widget.width()
        rect = viewport.rect()
        top_left_global = viewport.mapToGlobal(rect.topLeft())
        top_left = self.mapFromGlobal(top_left_global)
        x = max(margin, top_left.x() + rect.width() - width - margin)
        y = max(margin, top_left.y() + margin)
        self.toc_widget.move(x, y)
        self.toc_widget.raise_()

    def _scroll_cursor_to_top_quarter(self, cursor: QTextCursor, *, animate: bool, flash: bool) -> None:
        """Place the cursor so it sits in the top quarter of the viewport."""
        sb = self.editor.verticalScrollBar()
        self.editor.setTextCursor(cursor)
        if not sb:
            self.editor.ensureCursorVisible()
            if flash:
                self._flash_heading(cursor)
            return
        view_height = max(1, self.editor.viewport().height())
        target_rect = self.editor.cursorRect(cursor)
        desired_y = max(0, int(view_height * 0.25))
        cursor_doc_y = sb.value() + target_rect.top()
        target_val = int(cursor_doc_y - desired_y)
        target_val = max(0, min(target_val, sb.maximum()))
        current_val = sb.value()
        delta = abs(target_val - current_val)
        if not animate or delta <= 2:
            sb.setValue(target_val)
            self.editor.ensureCursorVisible()
            if flash:
                self._flash_heading(cursor)
            return
        if self._scroll_anim and self._scroll_anim.state() == QPropertyAnimation.Running:
            self._scroll_anim.stop()
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(min(150, max(60, delta)))
        anim.setStartValue(current_val)
        anim.setEndValue(target_val)
        def _finish_flash() -> None:
            try:
                self.editor.setTextCursor(cursor)
                self.editor.ensureCursorVisible()
                if flash:
                    self._flash_heading(cursor)
            except Exception:
                pass
        anim.finished.connect(_finish_flash)
        anim.start()
        self._scroll_anim = anim

    def _animate_or_flash_to_cursor(self, cursor: QTextCursor) -> None:
        """Smooth scroll to a heading; flash when positioned."""
        self._scroll_cursor_to_top_quarter(cursor, animate=True, flash=True)

    def _flash_heading(self, cursor: QTextCursor) -> None:
        """Briefly highlight the heading line."""
        try:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.cursor.clearSelection()
            sel.format.setBackground(theme_color("main_window.highlight.selection_bg", "#ffd54f"))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.format.setProperty(QTextFormat.UserProperty, 9991)
            current = self.editor.extraSelections()
            self.editor.setExtraSelections(current + [sel])

            def clear_flash() -> None:
                try:
                    keep = [
                        s
                        for s in self.editor.extraSelections()
                        if s.format.property(QTextFormat.UserProperty) != 9991
                    ]
                    self.editor.setExtraSelections(keep)
                except Exception:
                    pass

            QTimer.singleShot(220, clear_flash)
        except Exception:
            pass

    def _soft_autoscroll_main(self) -> None:
        """Gently keep the caret away from viewport edges while focused."""
        if not self.editor.hasFocus():
            return
        sb = self.editor.verticalScrollBar()
        viewport = self.editor.viewport()
        if not sb or not viewport:
            return
        rect = self.editor.cursorRect()
        height = viewport.height()
        line_height = max(12, rect.height() or self.editor.fontMetrics().height())
        target_val: Optional[int] = None
        threshold_px = max(4, int(height * 0.08))
        top_edge = rect.top()
        bottom_edge = height - rect.bottom()
        if bottom_edge < threshold_px:
            delta = int(self._main_soft_scroll_lines * line_height)
            target_val = sb.value() + delta
        elif top_edge < threshold_px:
            delta = int(self._main_soft_scroll_lines * line_height)
            target_val = sb.value() - delta
        if target_val is None:
            return
        target_val = max(sb.minimum(), min(sb.maximum(), target_val))
        if target_val == sb.value():
            return
        if self._scroll_anim and self._scroll_anim.state() == QPropertyAnimation.Running:
            try:
                self._scroll_anim.stop()
            except Exception:
                pass
        anim = QPropertyAnimation(sb, b"value", self)
        anim.setDuration(140)
        anim.setStartValue(sb.value())
        anim.setEndValue(target_val)
        anim.start()
        self._scroll_anim = anim

    def _navigate_hierarchy_up(self) -> None:
        """Navigate up in page hierarchy (Alt+Up): Move up one level, stop at root."""
        self._exit_vi_insert_on_activate()
        if not self.current_path:
            return
        current = Path(self.current_path.lstrip("/"))
        if current.suffix.lower() not in PAGE_SUFFIXES:
            return
        current_page_folder = current.parent
        parent_page_folder = current_page_folder.parent
        if str(parent_page_folder) in {"", "."}:
            parent_path = self._vault_root_page_path()
        else:
            parent_name = parent_page_folder.name
            parent_path = f"/{(parent_page_folder / f'{parent_name}{PAGE_SUFFIX}').as_posix()}"
        if not parent_path or parent_path == self.current_path:
            colon_path = path_to_colon(self.current_path)
            if colon_path:
                self.statusBar().showMessage(f"At root: {colon_path}")
            return
        self._hierarchy_last_child_by_parent[parent_path] = self.current_path
        self._remember_history_cursor()
        self._suspend_selection_open = True
        try:
            self._select_tree_path(parent_path)
            self._open_file(parent_path, add_to_history=True, restore_history_cursor=True)
        finally:
            self._suspend_selection_open = False
        parent_colon = path_to_colon(parent_path) or parent_path
        self.statusBar().showMessage(f"Up: {parent_colon}")

    def _on_nav_up_shortcut(self) -> None:
        if log_enabled("navigation"):
            print(f"[HIER] Alt+Up activated current={self.current_path!r}")
        self._navigate_hierarchy_up()

    def _on_nav_down_shortcut(self) -> None:
        if log_enabled("navigation"):
            print(f"[HIER] Alt+Down activated current={self.current_path!r}")
        self._navigate_hierarchy_down()

    def _navigate_hierarchy_down(self) -> None:
        """Navigate down in page hierarchy (Alt+Down): Open previous child, else first child page."""
        self._exit_vi_insert_on_activate()
        if not self.current_path:
            if log_enabled("navigation"):
                print("[HIER] down abort: no current_path")
            return
        if not self.vault_root:
            if log_enabled("navigation"):
                print("[HIER] down abort: no vault_root")
            return

        child_path: Optional[str] = None
        remembered_child = str(self._hierarchy_last_child_by_parent.get(self.current_path) or "").strip()
        if log_enabled("navigation"):
            print(f"[HIER] down parent={self.current_path!r} remembered_child={remembered_child!r}")
        if remembered_child:
            child_path = remembered_child
            if log_enabled("navigation"):
                print(f"[HIER] down using remembered child={child_path!r}")

        if not child_path:
            folder_path = self._file_path_to_folder(self.current_path)
            if not folder_path:
                if log_enabled("navigation"):
                    print("[HIER] down abort: empty folder_path")
                return
            root_page = self._vault_root_page_path()
            if root_page and self.current_path == root_page:
                folder = Path(self.vault_root)
            else:
                root_prefix = f"{self.vault_root_name}/" if self.vault_root_name else ""
                folder_rel = folder_path.lstrip("/")
                if root_prefix and folder_rel.startswith(root_prefix):
                    folder_rel = folder_rel[len(root_prefix) :]
                folder = Path(self.vault_root) / folder_rel
            if not folder.exists() or not folder.is_dir():
                if log_enabled("navigation"):
                    print(f"[HIER] down abort: parent folder missing/invalid {folder.as_posix()!r}")
                return
            subdirs = sorted([d for d in folder.iterdir() if d.is_dir()])
            if log_enabled("navigation"):
                print(f"[HIER] down fallback subdirs={[d.name for d in subdirs]!r}")
            for subdir in subdirs:
                candidate_file = subdir / f"{subdir.name}{PAGE_SUFFIX}"
                if candidate_file.exists():
                    candidate_rel = candidate_file.relative_to(Path(self.vault_root)).as_posix()
                    if self.vault_root_name and str(self.current_path).lstrip("/").startswith(f"{self.vault_root_name}/"):
                        child_path = f"/{self.vault_root_name}/{candidate_rel}"
                    else:
                        child_path = f"/{candidate_rel}"
                    if log_enabled("navigation"):
                        print(f"[HIER] down fallback selected child={child_path!r}")
                    break

        if not child_path:
            if log_enabled("navigation"):
                print("[HIER] down abort: no child_path resolved")
            return

        if log_enabled("navigation"):
            print(f"[HIER] down opening child={child_path!r}")
        self._remember_history_cursor()
        self._suspend_selection_open = True
        try:
            self._select_tree_path(child_path)
            self._open_file(child_path, add_to_history=True, restore_history_cursor=True)
        finally:
            self._suspend_selection_open = False

    def _navigate_tree(self, delta: int, leaves_only: bool) -> None:
        indexes = self._gather_indexes(leaves_only)
        if not indexes:
            return
        current = self.tree_view.currentIndex()
        try:
            idx = indexes.index(current)
        except ValueError:
            idx = -1 if delta > 0 else 0
        new_idx = max(0, min(len(indexes) - 1, idx + delta))
        if new_idx == idx:
            return
        target = indexes[new_idx]
        self._suspend_selection_open = True
        try:
            self.tree_view.setCurrentIndex(target)
            self.tree_view.scrollTo(target)
        finally:
            self._suspend_selection_open = False
        
        # Open the page for this node (whether folder or file)
        open_target = target.data(OPEN_ROLE) or target.data(PATH_ROLE)
        if open_target and open_target != self.current_path:
            self._schedule_tree_nav_open(str(open_target))

    def _schedule_tree_nav_open(self, open_target: str, debounce_ms: int = 280) -> None:
        target = str(open_target or "").strip()
        if not target:
            return
        self._tree_nav_open_target = target
        if self._tree_nav_open_timer is None:
            self._tree_nav_open_timer = QTimer(self)
            self._tree_nav_open_timer.setSingleShot(True)
            self._tree_nav_open_timer.timeout.connect(self._flush_tree_nav_open)
        self._tree_nav_open_timer.start(max(1, int(debounce_ms)))

    def _flush_tree_nav_open(self) -> None:
        target = str(self._tree_nav_open_target or "").strip()
        self._tree_nav_open_target = None
        if not target or target == self.current_path:
            return
        self._open_file(target)

    def _rebuild_vault_index_from_disk(self) -> None:
        """Drop and rebuild vault index from source files, preserving bookmarks/kv/ai tables."""
        if not self.vault_root:
            self._alert("Select a vault before rebuilding the index.")
            return
        if not self._ensure_writable("rebuild the vault index"):
            return
        confirm = QMessageBox.question(
            self,
            "Reindex",
            "Reindex vault from files?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self._search_sync.suspend("manual rebuild index")
        try:
            if self._remote_mode:
                # Remote reindex via API
                self._reindex_remote_vault(rebuild_search=False)
            else:
                # Local reindex
                if not config.has_active_vault():
                    self._alert("Select a vault before rebuilding the index.")
                    return
                print("[UI] Rebuild index from disk start")
                self.statusBar().showMessage("Reindexing vault from files...", 0)
                try:
                    # Close any active connection and wipe the settings DB so it is rebuilt like first-time startup
                    config.set_active_vault(None)
                    db_path = Path(self.vault_root) / ".stillpoint" / "settings.db"
                    try:
                        db_path.unlink()
                    except FileNotFoundError:
                        pass
                    config.set_active_vault(self.vault_root)
                except Exception as exc:
                    self.statusBar().showMessage("Reindex failed", 4000)
                    self._alert(f"Failed to reindex: {exc}")
                    print(f"[UI] Reindex failed: {exc}")
                    return
                print("[UI] Rebuild index from disk: indexing files")
                self._reindex_vault(show_progress=True)
                self.statusBar().showMessage("Reindex complete", 4000)
                print("[UI] Reindex from files complete")
        finally:
            self._search_sync.resume("manual rebuild index")

    def _rebuild_vault_search_index(self) -> None:
        """Rebuild the full-text search index from source files."""
        if not self.vault_root:
            self._alert("Select a vault before rebuilding the search index.")
            return
        if not self._ensure_writable("rebuild the vault search index"):
            return
        confirm = QMessageBox.question(
            self,
            "Rebuild Search Index",
            "Rebuild full-text search index from files?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if confirm != QMessageBox.Yes:
            return

        self._search_sync.suspend("manual rebuild search index")
        try:
            if self._remote_mode:
                # Remote reindex via API
                self._reindex_remote_vault(rebuild_search=True)
            else:
                # Local reindex
                if not config.has_active_vault():
                    self._alert("Select a vault before rebuilding the search index.")
                    return
                db_path = config._vault_db_path()
                if not db_path:
                    self._alert("No vault database found for search index.")
                    return
                self.statusBar().showMessage("Rebuilding search index...", 0)

                root = Path(self.vault_root)
                txt_files = []
                for suffix in PAGE_SUFFIXES:
                    for page_file in sorted(root.rglob(f"*{suffix}")):
                        if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                            continue
                        txt_files.append(page_file)

                progress = QProgressDialog("Indexing search...", None, 0, len(txt_files), self)
                progress.setWindowTitle("Search Index")
                progress.setCancelButton(None)
                progress.setWindowModality(Qt.WindowModal)
                progress.setMinimumDuration(0)
                progress.show()

                import sqlite3

                conn = sqlite3.connect(db_path, check_same_thread=False)
                try:
                    conn.execute(
                        """
                        CREATE TABLE IF NOT EXISTS pages_search_index (
                            id INTEGER PRIMARY KEY,
                            path TEXT NOT NULL UNIQUE,
                            mtime INTEGER NOT NULL
                        )
                        """
                    )
                    conn.execute("CREATE INDEX IF NOT EXISTS idx_pages_search_path ON pages_search_index(path)")
                    try:
                        conn.execute(
                            "CREATE VIRTUAL TABLE IF NOT EXISTS pages_search_fts USING fts5(content, content_rowid='id')"
                        )
                    except sqlite3.OperationalError as exc:
                        self.statusBar().showMessage("Search index unavailable", 4000)
                        self._alert(f"Search index unavailable: {exc}")
                        return
                    conn.execute("DELETE FROM pages_search_fts")
                    conn.execute("DELETE FROM pages_search_index")
                    conn.commit()

                    for idx, txt_file in enumerate(txt_files, start=1):
                        rel_path = txt_file.relative_to(root)
                        path_str = f"/{rel_path.as_posix()}"
                        try:
                            content = txt_file.read_text(encoding="utf-8")
                            mtime = int(txt_file.stat().st_mtime)
                            search_index.upsert_page(conn, path_str, mtime, content)
                        except Exception:
                            continue
                        progress.setValue(idx)
                        QApplication.processEvents()
                finally:
                    conn.close()
                    progress.close()

                page_count = len(txt_files)
                self.statusBar().showMessage(f"Search index rebuilt: {page_count} pages", 3000)
        finally:
            self._search_sync.resume("manual rebuild search index")

    def _reindex_remote_vault(self, rebuild_search: bool = False, on_complete=None) -> None:
        """Start remote reindex job and poll for progress."""
        try:
            resp = self.http.post("/api/vault/reindex", json={"rebuild_search": rebuild_search})
            resp.raise_for_status()
            job_data = resp.json()
            job_id = job_data.get("job_id")
            if not job_id:
                self._alert("Failed to start reindex: no job ID returned")
                return
        except httpx.HTTPError as exc:
            self._alert_api_error(exc, "Failed to start reindex")
            return
        
        print(f"[UI] Remote reindex started: job_id={job_id}")
        
        # Show progress dialog
        progress = QProgressDialog("Starting reindex...", None, 0, 100, self)
        progress.setWindowTitle("Reindexing")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.show()
        
        # Poll for status
        timer = QTimer()
        
        def poll_status():
            try:
                resp = self.http.get(f"/api/vault/reindex/status/{job_id}")
                resp.raise_for_status()
                status_data = resp.json()
                
                status = status_data.get("status", "unknown")
                progress_pct = status_data.get("progress", 0)
                message = status_data.get("message", "Processing...")
                current = status_data.get("current", 0)
                total = status_data.get("total", 0)
                
                if total > 0:
                    label = f"{message} ({current}/{total})"
                else:
                    label = message
                
                progress.setLabelText(label)
                progress.setValue(progress_pct)
                
                if status == "completed":
                    timer.stop()
                    progress.close()
                    self.statusBar().showMessage(message, 4000)
                    print(f"[UI] Remote reindex complete: {message}")
                    # Refresh UI
                    self.right_panel.refresh_tasks()
                    self.right_panel.refresh_links(self.current_path)
                    # Call completion callback if provided
                    if on_complete:
                        on_complete()
                elif status == "error":
                    timer.stop()
                    progress.close()
                    self._alert(f"Reindex failed: {message}")
                    print(f"[UI] Remote reindex failed: {message}")
            except Exception as exc:
                timer.stop()
                progress.close()
                self._alert(f"Failed to poll reindex status: {exc}")
                print(f"[UI] Remote reindex poll error: {exc}")
        
        timer.timeout.connect(poll_status)
        timer.start(1000)  # Poll every second
        poll_status()  # Initial poll

    def _open_webserver_dialog(self) -> None:
        """Open the web server control dialog."""
        if not self._require_local_mode("Start the web server"):
            return
        if not self.vault_root or not config.has_active_vault():
            self._alert("Select a vault before starting the web server.")
            return
        
        from sp.app.ui.webserver_dialog import WebServerDialog
        
        # Create non-modal dialog and keep reference to prevent garbage collection
        dialog = WebServerDialog(self.vault_root, config, parent=self)
        dialog.setAttribute(Qt.WA_DeleteOnClose)
        dialog.show()

    def _print_current_page(self) -> None:
        """Print or export current page to PDF."""
        if not self.current_path or not self.vault_root:
            self._alert("No page is currently open.")
            return
        self._print_page_for_path(self.current_path)

    def _print_page_for_path(self, path: str) -> None:
        if not path or not self.vault_root:
            self._alert("No page is currently open.")
            return
        normalized = self._normalize_editor_path(path)
        options = self._show_print_dialog()
        if not options:
            return

        try:
            token = self._get_print_token() if options["use_token"] else None
            mode = "tree" if options["include_subpages"] else "page"
            path_to_use = normalized.lstrip("/")
            if mode == "tree":
                parent = Path(normalized).parent.as_posix()
                path_to_use = parent if parent and parent != "." else Path(normalized).with_suffix("").as_posix()
            url = self._build_print_url(
                path_to_use,
                mode=mode,
                depth=options["depth"],
                token=token,
                show_header=options["include_header"],
                include_toc=options["include_toc"],
                toc_title=options["toc_title"],
                auto_pop=options.get("auto_pop_browser", True),
            )
            QDesktopServices.openUrl(QUrl(url))
            self.statusBar().showMessage("Print view opened in browser", 3000)
        except Exception as exc:
            self._alert(f"Failed to open print view: {exc}")
            print(f"[UI] Print page error: {exc}")

    def _show_print_dialog(self) -> Optional[dict]:
        from sp.app import config

        dialog = QDialog(self)
        dialog.setWindowTitle("Print to Browser")
        dialog.setMinimumWidth(400)
        dialog.setWindowModality(Qt.ApplicationModal)

        layout = QVBoxLayout()
        layout.addWidget(QLabel("Print options:"))

        include_header = QCheckBox("Include header (title/path)")
        include_header.setChecked(False)
        layout.addWidget(include_header)

        auto_pop_browser = QCheckBox("Auto pop the browser print dialogue?")
        auto_pop_browser.setChecked(config.load_print_auto_pop_browser())
        layout.addWidget(auto_pop_browser)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        layout.addWidget(divider)

        include_subpages = QCheckBox("Include subpages")
        include_subpages.setChecked(False)
        layout.addWidget(include_subpages)

        depth_row = QHBoxLayout()
        depth_label = QLabel("Max depth:")
        depth_input = QSpinBox()
        depth_input.setRange(1, 20)
        depth_input.setValue(1)
        depth_input.setEnabled(False)
        depth_label.setEnabled(False)
        depth_row.addWidget(depth_label)
        depth_row.addWidget(depth_input)
        depth_row.addStretch(1)
        layout.addLayout(depth_row)

        include_toc = QCheckBox("Include table of contents")
        include_toc.setChecked(False)
        include_toc.setEnabled(False)
        layout.addWidget(include_toc)

        toc_title_row = QHBoxLayout()
        toc_title_row.addSpacing(24)
        toc_title_label = QLabel("Page header title:")
        toc_title_input = QLineEdit()
        default_title = Path(self.current_path or "").stem if self.current_path else ""
        toc_title_input.setText(default_title)
        toc_title_input.setEnabled(False)
        toc_title_label.setEnabled(False)
        toc_title_row.addWidget(toc_title_label)
        toc_title_row.addWidget(toc_title_input)
        layout.addLayout(toc_title_row)

        def toggle_subpage_options(checked: bool) -> None:
            depth_input.setEnabled(checked)
            depth_label.setEnabled(checked)
            include_toc.setEnabled(checked)
            if checked:
                if not include_toc.isChecked():
                    include_toc.setChecked(True)
                toc_title_input.setEnabled(include_toc.isChecked())
                toc_title_label.setEnabled(include_toc.isChecked())
            else:
                toc_title_input.setEnabled(False)
                toc_title_label.setEnabled(False)

        def toggle_toc_title(checked: bool) -> None:
            if include_subpages.isChecked():
                toc_title_input.setEnabled(checked)
                toc_title_label.setEnabled(checked)

        include_subpages.toggled.connect(toggle_subpage_options)
        include_toc.toggled.connect(toggle_toc_title)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        dialog.setLayout(layout)
        dialog.setSizeGripEnabled(False)
        dialog.setFixedHeight(dialog.sizeHint().height())
        
        ok_button = buttons.button(QDialogButtonBox.Ok)
        if ok_button is not None:
            ok_button.setDefault(True)
            ok_button.setAutoDefault(True)
            ok_button.setFocus()
        dialog.raise_()
        dialog.activateWindow()
        if dialog.exec() != QDialog.Accepted:
            return None

        # Save the auto-pop preference
        config.save_print_auto_pop_browser(auto_pop_browser.isChecked())

        return {
            "include_subpages": include_subpages.isChecked(),
            "depth": depth_input.value(),
            "use_token": True,
            "include_header": include_header.isChecked(),
            "include_toc": include_toc.isChecked(),
            "toc_title": toc_title_input.text().strip(),
            "auto_pop_browser": auto_pop_browser.isChecked(),
        }

    def _get_print_token(self) -> Optional[str]:
        try:
            status = self.http.get("/auth/status")
            if status.is_success and not status.json().get("enabled"):
                return None
        except Exception:
            return None

        resp = self.http.post("/auth/print-token", json={"ttl_seconds": 900})
        if not resp.is_success:
            detail = "Failed to request print token."
            try:
                detail = resp.json().get("detail") or detail
            except Exception:
                pass
            raise RuntimeError(detail)
        return resp.json().get("token") or None

    def _build_print_url(
        self,
        path: str,
        *,
        mode: str,
        depth: int,
        token: Optional[str],
        show_header: bool,
        include_toc: bool,
        toc_title: str,
        auto_pop: bool = True,
    ) -> str:
        from urllib.parse import quote

        safe_path = quote(path.lstrip("/"), safe="/")
        auto_val = "1" if auto_pop else "0"
        url = f"{self.api_base}/print/{safe_path}?mode={mode}&auto={auto_val}"
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

    def _reindex_vault(self, show_progress: bool = False) -> None:
        """Reindex all pages in the vault."""
        if not self.vault_root or not config.has_active_vault():
            return
        if not self._ensure_writable("reindex the vault"):
            return
        print("[UI] Reindex start")
        
        root = Path(self.vault_root)
        txt_files = []
        for suffix in PAGE_SUFFIXES:
            for page_file in sorted(root.rglob(f"*{suffix}")):
                if suffix == LEGACY_SUFFIX and page_file.with_suffix(PAGE_SUFFIX).exists():
                    continue
                txt_files.append(page_file)
        
        progress = None
        if show_progress:
            progress = QProgressDialog("Indexing vault...", None, 0, len(txt_files), self)
            progress.setWindowTitle("Reindexing")
            progress.setCancelButton(None)
            progress.setWindowModality(Qt.WindowModal)
            progress.setMinimumDuration(0)
            progress.show()
            self.statusBar().showMessage("Building index...", 0)
        
        for idx, txt_file in enumerate(txt_files, start=1):
            rel_path = txt_file.relative_to(root)
            path_str = f"/{rel_path.as_posix()}"
            try:
                content = txt_file.read_text(encoding="utf-8")
                indexer.index_page(path_str, content)
            except Exception:
                continue
            if progress:
                progress.setValue(idx)
                QApplication.processEvents()
        
        self.right_panel.refresh_tasks()
        self.right_panel.refresh_links(self.current_path)
        
        page_count = len(txt_files)
        folder_count = len({p.parent for p in txt_files})
        if progress:
            progress.close()
            self.statusBar().showMessage(
                f"Index rebuilt: {page_count} pages across {folder_count} folders",
                4000,
            )
        print(f"[UI] Reindex summary: {page_count} pages across {folder_count} folders")
        print("[UI] Reindex complete")

    # --- Utilities -----------------------------------------------------
    def _alert(self, message: str) -> None:
        QMessageBox.critical(self, "StillPoint", message)

    def _build_issue_url(self, exception: str, stacktrace: str) -> Optional[QUrl]:
        if not (GITHUB_ISSUE_URL and GITHUB_OWNER and GITHUB_PROJECT):
            return None
        title = f"Stillpoint version {SP_VERSION} Issue"
        body_lines = [
            f"OS level: {platform.platform()}",
            f"version: {SP_VERSION}",
            f"exception: {exception or ''}",
            "stacktrace:",
            stacktrace or "",
            "",
            "User notes:",
            "",
        ]
        body = "\n".join(body_lines)
        url = GITHUB_ISSUE_URL.replace("<owner>", GITHUB_OWNER).replace("<repo>", GITHUB_PROJECT)
        url = url.format(title=quote(title), body=quote(body))
        return QUrl(url)

    def _alert_issue_report(self, message: str, exception: str, stacktrace: str) -> bool:
        box = QMessageBox(self)
        box.setWindowTitle("StillPoint")
        box.setIcon(QMessageBox.Critical)
        box.setText(message)
        detail_lines = []
        if exception:
            detail_lines.append(exception)
        if stacktrace:
            detail_lines.append(stacktrace)
        if detail_lines:
            box.setDetailedText("\n\n".join(detail_lines))
        report_btn = box.addButton("Report Issue", QMessageBox.ActionRole)
        box.addButton(QMessageBox.Ok)
        url = self._build_issue_url(exception, stacktrace)
        reported = False
        while True:
            box.exec()
            if box.clickedButton() == report_btn:
                if url:
                    QDesktopServices.openUrl(url)
                    reported = True
                    link = url.toString()
                    box.setText("Report Issue URL opened. Use the link below to open it again.")
                    box.setDetailedText(link)
                    report_btn.setText("Open Link Again")
                    continue
                return reported
            return reported

    def _alert_api_error(self, exc: httpx.HTTPError, fallback: str) -> None:
        detail = None
        resp = getattr(exc, "response", None)
        if resp is not None:
            try:
                data = resp.json()
                if isinstance(data, dict):
                    detail = data.get("detail") or data.get("message")
                    if isinstance(detail, dict):
                        message = detail.get("message") or fallback or str(exc)
                        exception = detail.get("exception") or ""
                        stacktrace = detail.get("traceback") or ""
                        if exception or stacktrace:
                            self._alert_issue_report(message, exception or str(exc), stacktrace)
                            return
                        detail = message
            except Exception:
                pass
            if resp.status_code == 401 and self._remote_mode:
                detail = detail or "Not authenticated. Use Vault → Server Login to sign in."
            if not detail:
                try:
                    text = resp.text
                    if text and text.strip():
                        detail = text.strip()
                except Exception:
                    pass
        message = detail or fallback or str(exc)
        self._alert(f"Reason: {message}")

    def _show_about_dialog(self) -> None:
        """Display a simple About dialog with app info and logo."""
        box = QMessageBox(self)
        box.setWindowTitle("About StillPoint")
        
        # Set icon pixmap (properly handles transparency)
        icon_path = self._find_asset("sp-full-transparent.png")
        if icon_path:
            try:
                pix = QPixmap(icon_path)
                if not pix.isNull():
                    # Scale to reasonable size while maintaining transparency
                    scaled_pix = pix.scaled(150, 150, Qt.KeepAspectRatio, Qt.SmoothTransformation)
                    box.setIconPixmap(scaled_pix)
            except Exception:
                pass
        
        box_text = (
            "<div style=\"text-align: center;\">"
            "<div style=\"font-size: 18px; font-weight: 600;\">StillPoint</div>"
            "<div style=\"margin-top: 6px;\">StillPoint is a local-first Markdown knowledge system for connected notes, tasks, and focused thinking — with optional AI & Agentic infusion — built to last.</div>"
            "<div style=\"margin-top: 10px;\"><b>Author:</b> Joseph Greenwood "
            "(<a href=\"mailto:info@stillpoint.info\">info@stillpoint.info</a>)</div>"
            f"<div><b>Version:</b> {APP_VERSION}</div>"
            "<div style=\"margin-top: 8px;\">"
            "<a href=\"https://github.com/grnwood/stillpoint/issues\">Report Issues</a>"
            " · "
            "<a href=\"https://stillpoint.info\">Docs</a>"
            "</div>"
            "<div>Open-source software, permissively licensed under Apache 2.0.</div>"
            "</div>"
        )
        box.setTextFormat(Qt.RichText)
        box.setText(box_text)
        box.setTextInteractionFlags(Qt.TextBrowserInteraction)
        try:
            for label in box.findChildren(QLabel):
                label.setOpenExternalLinks(True)
                label.setTextInteractionFlags(Qt.TextBrowserInteraction)
        except Exception:
            pass
        box.setStandardButtons(QMessageBox.Ok)
        box.exec()

    def _debug_crash_segfault(self) -> None:
        """Force a native crash for testing; guarded by env var in menu setup."""
        ctypes.string_at(0)

    def _update_window_title(self) -> None:
        parts: list[str] = []
        if self.current_path:
            colon = path_to_colon(self.current_path)
            if colon:
                parts.append(self._format_window_path(colon))
        if self.vault_root_name:
            parts.append(self.vault_root_name)
        if self._read_only:
            parts.append("Read-Only")
        suffix = "StillPoint Desktop"
        title = " | ".join(parts + [suffix]) if parts else suffix
        self.setWindowTitle(title)

    @staticmethod
    def _format_window_path(colon_path: str) -> str:
        segments = [seg for seg in (colon_path or "").split(":") if seg]
        if not segments:
            return colon_path
        if len(segments) <= 2:
            return " ▸ ".join(segments)
        return f"{segments[0]} ▸ … ▸ {segments[-1]}"

    def _apply_read_only_state(self) -> None:
        """Sync editor/widgets to the current read-only flag."""
        try:
            self.editor.set_read_only_mode(self._read_only)
        except Exception:
            try:
                self.editor.setReadOnly(self._read_only)
            except Exception:
                pass
        for win in list(getattr(self, "_page_windows", [])):
            try:
                win.set_read_only(self._read_only)
            except Exception:
                pass
        self._update_dirty_indicator()
        self._update_window_title()

    def eventFilter(self, obj, event):  # type: ignore[override]
        if event.type() == QEvent.Resize:
            if obj in (getattr(self, "bookmark_scroll_area", None), getattr(self, "toolbar", None)):
                QTimer.singleShot(0, self._sync_bookmark_scroll_range)
                QTimer.singleShot(0, self._update_bookmark_scroll_buttons)
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_G and (event.modifiers() & Qt.AltModifier):
                self._show_command_bar()
                return True
            if event.key() == Qt.Key_Tab and (event.modifiers() & Qt.ControlModifier):
                if event.modifiers() & Qt.ShiftModifier:
                    reverse = event.key() == Qt.Key_Backtab
                    if self._popup_mode == "history":
                        self._cycle_popup("history", reverse=True)
                    else:
                        self._cycle_popup("heading", reverse=reverse)
                else:
                    self._cycle_popup("history", reverse=event.key() == Qt.Key_Backtab)
                return True
        elif event.type() == QEvent.KeyRelease:
            if event.key() == Qt.Key_Control and self._popup_items:
                self._activate_history_popup_selection()
                return True
        return super().eventFilter(obj, event)


    def _update_dirty_indicator(self) -> None:
        """Refresh the dirty badge next to the VI indicator."""
        if getattr(self, "_mode_window_pending", False) or getattr(self, "_mode_window", None):
            return
        if not hasattr(self, "_dirty_status_label"):
            return
        if self._read_only:
            self._dirty_status_label.setText("O/")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style
                + " background-color: "
                f"{theme_value('main_window.badge.readonly_bg', '#9e9e9e')}; "
                "color: "
                f"{theme_value('main_window.badge.readonly_text', '#f5f5f5')}; "
                "margin-right: 6px; text-decoration: line-through;"
            )
            self._dirty_status_label.setToolTip("Read-only: changes cannot be saved in this window")
            return
        dirty = bool(getattr(self, "_dirty_flag", False))
        if dirty:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style
                + " background-color: "
                f"{theme_value('main_window.badge.dirty_bg', '#e57373')}; "
                "color: "
                f"{theme_value('main_window.badge.dirty_text', '#000000')}; "
                "margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("Unsaved changes")
        else:
            self._dirty_status_label.setText("●")
            self._dirty_status_label.setStyleSheet(
                self._badge_base_style
                + " background-color: "
                f"{theme_value('main_window.badge.clean_bg', '#81c784')}; "
                "color: "
                f"{theme_value('main_window.badge.clean_text', '#000000')}; "
                "margin-right: 6px;"
            )
            self._dirty_status_label.setToolTip("All changes saved")

    def _update_filter_indicator(self) -> None:
        """Refresh the filter badge next to the dirty indicator."""
        if not hasattr(self, "_filter_status_label"):
            return
        filter_path = getattr(self, "_nav_filter_path", None)
        if filter_path:
            display_path = path_to_colon(filter_path) or filter_path
            self._filter_status_label.setText("Filtered")
            self._filter_status_label.setToolTip(f"{display_path} (click to clear)")
            self._filter_status_label.show()
        else:
            self._filter_status_label.hide()
            self._filter_status_label.setText("")
            self._filter_status_label.setToolTip("")

    def _on_document_modified(self, modified: bool) -> None:
        """Lightweight dirty flag updater (avoid full markdown diff)."""
        if getattr(self, "_suspend_dirty_tracking", False):
            return
        new_state = bool(modified)
        if new_state and self._homebase_sync_engine:
            try:
                current_content = self.editor.to_markdown()
                if self._last_saved_content is not None and current_content == self._last_saved_content:
                    new_state = False
            except Exception:
                pass
        if new_state != getattr(self, "_dirty_flag", False):
            self._dirty_flag = new_state
            self._update_dirty_indicator()
            if not new_state and self._is_homebase_mode_enabled():
                status = self._homebase_sync_engine.get_status() if self._homebase_sync_engine else None
                self._update_homebase_status_badge(status)

    def _apply_vi_preferences(self) -> None:
        self._vi_enabled = config.load_vi_mode_enabled()
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
        if not self._vi_enabled:
            self._vi_enable_pending = False
            self.editor.set_vi_mode_enabled(False)
        elif self._vi_initial_page_loaded:
            self.editor.set_vi_mode_enabled(True)
            self._vi_enable_pending = False
        else:
            self._vi_enable_pending = True
            self.editor.set_vi_mode_enabled(False)
        self._update_vi_badge_visibility()

    def _mark_initial_page_loaded(self) -> None:
        if self._vi_initial_page_loaded:
            return
        self._vi_initial_page_loaded = True
        if self._vi_enable_pending and self._vi_enabled:
            QTimer.singleShot(0, self._activate_deferred_vi_mode)

    def _activate_deferred_vi_mode(self) -> None:
        if not (self._vi_enable_pending and self._vi_enabled):
            self._vi_enable_pending = False
            return
        self.editor.set_vi_mode_enabled(True)
        self._vi_enable_pending = False

    def _on_vi_insert_state_changed(self, insert_active: bool) -> None:
        self._vi_insert_active = insert_active
        self._update_vi_badge_style(insert_active)

    def _update_vi_badge_visibility(self) -> None:
        if not hasattr(self, "_vi_status_label"):
            return
        if self._vi_enabled:
            self._vi_status_label.show()
            self._update_vi_badge_style(self._vi_insert_active)
        else:
            self._vi_status_label.hide()

    def _update_vi_badge_style(self, insert_active: bool) -> None:
        if not hasattr(self, "_vi_status_label"):
            return
        if not self._vi_enabled:
            self._vi_status_label.hide()
            return
        style = self._vi_badge_base_style
        if insert_active:
            style += (
                " background-color: "
                f"{theme_value('main_window.vi_badge.active_bg', '#ffd54d')}; "
                "color: "
                f"{theme_value('main_window.vi_badge.active_text', '#000000')};"
            )
        else:
            style += " background-color: transparent;"
        self._vi_status_label.setStyleSheet(style)

    def _exit_vi_insert_on_activate(self) -> None:
        if not (self._vi_enabled and self._vi_insert_active):
            return
        try:
            self.editor._enter_vi_navigation_mode()  # type: ignore[attr-defined]
        except Exception:
            try:
                self.editor._handle_vi_escape()  # type: ignore[attr-defined]
            except Exception:
                pass

    # (Removed move/resize overlays; not used)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        """Handle window resize: reposition TOC and save geometry."""
        super().resizeEvent(event)
        self._position_toc_widget()
        self.geometry_save_timer.start()

    def moveEvent(self, event) -> None:  # type: ignore[override]
        """Persist window position changes (paired with resize)."""
        super().moveEvent(event)
        self.geometry_save_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        # Stop any pending timers
        self.autosave_timer.stop()
        self._search_sync.stop()
        self.geometry_save_timer.stop()
        self._shutdown_homebase_sync()
        
        # Disconnect signals to prevent callbacks after window deletion
        try:
            self.editor.focusLost.disconnect()
        except:
            pass
        try:
            app = QApplication.instance()
            if app:
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", RuntimeWarning)
                    app.focusChanged.disconnect(self._on_focus_changed)
        except:
            pass
        
        # Save current file and geometry
        self._save_current_file(auto=True, reason="window close")
        self._save_geometry()
        self._persist_recent_history()
        try:
            if self._mode_window:
                self._mode_window.close()
        except Exception:
            pass
        
        # Close HTTP client and clean up
        self.http.close()
        config.set_active_vault(None)
        self._release_vault_lock()
        self._release_tray_lock()
        self._transfer_tray_icon_if_owner()
        self._clear_quick_capture_hook_if_owner()
        
        # Unregister this process from cross-process tray menu
        self._unregister_process_window()
        
        return super().closeEvent(event)

    def _register_quick_capture_hook(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if getattr(app, "_stillpoint_quick_capture_hook_set", False):
            return
        try:
            from sp.server import api as api_module
        except Exception:
            return

        def _show_capture() -> bool:
            owner = getattr(app, "_stillpoint_tray_owner", None)
            if owner is None:
                windows = list(getattr(app, "_stillpoint_windows", []))
                owner = windows[0] if windows else None
            if not owner:
                return False
            print("[QuickCapture] UI hook invoked; showing overlay in running app.")
            def _focus_and_show() -> None:
                try:
                    if owner.isMinimized():
                        owner.showNormal()
                    owner.show()
                    owner.raise_()
                    owner.activateWindow()
                except Exception:
                    pass
                owner._show_quick_capture_overlay()
            QTimer.singleShot(0, owner, _focus_and_show)
            return True

        api_module.set_ui_quick_capture_hook(_show_capture)
        app._stillpoint_quick_capture_hook_set = True

    def _clear_quick_capture_hook_if_owner(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        windows = [w for w in list(getattr(app, "_stillpoint_windows", [])) if w is not self]
        if windows:
            return
        try:
            from sp.server import api as api_module
            api_module.set_ui_quick_capture_hook(None)
        except Exception:
            pass
        app._stillpoint_quick_capture_hook_set = False

    def _acquire_tray_lock(self) -> bool:
        if getattr(self, "_tray_lock_handle", None):
            return True
        lock_path = Path.home() / ".stillpoint" / "tray.lock"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(lock_path, "a+", encoding="utf-8")
        except Exception:
            return False
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except Exception:
            try:
                handle.close()
            except Exception:
                pass
            return False
        self._tray_lock_handle = handle
        return True

    def _release_tray_lock(self) -> None:
        handle = getattr(self, "_tray_lock_handle", None)
        if not handle:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            handle.close()
        except Exception:
            pass
        self._tray_lock_handle = None

    def _transfer_tray_icon_if_owner(self) -> None:
        app = QApplication.instance()
        if app is None:
            return
        if getattr(app, "_stillpoint_tray_owner", None) is not self:
            return
        try:
            windows = list(getattr(app, "_stillpoint_windows", []))
        except Exception:
            windows = []
        windows = [w for w in windows if w is not self]
        if not windows:
            app._stillpoint_tray_owner = None
            return
        app._stillpoint_tray_owner = None
        for window in windows:
            try:
                if window.isVisible():
                    window._setup_tray_icon()
                    if getattr(app, "_stillpoint_tray_owner", None) is window:
                        break
            except Exception:
                continue

    def _describe_index(self, index: QModelIndex) -> str:
        if not index.isValid():
            return "<invalid>"
        return (
            f"path={index.data(PATH_ROLE)}, open={index.data(OPEN_ROLE)}, is_dir={bool(index.data(TYPE_ROLE))}"
        )

    def _debug(self, message: str) -> None:
        if (
            log_enabled("navigation")
            or log_enabled("ui_state")
            or log_enabled("vault_io")
            or log_enabled("editor_markdown")
        ):
            print(f"[StillPoint] {message}")

    def _log_write(self, reason: str, path: str, content: str | None, auto: bool | None = None) -> None:
        label = reason or "save"
        try:
            size = len(content.encode("utf-8")) if content is not None else 0
        except Exception:
            size = len(content or "")
        if auto is None:
            mode = "n/a"
        else:
            mode = "auto" if auto else "manual"
        try:
            rel = Path((path or "").lstrip("/"))
            if len(rel.parts) == 1 and rel.suffix.lower() in PAGE_SUFFIXES:
                trace = "".join(traceback.format_stack(limit=12))
                self._debug(f"Invalid root write requested path={path} reason={label}\n{trace}")
        except Exception:
            pass
        self._debug(f"Write request reason={label} path={path} bytes={size} mode={mode}")
    def _history_leaf_label(self, path: str) -> str:
        journal_label = self._format_journal_history_label(path)
        if journal_label:
            return journal_label
        display = path_to_colon(path) or path
        if ":" in display:
            parts = [segment for segment in display.split(":") if segment]
            if parts:
                tail = parts[-1]
                return tail
        normalized = path.lstrip("/")
        leaf = Path(normalized).stem or normalized
        return leaf

    def _format_journal_history_label(self, path: str) -> Optional[str]:
        try:
            normalized = path.strip().lstrip("/")
            normalized = normalized.replace(":", "/")
            match = re.search(
                r"(?i)(?:^|/)journal/(\d{4})/(\d{2})/(\d{2})(?:/\3(?:\.[^/]+)?)?(?:\.[^/]+)?$",
                normalized,
            )
            if not match:
                return None
            year, month, day_file = match.group(1), match.group(2), match.group(3)
            day_stem = Path(day_file).stem
            if not (year.isdigit() and month.isdigit() and day_stem.isdigit()):
                return None
            y = int(year)
            m = int(month)
            d = int(day_stem)
            from datetime import date
            dt = date(y, m, d)
            dow = dt.strftime("%a")
            mon = dt.strftime("%b")
            return f"{dow} ({d:02d}) - {mon} {y}"
        except Exception:
            return None

    def _recent_history_display_label(self, path: str) -> str:
        base_label = self._history_leaf_label(path)
        suffix = self._first_line_title_suffix(path, base_label)
        if suffix:
            return f"{base_label} — {suffix}"
        return base_label

    def _first_line_title_suffix(self, path: str, base_label: str) -> Optional[str]:
        if not self.vault_root or self._remote_mode:
            return None
        try:
            target = (Path(self.vault_root) / path.lstrip("/")).resolve()
            if not target.exists() or not target.is_file():
                return None
            with target.open("r", encoding="utf-8", errors="ignore") as handle:
                for _ in range(20):
                    line = handle.readline()
                    if not line:
                        break
                    cleaned = line.strip()
                    if not cleaned:
                        continue
                    if cleaned.startswith("#"):
                        cleaned = cleaned.lstrip("#").strip()
                    if not cleaned:
                        continue
                    file_stem = target.stem
                    if cleaned == file_stem or cleaned == base_label:
                        return None
                    return cleaned
        except Exception:
            return None
        return None

    @staticmethod
    def _elide_history_label(text: str, max_chars: int) -> str:
        if max_chars <= 0 or len(text) <= max_chars:
            return text
        if max_chars <= 1:
            return "…"
        return text[: max_chars - 1] + "…"
