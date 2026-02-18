from __future__ import annotations

import hashlib
from typing import Optional

import httpx

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from sp.app import config


class VaultPreferencesDialog(QDialog):
    """Dialog for per-vault preference overrides."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Vault Preferences")
        self.setModal(True)
        self.resize(420, 360)

        layout = QVBoxLayout(self)
        note = QLabel(
            "These settings override the global application preferences for this vault.\n"
            "Feature/AI overrides: Checked = Enabled, Unchecked = Disabled, Dash = Use Global.\n"
            "Read-only is a direct per-vault setting."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        layout.addWidget(note)

        layout.addWidget(QLabel("<b>Features</b>"))
        self.feature_tasks_checkbox = self._make_override_checkbox(
            "Tasks",
            config.load_vault_feature_tasks_override(),
        )
        layout.addWidget(self.feature_tasks_checkbox)
        self.feature_calendar_checkbox = self._make_override_checkbox(
            "Calendar",
            config.load_vault_feature_calendar_override(),
        )
        layout.addWidget(self.feature_calendar_checkbox)
        self.feature_link_navigator_checkbox = self._make_override_checkbox(
            "Link Navigator",
            config.load_vault_feature_link_navigator_override(),
        )
        layout.addWidget(self.feature_link_navigator_checkbox)
        self.feature_tags_checkbox = self._make_override_checkbox(
            "Page Tags",
            config.load_vault_feature_tags_override(),
        )
        layout.addWidget(self.feature_tags_checkbox)
        self.feature_remote_vaults_checkbox = self._make_override_checkbox(
            "Remote Vaults",
            config.load_vault_feature_remote_vaults_override(),
        )
        layout.addWidget(self.feature_remote_vaults_checkbox)

        layout.addWidget(QLabel("<b>AI</b>"))
        self.ai_chats_checkbox = self._make_override_checkbox(
            "AI Chats",
            config.load_vault_enable_ai_chats_override(),
        )
        layout.addWidget(self.ai_chats_checkbox)
        layout.addWidget(QLabel("<b>Access</b>"))
        self.force_read_only_checkbox = QCheckBox("Force read-only mode for this vault")
        self.force_read_only_checkbox.setToolTip(
            "Open this vault without taking a lock or allowing writes in this window."
        )
        try:
            self.force_read_only_checkbox.setChecked(config.load_vault_force_read_only())
        except Exception:
            self.force_read_only_checkbox.setChecked(False)
        layout.addWidget(self.force_read_only_checkbox)

        layout.addWidget(QLabel("<b>Remote Mode</b>"))
        self.remote_mode_combo = QComboBox()
        self.remote_mode_combo.addItem("None", "none")
        self.remote_mode_combo.addItem("Plain Remote (legacy)", "plain_remote")
        self.remote_mode_combo.addItem("Homebase Remote", "homebase_remote")
        mode = config.load_vault_remote_mode()
        idx = self.remote_mode_combo.findData(mode)
        self.remote_mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        layout.addWidget(self.remote_mode_combo)

        self.homebase_form = QFormLayout()
        self.homebase_url_edit = QLineEdit(config.load_homebase_remote_url())
        self.homebase_url_edit.setPlaceholderText("https://server.example.com")
        self.homebase_vault_id_edit = QLineEdit(config.load_homebase_vault_id() or "")
        self.homebase_vault_id_edit.setPlaceholderText("Remote Homebase vault ID")
        self.homebase_bootstrap_mode_combo = QComboBox()
        self.homebase_bootstrap_mode_combo.addItem("Connect Existing", "connect")
        self.homebase_bootstrap_mode_combo.addItem("Create New", "create")
        self.homebase_vault_name_edit = QLineEdit()
        self.homebase_vault_name_edit.setPlaceholderText("Optional vault display name (create mode)")
        self.homebase_username_edit = QLineEdit(config.load_homebase_username())
        self.homebase_username_edit.setPlaceholderText("Username")
        self.homebase_password_edit = QLineEdit()
        self.homebase_password_edit.setEchoMode(QLineEdit.Password)
        self.homebase_password_edit.setPlaceholderText("Password")
        self.homebase_server_admin_edit = QLineEdit()
        self.homebase_server_admin_edit.setEchoMode(QLineEdit.Password)
        self.homebase_server_admin_edit.setPlaceholderText("Server admin password (required for create)")
        self.homebase_token_edit = QLineEdit(config.load_homebase_auth_token())
        self.homebase_token_edit.setPlaceholderText("Bearer token")
        self._homebase_refresh_token = config.load_homebase_refresh_token()
        self.homebase_passphrase_edit = QLineEdit(config.load_homebase_passphrase())
        self.homebase_passphrase_edit.setEchoMode(QLineEdit.Password)
        self.homebase_passphrase_edit.setPlaceholderText("Shared passphrase")
        self.homebase_auto_sync_checkbox = QCheckBox("Enable auto-sync")
        self.homebase_auto_sync_checkbox.setChecked(config.load_homebase_auto_sync())
        self.homebase_interval_spin = QSpinBox()
        self.homebase_interval_spin.setRange(5, 3600)
        self.homebase_interval_spin.setValue(config.load_homebase_interval_seconds())
        self.homebase_debounce_spin = QSpinBox()
        self.homebase_debounce_spin.setRange(1, 120)
        self.homebase_debounce_spin.setValue(config.load_homebase_push_debounce_seconds())
        self.homebase_parallel_spin = QSpinBox()
        self.homebase_parallel_spin.setRange(1, 32)
        self.homebase_parallel_spin.setValue(config.load_homebase_max_parallel_transfers())
        self.homebase_bootstrap_button = QPushButton("Connect / Create & Get Token")
        self.homebase_bootstrap_button.clicked.connect(self._bootstrap_homebase_auth)
        self.homebase_bootstrap_mode_combo.currentIndexChanged.connect(self._update_homebase_bootstrap_mode)
        self.homebase_form.addRow("Homebase URL", self.homebase_url_edit)
        self.homebase_form.addRow("Mode", self.homebase_bootstrap_mode_combo)
        self.homebase_form.addRow("Vault ID", self.homebase_vault_id_edit)
        self.homebase_form.addRow("Vault Name", self.homebase_vault_name_edit)
        self.homebase_form.addRow("Username", self.homebase_username_edit)
        self.homebase_form.addRow("Password", self.homebase_password_edit)
        self.homebase_form.addRow("Server Admin Password", self.homebase_server_admin_edit)
        self.homebase_form.addRow("", self.homebase_bootstrap_button)
        self.homebase_form.addRow("Auth Token", self.homebase_token_edit)
        self.homebase_form.addRow("Passphrase", self.homebase_passphrase_edit)
        self.homebase_form.addRow("", self.homebase_auto_sync_checkbox)
        self.homebase_form.addRow("Interval Seconds", self.homebase_interval_spin)
        self.homebase_form.addRow("Push Debounce Seconds", self.homebase_debounce_spin)
        self.homebase_form.addRow("Max Parallel Transfers", self.homebase_parallel_spin)
        layout.addLayout(self.homebase_form)
        self.remote_mode_combo.currentIndexChanged.connect(self._update_homebase_form_visibility)
        self._update_homebase_form_visibility()
        self._update_homebase_bootstrap_mode()

        layout.addStretch(1)

        self._initial_values = self._collect_values()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        reset_btn = buttons.addButton("Use Global Defaults", QDialogButtonBox.ResetRole)
        reset_btn.clicked.connect(self._reset_to_global)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    @staticmethod
    def _make_override_checkbox(label: str, value: Optional[bool]) -> QCheckBox:
        checkbox = QCheckBox(label)
        checkbox.setTristate(True)
        if value is None:
            checkbox.setCheckState(Qt.PartiallyChecked)
        else:
            checkbox.setCheckState(Qt.Checked if value else Qt.Unchecked)
        return checkbox

    @staticmethod
    def _checkbox_value(checkbox: QCheckBox) -> Optional[bool]:
        state = checkbox.checkState()
        if state == Qt.PartiallyChecked:
            return None
        return state == Qt.Checked

    def _collect_values(self) -> dict[str, Optional[bool]]:
        return {
            "tasks": self._checkbox_value(self.feature_tasks_checkbox),
            "calendar": self._checkbox_value(self.feature_calendar_checkbox),
            "link_navigator": self._checkbox_value(self.feature_link_navigator_checkbox),
            "tags": self._checkbox_value(self.feature_tags_checkbox),
            "remote_vaults": self._checkbox_value(self.feature_remote_vaults_checkbox),
            "ai_chats": self._checkbox_value(self.ai_chats_checkbox),
        }

    def _reset_to_global(self) -> None:
        for checkbox in (
            self.feature_tasks_checkbox,
            self.feature_calendar_checkbox,
            self.feature_link_navigator_checkbox,
            self.feature_tags_checkbox,
            self.feature_remote_vaults_checkbox,
            self.ai_chats_checkbox,
        ):
            checkbox.setCheckState(Qt.PartiallyChecked)

    def _update_homebase_form_visibility(self) -> None:
        is_homebase = self.remote_mode_combo.currentData() == "homebase_remote"
        self.homebase_url_edit.setEnabled(is_homebase)
        self.homebase_vault_id_edit.setEnabled(is_homebase)
        self.homebase_bootstrap_mode_combo.setEnabled(is_homebase)
        self.homebase_vault_name_edit.setEnabled(is_homebase)
        self.homebase_username_edit.setEnabled(is_homebase)
        self.homebase_password_edit.setEnabled(is_homebase)
        self.homebase_server_admin_edit.setEnabled(is_homebase)
        self.homebase_bootstrap_button.setEnabled(is_homebase)
        self.homebase_token_edit.setEnabled(is_homebase)
        self.homebase_passphrase_edit.setEnabled(is_homebase)
        self.homebase_auto_sync_checkbox.setEnabled(is_homebase)
        self.homebase_interval_spin.setEnabled(is_homebase)
        self.homebase_debounce_spin.setEnabled(is_homebase)
        self.homebase_parallel_spin.setEnabled(is_homebase)

    def _update_homebase_bootstrap_mode(self) -> None:
        create_mode = self.homebase_bootstrap_mode_combo.currentData() == "create"
        self.homebase_vault_name_edit.setEnabled(create_mode)
        self.homebase_server_admin_edit.setEnabled(create_mode)

    def _bootstrap_homebase_auth(self) -> None:
        base_url = self.homebase_url_edit.text().strip().rstrip("/")
        if not base_url:
            QMessageBox.warning(self, "Missing URL", "Enter a Homebase server URL first.")
            return
        username = self.homebase_username_edit.text().strip()
        password = self.homebase_password_edit.text()
        if not username or not password:
            QMessageBox.warning(self, "Missing Credentials", "Enter Homebase username and password.")
            return
        mode = self.homebase_bootstrap_mode_combo.currentData()
        headers: dict[str, str] = {}
        payload: dict[str, str] = {"username": username, "password": password}
        if mode == "create":
            admin_password = self.homebase_server_admin_edit.text().strip()
            if not admin_password:
                QMessageBox.warning(
                    self,
                    "Missing Admin Password",
                    "Creating a remote Homebase vault requires the server admin password.",
                )
                return
            headers["x-server-admin-password"] = hashlib.sha256(admin_password.encode("utf-8")).hexdigest()
            vault_name = self.homebase_vault_name_edit.text().strip()
            if vault_name:
                payload["vault_name"] = vault_name
            url = f"{base_url}/v1/homebase/bootstrap/create"
        else:
            vault_id = self.homebase_vault_id_edit.text().strip()
            if not vault_id:
                QMessageBox.warning(self, "Missing Vault ID", "Enter the remote Homebase vault ID to connect.")
                return
            payload["vault_id"] = vault_id
            url = f"{base_url}/v1/homebase/bootstrap/connect"
        try:
            resp = httpx.post(url, json=payload, headers=headers, timeout=20.0)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            msg = str(exc)
            try:
                if "resp" in locals():
                    msg = resp.text[:400] or msg
            except Exception:
                pass
            QMessageBox.critical(self, "Homebase Setup Failed", msg)
            return
        vault_id = str(data.get("vault_id") or "").strip()
        access_token = str(data.get("access_token") or "").strip()
        refresh_token = str(data.get("refresh_token") or "").strip()
        if not vault_id or not access_token:
            QMessageBox.critical(self, "Homebase Setup Failed", "Server returned an invalid token response.")
            return
        self.homebase_vault_id_edit.setText(vault_id)
        self.homebase_token_edit.setText(access_token)
        self._homebase_refresh_token = refresh_token
        QMessageBox.information(
            self,
            "Homebase Setup Complete",
            "Token acquired and applied to this vault's Homebase settings.",
        )

    def accept(self) -> None:  # type: ignore[override]
        values = self._collect_values()
        changed = values != self._initial_values
        config.save_vault_feature_tasks_override(values["tasks"])
        config.save_vault_feature_calendar_override(values["calendar"])
        config.save_vault_feature_link_navigator_override(values["link_navigator"])
        config.save_vault_feature_tags_override(values["tags"])
        config.save_vault_feature_remote_vaults_override(values["remote_vaults"])
        config.save_vault_enable_ai_chats_override(values["ai_chats"])
        config.save_vault_force_read_only(self.force_read_only_checkbox.isChecked())
        remote_mode = str(self.remote_mode_combo.currentData() or "none")
        config.save_vault_remote_mode(remote_mode)
        config.save_homebase_remote_url(self.homebase_url_edit.text().strip())
        config.save_homebase_vault_id(self.homebase_vault_id_edit.text().strip())
        config.save_homebase_username(self.homebase_username_edit.text().strip())
        config.save_homebase_auth_token(self.homebase_token_edit.text().strip())
        config.save_homebase_refresh_token(self._homebase_refresh_token)
        config.save_homebase_passphrase(self.homebase_passphrase_edit.text())
        config.save_homebase_auto_sync(self.homebase_auto_sync_checkbox.isChecked())
        config.save_homebase_interval_seconds(self.homebase_interval_spin.value())
        config.save_homebase_push_debounce_seconds(self.homebase_debounce_spin.value())
        config.save_homebase_max_parallel_transfers(self.homebase_parallel_spin.value())
        if remote_mode == "homebase_remote":
            config.ensure_homebase_vault_id()
            config.load_homebase_device_id()
        super().accept()
