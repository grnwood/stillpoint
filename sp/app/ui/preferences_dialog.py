from __future__ import annotations

import os
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QHBoxLayout,
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
)
from pathlib import Path
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtWidgets import QApplication

from sp.app import config


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

        # General
        general_layout = add_section("General")
        general_layout.addWidget(QLabel("<b>Markdown Editor</b>"))
        self.toc_widget_checkbox = QCheckBox("Enable TOC Widget")
        self.toc_widget_checkbox.setChecked(config.load_toc_widget_enabled())
        self.toc_widget_checkbox.setToolTip("Show floating transparent Heading navigator in editor")
        general_layout.addWidget(self.toc_widget_checkbox)

        general_layout.addWidget(QLabel("<b>Tray</b>"))
        self.tray_icon_checkbox = QCheckBox("Enable system tray icon")
        self.tray_icon_checkbox.setChecked(config.load_tray_icon_enabled())
        general_layout.addWidget(self.tray_icon_checkbox)
        self.minimize_to_tray_checkbox = QCheckBox("Minimize to tray on close")
        self.minimize_to_tray_checkbox.setChecked(config.load_minimize_to_tray_enabled())
        general_layout.addWidget(self.minimize_to_tray_checkbox)

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

        # Modes
        modes_layout = add_section("Modes")
        modes_layout.addWidget(QLabel("<b>VI Mode</b>"))
        self.vi_enable_checkbox = QCheckBox("Enable Vi Mode")
        self.vi_enable_checkbox.setChecked(config.load_vi_mode_enabled())
        self.vi_enable_checkbox.setToolTip("Turn on vi-style navigation keys in the Markdown editor.")
        modes_layout.addWidget(self.vi_enable_checkbox)
        self.vi_block_cursor_checkbox = QCheckBox("Use Vi Mode Block Cursor")
        self.vi_block_cursor_checkbox.setChecked(config.load_vi_block_cursor_enabled())
        self.vi_block_cursor_checkbox.setToolTip(
            "Show a colored block cursor when vi-mode is active.\nDisable this if you experience flickering on Linux/Cinnamon."
        )
        modes_layout.addWidget(self.vi_block_cursor_checkbox)
        modes_layout.addWidget(QLabel("<b>Focus Mode</b>"))
        self.focus_center_column_checkbox = QCheckBox("Centered column")
        self.focus_center_column_checkbox.setChecked(focus_settings.get("center_column", True))
        modes_layout.addWidget(self.focus_center_column_checkbox)
        row_focus_width = QHBoxLayout()
        row_focus_width.addWidget(QLabel("Max column width (chars):"))
        self.focus_width_spin = QSpinBox()
        self.focus_width_spin.setRange(40, 999)
        self.focus_width_spin.setValue(int(focus_settings.get("max_column_width_chars", 80)))
        row_focus_width.addWidget(self.focus_width_spin, 1)
        modes_layout.addLayout(row_focus_width)
        row_focus_font = QHBoxLayout()
        row_focus_font.addWidget(QLabel("Focus mode font size:"))
        self.focus_font_size_spin = QSpinBox()
        self.focus_font_size_spin.setRange(6, 72)
        self.focus_font_size_spin.setValue(int(focus_settings.get("font_size", 12)))
        row_focus_font.addWidget(self.focus_font_size_spin, 1)
        modes_layout.addLayout(row_focus_font)
        row_focus_scale = QHBoxLayout()
        row_focus_scale.addWidget(QLabel("Focus mode font scale:"))
        self.focus_font_scale_spin = QDoubleSpinBox()
        self.focus_font_scale_spin.setRange(0.5, 2.5)
        self.focus_font_scale_spin.setSingleStep(0.05)
        self.focus_font_scale_spin.setValue(float(focus_settings.get("font_scale", 1.0)))
        row_focus_scale.addWidget(self.focus_font_scale_spin, 1)
        modes_layout.addLayout(row_focus_scale)
        self.focus_typewriter_checkbox = QCheckBox("Enable typewriter scrolling")
        self.focus_typewriter_checkbox.setChecked(focus_settings.get("typewriter_scrolling", False))
        modes_layout.addWidget(self.focus_typewriter_checkbox)
        self.focus_paragraph_checkbox = QCheckBox("Highlight current paragraph")
        self.focus_paragraph_checkbox.setChecked(focus_settings.get("paragraph_focus", False))
        modes_layout.addWidget(self.focus_paragraph_checkbox)
        modes_layout.addWidget(QLabel("<b>Audience Mode</b>"))
        self.audience_center_column_checkbox = QCheckBox("Centered column")
        self.audience_center_column_checkbox.setChecked(audience_settings.get("center_column", True))
        modes_layout.addWidget(self.audience_center_column_checkbox)
        row_a_width = QHBoxLayout()
        row_a_width.addWidget(QLabel("Max column width (chars):"))
        self.audience_width_spin = QSpinBox()
        self.audience_width_spin.setRange(40, 999)
        self.audience_width_spin.setValue(int(audience_settings.get("max_column_width_chars", 120)))
        row_a_width.addWidget(self.audience_width_spin, 1)
        modes_layout.addLayout(row_a_width)
        row_a_base = QHBoxLayout()
        row_a_base.addWidget(QLabel("Audience base font size:"))
        self.audience_font_size_spin = QSpinBox()
        self.audience_font_size_spin.setRange(6, 72)
        self.audience_font_size_spin.setValue(int(audience_settings.get("font_size", 12)))
        row_a_base.addWidget(self.audience_font_size_spin, 1)
        modes_layout.addLayout(row_a_base)
        row_a_font = QHBoxLayout()
        row_a_font.addWidget(QLabel("Font scale:"))
        self.audience_font_scale_spin = QDoubleSpinBox()
        self.audience_font_scale_spin.setRange(1.0, 2.5)
        self.audience_font_scale_spin.setSingleStep(0.05)
        self.audience_font_scale_spin.setValue(float(audience_settings.get("font_scale", 1.15)))
        row_a_font.addWidget(self.audience_font_scale_spin, 1)
        modes_layout.addLayout(row_a_font)
        row_a_line = QHBoxLayout()
        row_a_line.addWidget(QLabel("Line height scale:"))
        self.audience_line_height_spin = QDoubleSpinBox()
        self.audience_line_height_spin.setRange(1.0, 2.5)
        self.audience_line_height_spin.setSingleStep(0.05)
        self.audience_line_height_spin.setValue(float(audience_settings.get("line_height_scale", 1.15)))
        row_a_line.addWidget(self.audience_line_height_spin, 1)
        modes_layout.addLayout(row_a_line)
        self.audience_cursor_checkbox = QCheckBox("Show cursor spotlight")
        self.audience_cursor_checkbox.setChecked(audience_settings.get("cursor_spotlight", True))
        modes_layout.addWidget(self.audience_cursor_checkbox)
        self.audience_paragraph_checkbox = QCheckBox("Highlight current paragraph")
        self.audience_paragraph_checkbox.setChecked(audience_settings.get("paragraph_highlight", True))
        modes_layout.addWidget(self.audience_paragraph_checkbox)
        self.audience_scroll_checkbox = QCheckBox("Enable soft auto-scroll")
        self.audience_scroll_checkbox.setChecked(audience_settings.get("soft_autoscroll", True))
        modes_layout.addWidget(self.audience_scroll_checkbox)
        self.audience_tools_checkbox = QCheckBox("Show floating tool strip")
        self.audience_tools_checkbox.setChecked(audience_settings.get("show_floating_tools", True))
        modes_layout.addWidget(self.audience_tools_checkbox)
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
        self.non_actionable_tags_edit.setPlaceholderText("@wait @wt")
        try:
            val = config.load_non_actionable_task_tags()
        except Exception:
            val = None
        self.non_actionable_tags_edit.setText(val or "@wait @wt")
        task_layout.addWidget(self.non_actionable_tags_edit)
        self.show_task_start_checkbox = QCheckBox("Show Start Date in Tasks")
        self.show_task_start_checkbox.setChecked(config.load_show_task_start_date())
        task_layout.addWidget(self.show_task_start_checkbox)
        self.show_task_page_checkbox = QCheckBox("Show Page in Tasks")
        self.show_task_page_checkbox.setChecked(config.load_show_task_page())
        task_layout.addWidget(self.show_task_page_checkbox)
        task_layout.addStretch(1)

        # Fonts
        font_layout = add_section("Fonts")
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
        font_layout.addLayout(row_fonts_app)

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
        font_layout.addLayout(row_fonts_size)

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
        font_layout.addLayout(row_fonts_md)
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
        font_layout.addLayout(row_fonts_md_size)

        row_fonts_ai = QHBoxLayout()
        row_fonts_ai.addWidget(QLabel("AI chat font:"))
        self.ai_chat_font_combo = self._build_font_combo("default")
        try:
            ai_font = config.load_ai_chat_font_family()
        except Exception:
            ai_font = None
        self._select_font(self.ai_chat_font_combo, ai_font)
        row_fonts_ai.addWidget(self.ai_chat_font_combo, 1)
        font_layout.addLayout(row_fonts_ai)

        self.minimal_font_scan_checkbox = QCheckBox("Use Minimal Font Scan (For Fast Window Startup)")
        try:
            self.minimal_font_scan_checkbox.setChecked(config.load_minimal_font_scan_enabled())
        except Exception:
            self.minimal_font_scan_checkbox.setChecked(True)
        self.minimal_font_scan_checkbox.setToolTip(
            "Limit Qt to a tiny font set to reduce startup time. Requires restart to take effect."
        )
        self.minimal_font_scan_checkbox.stateChanged.connect(lambda *_: None)
        font_layout.addWidget(self.minimal_font_scan_checkbox)
        font_layout.addStretch(1)

        # AI & Code
        ai_layout = add_section("AI & Code")
        self.enable_ai_chats_checkbox = QCheckBox("Enable AI Chats")
        self.enable_ai_chats_checkbox.setChecked(config.load_enable_ai_chats())
        self.enable_ai_chats_checkbox.stateChanged.connect(self._warn_restart_required)
        ai_layout.addWidget(self.enable_ai_chats_checkbox)
        self.manage_server_btn = QPushButton("Manage Servers")
        self.manage_server_btn.clicked.connect(self._open_manage_server_dialog)
        ai_layout.addWidget(self.manage_server_btn)
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
        ai_layout.addLayout(row2)
        self._load_default_server_model()

        ai_layout.addWidget(QLabel("<b>Code Highlighting</b>"))
        row3 = QHBoxLayout()
        row3.addWidget(QLabel("Pygments style:"))
        self.pygments_style_combo = QComboBox()
        self._load_pygments_styles()
        row3.addWidget(self.pygments_style_combo, 1)
        ai_layout.addLayout(row3)
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

        test_row = QHBoxLayout()
        self.plantuml_test_btn = QPushButton("Test PlantUML Setup")
        self.plantuml_test_btn.clicked.connect(self._run_plantuml_test)
        self.plantuml_test_status = QLabel("Not tested")
        self.plantuml_test_status.setStyleSheet("color: #888;")
        test_row.addWidget(self.plantuml_test_btn)
        test_row.addWidget(self.plantuml_test_status, 1)
        puml_layout.addLayout(test_row)
        puml_layout.addStretch(1)

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
        self.force_read_only_checkbox = QCheckBox("Force read-only mode for this vault")
        self.force_read_only_checkbox.setToolTip(
            "Open this vault without taking a lock or allowing writes from this window."
        )
        try:
            self.force_read_only_checkbox.setChecked(config.load_vault_force_read_only())
        except Exception:
            self.force_read_only_checkbox.setChecked(False)
        vault_layout.addWidget(self.force_read_only_checkbox)

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

    def _browse_plantuml_jar(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select plantuml.jar", "", "JAR Files (*.jar);;All Files (*)")
        if path:
            self.plantuml_jar_edit.setText(path)

    def _browse_java_path(self):
        path, _ = QFileDialog.getOpenFileName(self, "Select java executable", "", "Executable Files (*)")
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
    
    def _on_rebuild_clicked(self):
        """Handle rebuild index button click."""
        self.rebuildIndexRequested.emit()
        self.rebuild_button.setEnabled(False)
        self.rebuild_button.setText("Rebuilding...")
    
    def accept(self):
        """Save preferences when OK is clicked."""
        config.save_toc_widget_enabled(self.toc_widget_checkbox.isChecked())
        config.save_vi_mode_enabled(self.vi_enable_checkbox.isChecked())
        config.save_vi_block_cursor_enabled(self.vi_block_cursor_checkbox.isChecked())
        app_font = self._font_value(self.application_font_combo)
        config.save_application_font(app_font)
        size_val = self.application_font_size_spin.value()
        config.save_application_font_size(size_val if size_val > 0 else None)
        md_font = self._font_value(self.markdown_font_combo)
        config.save_default_markdown_font(md_font)
        config.save_default_markdown_font_size(self.markdown_font_size_spin.value())
        ai_font = self._font_value(self.ai_chat_font_combo)
        config.save_ai_chat_font_family(ai_font)
        config.save_minimal_font_scan_enabled(self.minimal_font_scan_checkbox.isChecked())
        config.save_tray_icon_enabled(self.tray_icon_checkbox.isChecked())
        config.save_minimize_to_tray_enabled(self.minimize_to_tray_checkbox.isChecked())
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
        if os.getenv("ZIMX_DEBUG_EDITOR", "0") not in ("0", "false", "False", ""):
            print(f"[DEBUG] Saving enable_ai_chats: {self.enable_ai_chats_checkbox.isChecked()}")
        config.save_enable_ai_chats(self.enable_ai_chats_checkbox.isChecked())
        config.save_default_ai_server(self.default_server_combo.currentText() or None)
        config.save_default_ai_model(self.default_model_combo.currentText() or None)
        config.save_vault_force_read_only(self.force_read_only_checkbox.isChecked())
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
        for vault in config.load_known_vaults():
            path = vault.get("path")
            if not path:
                continue
            name = vault.get("name") or Path(path).name
            self.quick_capture_vault_combo.addItem(name, path)
        saved = config.load_quick_capture_vault()
        if saved and self.quick_capture_vault_combo.findData(saved) == -1:
            display = Path(saved).name or saved
            self.quick_capture_vault_combo.addItem(f"{display} (missing)", saved)
        idx = self.quick_capture_vault_combo.findData(saved)
        self.quick_capture_vault_combo.setCurrentIndex(idx if idx != -1 else 0)
        self.quick_capture_vault_combo.blockSignals(False)

    def _update_quick_capture_custom_visibility(self) -> None:
        is_custom = self.quick_capture_page_combo.currentIndex() == 1
        self.quick_capture_custom_edit.setVisible(is_custom)

    def _warn_restart_required(self) -> None:
        return None

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
