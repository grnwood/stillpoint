from __future__ import annotations

import os
import json
import re
import sys
import shutil

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QCheckBox,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QMessageBox,
    QComboBox,
    QLineEdit,
    QSpinBox,
    QDoubleSpinBox,
    QListWidget,
    QListWidgetItem,
    QStackedWidget,
    QWidget,
    QFileDialog,
    QKeySequenceEdit,
    QFrame,
    QRadioButton,
    QTableWidget,
    QTableWidgetItem,
    QFrame,
)
from pathlib import Path
from PySide6.QtGui import QFontDatabase, QFont, QDesktopServices
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QUrl

from sp.app import config
from sp.logging_flags import log_enabled
from . import theme as theme_module


class PreferencesDialog(QDialog):
    rebuildIndexRequested = Signal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.resize(450, 250)
        app_instance = QApplication.instance()
        self._initial_app_font = QFont(app_instance.font()) if app_instance else QFont()
        self._font_families = sorted(QFontDatabase().families())

        root_layout = QHBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(12)

        self.section_list = QListWidget()
        self.section_list.setFixedWidth(180)
        self.section_list.setSpacing(2)
        root_layout.addWidget(self.section_list, 0)

        self.stack = QStackedWidget()
        right_container = QVBoxLayout()
        right_container.addWidget(self.stack, 1)

        btn_box = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btn_box.accepted.connect(self.accept)
        btn_box.rejected.connect(self.reject)
        right_container.addWidget(btn_box, 0, Qt.AlignRight)

        wrapper = QWidget()
        wrapper.setLayout(right_container)
        root_layout.addWidget(wrapper, 1)

        self._build_sections()
        if self.section_list.count():
            self.section_list.setCurrentRow(0)
        self.section_list.currentRowChanged.connect(self.stack.setCurrentIndex)

    def _build_sections(self) -> None:
        """Create a two-panel layout with section list on the left and pages on the right."""
        focus_settings = config.load_focus_mode_settings()
        audience_settings = config.load_audience_mode_settings()
        template_names = self._template_names()

        def add_section(title: str) -> QVBoxLayout:
            item = QListWidgetItem(title)
            self.section_list.addItem(item)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(8)
            self.stack.addWidget(page)
            return layout

        def add_divider(layout: QVBoxLayout) -> None:
            line = QFrame()
            line.setFrameShape(QFrame.HLine)
            line.setFrameShadow(QFrame.Sunken)
            line.setStyleSheet("color: #666;")
            layout.addSpacing(6)
            layout.addWidget(line)
            layout.addSpacing(6)

        # General
        general_layout = add_section("General")
        general_layout.addWidget(QLabel("<b>Markdown Editor</b>"))
        self.toc_widget_checkbox = QCheckBox("Enable TOC Widget")
        self.toc_widget_checkbox.setChecked(config.load_toc_widget_enabled())
        self.toc_widget_checkbox.setToolTip("Show floating transparent Heading navigator in editor")
        general_layout.addWidget(self.toc_widget_checkbox)
        row_image_max_width = QHBoxLayout()
        row_image_max_width.addWidget(QLabel("Attachment image max width:"))
        self.markdown_image_max_width_combo = QComboBox()
        for width in (300, 600, 900, 1200, 1500):
            self.markdown_image_max_width_combo.addItem(f"{width}px", width)
        current_image_max_width = config.load_markdown_image_max_width()
        idx = self.markdown_image_max_width_combo.findData(current_image_max_width)
        self.markdown_image_max_width_combo.setCurrentIndex(idx if idx >= 0 else 2)
        row_image_max_width.addWidget(self.markdown_image_max_width_combo, 1)
        general_layout.addLayout(row_image_max_width)
        general_layout.addWidget(QLabel("<b>Code Highlighting</b>"))
        row_pyg = QHBoxLayout()
        row_pyg.addWidget(QLabel("Pygments style:"))
        self.pygments_style_combo = QComboBox()
        self._load_pygments_styles()
        row_pyg.addWidget(self.pygments_style_combo, 1)
        general_layout.addLayout(row_pyg)
        add_divider(general_layout)

        general_layout.addWidget(QLabel("<b>Tray</b>"))
        self.tray_icon_checkbox = QCheckBox("Enable system tray icon")
        self.tray_icon_checkbox.setChecked(config.load_tray_icon_enabled())
        general_layout.addWidget(self.tray_icon_checkbox)
        self.minimize_to_tray_checkbox = QCheckBox("Minimize to tray on close")
        self.minimize_to_tray_checkbox.setChecked(config.load_minimize_to_tray_enabled())
        general_layout.addWidget(self.minimize_to_tray_checkbox)
        add_divider(general_layout)

        general_layout.addWidget(QLabel("<b>Features</b>"))
        self.feature_tasks_checkbox = QCheckBox("Enable Tasks")
        self.feature_tasks_checkbox.setChecked(config.load_global_feature_tasks_enabled())
        general_layout.addWidget(self.feature_tasks_checkbox)
        self.feature_calendar_checkbox = QCheckBox("Enable Calendar")
        self.feature_calendar_checkbox.setChecked(config.load_global_feature_calendar_enabled())
        general_layout.addWidget(self.feature_calendar_checkbox)
        self.feature_link_navigator_checkbox = QCheckBox("Enable Link Navigator")
        self.feature_link_navigator_checkbox.setChecked(config.load_global_feature_link_navigator_enabled())
        general_layout.addWidget(self.feature_link_navigator_checkbox)
        self.feature_map_checkbox = QCheckBox("Enable Mind Map View")
        self.feature_map_checkbox.setChecked(config.load_global_feature_map_enabled())
        general_layout.addWidget(self.feature_map_checkbox)
        self.feature_tags_checkbox = QCheckBox("Enable Page Tags")
        self.feature_tags_checkbox.setChecked(config.load_global_feature_tags_enabled())
        general_layout.addWidget(self.feature_tags_checkbox)
        self.feature_homebase_vaults_checkbox = QCheckBox("Enable Homebase Vaults")
        self.feature_homebase_vaults_checkbox.setChecked(config.load_global_feature_homebase_vaults_enabled())
        general_layout.addWidget(self.feature_homebase_vaults_checkbox)
        self.feature_keep_search_index_sync_checkbox = QCheckBox("Keep search index in sync periodically")
        self.feature_keep_search_index_sync_checkbox.setChecked(
            config.load_global_feature_keep_search_index_sync_enabled(default=False)
        )
        general_layout.addWidget(self.feature_keep_search_index_sync_checkbox)
        self.feature_remember_cursor_position_checkbox = QCheckBox("Remember and restore last cursor position")
        self.feature_remember_cursor_position_checkbox.setChecked(
            config.load_global_feature_remember_cursor_position_enabled(default=True)
        )
        general_layout.addWidget(self.feature_remember_cursor_position_checkbox)
        self.feature_tasks_checkbox.toggled.connect(self._warn_restart_required)
        self.feature_calendar_checkbox.toggled.connect(self._warn_restart_required)
        self.feature_link_navigator_checkbox.toggled.connect(self._warn_restart_required)
        self.feature_map_checkbox.toggled.connect(self._warn_restart_required)
        self.feature_tags_checkbox.toggled.connect(self._warn_restart_required)
        self.feature_homebase_vaults_checkbox.toggled.connect(self._warn_restart_required)
        self.feature_keep_search_index_sync_checkbox.toggled.connect(self._warn_restart_required)
        add_divider(general_layout)

        general_layout.addWidget(QLabel("<b>Network</b>"))
        row_remote_connect = QHBoxLayout()
        row_remote_connect.addWidget(QLabel("Homebase connect timeout (s):"))
        self.remote_connect_timeout_spin = QDoubleSpinBox()
        self.remote_connect_timeout_spin.setRange(0.1, 120.0)
        self.remote_connect_timeout_spin.setDecimals(1)
        self.remote_connect_timeout_spin.setSingleStep(0.5)
        self.remote_connect_timeout_spin.setValue(config.load_remote_connect_timeout(3.0))
        row_remote_connect.addWidget(self.remote_connect_timeout_spin, 1)
        general_layout.addLayout(row_remote_connect)

        row_remote_read = QHBoxLayout()
        row_remote_read.addWidget(QLabel("Homebase read timeout (s):"))
        self.remote_read_timeout_spin = QDoubleSpinBox()
        self.remote_read_timeout_spin.setRange(0.1, 300.0)
        self.remote_read_timeout_spin.setDecimals(1)
        self.remote_read_timeout_spin.setSingleStep(0.5)
        self.remote_read_timeout_spin.setValue(config.load_remote_read_timeout(10.0))
        row_remote_read.addWidget(self.remote_read_timeout_spin, 1)
        general_layout.addLayout(row_remote_read)
        add_divider(general_layout)

        general_layout.addWidget(QLabel("<b>Capture</b>"))
        row_capture_vault = QHBoxLayout()
        row_capture_vault.addWidget(QLabel("Home Quick Capture Vault:"))
        self.quick_capture_vault_combo = QComboBox()
        row_capture_vault.addWidget(self.quick_capture_vault_combo, 1)
        general_layout.addLayout(row_capture_vault)

        row_capture_page = QHBoxLayout()
        row_capture_page.addWidget(QLabel("Default Capture Page:"))
        self.quick_capture_page_combo = QComboBox()
        self.quick_capture_page_combo.addItems(["Today Journal Page", "Custom Page"])
        row_capture_page.addWidget(self.quick_capture_page_combo, 1)
        general_layout.addLayout(row_capture_page)

        self.quick_capture_custom_edit = QLineEdit()
        self.quick_capture_custom_edit.setPlaceholderText("Page name or :Colon:Path")
        general_layout.addWidget(self.quick_capture_custom_edit)

        capture_help = QLabel(
            "Used for quick captures via tray icon.\n"
            "If unset, StillPoint falls back to the currently open vault's Today page."
        )
        capture_help.setStyleSheet("color: #666; font-size: 11px;")
        capture_help.setWordWrap(True)
        general_layout.addWidget(capture_help)

        self._populate_quick_capture_vaults()
        page_mode = config.load_quick_capture_page_mode()
        self.quick_capture_page_combo.setCurrentIndex(0 if page_mode == "today" else 1)
        self.quick_capture_custom_edit.setText(config.load_quick_capture_custom_page() or "")
        self.quick_capture_page_combo.currentIndexChanged.connect(self._update_quick_capture_custom_visibility)
        self._update_quick_capture_custom_visibility()

        row_capture_hotkey = QHBoxLayout()
        row_capture_hotkey.addWidget(QLabel("Quick Capture Hotkey (in-app):"))
        self.quick_capture_hotkey_edit = QKeySequenceEdit()
        self.quick_capture_hotkey_edit.setKeySequence(config.load_quick_capture_app_hotkey())
        row_capture_hotkey.addWidget(self.quick_capture_hotkey_edit, 1)
        general_layout.addLayout(row_capture_hotkey)

        general_layout.addStretch(1)

        # Appearance
        appearance_layout = add_section("Appearance")
        appearance_layout.addWidget(QLabel("<b>Fonts</b>"))
        row_fonts_app = QHBoxLayout()
        row_fonts_app.addWidget(QLabel("Application font:"))
        self.application_font_combo = self._build_font_combo("System Default")
        try:
            app_font = config.load_application_font()
        except Exception:
            app_font = None
        self._select_font(self.application_font_combo, app_font)
        self.application_font_combo.currentIndexChanged.connect(self._apply_application_font_live)
        row_fonts_app.addWidget(self.application_font_combo, 1)
        appearance_layout.addLayout(row_fonts_app)

        row_fonts_size = QHBoxLayout()
        row_fonts_size.addWidget(QLabel("Application font size:"))
        self.application_font_size_spin = QSpinBox()
        self.application_font_size_spin.setRange(0, 72)
        try:
            size_val = config.load_application_font_size()
        except Exception:
            size_val = None
        default_size = 11
        self.application_font_size_spin.setValue(size_val if size_val is not None else default_size)
        self.application_font_size_spin.setToolTip("Set 0 to use system default size.")
        self.application_font_size_spin.valueChanged.connect(self._apply_application_font_live)
        row_fonts_size.addWidget(self.application_font_size_spin, 1)
        appearance_layout.addLayout(row_fonts_size)

        row_fonts_md = QHBoxLayout()
        row_fonts_md.addWidget(QLabel("Default Markdown font:"))
        self.markdown_font_combo = self._build_font_combo("Editor default")
        try:
            md_font = config.load_default_markdown_font()
        except Exception:
            md_font = None
        self._select_font(self.markdown_font_combo, md_font)
        self.markdown_font_combo.currentIndexChanged.connect(self._warn_restart_required)
        row_fonts_md.addWidget(self.markdown_font_combo, 1)
        appearance_layout.addLayout(row_fonts_md)
        row_fonts_md_size = QHBoxLayout()
        row_fonts_md_size.addWidget(QLabel("Default Markdown font size:"))
        self.markdown_font_size_spin = QSpinBox()
        self.markdown_font_size_spin.setRange(6, 72)
        try:
            md_font_size = config.load_default_markdown_font_size()
        except Exception:
            md_font_size = 12
        self.markdown_font_size_spin.setValue(md_font_size)
        row_fonts_md_size.addWidget(self.markdown_font_size_spin, 1)
        appearance_layout.addLayout(row_fonts_md_size)

        row_fonts_ai = QHBoxLayout()
        row_fonts_ai.addWidget(QLabel("AI chat font:"))
        self.ai_chat_font_combo = self._build_font_combo("default")
        try:
            ai_font = config.load_ai_chat_font_family()
        except Exception:
            ai_font = None
        self._select_font(self.ai_chat_font_combo, ai_font)
        row_fonts_ai.addWidget(self.ai_chat_font_combo, 1)
        appearance_layout.addLayout(row_fonts_ai)

        self.minimal_font_scan_checkbox = QCheckBox("Use Minimal Font Scan (For Fast Window Startup)")
        try:
            self.minimal_font_scan_checkbox.setChecked(config.load_minimal_font_scan_enabled())
        except Exception:
            self.minimal_font_scan_checkbox.setChecked(True)
        self.minimal_font_scan_checkbox.setToolTip(
            "Limit Qt to a tiny font set to reduce startup time. Requires restart to take effect."
        )
        self.minimal_font_scan_checkbox.stateChanged.connect(lambda *_: None)
        appearance_layout.addWidget(self.minimal_font_scan_checkbox)

        divider = QFrame()
        divider.setFrameShape(QFrame.HLine)
        divider.setFrameShadow(QFrame.Sunken)
        appearance_layout.addWidget(divider)

        appearance_layout.addWidget(QLabel("<b>Theme</b>"))
        row_theme = QHBoxLayout()
        row_theme.addWidget(QLabel("Theme:"))
        self.theme_combo = QComboBox()
        row_theme.addWidget(self.theme_combo, 1)
        appearance_layout.addLayout(row_theme)

        self._populate_theme_options()

        row_theme_actions = QHBoxLayout()
        self.refresh_theme_list_btn = QPushButton("Refresh Themes")
        self.refresh_theme_list_btn.clicked.connect(self._refresh_theme_options)
        row_theme_actions.addWidget(self.refresh_theme_list_btn)
        self.open_theme_folder_btn = QPushButton("Open Theme Folder")
        self.open_theme_folder_btn.clicked.connect(self._open_theme_folder)
        row_theme_actions.addWidget(self.open_theme_folder_btn)
        row_theme_actions.addStretch(1)
        appearance_layout.addLayout(row_theme_actions)

        divider2 = QFrame()
        divider2.setFrameShape(QFrame.HLine)
        divider2.setFrameShadow(QFrame.Sunken)
        appearance_layout.addWidget(divider2)

        appearance_layout.addWidget(QLabel("<b>Editor</b>"))
        row_hr = QHBoxLayout()
        row_hr.addWidget(QLabel("Horizontal rule height (pt):"))
        self.hr_line_height_spin = QDoubleSpinBox()
        self.hr_line_height_spin.setRange(0.5, 8.0)
        self.hr_line_height_spin.setSingleStep(0.5)
        self.hr_line_height_spin.setDecimals(1)
        self.hr_line_height_spin.setValue(config.load_hr_line_height())
        row_hr.addWidget(self.hr_line_height_spin)
        row_hr.addStretch(1)
        appearance_layout.addLayout(row_hr)

        appearance_layout.addStretch(1)

        # Modes
        modes_layout = add_section("Modes")
        modes_layout.addWidget(QLabel("<b>VI Mode</b>"))
        self.vi_enable_checkbox = QCheckBox("Enable Vi Mode")
        self.vi_enable_checkbox.setChecked(config.load_vi_mode_enabled())
        self.vi_enable_checkbox.setToolTip("Turn on vi-style navigation keys in the Markdown editor.")
        modes_layout.addWidget(self.vi_enable_checkbox)
        modes_layout.addWidget(QLabel("Vi navigation cursor style:"))
        self.vi_cursor_block_radio = QRadioButton("Use Vi Mode Block Cursor")
        self.vi_cursor_line_radio = QRadioButton("Use Vi Mode Line Highlight Cursor")
        cursor_style = config.load_vi_cursor_style()
        self.vi_cursor_block_radio.setChecked(cursor_style == "block")
        self.vi_cursor_line_radio.setChecked(cursor_style != "block")
        self.vi_cursor_block_radio.setToolTip(
            "Show an accent-colored block cursor while vi navigation mode is active."
        )
        self.vi_cursor_line_radio.setToolTip(
            "Highlight the current line with the vault accent color while vi navigation mode is active."
        )
        modes_layout.addWidget(self.vi_cursor_block_radio)
        modes_layout.addWidget(self.vi_cursor_line_radio)
        vi_divider = QFrame()
        vi_divider.setFrameShape(QFrame.HLine)
        vi_divider.setFrameShadow(QFrame.Sunken)
        modes_layout.addWidget(vi_divider)
        modes_divider = QFrame()
        modes_divider.setFrameShape(QFrame.HLine)
        modes_divider.setFrameShadow(QFrame.Sunken)
        modes_layout.addWidget(modes_divider)

        modes_table = QGridLayout()
        modes_table.setColumnStretch(0, 1)
        modes_table.setColumnStretch(1, 1)
        modes_table.setHorizontalSpacing(24)
        modes_table.setVerticalSpacing(6)
        modes_table.addWidget(QLabel("<b>Focus Mode</b>"), 0, 0)
        modes_table.addWidget(QLabel("<b>Audience Mode</b>"), 0, 1)

        self.focus_center_column_checkbox = QCheckBox("Centered column")
        self.focus_center_column_checkbox.setChecked(focus_settings.get("center_column", True))
        modes_table.addWidget(self.focus_center_column_checkbox, 1, 0)
        self.audience_center_column_checkbox = QCheckBox("Centered column")
        self.audience_center_column_checkbox.setChecked(audience_settings.get("center_column", False))
        modes_table.addWidget(self.audience_center_column_checkbox, 1, 1)

        row_focus_width = QHBoxLayout()
        row_focus_width.addWidget(QLabel("Max column width (chars):"))
        self.focus_width_spin = QSpinBox()
        self.focus_width_spin.setRange(40, 999)
        self.focus_width_spin.setValue(int(focus_settings.get("max_column_width_chars", 80)))
        row_focus_width.addWidget(self.focus_width_spin, 1)
        row_a_width = QHBoxLayout()
        row_a_width.addWidget(QLabel("Max column width (chars):"))
        self.audience_width_spin = QSpinBox()
        self.audience_width_spin.setRange(40, 999)
        self.audience_width_spin.setValue(int(audience_settings.get("max_column_width_chars", 150)))
        row_a_width.addWidget(self.audience_width_spin, 1)
        modes_table.addLayout(row_focus_width, 2, 0)
        modes_table.addLayout(row_a_width, 2, 1)

        row_focus_font = QHBoxLayout()
        row_focus_font.addWidget(QLabel("Font size:"))
        self.focus_font_size_spin = QSpinBox()
        self.focus_font_size_spin.setRange(6, 72)
        self.focus_font_size_spin.setValue(int(focus_settings.get("font_size", 12)))
        row_focus_font.addWidget(self.focus_font_size_spin, 1)
        row_a_base = QHBoxLayout()
        row_a_base.addWidget(QLabel("Font size:"))
        self.audience_font_size_spin = QSpinBox()
        self.audience_font_size_spin.setRange(6, 72)
        self.audience_font_size_spin.setValue(int(audience_settings.get("font_size", 12)))
        row_a_base.addWidget(self.audience_font_size_spin, 1)
        modes_table.addLayout(row_focus_font, 3, 0)
        modes_table.addLayout(row_a_base, 3, 1)

        row_focus_scale = QHBoxLayout()
        row_focus_scale.addWidget(QLabel("Font scale:"))
        self.focus_font_scale_spin = QDoubleSpinBox()
        self.focus_font_scale_spin.setRange(0.5, 2.5)
        self.focus_font_scale_spin.setSingleStep(0.05)
        self.focus_font_scale_spin.setValue(float(focus_settings.get("font_scale", 1.0)))
        row_focus_scale.addWidget(self.focus_font_scale_spin, 1)
        row_a_font = QHBoxLayout()
        row_a_font.addWidget(QLabel("Font scale:"))
        self.audience_font_scale_spin = QDoubleSpinBox()
        self.audience_font_scale_spin.setRange(1.0, 2.5)
        self.audience_font_scale_spin.setSingleStep(0.05)
        self.audience_font_scale_spin.setValue(float(audience_settings.get("font_scale", 1.15)))
        row_a_font.addWidget(self.audience_font_scale_spin, 1)
        modes_table.addLayout(row_focus_scale, 4, 0)
        modes_table.addLayout(row_a_font, 4, 1)

        row_a_line = QHBoxLayout()
        row_a_line.addWidget(QLabel("Line height scale:"))
        self.audience_line_height_spin = QDoubleSpinBox()
        self.audience_line_height_spin.setRange(1.0, 2.5)
        self.audience_line_height_spin.setSingleStep(0.05)
        self.audience_line_height_spin.setValue(float(audience_settings.get("line_height_scale", 1.15)))
        row_a_line.addWidget(self.audience_line_height_spin, 1)
        modes_table.addLayout(row_a_line, 5, 1)

        self.focus_typewriter_checkbox = QCheckBox("Enable typewriter scrolling")
        self.focus_typewriter_checkbox.setChecked(focus_settings.get("typewriter_scrolling", False))
        modes_table.addWidget(self.focus_typewriter_checkbox, 5, 0)
        self.focus_paragraph_checkbox = QCheckBox("Highlight current paragraph")
        self.focus_paragraph_checkbox.setChecked(focus_settings.get("paragraph_focus", False))
        modes_table.addWidget(self.focus_paragraph_checkbox, 6, 0)

        self.audience_cursor_checkbox = QCheckBox("Show cursor spotlight")
        self.audience_cursor_checkbox.setChecked(audience_settings.get("cursor_spotlight", True))
        modes_table.addWidget(self.audience_cursor_checkbox, 6, 1)
        self.audience_paragraph_checkbox = QCheckBox("Highlight current paragraph")
        self.audience_paragraph_checkbox.setChecked(audience_settings.get("paragraph_highlight", True))
        modes_table.addWidget(self.audience_paragraph_checkbox, 7, 1)
        self.audience_scroll_checkbox = QCheckBox("Enable soft auto-scroll")
        self.audience_scroll_checkbox.setChecked(audience_settings.get("soft_autoscroll", True))
        modes_table.addWidget(self.audience_scroll_checkbox, 8, 1)
        self.audience_tools_checkbox = QCheckBox("Show floating tool strip")
        self.audience_tools_checkbox.setChecked(audience_settings.get("show_floating_tools", True))
        modes_table.addWidget(self.audience_tools_checkbox, 9, 1)

        modes_layout.addLayout(modes_table)
        self.main_soft_scroll_checkbox = QCheckBox("Enable main editor soft auto-scroll")
        try:
            self.main_soft_scroll_checkbox.setChecked(config.load_enable_main_soft_scroll())
        except Exception:
            self.main_soft_scroll_checkbox.setChecked(True)
        modes_layout.addWidget(self.main_soft_scroll_checkbox)
        row_soft_lines = QHBoxLayout()
        row_soft_lines.addWidget(QLabel("Soft auto-scroll lines to scroll:"))
        self.main_soft_scroll_lines_spin = QSpinBox()
        self.main_soft_scroll_lines_spin.setRange(1, 50)
        try:
            self.main_soft_scroll_lines_spin.setValue(config.load_main_soft_scroll_lines(5))
        except Exception:
            self.main_soft_scroll_lines_spin.setValue(5)
        row_soft_lines.addWidget(self.main_soft_scroll_lines_spin, 1)
        modes_layout.addLayout(row_soft_lines)
        modes_layout.addStretch(1)

        # Tasks
        task_layout = add_section("Tasks")
        task_layout.addWidget(QLabel("<b>Non Actionable Task Tags</b>"))
        self.non_actionable_tags_edit = QLineEdit()
        self.non_actionable_tags_edit.setPlaceholderText("@wait @wt @someday")
        try:
            val = config.load_non_actionable_task_tags()
        except Exception:
            val = None
        self.non_actionable_tags_edit.setText(val or "@wait @wt @someday")
        task_layout.addWidget(self.non_actionable_tags_edit)
        task_layout.addWidget(QLabel("Space-separated tags (e.g., @wait @wt @someday)."))
        self.show_task_start_checkbox = QCheckBox("Show Start Date in Tasks")
        self.show_task_start_checkbox.setChecked(config.load_show_task_start_date())
        task_layout.addWidget(self.show_task_start_checkbox)
        self.show_task_page_checkbox = QCheckBox("Show Page in Tasks")
        self.show_task_page_checkbox.setChecked(config.load_show_task_page())
        task_layout.addWidget(self.show_task_page_checkbox)
        task_layout.addStretch(1)

        # AI & Code
        ai_layout = add_section("AI Chats and Agents")
        self.enable_ai_chats_checkbox = QCheckBox("Enable AI Chats")
        self.enable_ai_chats_checkbox.setChecked(config.load_global_enable_ai_chats())
        self.enable_ai_chats_checkbox.stateChanged.connect(self._warn_restart_required)
        ai_layout.addWidget(self.enable_ai_chats_checkbox)
        self.manage_server_btn = QPushButton("Manage Servers")
        self.manage_server_btn.clicked.connect(self._open_manage_server_dialog)
        ai_layout.addWidget(self.manage_server_btn)
        add_divider(ai_layout)
        ai_layout.addWidget(QLabel("<b>Default Server and Model</b>"))
        row = QHBoxLayout()
        row.addWidget(QLabel("Server:"))
        self.default_server_combo = QComboBox()
        self.default_server_combo.currentIndexChanged.connect(self._on_default_server_changed)
        row.addWidget(self.default_server_combo, 1)
        ai_layout.addLayout(row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Model:"))
        self.default_model_combo = QComboBox()
        row2.addWidget(self.default_model_combo, 1)
        self.refresh_models_btn = QPushButton("Refresh Models")
        self.refresh_models_btn.clicked.connect(self._refresh_default_models_from_server)
        row2.addWidget(self.refresh_models_btn)
        ai_layout.addLayout(row2)
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Connect timeout (s):"))
        self.ai_connect_timeout_spin = QDoubleSpinBox()
        self.ai_connect_timeout_spin.setRange(0.1, 120.0)
        self.ai_connect_timeout_spin.setDecimals(1)
        self.ai_connect_timeout_spin.setSingleStep(0.5)
        self.ai_connect_timeout_spin.setValue(config.load_ai_chat_connect_timeout(5.0))
        row3.addWidget(self.ai_connect_timeout_spin, 1)
        ai_layout.addLayout(row3)

        row4 = QHBoxLayout()
        row4.addWidget(QLabel("Read timeout (s):"))
        self.ai_read_timeout_spin = QDoubleSpinBox()
        self.ai_read_timeout_spin.setRange(0.1, 300.0)
        self.ai_read_timeout_spin.setDecimals(1)
        self.ai_read_timeout_spin.setSingleStep(0.5)
        self.ai_read_timeout_spin.setValue(config.load_ai_chat_read_timeout(15.0))
        row4.addWidget(self.ai_read_timeout_spin, 1)
        ai_layout.addLayout(row4)
        self._load_default_server_model()
        add_divider(ai_layout)
        ai_layout.addWidget(QLabel("<b>Agents</b>"))
        self.enable_ai_agents_checkbox = QCheckBox("Enable AI Agents in chat")
        self.enable_ai_agents_checkbox.setChecked(config.load_global_enable_ai_agents())
        self.enable_ai_agents_checkbox.stateChanged.connect(self._warn_restart_required)
        ai_layout.addWidget(self.enable_ai_agents_checkbox)
        self.seed_agents_workspace_checkbox = QCheckBox("Add AGENTS.md to vault workspace when opening a terminal")
        self.seed_agents_workspace_checkbox.setChecked(config.load_seed_agents_workspace())
        ai_layout.addWidget(self.seed_agents_workspace_checkbox)
        quiet_row = QHBoxLayout()
        quiet_row.addWidget(QLabel("Local filesystem quiet time (s):"))
        self.local_filesystem_quiet_spin = QSpinBox()
        self.local_filesystem_quiet_spin.setRange(1, 120)
        self.local_filesystem_quiet_spin.setValue(config.load_local_filesystem_quiet_seconds())
        quiet_row.addWidget(self.local_filesystem_quiet_spin, 1)
        ai_layout.addLayout(quiet_row)
        self._agents_box = QWidget()
        agents_layout = QVBoxLayout(self._agents_box)
        agents_layout.setContentsMargins(8, 4, 8, 4)
        agents_layout.setSpacing(6)
        agents_layout.addWidget(
            QLabel(
                "<b>Vault Assistant</b><br>"
                "Understands vault tools (search/read/write, tasks, daily).<br>"
                "<i>Example:</i> \"Search my vault for SVS and write a summary page.\""
            )
        )
        agents_layout.addWidget(
            QLabel(
                "<b>Task Assistant</b><br>"
                "Finds and organizes tasks based on tags or due dates.<br>"
                "<i>Example:</i> \"Find tasks tagged @todo and write a new page.\""
            )
        )
        agents_layout.addWidget(
            QLabel(
                "<b>Web Research Assistant</b><br>"
                "Fetches/scrapes URLs or runs a quick web search.<br>"
                "<i>Example:</i> \"Search the web for todays news on oil prices.\""
            )
        )
        self._agents_box.setVisible(self.enable_ai_agents_checkbox.isChecked())
        self.enable_ai_agents_checkbox.toggled.connect(self._agents_box.setVisible)
        ai_layout.addWidget(self._agents_box)
        add_divider(ai_layout)
        ai_layout.addWidget(QLabel("<b>Agent Tools</b>"))
        self.agent_tools_table = QTableWidget()
        self.agent_tools_table.setColumnCount(3)
        self.agent_tools_table.setHorizontalHeaderLabels(["Tool", "Sample Query", "Tweaks / Dials"])
        self.agent_tools_table.horizontalHeader().setStretchLastSection(True)
        self.agent_tools_table.verticalHeader().setVisible(False)
        self._load_agent_tools_table()
        ai_layout.addWidget(self.agent_tools_table)
        ai_layout.addStretch(1)

        # PlantUML
        puml_layout = add_section("PlantUML")
        self.plantuml_enable_checkbox = QCheckBox("Enable PlantUML rendering")
        self.plantuml_enable_checkbox.setChecked(config.load_plantuml_enabled())
        puml_layout.addWidget(self.plantuml_enable_checkbox)

        jar_row = QHBoxLayout()
        jar_row.addWidget(QLabel("PlantUML JAR path:"))
        self.plantuml_jar_edit = QLineEdit()
        try:
            jar_val = config.load_plantuml_jar_path() or ""
        except Exception:
            jar_val = ""
        self.plantuml_jar_edit.setText(jar_val)
        jar_row.addWidget(self.plantuml_jar_edit, 1)
        jar_browse = QPushButton("Browse…")
        jar_browse.clicked.connect(self._browse_plantuml_jar)
        jar_row.addWidget(jar_browse)
        puml_layout.addLayout(jar_row)

        java_row = QHBoxLayout()
        java_row.addWidget(QLabel("Java path (optional):"))
        self.plantuml_java_edit = QLineEdit()
        try:
            java_val = config.load_plantuml_java_path() or ""
        except Exception:
            java_val = ""
        self.plantuml_java_edit.setText(java_val)
        java_row.addWidget(self.plantuml_java_edit, 1)
        java_browse = QPushButton("Browse…")
        java_browse.clicked.connect(self._browse_java_path)
        java_row.addWidget(java_browse)
        puml_layout.addLayout(java_row)

        debounce_row = QHBoxLayout()
        debounce_row.addWidget(QLabel("Render debounce (ms):"))
        self.plantuml_debounce_spin = QSpinBox()
        self.plantuml_debounce_spin.setRange(100, 5000)
        try:
            self.plantuml_debounce_spin.setValue(config.load_plantuml_render_debounce_ms())
        except Exception:
            self.plantuml_debounce_spin.setValue(500)
        debounce_row.addWidget(self.plantuml_debounce_spin, 1)
        puml_layout.addLayout(debounce_row)

        puml_font_row = QHBoxLayout()
        puml_font_row.addWidget(QLabel("Editor font:"))
        self.plantuml_font_combo = self._build_font_combo("Default (Courier)")
        try:
            puml_font = config.load_puml_editor_font()
        except Exception:
            puml_font = None
        self._select_font(self.plantuml_font_combo, puml_font)
        puml_font_row.addWidget(self.plantuml_font_combo, 1)
        puml_layout.addLayout(puml_font_row)

        puml_font_size_row = QHBoxLayout()
        puml_font_size_row.addWidget(QLabel("Editor font size:"))
        self.plantuml_font_size_spin = QSpinBox()
        self.plantuml_font_size_spin.setRange(6, 72)
        try:
            self.plantuml_font_size_spin.setValue(config.load_puml_editor_font_size())
        except Exception:
            self.plantuml_font_size_spin.setValue(11)
        puml_font_size_row.addWidget(self.plantuml_font_size_spin, 1)
        puml_layout.addLayout(puml_font_size_row)

        test_row = QHBoxLayout()
        self.plantuml_test_btn = QPushButton("Test PlantUML Setup")
        self.plantuml_test_btn.clicked.connect(self._run_plantuml_test)
        self.plantuml_test_status = QLabel("Not tested")
        self.plantuml_test_status.setStyleSheet("color: #888;")
        test_row.addWidget(self.plantuml_test_btn)
        test_row.addWidget(self.plantuml_test_status, 1)
        puml_layout.addLayout(test_row)
        puml_layout.addStretch(1)

        # Mermaid
        mermaid_layout = add_section("Mermaid")
        self.mermaid_enable_checkbox = QCheckBox("Enable Mermaid rendering")
        self.mermaid_enable_checkbox.setChecked(config.load_mermaid_enabled())
        mermaid_layout.addWidget(self.mermaid_enable_checkbox)

        mermaid_info_label = QLabel(
            "Mermaid preview normally uses the built-in web renderer.\n"
            "On Linux, the safer default preview path uses Mermaid CLI (mmdc) unless SP_ENABLE_MERMAID_WEB_PREVIEW=1 is set."
        )
        mermaid_info_label.setWordWrap(True)
        mermaid_layout.addWidget(mermaid_info_label)

        mermaid_font_row = QHBoxLayout()
        mermaid_font_row.addWidget(QLabel("Editor font:"))
        self.mermaid_font_combo = self._build_font_combo("Default (Courier)")
        try:
            mermaid_font = config.load_mermaid_editor_font()
        except Exception:
            mermaid_font = None
        self._select_font(self.mermaid_font_combo, mermaid_font)
        mermaid_font_row.addWidget(self.mermaid_font_combo, 1)
        mermaid_layout.addLayout(mermaid_font_row)

        mermaid_font_size_row = QHBoxLayout()
        mermaid_font_size_row.addWidget(QLabel("Editor font size:"))
        self.mermaid_font_size_spin = QSpinBox()
        self.mermaid_font_size_spin.setRange(6, 72)
        try:
            self.mermaid_font_size_spin.setValue(config.load_mermaid_editor_font_size())
        except Exception:
            self.mermaid_font_size_spin.setValue(11)
        mermaid_font_size_row.addWidget(self.mermaid_font_size_spin, 1)
        mermaid_layout.addLayout(mermaid_font_size_row)
        
        mermaid_layout.addStretch(1)

        # Templates
        tpl_layout = add_section("Templates")
        row_tpl_page = QHBoxLayout()
        row_tpl_page.addWidget(QLabel("Default Template for New Page:"))
        self.page_template_combo = QComboBox()
        self.page_template_combo.addItems(template_names)
        try:
            current_page_tpl = config.load_default_page_template()
        except Exception:
            current_page_tpl = "Default"
        if current_page_tpl in template_names:
            self.page_template_combo.setCurrentText(current_page_tpl)
        row_tpl_page.addWidget(self.page_template_combo, 1)
        tpl_layout.addLayout(row_tpl_page)

        row_tpl_journal = QHBoxLayout()
        row_tpl_journal.addWidget(QLabel("Default Template for New Journal Entry:"))
        self.journal_template_combo = QComboBox()
        self.journal_template_combo.addItems(template_names)
        try:
            current_journal_tpl = config.load_default_journal_template()
        except Exception:
            current_journal_tpl = "JournalDay"
        if current_journal_tpl in template_names:
            self.journal_template_combo.setCurrentText(current_journal_tpl)
        row_tpl_journal.addWidget(self.journal_template_combo, 1)
        tpl_layout.addLayout(row_tpl_journal)
        
        # Add template help text
        help_label = QLabel(
            "You can add any templates you want into your ~/.stillpoint/templates folder.\n\n"
            "Supported Variables:\n"
            "  {{PageName}}     - Name of the page being created\n"
            "  {{DayDateYear}}  - Full date (e.g., Tuesday 29 April 2025)\n"
            "  {{DOW}}          - Day of week (Monday, Tuesday, etc.)\n"
            "  {{Month}}        - Full month name (January, February, etc.)\n"
            "  {{YYYY}}         - 4-digit year\n"
            "  {{MM}}           - 2-digit month (01-12)\n"
            "  {{dd}}           - 2-digit day of month (01-31)\n"
            "  {{QOTD}}         - Random quote of the day from quotationspage.com\n"
            "  {{cursor}}       - Position for cursor after page creation (removed from final content)"
        )
        help_label.setStyleSheet("color: #666; font-size: 11px; margin-top: 10px;")
        help_label.setWordWrap(True)
        tpl_layout.addWidget(help_label)
        tpl_layout.addStretch(1)

        # Vault & Links
        vault_layout = add_section("Vault & Links")
        self.rebuild_button = QPushButton("Rebuild Vault Index")
        self.rebuild_button.clicked.connect(self._on_rebuild_clicked)
        vault_layout.addWidget(self.rebuild_button)

        self.rewrite_backlinks_checkbox = QCheckBox("Rewrite backlinks on page move")
        try:
            self.rewrite_backlinks_checkbox.setChecked(config.load_rewrite_backlinks_on_move())
        except Exception:
            self.rewrite_backlinks_checkbox.setChecked(True)
        vault_layout.addWidget(self.rewrite_backlinks_checkbox)

        self.prefer_short_links_checkbox = QCheckBox("Prefer shorter links on link generation?")
        self.prefer_short_links_checkbox.setToolTip(
            "For Page :Home:PageOne:PageTwo\n"
            "link: PageTwo instead of full link."
        )
        try:
            self.prefer_short_links_checkbox.setChecked(config.load_prefer_short_links())
        except Exception:
            self.prefer_short_links_checkbox.setChecked(True)
        vault_layout.addWidget(self.prefer_short_links_checkbox)
        vault_layout.addStretch(1)

    def _open_manage_server_dialog(self):
        # Prevent duplicate Manage Server buttons by not adding UI elements here
        from sp.app.ui.ai_chat_panel import ServerManager, ServerConfigDialog
        from PySide6.QtWidgets import QDialog, QComboBox, QPushButton, QHBoxLayout, QVBoxLayout, QLabel, QWidget
        from PySide6.QtCore import Qt

        # Create dialog
        dlg = QDialog(self)
        dlg.setWindowTitle("Manage Servers")
        dlg.setModal(True)
        dlg.resize(480, 220)

        layout = QVBoxLayout(dlg)
        row = QHBoxLayout()
        label = QLabel("Server:")
        row.addWidget(label)
        server_manager = ServerManager()
        servers = server_manager.load_servers()
        server_names = [s["name"] for s in servers]
        combo = QComboBox()
        combo.addItems(server_names + ["Add New..."])
        row.addWidget(combo, 1)
        edit_btn = QPushButton("Edit")
        row.addWidget(edit_btn)
        add_btn = QPushButton("Add New")
        row.addWidget(add_btn)
        layout.addLayout(row)

        # Info label
        info_label = QLabel("")
        layout.addWidget(info_label)

        def open_server_dialog(existing=None):
            dialog = ServerConfigDialog(dlg, existing, existing_names=server_manager.list_server_names())
            if dialog.exec() == QDialog.Accepted and dialog.result:
                try:
                    new_server = server_manager.add_or_update_server(dialog.result)
                    # Refresh combo
                    combo.clear()
                    servers2 = server_manager.load_servers()
                    combo.addItems([s["name"] for s in servers2] + ["Add New..."])
                    combo.setCurrentText(new_server["name"])
                    info_label.setText(f"Saved server: {new_server['name']}")
                except Exception as exc:
                    info_label.setText(f"Error: {exc}")

        def on_combo_changed(idx):
            if combo.currentText() == "Add New...":
                open_server_dialog(None)
        combo.currentIndexChanged.connect(on_combo_changed)

        def on_edit():
            name = combo.currentText()
            if name == "Add New...":
                open_server_dialog(None)
            else:
                server = server_manager.get_server(name)
                open_server_dialog(server)
        edit_btn.clicked.connect(on_edit)
        add_btn.clicked.connect(lambda: open_server_dialog(None))

        # OK/Cancel
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(dlg.accept)
        btns.rejected.connect(dlg.reject)
        layout.addWidget(btns)

        dlg.exec()
        self._load_default_server_model()

    def _load_default_server_model(self):
        """Populate default server/model dropdowns based on configured servers."""
        try:
            from sp.app.ui.ai_chat_panel import ServerManager, get_available_models

            mgr = ServerManager()
            servers = mgr.load_servers()
            names = [srv["name"] for srv in servers]
            self.default_server_combo.clear()
            self.default_server_combo.addItems(names)
            desired_server = config.load_default_ai_server()
            if desired_server and desired_server in names:
                self.default_server_combo.setCurrentText(desired_server)
            elif names:
                self.default_server_combo.setCurrentIndex(0)
            self._refresh_default_models(mgr)
        except Exception:
            self.default_server_combo.clear()
            self.default_model_combo.clear()

    def _refresh_default_models(self, mgr=None):
        try:
            from sp.app.ui.ai_chat_panel import ServerManager, get_available_models

            manager = mgr or ServerManager()
            server = manager.get_server(self.default_server_combo.currentText())
            models = get_available_models(server)
            self.default_model_combo.clear()
            self.default_model_combo.addItems(models)
            desired_model = config.load_default_ai_model()
            if desired_model and desired_model in models:
                self.default_model_combo.setCurrentText(desired_model)
            elif models:
                self.default_model_combo.setCurrentIndex(0)
        except Exception:
            self.default_model_combo.clear()

    def _on_default_server_changed(self):
        self._refresh_default_models()

    def _refresh_default_models_from_server(self) -> None:
        try:
            from sp.app.ui.ai_chat_panel import ServerManager, fetch_and_cache_models

            manager = ServerManager()
            server = manager.get_server(self.default_server_combo.currentText())
            if not server:
                QMessageBox.warning(self, "No Server", "Please select a server to refresh models.")
                return
            fetch_and_cache_models(server)
            self._refresh_default_models(manager)
        except Exception:
            QMessageBox.warning(self, "Refresh Failed", "Failed to refresh models from the server.")

    def _browse_plantuml_jar(self):
        options = QFileDialog.Options()
        if sys.platform == "win32":
            options |= QFileDialog.DontUseNativeDialog
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select plantuml.jar",
            "",
            "JAR Files (*.jar);;All Files (*)",
            options=options,
        )
        if path:
            self.plantuml_jar_edit.setText(path)

    def _browse_java_path(self):
        options = QFileDialog.Options()
        if sys.platform == "win32":
            options |= QFileDialog.DontUseNativeDialog
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Select java executable",
            "",
            "Executable Files (*)",
            options=options,
        )
        if path:
            self.plantuml_java_edit.setText(path)

    def _run_plantuml_test(self):
        from sp.app.plantuml_renderer import PlantUMLRenderer

        self.plantuml_test_status.setText("Testing…")
        self.plantuml_test_status.setStyleSheet("color: #888;")
        renderer = PlantUMLRenderer()
        if self.plantuml_jar_edit.text().strip():
            renderer.set_jar_path(self.plantuml_jar_edit.text().strip())
        if self.plantuml_java_edit.text().strip():
            renderer.set_java_path(self.plantuml_java_edit.text().strip())
        result = renderer.test_setup()
        if result.success:
            self.plantuml_test_status.setText(f"OK ({result.duration_ms:.0f} ms)")
            self.plantuml_test_status.setStyleSheet("color: #2a8f2a;")
        else:
            details = result.stderr or ""
            self.plantuml_test_status.setText(f"Failed: {result.error_message or 'Unknown error'}")
            self.plantuml_test_status.setStyleSheet("color: #c00;")
            if details:
                QMessageBox.warning(self, "PlantUML Test", details)

    def _load_pygments_styles(self) -> None:
        styles = ["monokai"]
        try:
            from pygments.styles import get_all_styles

            styles = sorted(set(get_all_styles())) or styles
        except Exception:
            pass
        current = config.load_pygments_style("monokai")
        self.pygments_style_combo.clear()
        self.pygments_style_combo.addItems(styles)
        if current in styles:
            self.pygments_style_combo.setCurrentText(current)

    def _default_agent_tools_config(self) -> dict:
        return {
            "tools": [
                {
                    "name": "web.search",
                    "sample": "search the web for 'current oil prices'",
                    "settings": "engine=duckduckgo",
                },
                {
                    "name": "tasks.list",
                    "sample": "find tasks with @todo",
                    "settings": "triggers=task,tasks,todo,to-do,overdue",
                },
                {
                    "name": "vault.search",
                    "sample": "search my vault for 'TODO' references",
                    "settings": "triggers=search,find references,look for",
                },
                {
                    "name": "vault.write",
                    "sample": "write a new page titled \"Daily Summary\"",
                    "settings": "triggers=write,create a page,new page,make a page,add a page",
                },
                {
                    "name": "vault.write.append",
                    "sample": "add to my favorite poems page a haiku",
                    "settings": "triggers=add to,append,insert into,update,edit",
                },
                {
                    "name": "daily.open",
                    "sample": "add to my journal for today",
                    "settings": "triggers=daily,journal,today",
                },
            ]
        }

    def _load_agent_tools_table(self) -> None:
        settings = config.load_agent_tool_settings()
        if not settings:
            settings = self._default_agent_tools_config()
        tools = settings.get("tools") if isinstance(settings, dict) else None
        if not isinstance(tools, list):
            tools = []
        self.agent_tools_table.setRowCount(len(tools))
        for row, tool in enumerate(tools):
            name = (tool or {}).get("name", "")
            sample = (tool or {}).get("sample", "")
            tweaks = (tool or {}).get("settings", "")
            item_name = QTableWidgetItem(str(name))
            item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
            self.agent_tools_table.setItem(row, 0, item_name)
            self.agent_tools_table.setItem(row, 1, QTableWidgetItem(str(sample)))
            self.agent_tools_table.setItem(row, 2, QTableWidgetItem(str(tweaks)))
        self.agent_tools_table.resizeColumnsToContents()
    
    def _on_rebuild_clicked(self):
        """Handle rebuild index button click."""
        self.rebuildIndexRequested.emit()
        self.rebuild_button.setEnabled(False)
        self.rebuild_button.setText("Rebuilding...")
    
    def accept(self):
        """Save preferences when OK is clicked."""
        config.save_toc_widget_enabled(self.toc_widget_checkbox.isChecked())
        config.save_vi_mode_enabled(self.vi_enable_checkbox.isChecked())
        config.save_vi_cursor_style("block" if self.vi_cursor_block_radio.isChecked() else "line")
        app_font = self._font_value(self.application_font_combo)
        config.save_application_font(app_font)
        size_val = self.application_font_size_spin.value()
        config.save_application_font_size(size_val if size_val > 0 else None)
        md_font = self._font_value(self.markdown_font_combo)
        config.save_default_markdown_font(md_font)
        config.save_default_markdown_font_size(self.markdown_font_size_spin.value())
        config.save_markdown_image_max_width(
            int(self.markdown_image_max_width_combo.currentData() or 900)
        )
        ai_font = self._font_value(self.ai_chat_font_combo)
        config.save_ai_chat_font_family(ai_font)
        config.save_minimal_font_scan_enabled(self.minimal_font_scan_checkbox.isChecked())
        config.save_hr_line_height(self.hr_line_height_spin.value())
        config.save_tray_icon_enabled(self.tray_icon_checkbox.isChecked())
        config.save_minimize_to_tray_enabled(self.minimize_to_tray_checkbox.isChecked())
        config.save_feature_tasks_enabled(self.feature_tasks_checkbox.isChecked())
        config.save_feature_calendar_enabled(self.feature_calendar_checkbox.isChecked())
        config.save_feature_link_navigator_enabled(self.feature_link_navigator_checkbox.isChecked())
        config.save_feature_map_enabled(self.feature_map_checkbox.isChecked())
        config.save_feature_tags_enabled(self.feature_tags_checkbox.isChecked())
        config.save_feature_homebase_vaults_enabled(self.feature_homebase_vaults_checkbox.isChecked())
        config.save_feature_keep_search_index_sync_enabled(self.feature_keep_search_index_sync_checkbox.isChecked())
        config.save_feature_remember_cursor_position_enabled(self.feature_remember_cursor_position_checkbox.isChecked())
        config.save_remote_connect_timeout(self.remote_connect_timeout_spin.value())
        config.save_remote_read_timeout(self.remote_read_timeout_spin.value())
        selected_theme = self.theme_combo.currentData() or "default"
        if not self._validate_theme_selection(selected_theme):
            return
        if selected_theme != getattr(self, "_initial_theme_selection", "default"):
            config.save_theme_preference(selected_theme)
            theme_module.reload_theme()
            QMessageBox.information(
                self,
                "Theme Applied",
                "Theme changes will take effect after restarting the app.",
            )
        config.save_quick_capture_vault(self.quick_capture_vault_combo.currentData())
        capture_mode = "today" if self.quick_capture_page_combo.currentIndex() == 0 else "custom"
        config.save_quick_capture_page_mode(capture_mode)
        config.save_quick_capture_custom_page(self.quick_capture_custom_edit.text())
        hotkey = self.quick_capture_hotkey_edit.keySequence().toString().strip()
        if hotkey and re.match(r"^Alt\+[A-Za-z]$", hotkey):
            QMessageBox.warning(
                self,
                "Quick Capture Hotkey",
                "Alt+letter shortcuts are reserved for menu access and can be ambiguous.\n"
                "Please choose a different shortcut.",
            )
            return
        if not hotkey:
            config.save_quick_capture_app_hotkey("")
        else:
            config.save_quick_capture_app_hotkey(hotkey)
        config.save_focus_mode_settings(
            {
                "center_column": self.focus_center_column_checkbox.isChecked(),
                "max_column_width_chars": self.focus_width_spin.value(),
                "typewriter_scrolling": self.focus_typewriter_checkbox.isChecked(),
                "paragraph_focus": self.focus_paragraph_checkbox.isChecked(),
                "font_size": self.focus_font_size_spin.value(),
                "font_scale": self.focus_font_scale_spin.value(),
            }
        )
        config.save_audience_mode_settings(
            {
                "center_column": self.audience_center_column_checkbox.isChecked(),
                "max_column_width_chars": self.audience_width_spin.value(),
                "font_size": self.audience_font_size_spin.value(),
                "font_scale": self.audience_font_scale_spin.value(),
                "line_height_scale": self.audience_line_height_spin.value(),
                "cursor_spotlight": self.audience_cursor_checkbox.isChecked(),
                "paragraph_highlight": self.audience_paragraph_checkbox.isChecked(),
                "soft_autoscroll": self.audience_scroll_checkbox.isChecked(),
                "show_floating_tools": self.audience_tools_checkbox.isChecked(),
            }
        )
        try:
            config.save_enable_main_soft_scroll(self.main_soft_scroll_checkbox.isChecked())
            config.save_main_soft_scroll_lines(self.main_soft_scroll_lines_spin.value())
        except Exception:
            pass
        if log_enabled("editor_markdown"):
            print(f"[DEBUG] Saving enable_ai_chats: {self.enable_ai_chats_checkbox.isChecked()}")
        config.save_enable_ai_chats(self.enable_ai_chats_checkbox.isChecked())
        config.save_enable_ai_agents(self.enable_ai_agents_checkbox.isChecked())
        config.save_seed_agents_workspace(self.seed_agents_workspace_checkbox.isChecked())
        config.save_local_filesystem_quiet_seconds(self.local_filesystem_quiet_spin.value())
        config.save_default_ai_server(self.default_server_combo.currentText() or None)
        config.save_default_ai_model(self.default_model_combo.currentText() or None)
        config.save_ai_chat_connect_timeout(self.ai_connect_timeout_spin.value())
        config.save_ai_chat_read_timeout(self.ai_read_timeout_spin.value())
        try:
            tools = []
            for row in range(self.agent_tools_table.rowCount()):
                name_item = self.agent_tools_table.item(row, 0)
                sample_item = self.agent_tools_table.item(row, 1)
                tweak_item = self.agent_tools_table.item(row, 2)
                tools.append(
                    {
                        "name": name_item.text() if name_item else "",
                        "sample": sample_item.text() if sample_item else "",
                        "settings": tweak_item.text() if tweak_item else "",
                    }
                )
            config.save_agent_tool_settings({"tools": tools})
        except Exception:
            pass
        config.save_non_actionable_task_tags(self.non_actionable_tags_edit.text())
        config.save_show_task_start_date(self.show_task_start_checkbox.isChecked())
        config.save_show_task_page(self.show_task_page_checkbox.isChecked())
        try:
            # Template preferences are stored per vault. Warn if no vault is active.
            if hasattr(config, "has_active_vault") and not config.has_active_vault():
                QMessageBox.warning(
                    self,
                    "No Active Vault",
                    (
                        "Template preferences are saved per vault.\n\n"
                        "Select a vault first (File → Open Vault), then reopen Preferences to save your default templates."
                    ),
                )
            else:
                config.save_default_page_template(self.page_template_combo.currentText() or "Default")
                config.save_default_journal_template(self.journal_template_combo.currentText() or "JournalDay")
        except Exception:
            pass
        try:
            config.save_pygments_style(self.pygments_style_combo.currentText() or "monokai")
        except Exception:
            pass
        try:
            config.save_plantuml_enabled(self.plantuml_enable_checkbox.isChecked())
            config.save_plantuml_jar_path(self.plantuml_jar_edit.text())
            config.save_plantuml_java_path(self.plantuml_java_edit.text())
            config.save_plantuml_render_debounce_ms(self.plantuml_debounce_spin.value())
            # Save PlantUML editor font preferences
            puml_font = self.plantuml_font_combo.currentData()
            config.save_puml_editor_font(puml_font if puml_font else None)
            config.save_puml_editor_font_size(self.plantuml_font_size_spin.value())
        except Exception:
            pass
        try:
            config.save_mermaid_enabled(self.mermaid_enable_checkbox.isChecked())
            # Save Mermaid editor font preferences
            mermaid_font = self.mermaid_font_combo.currentData()
            config.save_mermaid_editor_font(mermaid_font if mermaid_font else None)
            config.save_mermaid_editor_font_size(self.mermaid_font_size_spin.value())
        except Exception:
            pass
        try:
            config.save_rewrite_backlinks_on_move(self.rewrite_backlinks_checkbox.isChecked())
            config.save_prefer_short_links(self.prefer_short_links_checkbox.isChecked())
        except Exception:
            pass
        super().accept()

    def _populate_quick_capture_vaults(self) -> None:
        self.quick_capture_vault_combo.blockSignals(True)
        self.quick_capture_vault_combo.clear()
        self.quick_capture_vault_combo.addItem("No home vault (use current vault)", None)
        seen: set[str] = set()
        homebase_profiles = config.load_homebase_vault_profiles()
        for vault in config.load_known_vaults():
            path = vault.get("path")
            if not path:
                continue
            if path in seen:
                continue
            name = vault.get("name") or Path(path).name
            self.quick_capture_vault_combo.addItem(name, path)
            seen.add(path)
        for profile in homebase_profiles:
            path = str(profile.get("path") or "").strip()
            if not path or path in seen:
                continue
            name = str(profile.get("name") or Path(path).name)
            self.quick_capture_vault_combo.addItem(f"[Homebase] {name}", path)
            seen.add(path)
        for server in config.load_remote_servers():
            host = str(server.get("host") or "").strip()
            port = server.get("port")
            scheme = str(server.get("scheme") or "http").strip() or "http"
            if not host or not port:
                continue
            selected_vaults = server.get("selected_vaults")
            if not isinstance(selected_vaults, list):
                continue
            base_url = f"{scheme}://{host}:{port}"
            for vault_path in selected_vaults:
                path = str(vault_path or "").strip()
                if not path:
                    continue
                ref = f"remote::{base_url}::{path}"
                if ref in seen:
                    continue
                name = f"[Remote] {host}: {Path(path).name or path}"
                self.quick_capture_vault_combo.addItem(name, ref)
                seen.add(ref)
        saved = config.load_quick_capture_vault()
        selected_value = saved
        if saved and saved.startswith("homebase::"):
            for profile in homebase_profiles:
                profile_id = str(profile.get("id") or "").strip()
                if profile_id != saved:
                    continue
                profile_path = str(profile.get("path") or "").strip()
                if profile_path:
                    selected_value = profile_path
                break
        if saved and self.quick_capture_vault_combo.findData(saved) == -1:
            if saved.startswith("remote::"):
                parts = saved.split("::", 2)
                if len(parts) == 3:
                    display = f"[Remote] {parts[1]}: {Path(parts[2]).name or parts[2]}"
                else:
                    display = saved
            elif saved.startswith("homebase::"):
                display = saved
                for profile in homebase_profiles:
                    profile_id = str(profile.get("id") or "").strip()
                    if profile_id != saved:
                        continue
                    profile_name = str(profile.get("name") or "").strip()
                    profile_path = str(profile.get("path") or "").strip()
                    label = profile_name or (Path(profile_path).name if profile_path else "")
                    display = f"[Homebase] {label or saved}"
                    break
            else:
                display = Path(saved).name or saved
            self.quick_capture_vault_combo.addItem(f"{display} (missing)", saved)
        idx = self.quick_capture_vault_combo.findData(selected_value)
        if idx == -1:
            idx = self.quick_capture_vault_combo.findData(saved)
        self.quick_capture_vault_combo.setCurrentIndex(idx if idx != -1 else 0)
        self.quick_capture_vault_combo.blockSignals(False)

    def _update_quick_capture_custom_visibility(self) -> None:
        is_custom = self.quick_capture_page_combo.currentIndex() == 1
        self.quick_capture_custom_edit.setVisible(is_custom)

    def _theme_dir(self) -> Path:
        return Path.home() / ".stillpoint" / "themes"

    def _list_theme_files(self) -> list[Path]:
        theme_dir = self._theme_dir()
        if not theme_dir.exists():
            return []
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
        self.theme_combo.clear()
        self.theme_combo.addItem("Default Theme", "default")
        for path in self._list_theme_files():
            self.theme_combo.addItem(path.name, path.name)
        current_global = config.load_theme_preference()
        idx = self.theme_combo.findData(current_global)
        if idx == -1:
            idx = 0
        self.theme_combo.setCurrentIndex(idx)
        self._initial_theme_selection = self.theme_combo.currentData()

    def _refresh_theme_options(self) -> None:
        current = self.theme_combo.currentData() or "default"
        self._populate_theme_options()
        idx = self.theme_combo.findData(current)
        if idx != -1:
            self.theme_combo.setCurrentIndex(idx)

    def _validate_theme_selection(self, theme_name: str) -> bool:
        if not theme_name or theme_name == "default":
            return True
        candidate = Path(theme_name)
        if candidate.suffix.lower() != ".json":
            candidate = candidate.with_suffix(".json")
        if not candidate.is_absolute():
            candidate = self._theme_dir() / candidate.name
        if not candidate.exists():
            QMessageBox.warning(
                self,
                "Theme Not Found",
                f"Theme file not found:\n{candidate}",
            )
            return False
        try:
            payload = json.loads(candidate.read_text(encoding="utf-8"))
        except Exception as exc:
            QMessageBox.warning(
                self,
                "Theme Parse Error",
                f"Failed to parse theme JSON:\n{candidate}\n\n{exc}",
            )
            return False
        if not isinstance(payload, dict):
            QMessageBox.warning(
                self,
                "Theme Parse Error",
                f"Theme JSON must be an object at the top level:\n{candidate}",
            )
            return False
        return True

    def _open_theme_folder(self) -> None:
        theme_dir = self._theme_dir()
        try:
            theme_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        try:
            if not any(theme_dir.iterdir()):
                default_theme = theme_module.default_theme_path()
                if default_theme.exists():
                    shutil.copy2(default_theme, theme_dir / "dark-theme.json")
                light_theme = default_theme.with_name("light-theme.json")
                if light_theme.exists():
                    shutil.copy2(light_theme, theme_dir / "light-theme.json")
        except Exception:
            pass
        try:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(theme_dir)))
        except Exception:
            QMessageBox.information(
                self,
                "Theme Folder",
                f"Theme folder path:\n{theme_dir}",
            )

    def _warn_restart_required(self) -> None:
        QMessageBox.information(
            self,
            "Restart Required",
            "This change requires a restart to take effect.",
        )

    def _apply_application_font_live(self) -> None:
        """Apply and save application font immediately when changed."""
        family = self._font_value(self.application_font_combo)
        size_val = self.application_font_size_spin.value()
        size = size_val if size_val > 0 else None
        try:
            config.save_application_font(family)
            config.save_application_font_size(size)
        except Exception:
            pass
        app = QApplication.instance()
        if app:
            try:
                base_font = QFont(self._initial_app_font)
                if family:
                    base_font.setFamily(family)
                if size:
                    base_font.setPointSize(max(6, size))
                app.setFont(base_font)
            except Exception:
                pass
 
    def _build_font_combo(self, default_label: str) -> QComboBox:
        combo = QComboBox()
        combo.setEditable(True)
        combo.addItem(default_label, "")
        for family in self._font_families:
            combo.addItem(family, family)
        combo.setInsertPolicy(QComboBox.NoInsert)
        return combo
    
    def _template_names(self) -> list[str]:
        """Return available template names (stems) from built-in and user templates."""
        names: list[str] = []
        builtin_dir = Path(__file__).parent.parent.parent / "templates"
        user_dir = Path.home() / ".stillpoint" / "templates"
        for tpl_dir in (builtin_dir, user_dir):
            if tpl_dir.exists():
                # Only load .txt files as templates (excludes README.md, etc.)
                for tpl in sorted(tpl_dir.glob("*.txt")):
                    names.append(tpl.stem)
        # Preserve order but drop duplicates
        seen = set()
        unique = []
        for n in names:
            if n not in seen:
                seen.add(n)
                unique.append(n)
        return unique or ["Default"]

    def _select_font(self, combo: QComboBox, family: str | None) -> None:
        if not family:
            combo.setCurrentIndex(0)
            return
        idx = combo.findData(family)
        if idx == -1:
            combo.addItem(family, family)
            idx = combo.findData(family)
        combo.setCurrentIndex(max(0, idx))

    def _font_value(self, combo: QComboBox) -> str | None:
        if combo.currentIndex() == 0:
            return None
        value = combo.currentData()
        if isinstance(value, str) and value.strip():
            return value.strip()
        # Fall back to text if user typed a custom font
        text = combo.currentText().strip()
        if text.lower() in {"system default", "application default"}:
            return None
        return text or None
