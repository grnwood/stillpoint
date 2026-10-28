from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from sp.app import git_support


class VaultPreferencesDialog(QDialog):
    """Dialog for per-vault preference overrides."""

    ACCENT_CHOICES: list[tuple[str, str]] = [
        ("Use Theme Default", ""),
        ("Ocean Blue", "#3B82F6"),
        ("Cyan", "#22D3EE"),
        ("Violet", "#A78BFA"),
        ("Magenta", "#EC4899"),
        ("Coral", "#FB7185"),
        ("Sunset Orange", "#F97316"),
        ("Amber", "#F59E0B"),
        ("Lime", "#84CC16"),
        ("Emerald", "#10B981"),
        ("Slate", "#64748B"),
    ]

    def __init__(self, parent=None, *, remote_mode: bool = False, remote_read_only: bool = False) -> None:
        super().__init__(parent)
        self.setWindowTitle("Vault Preferences")
        self.setModal(True)
        self.resize(720, 680)
        self.setMinimumSize(620, 560)
        self._remote_mode = bool(remote_mode)
        self._remote_read_only = bool(remote_read_only)
        self._vault_root = Path(config.get_active_vault()).resolve() if config.get_active_vault() else None
        self._initial_version_mode = config.load_vault_remote_mode()

        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        content = QWidget(scroll)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(6)
        scroll.setWidget(content)
        layout.addWidget(scroll, 1)

        note = QLabel(
            "These settings override the global application preferences for this vault.\n"
            "Feature/AI overrides: Checked = Enabled, Unchecked = Disabled, Dash = Use Global.\n"
            "Read-only is a direct per-vault setting."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #666;")
        content_layout.addWidget(note)

        content_layout.addWidget(QLabel("<b>Theme</b>"))
        theme_row = QHBoxLayout()
        theme_row.addWidget(QLabel("Theme Override:"))
        self.vault_theme_combo = QComboBox()
        self._populate_theme_options()
        theme_row.addWidget(self.vault_theme_combo, 1)
        content_layout.addLayout(theme_row)

        content_layout.addWidget(QLabel("<b>Vault Accent</b>"))
        accent_row = QHBoxLayout()
        accent_row.addWidget(QLabel("Accent Color:"))
        self.vault_accent_combo = QComboBox()
        for label, color in self.ACCENT_CHOICES:
            self.vault_accent_combo.addItem(label, color)
        current_accent = config.load_vault_accent_color() or ""
        accent_idx = self.vault_accent_combo.findData(current_accent)
        self.vault_accent_combo.setCurrentIndex(accent_idx if accent_idx != -1 else 0)
        accent_row.addWidget(self.vault_accent_combo, 1)
        content_layout.addLayout(accent_row)

        content_layout.addWidget(QLabel("<b>Features</b>"))
        self.feature_tasks_checkbox = self._make_override_checkbox(
            "Tasks",
            config.load_vault_feature_tasks_override(),
        )
        content_layout.addWidget(self.feature_tasks_checkbox)
        self.feature_calendar_checkbox = self._make_override_checkbox(
            "Calendar",
            config.load_vault_feature_calendar_override(),
        )
        content_layout.addWidget(self.feature_calendar_checkbox)
        self.feature_link_navigator_checkbox = self._make_override_checkbox(
            "Link Navigator",
            config.load_vault_feature_link_navigator_override(),
        )
        content_layout.addWidget(self.feature_link_navigator_checkbox)
        self.feature_tags_checkbox = self._make_override_checkbox(
            "Page Tags",
            config.load_vault_feature_tags_override(),
        )
        content_layout.addWidget(self.feature_tags_checkbox)
        self.feature_remember_cursor_position_checkbox = self._make_override_checkbox(
            "Remember and restore last cursor position",
            config.load_vault_feature_remember_cursor_position_override(),
        )
        content_layout.addWidget(self.feature_remember_cursor_position_checkbox)

        content_layout.addWidget(QLabel("<b>AI</b>"))
        self.ai_chats_checkbox = self._make_override_checkbox(
            "AI Chats",
            config.load_vault_enable_ai_chats_override(),
        )
        content_layout.addWidget(self.ai_chats_checkbox)

        content_layout.addWidget(QLabel("<b>Access</b>"))
        self.force_read_only_checkbox = QCheckBox("Force read-only mode for this vault")
        self.force_read_only_checkbox.setToolTip(
            "Open this vault without taking a lock or allowing writes in this window."
        )
        try:
            self.force_read_only_checkbox.setChecked(config.load_vault_force_read_only())
        except Exception:
            self.force_read_only_checkbox.setChecked(False)
        content_layout.addWidget(self.force_read_only_checkbox)

        content_layout.addWidget(QLabel("<b>Versioning &amp; Sync</b>"))
        version_box = QGroupBox("Choose one mode for this vault")
        version_layout = QVBoxLayout(version_box)
        self.version_mode_group = QButtonGroup(self)
        self.local_mode_radio = QRadioButton("Local only")
        self.homebase_mode_radio = QRadioButton("Homebase")
        self.git_mode_radio = QRadioButton("Git")
        for button in (self.local_mode_radio, self.homebase_mode_radio, self.git_mode_radio):
            self.version_mode_group.addButton(button)
            version_layout.addWidget(button)
        if self._initial_version_mode == "homebase_remote":
            self.homebase_mode_radio.setChecked(True)
        elif self._initial_version_mode == "git":
            self.git_mode_radio.setChecked(True)
        else:
            self.local_mode_radio.setChecked(True)
        if self._remote_mode:
            version_box.setEnabled(False)
        elif self._initial_version_mode != "homebase_remote":
            self.homebase_mode_radio.setEnabled(False)
            self.homebase_mode_radio.setToolTip(
                "Use Vault → Convert This Vault to Homebase to configure Homebase."
            )

        self.version_mode_warning = QLabel()
        self.version_mode_warning.setWordWrap(True)
        self.version_mode_warning.setStyleSheet("color: #b26a00; padding: 4px 0;")
        version_layout.addWidget(self.version_mode_warning)

        self.git_setup_widget = QWidget(version_box)
        git_setup_layout = QVBoxLayout(self.git_setup_widget)
        git_setup_layout.setContentsMargins(20, 4, 0, 0)
        self.git_status_label = QLabel()
        self.git_status_label.setWordWrap(True)
        git_setup_layout.addWidget(self.git_status_label)
        git_warning = QLabel(
            "Git remotes normally receive your vault files as plaintext. StillPoint does not "
            "store Git passwords or access tokens; authentication uses your existing Git, SSH, "
            "or credential-manager setup."
        )
        git_warning.setWordWrap(True)
        git_warning.setStyleSheet("color: #b26a00;")
        git_setup_layout.addWidget(git_warning)
        self.git_init_button = QPushButton("Initialize Repository")
        self.git_init_button.clicked.connect(self._initialize_git_repository)
        git_setup_layout.addWidget(self.git_init_button)
        remote_form = QFormLayout()
        self.git_remote_edit = QLineEdit()
        self.git_remote_edit.setPlaceholderText("git@host:owner/vault.git or https://…")
        self._git_remote_name = "origin"
        self.git_remote_label = QLabel("Remote (origin):")
        remote_form.addRow(self.git_remote_label, self.git_remote_edit)
        git_setup_layout.addLayout(remote_form)
        remote_buttons = QHBoxLayout()
        self.git_save_remote_button = QPushButton("Save Remote")
        self.git_save_remote_button.clicked.connect(self._save_git_remote)
        self.git_test_remote_button = QPushButton("Test Connection")
        self.git_test_remote_button.clicked.connect(self._test_git_remote)
        remote_buttons.addWidget(self.git_save_remote_button)
        remote_buttons.addWidget(self.git_test_remote_button)
        remote_buttons.addStretch(1)
        git_setup_layout.addLayout(remote_buttons)
        self.git_publish_versions_checkbox = QCheckBox("Publish after Save Version")
        self.git_publish_versions_checkbox.setChecked(config.load_git_publish_versions(False))
        git_setup_layout.addWidget(self.git_publish_versions_checkbox)
        backup_box = QGroupBox("Automatic backups")
        backup_layout = QVBoxLayout(backup_box)
        backup_help = QLabel(
            "Automatic backups save ordinary Git history without creating named vault versions. "
            "They run while StillPoint is open. StillPoint never pulls or merges in the background."
        )
        backup_help.setWordWrap(True)
        backup_layout.addWidget(backup_help)
        self.git_auto_backup_checkbox = QCheckBox("Back up changes automatically")
        self.git_auto_backup_checkbox.setChecked(config.load_git_auto_backup_enabled(False))
        backup_layout.addWidget(self.git_auto_backup_checkbox)
        backup_form = QFormLayout()
        self.git_auto_backup_interval_combo = QComboBox()
        for label, minutes in (("Every 15 minutes", 15), ("Every 30 minutes", 30), ("Hourly", 60), ("Daily", 1440)):
            self.git_auto_backup_interval_combo.addItem(label, minutes)
        interval_index = self.git_auto_backup_interval_combo.findData(
            config.load_git_auto_backup_interval_minutes(15)
        )
        self.git_auto_backup_interval_combo.setCurrentIndex(max(0, interval_index))
        backup_form.addRow("Frequency:", self.git_auto_backup_interval_combo)
        backup_layout.addLayout(backup_form)
        self.git_auto_publish_checkbox = QCheckBox("Publish backups to the remote")
        self.git_auto_publish_checkbox.setChecked(config.load_git_auto_publish_enabled(True))
        backup_layout.addWidget(self.git_auto_publish_checkbox)
        self.git_backup_on_close_checkbox = QCheckBox("Back up when closing StillPoint")
        self.git_backup_on_close_checkbox.setChecked(config.load_git_backup_on_close(True))
        backup_layout.addWidget(self.git_backup_on_close_checkbox)
        self.git_auto_backup_checkbox.toggled.connect(self._update_git_backup_controls)
        git_setup_layout.addWidget(backup_box)
        version_layout.addWidget(self.git_setup_widget)
        content_layout.addWidget(version_box)

        self.git_mode_radio.toggled.connect(self._on_git_mode_toggled)
        self.homebase_mode_radio.toggled.connect(self._update_version_mode_ui)
        self.local_mode_radio.toggled.connect(self._update_version_mode_ui)
        self._refresh_git_setup_state()
        self._update_version_mode_ui()
        self._update_git_backup_controls()

        content_layout.addStretch(1)

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
            "remember_cursor_position": self._checkbox_value(self.feature_remember_cursor_position_checkbox),
            "ai_chats": self._checkbox_value(self.ai_chats_checkbox),
        }

    @staticmethod
    def _theme_dir() -> Path:
        return Path.home() / ".stillpoint" / "themes"

    def _list_theme_files(self) -> list[Path]:
        theme_dir = self._theme_dir()
        try:
            return sorted(
                [
                    path
                    for path in theme_dir.iterdir()
                    if path.is_file()
                    and path.suffix.lower() == ".json"
                    and path.name != "theme-config.json"
                ],
                key=lambda p: p.name.lower(),
            )
        except Exception:
            return []

    def _populate_theme_options(self) -> None:
        self.vault_theme_combo.clear()
        self.vault_theme_combo.addItem("Use Global Theme", None)
        for path in self._list_theme_files():
            self.vault_theme_combo.addItem(path.name, path.name)
        current = config.load_vault_theme_override()
        idx = self.vault_theme_combo.findData(current)
        if idx == -1:
            idx = 0
        self.vault_theme_combo.setCurrentIndex(idx)

    def _reset_to_global(self) -> None:
        self.vault_theme_combo.setCurrentIndex(0)
        self.vault_accent_combo.setCurrentIndex(0)
        for checkbox in (
            self.feature_tasks_checkbox,
            self.feature_calendar_checkbox,
            self.feature_link_navigator_checkbox,
            self.feature_tags_checkbox,
            self.feature_remember_cursor_position_checkbox,
            self.ai_chats_checkbox,
        ):
            checkbox.setCheckState(Qt.PartiallyChecked)

    def selected_version_mode(self) -> str:
        if self.git_mode_radio.isChecked():
            return "git"
        if self.homebase_mode_radio.isChecked():
            return "homebase_remote"
        return "none"

    def _update_version_mode_ui(self) -> None:
        selected = self.selected_version_mode()
        self.git_setup_widget.setVisible(selected == "git")
        if selected == self._initial_version_mode:
            message = ""
        elif self._initial_version_mode == "homebase_remote":
            message = "Changing modes stops Homebase synchronization. Existing Homebase data is not deleted."
            if selected == "git":
                message += " Git remotes do not receive Homebase-encrypted objects."
        elif self._initial_version_mode == "git" and selected == "homebase_remote":
            message = (
                "Changing to Homebase disables Git operations in StillPoint. The .git repository "
                "and its history remain on disk."
            )
        elif self._initial_version_mode == "git":
            message = (
                "Turning Git off hides Git operations in StillPoint. The .git repository and its "
                "history remain on disk."
            )
        elif selected == "git":
            message = "Git and Homebase cannot operate on this vault at the same time."
        else:
            message = ""
        self.version_mode_warning.setText(message)
        self._update_git_backup_controls()

    def _on_git_mode_toggled(self, checked: bool) -> None:
        if checked:
            # Probe again when the user selects Git. The repository or remote
            # may have been configured since this dialog was opened.
            self._refresh_git_setup_state()
        self._update_version_mode_ui()

    def _update_git_backup_controls(self) -> None:
        enabled = self.git_mode_radio.isChecked() and self.git_auto_backup_checkbox.isChecked()
        self.git_auto_backup_interval_combo.setEnabled(enabled)
        self.git_auto_publish_checkbox.setEnabled(enabled)
        self.git_backup_on_close_checkbox.setEnabled(enabled)

    def _refresh_git_setup_state(self) -> None:
        if not self._vault_root:
            self.git_status_label.setText("Open a local vault to configure Git.")
            self.git_init_button.setEnabled(False)
            self.git_save_remote_button.setEnabled(False)
            self.git_test_remote_button.setEnabled(False)
            return
        try:
            state = git_support.repository_state(self._vault_root)
        except git_support.GitError as exc:
            self.git_status_label.setText(f"Git unavailable: {exc}")
            self.git_init_button.setEnabled(False)
            self.git_save_remote_button.setEnabled(False)
            self.git_test_remote_button.setEnabled(False)
            return
        if not state.initialized:
            self.git_status_label.setText("Repository status: Not initialized")
            self.git_init_button.setText("Initialize Repository")
            self.git_init_button.setEnabled(True)
            self._git_remote_name = "origin"
            self.git_remote_label.setText("Remote (origin):")
            ready = False
        elif not state.exact_root:
            self.git_status_label.setText(
                "Repository status: This vault is inside a repository rooted elsewhere."
            )
            self.git_init_button.setText("Initialize Repository Here")
            self.git_init_button.setEnabled(True)
            ready = False
        else:
            branch = state.branch or "no commits yet"
            remote_detail = (
                f" · Remote: {state.remote_name} ({state.remote_url})"
                if state.remote_url
                else " · No remote configured"
            )
            self.git_status_label.setText(
                f"Repository status: Existing repository detected · Branch: {branch}{remote_detail}"
            )
            self.git_init_button.setText("Repository Ready")
            self.git_init_button.setEnabled(False)
            ready = True
            self._git_remote_name = state.remote_name or "origin"
            self.git_remote_label.setText(f"Remote ({self._git_remote_name}):")
            self.git_remote_edit.setText(state.remote_url)
        self.git_save_remote_button.setEnabled(ready)
        self.git_test_remote_button.setEnabled(ready and bool(self.git_remote_edit.text().strip()))

    def _initialize_git_repository(self) -> None:
        if not self._vault_root:
            return
        try:
            git_support.initialize_repository(self._vault_root)
        except git_support.GitError as exc:
            QMessageBox.warning(self, "Could Not Initialize Repository", str(exc))
            return
        self._refresh_git_setup_state()

    def _save_git_remote(self) -> None:
        if not self._vault_root:
            return
        try:
            git_support.set_remote(
                self._vault_root,
                self.git_remote_edit.text(),
                name=self._git_remote_name,
            )
        except git_support.GitError as exc:
            QMessageBox.warning(self, "Could Not Save Remote", str(exc))
            return
        self._refresh_git_setup_state()

    def _test_git_remote(self) -> None:
        if not self._vault_root:
            return
        try:
            if self.git_remote_edit.text().strip() != git_support.remote_url(
                self._vault_root, name=self._git_remote_name
            ):
                git_support.set_remote(
                    self._vault_root,
                    self.git_remote_edit.text(),
                    name=self._git_remote_name,
                )
            git_support.test_remote(self._vault_root, name=self._git_remote_name)
        except git_support.GitError as exc:
            QMessageBox.warning(self, "Git Connection Failed", str(exc))
            return
        QMessageBox.information(self, "Git Connection", "The remote repository is reachable.")

    def accept(self) -> None:  # type: ignore[override]
        selected_mode = self.selected_version_mode()
        if selected_mode == "git":
            if not self._vault_root:
                QMessageBox.warning(self, "Git Setup Required", "Open a local vault first.")
                return
            try:
                git_support.require_exact_repository(self._vault_root)
                git_support.ensure_metadata_ignored(self._vault_root)
            except git_support.GitError as exc:
                QMessageBox.warning(self, "Git Setup Required", str(exc))
                return
        if selected_mode != self._initial_version_mode:
            if selected_mode == "git":
                detail = (
                    "Enable Git for this vault? Homebase synchronization will be disabled. "
                    "Vault files sent to a Git remote are not protected by Homebase encryption."
                )
            elif self._initial_version_mode == "homebase_remote":
                detail = (
                    "Stop Homebase synchronization for this vault? Existing Homebase data will not be deleted."
                )
            elif self._initial_version_mode == "git":
                detail = (
                    "Disable Git operations in StillPoint? The repository and history will remain on disk."
                )
            else:
                detail = "Change this vault's versioning and sync mode?"
            if QMessageBox.warning(
                self,
                "Change Versioning Mode",
                detail,
                QMessageBox.Yes | QMessageBox.Cancel,
                QMessageBox.Cancel,
            ) != QMessageBox.Yes:
                return
        values = self._collect_values()
        config.save_vault_theme_override(self.vault_theme_combo.currentData())
        config.save_vault_accent_color(self.vault_accent_combo.currentData() or None)
        config.save_vault_feature_tasks_override(values["tasks"])
        config.save_vault_feature_calendar_override(values["calendar"])
        config.save_vault_feature_link_navigator_override(values["link_navigator"])
        config.save_vault_feature_tags_override(values["tags"])
        config.save_vault_feature_remember_cursor_position_override(values["remember_cursor_position"])
        config.save_vault_enable_ai_chats_override(values["ai_chats"])
        config.save_vault_force_read_only(self.force_read_only_checkbox.isChecked())
        config.save_vault_remote_mode(selected_mode)
        config.save_git_publish_versions(self.git_publish_versions_checkbox.isChecked())
        config.save_git_auto_backup_enabled(self.git_auto_backup_checkbox.isChecked())
        config.save_git_auto_backup_interval_minutes(
            int(self.git_auto_backup_interval_combo.currentData() or 15)
        )
        config.save_git_auto_publish_enabled(self.git_auto_publish_checkbox.isChecked())
        config.save_git_backup_on_close(self.git_backup_on_close_checkbox.isChecked())
        super().accept()
