from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from sp.app import config


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

    def accept(self) -> None:  # type: ignore[override]
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
        super().accept()
