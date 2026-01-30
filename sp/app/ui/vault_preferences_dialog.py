from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
    QMessageBox,
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
            "Checked = Enabled, Unchecked = Disabled, Dash = Use Global."
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

    def accept(self) -> None:  # type: ignore[override]
        values = self._collect_values()
        changed = values != self._initial_values
        config.save_vault_feature_tasks_override(values["tasks"])
        config.save_vault_feature_calendar_override(values["calendar"])
        config.save_vault_feature_link_navigator_override(values["link_navigator"])
        config.save_vault_feature_tags_override(values["tags"])
        config.save_vault_feature_remote_vaults_override(values["remote_vaults"])
        config.save_vault_enable_ai_chats_override(values["ai_chats"])
        if changed:
            QMessageBox.information(
                self,
                "Reopen Vault Required",
                "Reopen this vault for changes to take effect.",
            )
        super().accept()
