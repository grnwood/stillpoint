"""Standalone Mermaid editor window with split view and rendering."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QSize, QUrl, QByteArray, QBuffer, QIODevice, QMimeData, QEventLoop
from PySide6.QtGui import QKeySequence, QShortcut, QPixmap, QPainter, QColor
from PySide6.QtWidgets import (
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSplitter,
    QToolButton,
    QComboBox,
    QMenu,
    QApplication,
    QMessageBox,
    QFileDialog,
    QCheckBox,
    QDialog,
    QPushButton,
    QTextEdit,
)
from PySide6.QtGui import QDesktopServices


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _configure_linux_webengine_env() -> None:
    """Apply conservative Chromium flags for Linux WebEngine preview stability.

    These flags are applied only when Mermaid inline web preview is enabled.
    They must be set before importing QtWebEngine modules.
    """
    if not sys.platform.startswith("linux"):
        return

    if _env_truthy("SP_DISABLE_MERMAID_WEB_PREVIEW"):
        return

    inline_pref = False
    if _env_truthy("SP_ENABLE_MERMAID_WEB_PREVIEW"):
        inline_pref = True
    else:
        cfg_path = Path.home() / ".stillpoint_config.json"
        if cfg_path.exists():
            try:
                payload = json.loads(cfg_path.read_text(encoding="utf-8"))
                inline_pref = bool(payload.get("mermaid_inline_web_preview", False))
            except Exception:
                inline_pref = False

    if not inline_pref:
        return

    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

    required_flags = [
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--disable-gpu-compositing",
        "--disable-features=VizDisplayCompositor",
    ]
    existing = (os.getenv("QTWEBENGINE_CHROMIUM_FLAGS") or "").strip()
    for flag in required_flags:
        if flag not in existing:
            existing = f"{existing} {flag}".strip()
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = existing


_QWEBENGINE_VIEW_CLASS = None
_QWEBENGINE_IMPORT_ATTEMPTED = False

from sp.app import config
from sp.app.mermaid_renderer import MermaidRenderer
from sp.logging_flags import log_enabled
from .theme import apply_menu_theme, theme_color, theme_value
from .ai_chat_panel import ApiWorker, ServerManager
from .plantuml_editor_window import BackgroundRenderNotifier, ChatLineEdit, ViPlainTextEdit, ZoomablePreviewLabel

_LOGGING = log_enabled("diagrams")


def _truthy_env(name: str) -> bool:
    return _env_truthy(name)


def _load_qwebengine_view_class():
    """Load QtWebEngine lazily to avoid hard crashes on unsupported Linux setups."""
    global _QWEBENGINE_VIEW_CLASS, _QWEBENGINE_IMPORT_ATTEMPTED
    if _QWEBENGINE_IMPORT_ATTEMPTED:
        return _QWEBENGINE_VIEW_CLASS
    _QWEBENGINE_IMPORT_ATTEMPTED = True
    try:
        from PySide6.QtWebEngineWidgets import QWebEngineView as _QWebEngineView  # type: ignore
        _QWEBENGINE_VIEW_CLASS = _QWebEngineView
    except Exception:
        _QWEBENGINE_VIEW_CLASS = None
    return _QWEBENGINE_VIEW_CLASS


def _inline_preview_preference_enabled() -> bool:
    value = config.load_mermaid_inline_web_preview()
    if _truthy_env("SP_ENABLE_MERMAID_WEB_PREVIEW"):
        value = True
    if _truthy_env("SP_DISABLE_MERMAID_WEB_PREVIEW"):
        value = False
    return value


def _should_use_web_preview() -> bool:
    if not _inline_preview_preference_enabled():
        return False
    if _load_qwebengine_view_class() is None:
        return False
    return True


def _generate_error_svg(error_message: str, line_number: int = 0) -> str:
    line_info = f" (Line {line_number})" if line_number > 0 else ""
    error_display = (
        error_message.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace("\n", "<br>")
    )
    bg = theme_value("mermaid_editor.error.bg", "#f8f8f8")
    title = theme_value("mermaid_editor.error.title", "#cc0000")
    msg = theme_value("mermaid_editor.error.message", "#333333")
    line = theme_value("mermaid_editor.error.line", "#666666")
    box_fill = theme_value("mermaid_editor.error.box_fill", "#ffe6e6")
    box_stroke = theme_value("mermaid_editor.error.box_stroke", "#ff9999")
    box_text = theme_value("mermaid_editor.error.box_text", "#333333")
    error_font = theme_value("mermaid_editor.error.font_family", "monospace")
    title_size = theme_value("mermaid_editor.error.title_size_px", 24)
    title_weight = theme_value("mermaid_editor.error.title_weight", "bold")
    message_size = theme_value("mermaid_editor.error.message_size_px", 14)
    line_size = theme_value("mermaid_editor.error.line_size_px", 12)
    box_font_size = theme_value("mermaid_editor.error.box_font_size_px", 13)
    box_line_height = theme_value("mermaid_editor.error.box_line_height", 1.4)
    svg = f"""<svg width="800" height="400" xmlns="http://www.w3.org/2000/svg">
    <rect width="800" height="400" fill="{bg}"/>
    <defs>
        <style type="text/css"><![CDATA[
            .error-title {{ font-size: {title_size}px; font-weight: {title_weight}; fill: {title}; font-family: {error_font}; }}
            .error-message {{ font-size: {message_size}px; fill: {msg}; font-family: {error_font}; word-wrap: break-word; }}
            .error-line {{ font-size: {line_size}px; fill: {line}; font-family: {error_font}; }}
            .error-box {{ fill: {box_fill}; stroke: {box_stroke}; stroke-width: 2; }}
        ]]></style>
    </defs>
    <rect class="error-box" x="20" y="20" width="760" height="360" rx="5" ry="5"/>
    <text class="error-title" x="40" y="60">Mermaid Render Error{line_info}</text>
    <foreignObject x="40" y="90" width="720" height="280">
        <div xmlns="http://www.w3.org/1999/xhtml" style="font-family: {error_font}; font-size: {box_font_size}px; color: {box_text}; white-space: pre-wrap; word-break: break-word; line-height: {box_line_height};">
            {error_display}
        </div>
    </foreignObject>
</svg>"""
    return svg


class MermaidEditorWindow(QMainWindow):
    """Non-modal editor window for Mermaid diagrams with split editor/preview."""

    def __init__(self, file_path: str, parent=None, on_save=None) -> None:
        super().__init__(parent)

        from sp.app.main import get_app_icon
        self.setWindowIcon(get_app_icon())

        self.file_path = Path(file_path)
        self._on_save = on_save
        self._external_browser_preview = (
            sys.platform.startswith("linux") and not _inline_preview_preference_enabled()
        )
        self._use_web_preview = _should_use_web_preview()
        if self._external_browser_preview:
            self._use_web_preview = False
        self.preview_web = None
        self.renderer = MermaidRenderer()
        self._vi_enabled: bool = config.load_vi_mode_enabled()
        self._vi_insert_active: bool = False
        self._ai_prompt_history: list[str] = []
        self._ai_chat_enabled: bool = config.load_enable_ai_chats()
        self._auto_render_enabled: bool = config.load_mermaid_auto_render(default=False)
        self._mermaid_theme: str = config.load_mermaid_render_theme(default="neutral")
        self._editor_dirty: bool = False
        self._last_saved_content: Optional[str] = None
        self._last_svg: str = ""
        self._window_shown = False
        self._startup_render_pending = True
        self._render_in_progress = False
        self._render_requeued = False
        self._render_request_token = 0
        self._closing = False
        self._render_notifier = BackgroundRenderNotifier()
        self._render_notifier.finished.connect(self._on_background_render_finished)
        self.setWindowTitle(f"Mermaid Editor - {self.file_path.name}")
        self.setGeometry(100, 100, 1400, 800)

        main_widget = QWidget()
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)

        toolbar_layout = QHBoxLayout()
        toolbar_layout.setContentsMargins(5, 5, 5, 5)

        editor_section = QHBoxLayout()

        self.save_btn = QToolButton()
        self.save_btn.setText("Save")
        self.save_btn.setToolTip("Save file (Ctrl+S or Ctrl+Enter)")
        self.save_btn.clicked.connect(self._save_file)
        editor_section.addWidget(self.save_btn)

        editor_zoom_label = QLabel("Zoom:")
        editor_section.addWidget(editor_zoom_label)

        self.editor_zoom_out_btn = QToolButton()
        self.editor_zoom_out_btn.setText("−")
        self.editor_zoom_out_btn.setToolTip("Zoom out editor")
        self.editor_zoom_out_btn.clicked.connect(self._zoom_out_editor)
        editor_section.addWidget(self.editor_zoom_out_btn)

        self.editor_zoom_in_btn = QToolButton()
        self.editor_zoom_in_btn.setText("+")
        self.editor_zoom_in_btn.setToolTip("Zoom in editor")
        self.editor_zoom_in_btn.clicked.connect(self._zoom_in_editor)
        editor_section.addWidget(self.editor_zoom_in_btn)

        editor_section.addSpacing(15)

        self.auto_render_checkbox = QCheckBox("Auto render?")
        self.auto_render_checkbox.setToolTip("Automatically render diagram on text changes")
        self.auto_render_checkbox.setChecked(self._auto_render_enabled)
        self.auto_render_checkbox.toggled.connect(self._on_auto_render_toggled)
        if self._external_browser_preview:
            self.auto_render_checkbox.setChecked(False)
            self.auto_render_checkbox.setEnabled(False)
            self.auto_render_checkbox.setToolTip("Disabled in external browser preview mode")
            self._auto_render_enabled = False
        editor_section.addWidget(self.auto_render_checkbox)

        self.render_status_label = QLabel()
        self.render_status_label.setStyleSheet(
            "color: "
            f"{theme_value('mermaid_editor.render_status.color', '#ffa500')}; "
            "font-style: italic; margin-left: 10px;"
        )
        editor_section.addWidget(self.render_status_label)
        self._update_render_status_label()

        toolbar_layout.addLayout(editor_section)
        toolbar_layout.addStretch()

        preview_section = QHBoxLayout()

        self.render_btn = QToolButton()
        self.render_btn.setText("Render")
        self.render_btn.setToolTip("Render diagram (Ctrl+S)")
        self.render_btn.clicked.connect(self._render)
        preview_section.addWidget(self.render_btn)

        preview_section.addSpacing(10)

        preview_theme_label = QLabel("Theme:")
        preview_section.addWidget(preview_theme_label)

        self.preview_theme_combo = QComboBox()
        self.preview_theme_combo.addItem("Default", "default")
        self.preview_theme_combo.addItem("Neutral", "neutral")
        self.preview_theme_combo.addItem("Forest", "forest")
        self.preview_theme_combo.addItem("Dark", "dark")
        self.preview_theme_combo.addItem("Base", "base")
        idx = self.preview_theme_combo.findData(self._mermaid_theme)
        self.preview_theme_combo.setCurrentIndex(idx if idx >= 0 else self.preview_theme_combo.findData("neutral"))
        self.preview_theme_combo.currentIndexChanged.connect(self._on_mermaid_theme_changed)
        preview_section.addWidget(self.preview_theme_combo)

        preview_section.addSpacing(10)

        preview_zoom_label = QLabel("Zoom:")
        preview_section.addWidget(preview_zoom_label)

        self.preview_zoom_out_btn = QToolButton()
        self.preview_zoom_out_btn.setText("−")
        self.preview_zoom_out_btn.setToolTip("Zoom out preview")
        self.preview_zoom_out_btn.clicked.connect(self._zoom_out_preview)
        preview_section.addWidget(self.preview_zoom_out_btn)

        self.preview_zoom_in_btn = QToolButton()
        self.preview_zoom_in_btn.setText("+")
        self.preview_zoom_in_btn.setToolTip("Zoom in preview")
        self.preview_zoom_in_btn.clicked.connect(self._zoom_in_preview)
        preview_section.addWidget(self.preview_zoom_in_btn)

        preview_section.addSpacing(10)

        self.export_btn = QToolButton()
        self.export_btn.setText("Export")
        self.export_btn.setToolTip("Export diagram as SVG or PNG")
        self.export_btn.clicked.connect(self._show_export_menu)
        preview_section.addWidget(self.export_btn)

        toolbar_layout.addLayout(preview_section)

        main_layout.addLayout(toolbar_layout)

        center_widget = QWidget()
        center_layout = QVBoxLayout()
        center_layout.setContentsMargins(0, 0, 0, 0)

        shortcuts_layout = QHBoxLayout()
        shortcuts_layout.setContentsMargins(5, 5, 5, 5)

        shortcuts_label = QLabel("Shortcuts:")
        self.shortcuts_category_combo = QComboBox()
        self.shortcuts_variant_combo = QComboBox()
        self.shortcuts_help_btn = QToolButton()
        self.shortcuts_help_btn.setText("?")
        self.shortcuts_help_btn.setToolTip("Open documentation for selected diagram type")
        self.shortcuts_help_btn.setEnabled(False)

        self._shortcuts_data: list[dict] = []
        self._load_shortcuts()

        self.shortcuts_category_combo.currentIndexChanged.connect(self._on_shortcuts_category_changed)
        self.shortcuts_variant_combo.currentIndexChanged.connect(self._on_shortcut_variant_selected)
        self.shortcuts_help_btn.clicked.connect(self._on_shortcuts_help_clicked)

        shortcuts_layout.addWidget(shortcuts_label)
        shortcuts_layout.addWidget(self.shortcuts_category_combo)
        shortcuts_layout.addWidget(self.shortcuts_variant_combo)
        shortcuts_layout.addWidget(self.shortcuts_help_btn)

        shortcuts_layout.addSpacing(20)

        server_label = QLabel("Server:")
        self.ai_server_combo = QComboBox()
        self.ai_server_combo.setMaximumWidth(150)
        self.ai_server_combo.currentTextChanged.connect(self._on_ai_server_changed)
        server_label.setVisible(self._ai_chat_enabled)
        self.ai_server_combo.setVisible(self._ai_chat_enabled)
        shortcuts_layout.addWidget(server_label)
        shortcuts_layout.addWidget(self.ai_server_combo)

        model_label = QLabel("Model:")
        self.ai_model_combo = QComboBox()
        self.ai_model_combo.setMaximumWidth(150)
        model_label.setVisible(self._ai_chat_enabled)
        self.ai_model_combo.setVisible(self._ai_chat_enabled)
        shortcuts_layout.addWidget(model_label)
        shortcuts_layout.addWidget(self.ai_model_combo)

        shortcuts_layout.addStretch()

        if self._ai_chat_enabled:
            self._load_ai_servers_models()

        center_layout.addLayout(shortcuts_layout)

        main_h_splitter = QSplitter(Qt.Horizontal)

        self.editor = ViPlainTextEdit()
        self.editor.setPlaceholderText("Enter Mermaid diagram code here...")
        self.editor.setFont(self._get_monospace_font())
        self.editor.setStyleSheet(
            f"""
            QPlainTextEdit {{
                background-color: {theme_value('mermaid_editor.editor.bg', '#1e1e1e')};
                color: {theme_value('mermaid_editor.editor.text', '#d4d4d4')};
                border: 1px solid {theme_value('mermaid_editor.editor.border', '#3e3e3e')};
                padding: 8px;
                selection-background-color: {theme_value('mermaid_editor.editor.selection_bg', '#264f78')};
                selection-color: {theme_value('mermaid_editor.editor.selection_text', '#ffffff')};
            }}
        """
        )
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(' ') * 2)

        editor_container = QWidget()
        editor_layout = QVBoxLayout()
        editor_layout.setContentsMargins(0, 0, 0, 0)
        editor_layout.addWidget(self.editor)
        editor_container.setLayout(editor_layout)
        main_h_splitter.addWidget(editor_container)

        right_v_splitter = None
        self.loading_overlay = None
        if not self._external_browser_preview:
            right_v_splitter = QSplitter(Qt.Vertical)

            preview_container = QWidget()
            preview_layout = QVBoxLayout()
            preview_layout.setContentsMargins(0, 0, 0, 0)

            if self._use_web_preview:
                web_view_class = _load_qwebengine_view_class()
                if web_view_class is None:
                    self._use_web_preview = False
                else:
                    self.preview_web = web_view_class(preview_container)
            if self._use_web_preview and self.preview_web is not None:
                self.preview_web.setContextMenuPolicy(Qt.NoContextMenu)
                self.preview_web.installEventFilter(self)
                self.preview_web.loadStarted.connect(self._on_web_preview_load_started)
                self.preview_web.loadFinished.connect(self._on_web_preview_load_finished)
                preview_layout.addWidget(self.preview_web)
            else:
                self.preview_label = ZoomablePreviewLabel()
                self.preview_label.setAlignment(Qt.AlignCenter)
                self.preview_label.setMinimumSize(400, 300)
                self.preview_label.setStyleSheet(
                    "background-color: "
                    f"{theme_value('mermaid_editor.preview.bg', '#f8f8f8')}; "
                    "color: "
                    f"{theme_value('mermaid_editor.preview.text', '#000000')};"
                )
                self.preview_label.setWordWrap(True)
                self.preview_label.zoomRequested.connect(self._on_preview_wheel_zoom)
                self.preview_label.setContextMenuPolicy(Qt.CustomContextMenu)
                self.preview_label.customContextMenuRequested.connect(self._show_preview_context_menu)

                self.preview_scroll_area = QScrollArea()
                self.preview_scroll_area.setWidgetResizable(True)
                self.preview_scroll_area.setWidget(self.preview_label)
                self.preview_scroll_area.setStyleSheet(
                    "background-color: "
                    f"{theme_value('mermaid_editor.preview.bg', '#f8f8f8')};"
                )
                preview_layout.addWidget(self.preview_scroll_area)
            self.loading_overlay = self._create_loading_overlay(preview_container)
            preview_container.setLayout(preview_layout)
            right_v_splitter.addWidget(preview_container)

            if self._ai_chat_enabled:
                self.ai_panel = self._create_ai_chat_panel()
                right_v_splitter.addWidget(self.ai_panel)
                right_v_splitter.setSizes([490, 210])
            else:
                self.ai_panel = None
                right_v_splitter.setSizes([700])
            right_v_splitter.setCollapsible(0, False)
            right_v_splitter.setCollapsible(1, True)

            main_h_splitter.addWidget(right_v_splitter)
            main_h_splitter.setSizes([400, 600])
            main_h_splitter.setCollapsible(0, False)
            main_h_splitter.setCollapsible(1, False)
        else:
            self.ai_panel = None
            main_h_splitter.setSizes([1000])
            main_h_splitter.setCollapsible(0, False)

        center_layout.addWidget(main_h_splitter)
        center_widget.setLayout(center_layout)
        main_layout.addWidget(center_widget)

        main_widget.setLayout(main_layout)
        self.setCentralWidget(main_widget)

        self.editor_preview_splitter = main_h_splitter
        self._vertical_splitter = right_v_splitter

        self._geom_timer = QTimer()
        self._geom_timer.setSingleShot(True)
        self._geom_timer.setInterval(500)
        self._geom_timer.timeout.connect(self._save_geometry_prefs)
        self.editor_preview_splitter.splitterMoved.connect(lambda *_: self._geom_timer.start())
        if self._ai_chat_enabled and self._vertical_splitter is not None:
            self._vertical_splitter.splitterMoved.connect(lambda *_: self._geom_timer.start())

        self._restore_geometry_prefs()

        # Enable legacy panning only for label fallback mode.
        if (not self._use_web_preview) and (not self._external_browser_preview):
            self.preview_label.setMouseTracking(True)
            self.preview_label.installEventFilter(self)
        self._preview_pan_start = None
        self._preview_pan_origin = None

        self._badge_base_style = (
            "border: 1px solid "
            f"{theme_value('mermaid_editor.badge.border', '#666666')}; "
            "padding: 2px 6px; border-radius: 3px;"
        )
        self._vi_badge_base_style = self._badge_base_style
        self._vi_status_label = QLabel("INS")
        self._vi_status_label.setObjectName("viStatusLabel")
        self._vi_status_label.setToolTip("Vi insert mode indicator")
        self.statusBar().addPermanentWidget(self._vi_status_label, 0)
        self.editor.set_vi_cursor_style(config.load_vi_cursor_style())
        self.editor.viInsertModeChanged.connect(self._on_vi_insert_state_changed)
        self.editor.set_vi_mode_enabled(self._vi_enabled)
        self._update_vi_badge_visibility()

        self._load_file()

        self.render_timer = QTimer()
        self.render_timer.setSingleShot(True)
        self.render_timer.setInterval(1000)
        self.render_timer.timeout.connect(self._render)

        self.editor.textChanged.connect(self._on_editor_changed)

        QShortcut(QKeySequence.Save, self, self._save_file)
        ctrl_enter_shortcut = QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Return), self.editor)
        ctrl_enter_shortcut.activated.connect(self._render)
        if self._ai_chat_enabled:
            QShortcut(QKeySequence(Qt.CTRL | Qt.SHIFT | Qt.Key_Space), self, self._toggle_focus_editor_ai)

        try:
            self.editor_zoom_level = int(config.load_mermaid_editor_zoom(0))
        except Exception:
            self.editor_zoom_level = 0
        try:
            self.preview_zoom_level = int(config.load_mermaid_preview_zoom(0))
        except Exception:
            self.preview_zoom_level = 0
        self.preview_pixmap: Optional[QPixmap] = None
        try:
            base_pt = 11
            font = self.editor.font()
            font.setPointSize(max(6, base_pt + self.editor_zoom_level))
            self.editor.setFont(font)
        except Exception:
            pass

        self._set_preview_loading_state("Generating preview…")

    def _create_loading_overlay(self, parent: QWidget) -> QWidget:
        overlay = QWidget(parent)
        overlay.setObjectName("loadingOverlay")
        overlay_bg = theme_value("mermaid_editor.loading.overlay_bg", "rgba(12, 18, 28, 215)")
        label_color = theme_value("mermaid_editor.loading.label_text", "#f4f7fb")
        label_size = theme_value("mermaid_editor.loading.label_size_px", 16)
        label_weight = theme_value("mermaid_editor.loading.label_weight", "bold")
        overlay.setStyleSheet(
            f"""
            QWidget#loadingOverlay {{
                background-color: {overlay_bg};
            }}
            QLabel {{
                color: {label_color};
                font-size: {label_size}px;
                font-weight: {label_weight};
            }}
        """
        )
        layout = QVBoxLayout(overlay)
        layout.setAlignment(Qt.AlignCenter)
        self.spinner_label = QLabel("⠋")
        spinner_size = theme_value("mermaid_editor.loading.spinner_size_px", 80)
        spinner_color = theme_value("mermaid_editor.loading.spinner_color", "#9ad1ff")
        self.spinner_label.setStyleSheet(f"font-size: {spinner_size}px; color: {spinner_color};")
        self.spinner_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.spinner_label)
        self.loading_text_label = QLabel("Generating preview…")
        self.loading_text_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.loading_text_label)
        self.spinner_timer = QTimer()
        self.spinner_timer.setInterval(100)
        self.spinner_angle = 0
        self.spinner_timer.timeout.connect(self._animate_spinner)
        return overlay

    def _animate_spinner(self) -> None:
        spinners = ['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏']
        if hasattr(self, 'spinner_label'):
            self.spinner_angle = (self.spinner_angle + 1) % len(spinners)
            self.spinner_label.setText(spinners[self.spinner_angle])

    def _show_loading(self, message: str = "Generating preview…") -> None:
        if self._external_browser_preview:
            return
        if not hasattr(self, 'loading_overlay'):
            return
        try:
            if hasattr(self, "loading_text_label"):
                self.loading_text_label.setText(message)
            parent = self.loading_overlay.parent()
            if parent:
                self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())
            self.loading_overlay.raise_()
            self.loading_overlay.show()
            self.spinner_timer.start()
        except Exception:
            pass

    def _hide_loading(self) -> None:
        if self._external_browser_preview:
            return
        if not hasattr(self, 'loading_overlay'):
            return
        try:
            self.spinner_timer.stop()
            self.loading_overlay.hide()
        except Exception:
            pass

    def _set_preview_loading_state(self, message: str = "Generating preview…") -> None:
        if self._external_browser_preview:
            return
        if self._use_web_preview and self.preview_web is not None:
            return
        try:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setText(message)
        except Exception:
            pass

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        if not self._window_shown:
            self._window_shown = True
            # In external browser mode don't auto-render on open —
            # the user triggers preview explicitly via Render button.
            if self._startup_render_pending and not self._external_browser_preview:
                QTimer.singleShot(0, self._render)

    def _run_render_in_background(self, token: int, render_fn) -> None:
        def _worker() -> None:
            try:
                result = render_fn()
                error = None
            except Exception as exc:  # pragma: no cover - surfaced in callback
                result = None
                error = exc
            try:
                self._render_notifier.finished.emit(token, result, error)
            except RuntimeError:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _on_web_preview_load_started(self) -> None:
        self._show_loading("Generating preview…")

    def _on_web_preview_load_finished(self, ok: bool) -> None:
        self._render_in_progress = False
        self._hide_loading()
        self.render_btn.setText("Render OK" if ok else "Render Failed")
        if self._render_requeued and not self._closing:
            QTimer.singleShot(0, self._render)

    def _restore_geometry_prefs(self) -> None:
        """Restore window and splitter geometry from config."""
        try:
            geom = config.load_mermaid_editor_geometry()
            if geom:
                self.restoreGeometry(QByteArray.fromBase64(geom.encode("utf-8")))
        except Exception:
            pass
        try:
            splitter = config.load_mermaid_editor_splitter()
            if splitter:
                self.editor_preview_splitter.restoreState(QByteArray.fromBase64(splitter.encode("utf-8")))
        except Exception:
            pass

    def _save_geometry_prefs(self) -> None:
        """Save window and splitter geometry to config."""
        try:
            geom = self.saveGeometry().toBase64().data().decode("utf-8")
            config.save_mermaid_editor_geometry(geom)
        except Exception:
            pass
        try:
            splitter = self.editor_preview_splitter.saveState().toBase64().data().decode("utf-8")
            config.save_mermaid_editor_splitter(splitter)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._geom_timer.start()
        if hasattr(self, 'loading_overlay') and self.loading_overlay.isVisible():
            parent = self.loading_overlay.parent()
            if parent:
                self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        self._geom_timer.start()

    def closeEvent(self, event) -> None:
        self._save_geometry_prefs()
        super().closeEvent(event)

    # --- Preview panning (mouse drag to scroll) using eventFilter ---
    def eventFilter(self, obj, event):
        if self._use_web_preview and self.preview_web is not None and obj is self.preview_web:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton:
                pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
                self._show_preview_context_menu(pos)
                return True
        if (not self._use_web_preview) and hasattr(self, "preview_label") and obj is self.preview_label:
            from PySide6.QtCore import QEvent
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._preview_pan_start = event.pos()
                self._preview_pan_origin = self.preview_scroll_area.horizontalScrollBar().value(), self.preview_scroll_area.verticalScrollBar().value()
                return True
            elif event.type() == QEvent.MouseMove and self._preview_pan_start is not None and event.buttons() & Qt.LeftButton:
                dx = event.pos().x() - self._preview_pan_start.x()
                dy = event.pos().y() - self._preview_pan_start.y()
                orig_x, orig_y = self._preview_pan_origin
                self.preview_scroll_area.horizontalScrollBar().setValue(orig_x - dx)
                self.preview_scroll_area.verticalScrollBar().setValue(orig_y - dy)
                return True
            elif event.type() == QEvent.MouseButtonRelease:
                self._preview_pan_start = None
                self._preview_pan_origin = None
                return True
        return super().eventFilter(obj, event)

    def _get_monospace_font(self):
        from PySide6.QtGui import QFont
        font = QFont()
        
        # Load font preferences
        font_family = config.load_mermaid_editor_font()
        font_size = config.load_mermaid_editor_font_size()
        
        if font_family:
            font.setFamily(font_family)
        else:
            # Default to platform-specific monospace
            font.setFamily("Courier New" if os.name == "nt" else "Courier")
        
        font.setPointSize(font_size)
        font.setFixedPitch(True)
        return font

    def _load_shortcuts(self) -> None:
        """Load Mermaid diagram templates from mmd_shortcuts.json into two-level combos."""
        self.shortcuts_category_combo.clear()
        self.shortcuts_variant_combo.clear()
        self.shortcuts_category_combo.addItem("-- Pick a type --", None)
        self.shortcuts_variant_combo.addItem("-- Pick variant --", None)
        self.shortcuts_variant_combo.setEnabled(False)
        self.shortcuts_help_btn.setEnabled(False)
        self._shortcuts_data = []
        try:
            shortcuts = None
            loaded_from = None
            candidates = [
                Path(__file__).resolve().parents[1] / "mmd_shortcuts.json",
                Path(__file__).resolve().parents[0] / "mmd_shortcuts.json",
                Path.cwd() / "sp" / "app" / "mmd_shortcuts.json",
            ]
            for p in candidates:
                try:
                    if p.exists():
                        with open(p, "r", encoding="utf-8") as f:
                            shortcuts = json.load(f)
                        loaded_from = str(p)
                        print(f"[Mermaid] Loaded shortcuts from {p} ({len(shortcuts) if isinstance(shortcuts, list) else 'invalid'} items)")
                        break
                except Exception as exc:
                    print(f"[Mermaid] Failed reading {p}: {exc}")
            if shortcuts is None:
                try:
                    import importlib.resources as ilr
                    data = ilr.files("sp.app").joinpath("mmd_shortcuts.json").read_text(encoding="utf-8")
                    shortcuts = json.loads(data)
                    loaded_from = "package:sp.app/mmd_shortcuts.json"
                    print("[Mermaid] Loaded shortcuts via importlib.resources")
                except Exception as exc:
                    print(f"[Mermaid] resources load failed: {exc}")

            if isinstance(shortcuts, list):
                self._shortcuts_data = shortcuts
                for item in shortcuts:
                    name = item.get("name", "")
                    if name:
                        self.shortcuts_category_combo.addItem(name, name)
                print(f"[Mermaid] Shortcuts loaded: {len(shortcuts)} categories from {loaded_from}")
            else:
                print("[Mermaid] No shortcuts loaded (not a list)")
        except Exception as exc:
            print(f"[Mermaid] Failed to load shortcuts: {exc}")

    def _on_shortcuts_category_changed(self, index: int) -> None:
        """When the category changes, offer Simple/Advanced variants and enable help."""
        self.shortcuts_variant_combo.blockSignals(True)
        self.shortcuts_variant_combo.clear()
        self.shortcuts_variant_combo.addItem("-- Pick variant --", None)
        self.shortcuts_variant_combo.blockSignals(False)
        self.shortcuts_variant_combo.setEnabled(False)
        self.shortcuts_help_btn.setEnabled(False)

        if index <= 0:
            return
        name = self.shortcuts_category_combo.itemData(index)
        item = next((it for it in self._shortcuts_data if it.get("name") == name), None)
        if not item:
            return
        variants = []
        if item.get("simple_mmd"):
            variants.append(("Simple", "simple_mmd"))
        if item.get("advanced_mmd"):
            variants.append(("Advanced", "advanced_mmd"))
        for label, key in variants:
            self.shortcuts_variant_combo.addItem(label, key)
        self.shortcuts_variant_combo.setEnabled(bool(variants))
        self.shortcuts_help_btn.setEnabled(bool(item.get("help")))

    def _on_shortcut_variant_selected(self, index: int) -> None:
        """Insert the selected variant (Simple/Advanced) for the current category."""
        if index <= 0:
            return
        cat_index = self.shortcuts_category_combo.currentIndex()
        if cat_index <= 0:
            return
        name = self.shortcuts_category_combo.itemData(cat_index)
        item = next((it for it in self._shortcuts_data if it.get("name") == name), None)
        if not item:
            return
        key = self.shortcuts_variant_combo.itemData(index)
        code = item.get(key or "", "")
        if not code:
            return
        code = code.replace("!!BR!!", "\n")
        cursor = self.editor.textCursor()
        pos = cursor.position()
        text = self.editor.toPlainText()
        if pos > 0 and text and text[pos - 1] != "\n":
            cursor.insertText("\n")
        cursor.insertText(code)
        cursor.insertText("\n")
        try:
            self.shortcuts_variant_combo.blockSignals(True)
            self.shortcuts_variant_combo.setCurrentIndex(0)
        finally:
            self.shortcuts_variant_combo.blockSignals(False)

    def _on_shortcuts_help_clicked(self) -> None:
        cat_index = self.shortcuts_category_combo.currentIndex()
        if cat_index <= 0:
            return
        name = self.shortcuts_category_combo.itemData(cat_index)
        item = next((it for it in self._shortcuts_data if it.get("name") == name), None)
        if not item:
            return
        url = item.get("help")
        if not url:
            return
        try:
            QDesktopServices.openUrl(QUrl(url))
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] Failed to open help URL: {exc}")

    def _toggle_focus_editor_ai(self) -> None:
        """Toggle focus between editor and AI chat input (Ctrl+Shift+Space)."""
        if self.editor.hasFocus():
            self.ai_input.setFocus()
            self.ai_input.selectAll()
        else:
            self.editor.setFocus()

    def _create_ai_chat_panel(self) -> QWidget:
        """Create AI chat panel with message input and send button."""
        panel = QWidget()
        panel.setStyleSheet(
            f"QWidget {{ background-color: {theme_value('mermaid_editor.chat_panel.bg', '#1e1e1e')}; "
            f"border-top: 1px solid {theme_value('mermaid_editor.chat_panel.border', '#444444')}; }} "
            f"QLineEdit {{ background-color: {theme_value('mermaid_editor.chat_panel.input_bg', '#2d2d2d')}; "
            f"color: {theme_value('mermaid_editor.chat_panel.input_text', '#e0e0e0')}; border: 1px solid "
            f"{theme_value('mermaid_editor.chat_panel.input_border', '#444444')}; }}"
        )
        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.ai_input = ChatLineEdit(self._ai_prompt_history)
        self.ai_input.setPlaceholderText("Describe the diagram you want to generate or update...")
        self.ai_input.sendRequested.connect(self._on_ai_send)
        font_metrics = self.ai_input.fontMetrics()
        line_height = font_metrics.lineSpacing()
        self.ai_input.setFixedHeight(line_height * 3)
        layout.addWidget(self.ai_input)

        self.ai_send_btn = QToolButton()
        self.ai_send_btn.setText("Send")
        self.ai_send_btn.setToolTip("Send message to AI (Ctrl+Enter)")
        self.ai_send_btn.clicked.connect(self._on_ai_send)
        self.ai_send_btn.setFixedHeight(line_height * 3)
        layout.addWidget(self.ai_send_btn)

        panel.setLayout(layout)
        return panel

    def _load_ai_servers_models(self) -> None:
        """Load available servers and models from configuration."""
        try:
            from sp.app.ui.ai_chat_panel import get_available_models

            mgr = ServerManager()
            servers = mgr.load_servers()
            server_names = [srv["name"] for srv in servers]

            self.ai_server_combo.clear()
            self.ai_server_combo.addItems(server_names)

            try:
                default_server = config.load_default_ai_server()
                if default_server and default_server in server_names:
                    self.ai_server_combo.setCurrentText(default_server)
                elif server_names:
                    self.ai_server_combo.setCurrentIndex(0)
            except Exception:
                if server_names:
                    self.ai_server_combo.setCurrentIndex(0)

            self._refresh_ai_models()
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] Failed to load AI servers: {exc}")

    def _on_ai_server_changed(self, server_name: str) -> None:
        self._refresh_ai_models()

    def _refresh_ai_models(self) -> None:
        try:
            from sp.app.ui.ai_chat_panel import get_available_models

            mgr = ServerManager()
            server_name = self.ai_server_combo.currentText()
            if not server_name:
                self.ai_model_combo.clear()
                return

            server = mgr.get_server(server_name)
            if not server:
                self.ai_model_combo.clear()
                return

            models = get_available_models(server)
            self.ai_model_combo.clear()
            self.ai_model_combo.addItems(models)

            try:
                default_model = config.load_default_ai_model()
                if default_model and default_model in models:
                    self.ai_model_combo.setCurrentText(default_model)
                elif models:
                    self.ai_model_combo.setCurrentIndex(0)
            except Exception:
                if models:
                    self.ai_model_combo.setCurrentIndex(0)
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] Failed to refresh AI models: {exc}")

    def _on_ai_send(self) -> None:
        user_message = self.ai_input.text().strip()
        if not user_message:
            return

        self.ai_input.clear()
        self.ai_input.setEnabled(False)
        self.ai_send_btn.setEnabled(False)

        self.statusBar().showMessage("AI is thinking...", 0)
        QApplication.setOverrideCursor(Qt.BusyCursor)

        try:
            if not self._ai_prompt_history or self._ai_prompt_history[-1] != user_message:
                self._ai_prompt_history.append(user_message)
            if hasattr(self.ai_input, "_history_index"):
                self.ai_input._history_index = None
        except Exception:
            pass

        try:
            server_name = self.ai_server_combo.currentText()
            model_name = self.ai_model_combo.currentText()

            if not server_name or not model_name:
                QMessageBox.warning(self, "Error", "Please select a server and model")
                self.ai_input.setEnabled(True)
                self.ai_send_btn.setEnabled(True)
                return

            try:
                mgr = ServerManager()
                server_config = mgr.get_server(server_name)
                if not server_config:
                    QMessageBox.warning(self, "Error", f"Server '{server_name}' not found")
                    self.ai_input.setEnabled(True)
                    self.ai_send_btn.setEnabled(True)
                    return
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Failed to get server config: {exc}")
                self.ai_input.setEnabled(True)
                self.ai_send_btn.setEnabled(True)
                return

            try:
                prompt_path = Path(__file__).resolve().parents[1] / "mmd_prompt.txt"
                system_prompt = prompt_path.read_text(encoding="utf-8")
            except Exception:
                system_prompt = "You are a helpful assistant. You generate Mermaid diagrams."

            editor_content = self.editor.toPlainText()
            user_content = f"Current diagram:\n```\n{editor_content}\n```\n\nUser request: {user_message}"
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ]

            worker = ApiWorker(server_config, messages, model_name, stream=True, parent=self)
            self._ai_worker = worker
            self._ai_response_buffer = ""
            worker.chunk.connect(self._on_ai_response_chunk)
            worker.finished.connect(self._on_ai_response_finished)
            worker.failed.connect(self._on_ai_response_failed)
            worker.start()
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] AI send error: {exc}")
            QMessageBox.critical(self, "Error", f"AI request failed: {exc}")
            self.ai_input.setEnabled(True)
            self.ai_send_btn.setEnabled(True)

    def _on_ai_response_chunk(self, chunk: str) -> None:
        try:
            if not hasattr(self, "_ai_response_buffer"):
                self._ai_response_buffer = ""
            self._ai_response_buffer += chunk or ""
            if _LOGGING and chunk:
                print(f"[Mermaid] Received chunk: {len(chunk)} chars")
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] Chunk error: {exc}")

    def _on_ai_response_finished(self, content: str) -> None:
        QApplication.restoreOverrideCursor()
        self.ai_input.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        self.statusBar().showMessage("AI response received", 3000)

        try:
            response = self._ai_response_buffer or content or ""
            if _LOGGING:
                print(f"[Mermaid] Response finished. Buffer len: {len(self._ai_response_buffer)}, Content len: {len(content)}, Final response len: {len(response)}")
            if not response:
                return

            if "```mermaid" in response.lower():
                start = response.lower().find("```mermaid") + 10
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()
            elif "```" in response:
                start = response.find("```") + 3
                end = response.find("```", start)
                if end > start:
                    response = response[start:end].strip()

            if _LOGGING:
                print(f"[Mermaid] Extracted response len: {len(response)}")

            self._show_ai_response_dialog(response)
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] Response finish error: {exc}")
        finally:
            try:
                self._ai_worker = None
            except Exception:
                pass

    def _on_ai_response_failed(self, message: str) -> None:
        QApplication.restoreOverrideCursor()
        self.ai_input.setEnabled(True)
        self.ai_send_btn.setEnabled(True)
        self.statusBar().showMessage("AI request failed", 3000)
        QMessageBox.warning(self, "AI Error", f"Failed to get AI response: {message}")
        try:
            self._ai_worker = None
        except Exception:
            pass

    def _show_ai_response_dialog(self, ai_text: str) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Review AI Generated Diagram - Accept or Decline")
        dialog.setGeometry(50, 50, 1400, 800)
        dialog_bg = theme_value("mermaid_editor.diff_dialog.bg", "#1e1e1e")
        dialog_label = theme_value("mermaid_editor.diff_dialog.label", "#e0e0e0")
        button_bg = theme_value("mermaid_editor.diff_dialog.button_bg", "#2d2d2d")
        button_text = theme_value("mermaid_editor.diff_dialog.button_text", "#e0e0e0")
        button_border = theme_value("mermaid_editor.diff_dialog.button_border", "#444444")
        button_hover = theme_value("mermaid_editor.diff_dialog.button_hover", "#3d3d3d")
        button_weight = theme_value("mermaid_editor.diff_dialog.button_weight", "bold")
        dialog.setStyleSheet(
            f"""
            QDialog {{
                background-color: {dialog_bg};
            }}
            QLabel {{
                color: {dialog_label};
            }}
            QPushButton {{
                background-color: {button_bg};
                color: {button_text};
                border: 1px solid {button_border};
                padding: 8px 16px;
                font-weight: {button_weight};
            }}
            QPushButton:hover {{
                background-color: {button_hover};
            }}
        """
        )

        layout = QVBoxLayout()

        title = QLabel("Review Changes - Left: Current | Right: AI Generated")
        title_color = theme_value("mermaid_editor.diff_dialog.title_color", "#e0e0e0")
        title_weight = theme_value("mermaid_editor.diff_dialog.title_weight", "bold")
        title_size = theme_value("mermaid_editor.diff_dialog.title_size_px", 12)
        title.setStyleSheet(
            f"color: {title_color}; font-weight: {title_weight}; font-size: {title_size}px; padding: 5px;"
        )
        layout.addWidget(title)

        diff_layout = QHBoxLayout()

        left_panel = QWidget()
        left_layout = QVBoxLayout()
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_label = QLabel("Current Mermaid")
        label_weight = theme_value("mermaid_editor.diff_dialog.label_weight", "bold")
        left_label.setStyleSheet(f"color: {dialog_label}; font-weight: {label_weight};")
        left_layout.addWidget(left_label)

        original_text = self.editor.toPlainText()
        left_display = self._create_diff_display(original_text, ai_text, is_original=True)
        left_layout.addWidget(left_display)
        left_panel.setLayout(left_layout)

        right_panel = QWidget()
        right_layout = QVBoxLayout()
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_label = QLabel("AI Generated Mermaid")
        right_label.setStyleSheet(f"color: {dialog_label}; font-weight: {label_weight};")
        right_layout.addWidget(right_label)

        right_display = self._create_diff_display(original_text, ai_text, is_original=False)
        right_layout.addWidget(right_display)
        right_panel.setLayout(right_layout)

        diff_layout.addWidget(left_panel, stretch=1)
        diff_layout.addWidget(right_panel, stretch=1)
        layout.addLayout(diff_layout, stretch=1)

        button_layout = QHBoxLayout()
        button_layout.addStretch()

        accept_btn = QPushButton("Accept Changes")
        accept_btn.setMinimumWidth(150)
        accept_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {theme_value('mermaid_editor.diff_dialog.accept_bg', '#1e5c1e')};
                color: {theme_value('mermaid_editor.diff_dialog.accept_text', '#7CFC98')};
                border: 1px solid {theme_value('mermaid_editor.diff_dialog.accept_border', '#4caf50')};
                padding: 8px 16px;
                font-weight: {button_weight};
            }}
            QPushButton:hover {{
                background-color: {theme_value('mermaid_editor.diff_dialog.accept_hover', '#2d7a2d')};
            }}
        """
        )
        accept_btn.clicked.connect(lambda: self._accept_ai_response(ai_text, dialog))
        button_layout.addWidget(accept_btn)

        decline_btn = QPushButton("Decline")
        decline_btn.setMinimumWidth(150)
        decline_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {theme_value('mermaid_editor.diff_dialog.decline_bg', '#5c1e1e')};
                color: {theme_value('mermaid_editor.diff_dialog.decline_text', '#ff6b6b')};
                border: 1px solid {theme_value('mermaid_editor.diff_dialog.decline_border', '#f44336')};
                padding: 8px 16px;
                font-weight: {button_weight};
            }}
            QPushButton:hover {{
                background-color: {theme_value('mermaid_editor.diff_dialog.decline_hover', '#7a2d2d')};
            }}
        """
        )
        decline_btn.clicked.connect(dialog.reject)
        button_layout.addWidget(decline_btn)

        layout.addLayout(button_layout)
        dialog.setLayout(layout)

        self._active_diff_dialog = dialog
        dialog.exec()

    def _create_diff_display(self, original: str, modified: str, is_original: bool) -> QTextEdit:
        display = QTextEdit()
        display.setReadOnly(True)
        display.setStyleSheet(
            f"""
            QTextEdit {{
                background-color: {theme_value('mermaid_editor.diff_display.bg', '#2d2d2d')};
                color: {theme_value('mermaid_editor.diff_display.text', '#e0e0e0')};
                border: 1px solid {theme_value('mermaid_editor.diff_display.border', '#444444')};
                padding: 5px;
                font-family: {theme_value('mermaid_editor.diff_display.font_family', 'Courier, monospace')};
                font-size: {theme_value('mermaid_editor.diff_display.font_size_px', 10)}px;
            }}
        """
        )

        if is_original:
            display.setPlainText(original)
            self._highlight_diff_lines(display, original, modified, is_added=False)
        else:
            display.setPlainText(modified)
            self._highlight_diff_lines(display, original, modified, is_added=True)

        return display

    def _highlight_diff_lines(self, text_edit: QTextEdit, original: str, modified: str, is_added: bool) -> None:
        from PySide6.QtGui import QTextCharFormat, QBrush
        from PySide6.QtGui import QTextCursor as QtgQTextCursor
        from PySide6.QtGui import QColor

        original_lines = original.splitlines()
        modified_lines = modified.splitlines()

        cursor = text_edit.textCursor()
        cursor.movePosition(QtgQTextCursor.MoveOperation.Start)

        if is_added:
            for i, line in enumerate(modified_lines):
                if i < len(original_lines):
                    if line != original_lines[i]:
                        fmt = QTextCharFormat()
                        fmt.setBackground(QBrush(theme_color("mermaid_editor.diff.changed_bg", "#645000")))
                        cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                        cursor.setCharFormat(fmt)
                else:
                    fmt = QTextCharFormat()
                    fmt.setBackground(QBrush(theme_color("mermaid_editor.diff.added_bg", "#005000")))
                    cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt)

                if not cursor.movePosition(QtgQTextCursor.MoveOperation.Down):
                    break
        else:
            for i, line in enumerate(original_lines):
                if i < len(modified_lines):
                    if line != modified_lines[i]:
                        fmt = QTextCharFormat()
                        fmt.setBackground(QBrush(theme_color("mermaid_editor.diff.changed_bg", "#645000")))
                        cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                        cursor.setCharFormat(fmt)
                else:
                    fmt = QTextCharFormat()
                    fmt.setBackground(QBrush(theme_color("mermaid_editor.diff.removed_bg", "#500000")))
                    cursor.select(QtgQTextCursor.SelectionType.LineUnderCursor)
                    cursor.setCharFormat(fmt)

                if not cursor.movePosition(QtgQTextCursor.MoveOperation.Down):
                    break

    def _accept_ai_response(self, ai_text: str, dialog: QDialog) -> None:
        try:
            self.editor.setPlainText(ai_text)
            self._save_file()
            self._render()
            dialog.accept()
        except Exception as exc:
            if _LOGGING:
                print(f"[Mermaid] Accept error: {exc}")
            QMessageBox.critical(self, "Error", f"Failed to accept response: {exc}")

    def _on_vi_insert_state_changed(self, insert_active: bool) -> None:
        self._vi_insert_active = bool(insert_active)
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
        style = self._vi_badge_base_style
        if self._vi_enabled:
            if insert_active:
                style += (
                    " background-color: "
                    f"{theme_value('mermaid_editor.vi_badge.active_bg', '#ffd54d')}; "
                    "color: "
                    f"{theme_value('mermaid_editor.vi_badge.active_text', '#000000')};"
                )
            else:
                style += (
                    " background-color: transparent; color: "
                    f"{theme_value('mermaid_editor.vi_badge.inactive_text', '#e0e0e0')};"
                )
        else:
            style += (
                " background-color: transparent; color: "
                f"{theme_value('mermaid_editor.vi_badge.inactive_text', '#e0e0e0')};"
            )
        self._vi_status_label.setStyleSheet(style)

    def _on_auto_render_toggled(self, checked: bool) -> None:
        self._auto_render_enabled = checked
        config.save_mermaid_auto_render(checked)
        self._update_render_status_label()

    def _on_mermaid_theme_changed(self, index: int) -> None:
        if index < 0:
            return
        value = self.preview_theme_combo.itemData(index)
        if not isinstance(value, str) or not value:
            value = "neutral"
        self._mermaid_theme = value
        try:
            config.save_mermaid_render_theme(value)
        except Exception:
            pass
        self._render()

    def _update_render_status_label(self) -> None:
        if not hasattr(self, 'render_status_label'):
            return
        if not self._auto_render_enabled and self._editor_dirty:
            self.render_status_label.setText("Ctrl+Enter to render...")
        else:
            self.render_status_label.setText("")

    def _restore_geometry_prefs(self) -> None:
        try:
            geom64 = config.load_mermaid_window_geometry()
            if geom64:
                self.restoreGeometry(QByteArray.fromBase64(geom64.encode("utf-8")))
        except Exception:
            pass
        try:
            if not self._external_browser_preview:
                hstate64 = config.load_mermaid_hsplit_state()
                if hstate64:
                    self.editor_preview_splitter.restoreState(QByteArray.fromBase64(hstate64.encode("utf-8")))
        except Exception:
            pass
        try:
            vstate64 = config.load_mermaid_vsplit_state()
            if vstate64 and hasattr(self, "_vertical_splitter") and self._vertical_splitter:
                self._vertical_splitter.restoreState(QByteArray.fromBase64(vstate64.encode("utf-8")))
        except Exception:
            pass

    def _save_geometry_prefs(self) -> None:
        try:
            g = self.saveGeometry().toBase64().data().decode("utf-8")
            config.save_mermaid_window_geometry(g)
        except Exception:
            pass
        try:
            if not self._external_browser_preview:
                h = self.editor_preview_splitter.saveState().toBase64().data().decode("utf-8")
                config.save_mermaid_hsplit_state(h)
        except Exception:
            pass
        try:
            if hasattr(self, "_vertical_splitter") and self._vertical_splitter:
                v = self._vertical_splitter.saveState().toBase64().data().decode("utf-8")
                config.save_mermaid_vsplit_state(v)
        except Exception:
            pass

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()
        if hasattr(self, 'loading_overlay') and self.loading_overlay is not None and self.loading_overlay.isVisible():
            parent = self.loading_overlay.parent()
            if parent:
                self.loading_overlay.setGeometry(0, 0, parent.width(), parent.height())

    def moveEvent(self, event) -> None:  # type: ignore[override]
        super().moveEvent(event)
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._closing = True
        try:
            if self._is_dirty():
                self._save_file()
        except Exception:
            pass
        try:
            self.render_timer.stop()
        except Exception:
            pass
        try:
            if hasattr(self, "_geom_timer"):
                self._geom_timer.stop()
        except Exception:
            pass
        self._save_geometry_prefs()
        return super().closeEvent(event)

    def _load_file(self) -> None:
        if self.file_path.exists():
            try:
                content = self.file_path.read_text(encoding="utf-8")
                self.editor.setPlainText(content)
                self._last_saved_content = content
                self._editor_dirty = False
                self._update_render_status_label()
            except Exception as exc:
                QMessageBox.warning(self, "Error", f"Failed to load file: {exc}")
        else:
            template = """flowchart TD
  A[Start] --> B[End]
"""
            self.editor.setPlainText(template)
            self._last_saved_content = ""
            self._editor_dirty = True
            self._update_render_status_label()

    def _save_file(self) -> None:
        try:
            content = self.editor.toPlainText()
            if self._on_save is not None:
                ok, message = self._on_save(content)
                if not ok:
                    QMessageBox.critical(self, "Error", message or "Failed to save file.")
                    return
            self.file_path.write_text(content, encoding="utf-8")
            self._last_saved_content = content
            self._editor_dirty = False
            self._update_render_status_label()
        except Exception as exc:
            QMessageBox.critical(self, "Error", f"Failed to save file: {exc}")

    def _is_dirty(self) -> bool:
        current = self.editor.toPlainText()
        return current != (self._last_saved_content or "")

    def _on_editor_changed(self) -> None:
        self._editor_dirty = True
        self._update_render_status_label()
        if self._auto_render_enabled and not self._external_browser_preview:
            self.render_timer.start()

    def _render(self) -> None:
        self.render_timer.stop()
        if self._closing:
            return
        if not self._window_shown:
            self._startup_render_pending = True
            self._set_preview_loading_state("Generating preview…")
            return
        if self._render_in_progress:
            self._render_requeued = True
            return

        try:
            if not self or not hasattr(self, 'editor'):
                return
            _ = self.editor
        except RuntimeError:
            return

        self._startup_render_pending = False
        self._render_in_progress = True
        self._render_requeued = False
        self._editor_dirty = False
        self._update_render_status_label()
        self._render_request_token += 1
        token = self._render_request_token
        mermaid_text = self.editor.toPlainText()
        self.render_btn.setText("Generating…")

        if self._external_browser_preview:
            self._render_in_progress = False
            self._render_requeued = False
            self._open_external_browser_preview(mermaid_text)
            self.render_btn.setText("Opened in Browser")
            return

        if self._use_web_preview and self.preview_web is not None:
            self._last_svg = ""
            self._show_loading("Generating preview…")
            html_doc = self._build_mermaid_html(mermaid_text)
            self.preview_web.setHtml(html_doc, self._web_preview_base_url())
            return
        preview_bg = self._preview_background_color()
        self._show_loading("Generating preview…")
        self._run_render_in_background(
            token,
            lambda: self.renderer.render_svg(
                mermaid_text,
                theme=self._mermaid_theme,
                background_color=preview_bg,
            ),
        )

    def _open_external_browser_preview(self, mermaid_text: str) -> None:
        """Render Mermaid using browser runtime in the system browser."""
        try:
            cache_dir = Path.home() / ".stillpoint_cache" / "mermaid_browser_preview"
            cache_dir.mkdir(parents=True, exist_ok=True)
            html_path = cache_dir / f"preview_{int(time.time() * 1000)}.html"
            html_doc = self._build_mermaid_html(mermaid_text)
            html_path.write_text(html_doc, encoding="utf-8")
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(html_path)))
            self.statusBar().showMessage("Opened Mermaid preview in browser", 3000)
        except Exception as exc:
            QMessageBox.critical(self, "Preview Error", f"Failed to open browser preview: {exc}")
            self.render_btn.setText("Render Failed")

    def _on_background_render_finished(self, token: int, result: object, error: object) -> None:
        if self._closing or token != self._render_request_token:
            return

        self._render_in_progress = False
        preview_bg = self._preview_background_color()

        try:
            if error is not None:
                error_svg = _generate_error_svg(f"Mermaid render error\n\n{error}")
                self._last_svg = error_svg
                self.preview_pixmap = self._svg_to_pixmap(error_svg, background_color=preview_bg)
                self._update_preview_display()
                self.render_btn.setText("Render Failed")
                return

            if result and getattr(result, "success", False) and getattr(result, "svg_content", ""):
                self._last_svg = result.svg_content
                self.preview_pixmap = self._svg_to_pixmap(result.svg_content, background_color=preview_bg)

                if self.preview_pixmap:
                    self._update_preview_display()
                    self.render_btn.setText("Render OK")
                    return
                error_svg = _generate_error_svg("Failed to convert Mermaid SVG to image")
                self._last_svg = error_svg
                self.preview_pixmap = self._svg_to_pixmap(error_svg, background_color=preview_bg)
                self._update_preview_display()
                self.render_btn.setText("Render Failed")
                return

            details = "Mermaid preview is unavailable."
            if result and getattr(result, "error_message", ""):
                details = result.error_message
            if result and getattr(result, "stderr", "") and result.stderr != details:
                details = f"{details}\n\nDetails:\n{result.stderr[:500]}"
            error_svg = _generate_error_svg(details)
            self._last_svg = error_svg
            self.preview_pixmap = self._svg_to_pixmap(error_svg, background_color=preview_bg)
            self._update_preview_display()
            self.render_btn.setText("Render Failed")
        finally:
            self._hide_loading()
            if self._render_requeued and not self._closing:
                QTimer.singleShot(0, self._render)

    def _preview_background_color(self) -> str:
        value = theme_value("mermaid_editor.preview.bg", "#ffffff")
        if isinstance(value, str) and value.strip():
            return value.strip()
        return "#ffffff"

    def _svg_to_pixmap(self, svg_content: str, *, background_color: Optional[str] = None) -> Optional[QPixmap]:
        try:
            from PySide6.QtSvg import QSvgRenderer

            renderer = QSvgRenderer(QByteArray(svg_content.encode("utf-8")))
            if not renderer.isValid():
                return None
            size = renderer.defaultSize()
            if not size.isValid():
                size = QSize(800, 600)
            pixmap = QPixmap(size)
            bg = QColor(background_color or self._preview_background_color())
            pixmap.fill(bg if bg.isValid() else Qt.white)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.end()
            return pixmap
        except Exception:
            return None

    def _update_preview_display(self) -> None:
        if self._use_web_preview:
            self._apply_web_zoom_level()
            return
        try:
            if not self or not hasattr(self, 'preview_label') or not self.preview_pixmap:
                return
            _ = self.preview_label
        except RuntimeError:
            return

        try:
            self.preview_label.setText("")
        except Exception:
            pass
        zoom_factor = 1.0 + (self.preview_zoom_level * 0.1)
        size = self.preview_pixmap.size()
        new_size = QSize(int(size.width() * zoom_factor), int(size.height() * zoom_factor))
        scaled_pixmap = self.preview_pixmap.scaledToWidth(
            new_size.width(),
            Qt.SmoothTransformation
        )
        try:
            self.preview_label.setMinimumSize(scaled_pixmap.size())
            self.preview_label.resize(scaled_pixmap.size())
            self.preview_label.setPixmap(scaled_pixmap)
        except RuntimeError:
            pass

    def _show_preview_error(self, message: str) -> None:
        if self._use_web_preview and self.preview_web is not None:
            self.preview_web.setHtml(self._build_mermaid_html("", error_message=message), self._web_preview_base_url())
            return
        try:
            if not self or not hasattr(self, "preview_label"):
                return
            _ = self.preview_label
        except RuntimeError:
            return
        try:
            self.preview_label.setPixmap(QPixmap())
            self.preview_label.setMinimumSize(400, 300)
            self.preview_label.setText(message)
        except Exception:
            pass

    def _zoom_in_editor(self) -> None:
        self.editor_zoom_level += 1
        font = self.editor.font()
        font.setPointSize(11 + self.editor_zoom_level)
        self.editor.setFont(font)
        try:
            config.save_mermaid_editor_zoom(self.editor_zoom_level)
        except Exception:
            pass
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()

    def _zoom_out_editor(self) -> None:
        if self.editor_zoom_level > -5:
            self.editor_zoom_level -= 1
            font = self.editor.font()
            font.setPointSize(11 + self.editor_zoom_level)
            self.editor.setFont(font)
            try:
                config.save_mermaid_editor_zoom(self.editor_zoom_level)
            except Exception:
                pass
            if hasattr(self, "_geom_timer"):
                self._geom_timer.start()

    def _zoom_in_preview(self) -> None:
        self.preview_zoom_level += 1
        try:
            self._update_preview_display()
        except RuntimeError:
            pass
        try:
            config.save_mermaid_preview_zoom(self.preview_zoom_level)
        except Exception:
            pass
        if hasattr(self, "_geom_timer"):
            self._geom_timer.start()

    def _zoom_out_preview(self) -> None:
        if self.preview_zoom_level > -10:
            self.preview_zoom_level -= 1
            try:
                self._update_preview_display()
            except RuntimeError:
                pass
            try:
                config.save_mermaid_preview_zoom(self.preview_zoom_level)
            except Exception:
                pass
            if hasattr(self, "_geom_timer"):
                self._geom_timer.start()

    def _on_preview_wheel_zoom(self, delta: int) -> None:
        if delta > 0:
            self._zoom_in_preview()
        else:
            self._zoom_out_preview()

    def _show_export_menu(self) -> None:
        menu = QMenu(self)
        apply_menu_theme(menu, self)

        export_svg = menu.addAction("Export as SVG...")
        export_svg.triggered.connect(self._export_svg)

        export_png = menu.addAction("Export as PNG...")
        export_png.triggered.connect(self._export_png)

        menu.addSeparator()

        copy_svg = menu.addAction("Copy SVG")
        copy_svg.triggered.connect(self._copy_svg)

        copy_png = menu.addAction("Copy PNG")
        copy_png.triggered.connect(self._copy_png)

        menu.exec(self.export_btn.mapToGlobal(self.export_btn.rect().bottomLeft()))

    def _show_preview_context_menu(self, pos) -> None:
        menu = QMenu(self)
        apply_menu_theme(menu, self)
        copy_svg = menu.addAction("Copy SVG")
        copy_svg.triggered.connect(self._copy_svg)
        copy_png = menu.addAction("Copy PNG")
        copy_png.triggered.connect(self._copy_png)
        if self._use_web_preview and self.preview_web is not None:
            menu.exec(self.preview_web.mapToGlobal(pos))
        else:
            menu.exec(self.preview_label.mapToGlobal(pos))

    def _export_svg(self) -> None:
        if not self._ensure_svg():
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as SVG",
            str(self.file_path.with_suffix(".svg")),
            "SVG Files (*.svg)"
        )

        if file_path:
            try:
                Path(file_path).write_text(self._last_svg, encoding="utf-8")
                QMessageBox.information(self, "Exported", f"Saved to {file_path}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Failed to export: {exc}")

    def _export_png(self) -> None:
        if self._external_browser_preview:
            result = self.renderer.render_png(
                self.editor.toPlainText(),
                theme=self._mermaid_theme,
                background_color=self._preview_background_color(),
            )
            pixmap = None
            if result.success and result.png_bytes:
                pixmap = QPixmap()
                pixmap.loadFromData(result.png_bytes, "PNG")
        elif self._use_web_preview and self.preview_web is not None:
            pixmap = self.preview_web.grab()
        else:
            pixmap = self.preview_pixmap

        if not pixmap:
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export as PNG",
            str(self.file_path.with_suffix(".png")),
            "PNG Files (*.png)"
        )

        if file_path:
            try:
                pixmap.save(file_path, "PNG")
                QMessageBox.information(self, "Exported", f"Saved to {file_path}")
            except Exception as exc:
                QMessageBox.critical(self, "Error", f"Failed to export: {exc}")

    def _copy_svg(self) -> None:
        if not self._ensure_svg():
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self._last_svg)
            self.statusBar().showMessage("SVG copied to clipboard", 2000)

    def _ensure_svg(self) -> bool:
        if hasattr(self, "_last_svg") and self._last_svg:
            return True
        if self._external_browser_preview:
            result = self.renderer.render_svg(
                self.editor.toPlainText(),
                theme=self._mermaid_theme,
                background_color=self._preview_background_color(),
            )
            if result.success and result.svg_content:
                self._last_svg = result.svg_content
                return True
            self._show_preview_error("Mermaid Error\n\nUnable to generate SVG for export.")
            return False
        if self._use_web_preview and self.preview_web is not None:
            svg = self._get_svg_from_web_preview()
            if svg:
                self._last_svg = svg
                return True
            self._show_preview_error("Mermaid Error\n\nUnable to read SVG from web preview.")
            return False
        self._show_preview_error("Mermaid web preview is unavailable. Install/enable Qt WebEngine.")
        return False

    def _copy_png(self) -> None:
        if self._external_browser_preview:
            result = self.renderer.render_png(
                self.editor.toPlainText(),
                theme=self._mermaid_theme,
                background_color=self._preview_background_color(),
            )
            pixmap = None
            if result.success and result.png_bytes:
                pixmap = QPixmap()
                pixmap.loadFromData(result.png_bytes, "PNG")
        elif self._use_web_preview and self.preview_web is not None:
            pixmap = self.preview_web.grab()
        else:
            pixmap = self.preview_pixmap
        if not pixmap:
            QMessageBox.warning(self, "No Diagram", "Render the diagram first.")
            return
        clipboard = QApplication.clipboard()
        if clipboard:
            buffer = QBuffer()
            buffer.open(QIODevice.WriteOnly)
            pixmap.save(buffer, "PNG")
            png_bytes = bytes(buffer.data())
            mime = QMimeData()
            mime.setData("image/png", png_bytes)
            mime.setImageData(pixmap.toImage())
            clipboard.setMimeData(mime)
            self.statusBar().showMessage("PNG copied to clipboard", 2000)

    def _apply_web_zoom_level(self) -> None:
        if not self._use_web_preview or self.preview_web is None:
            return
        zoom = max(0.2, min(4.0, 1.0 + (self.preview_zoom_level * 0.1)))
        script = f"if (window.spSetZoom) window.spSetZoom({zoom});"
        self.preview_web.page().runJavaScript(script)

    def _get_svg_from_web_preview(self) -> Optional[str]:
        if not self._use_web_preview or self.preview_web is None:
            return None
        loop = QEventLoop(self)
        holder = {"done": False, "value": None}

        def _callback(value):
            holder["done"] = True
            holder["value"] = value
            loop.quit()

        self.preview_web.page().runJavaScript(
            "window.spGetSvg ? window.spGetSvg() : '';",
            _callback,
        )
        QTimer.singleShot(1500, loop.quit)
        loop.exec()
        value = holder.get("value")
        if isinstance(value, str) and "<svg" in value:
            return value
        return None

    def _build_mermaid_html(self, mermaid_text: str, error_message: str = "") -> str:
        preview_bg = theme_value('mermaid_editor.preview.bg', '#f8f8f8')
        preview_text = theme_value('mermaid_editor.preview.text', '#000000')
        mermaid_theme = json.dumps(self._mermaid_theme)
        escaped_source = json.dumps(mermaid_text)
        escaped_error = json.dumps(error_message)
        error_bg = json.dumps(theme_value("mermaid_editor.error.bg", "#ffffff"))
        error_title = json.dumps(theme_value("mermaid_editor.error.title", "#cc0000"))
        error_msg = json.dumps(theme_value("mermaid_editor.error.message", "#333333"))
        error_box_fill = json.dumps(theme_value("mermaid_editor.error.box_fill", "#ffe6e6"))
        error_box_stroke = json.dumps(theme_value("mermaid_editor.error.box_stroke", "#ff9999"))
        error_box_text = json.dumps(theme_value("mermaid_editor.error.box_text", "#333333"))
        error_font = json.dumps(theme_value("mermaid_editor.error.font_family", "monospace"))
        error_title_size = int(theme_value("mermaid_editor.error.title_size_px", 24))
        error_title_weight = json.dumps(theme_value("mermaid_editor.error.title_weight", "bold"))
        error_box_font_size = int(theme_value("mermaid_editor.error.box_font_size_px", 13))
        error_box_line_height = float(theme_value("mermaid_editor.error.box_line_height", 1.4))
        initial_zoom = max(0.2, min(4.0, 1.0 + (self.preview_zoom_level * 0.1)))
        local_mermaid_js = json.dumps((Path(__file__).resolve().parents[2] / "assets" / "vendor" / "mermaid.min.js").as_uri())
        return f"""<!doctype html>
<html>
<head>
    <meta charset=\"utf-8\" />
    <style>
        html, body {{ margin: 0; padding: 0; width: 100%; height: 100%; background: {preview_bg}; color: {preview_text}; overflow: hidden; }}
        #viewport {{ width: 100%; height: 100%; overflow: hidden; cursor: default; user-select: none; }}
        #diagram-host {{ width: 100%; height: 100%; display: flex; align-items: center; justify-content: center; }}
        #diagram-host svg {{ max-width: none; max-height: none; transform-origin: 0 0; }}
    </style>
    <script>
        let viewport = null;
        let host = null;
        const state = {{ zoom: {initial_zoom}, tx: 0, ty: 0, panning: false, panX: 0, panY: 0 }};
        const errorTheme = {{
            bg: {error_bg},
            title: {error_title},
            message: {error_msg},
            boxFill: {error_box_fill},
            boxStroke: {error_box_stroke},
            boxText: {error_box_text},
            fontFamily: {error_font},
            titleSize: {error_title_size},
            titleWeight: {error_title_weight},
            boxFontSize: {error_box_font_size},
            boxLineHeight: {error_box_line_height}
        }};

        function currentSvg() {{
            return host ? host.querySelector('svg') : null;
        }}

        function escapeHtml(value) {{
            return String(value)
                .replace(/&/g, '&amp;')
                .replace(/</g, '&lt;')
                .replace(/>/g, '&gt;');
        }}

        function buildErrorSvg(message, title = 'Mermaid Render Error') {{
            const safeMessage = escapeHtml(message || 'Unknown Mermaid render error').replace(/\\n/g, '<br/>');
            return `
                <svg width="800" height="400" viewBox="0 0 800 400" xmlns="http://www.w3.org/2000/svg">
                    <rect width="800" height="400" fill="${{errorTheme.bg}}"/>
                    <defs>
                        <style type="text/css"><![CDATA[
                            .error-title {{ font-size: ${{errorTheme.titleSize}}px; font-weight: ${{errorTheme.titleWeight}}; fill: ${{errorTheme.title}}; font-family: ${{errorTheme.fontFamily}}; }}
                            .error-box {{ fill: ${{errorTheme.boxFill}}; stroke: ${{errorTheme.boxStroke}}; stroke-width: 2; }}
                        ]]></style>
                    </defs>
                    <rect class="error-box" x="20" y="20" width="760" height="360" rx="5" ry="5"/>
                    <text class="error-title" x="40" y="60">${{escapeHtml(title)}}</text>
                    <foreignObject x="40" y="90" width="720" height="270">
                        <div xmlns="http://www.w3.org/1999/xhtml"
                             style="font-family: ${{errorTheme.fontFamily}}; font-size: ${{errorTheme.boxFontSize}}px; color: ${{errorTheme.boxText}}; white-space: pre-wrap; word-break: break-word; line-height: ${{errorTheme.boxLineHeight}};">
                            ${{safeMessage}}
                        </div>
                    </foreignObject>
                </svg>
            `;
        }}

        function applyTransform() {{
            const svg = currentSvg();
            if (!svg) return;
            svg.style.transform = `translate(${{state.tx}}px, ${{state.ty}}px) scale(${{state.zoom}})`;
        }}

        function zoomAbout(delta, cx, cy) {{
            const oldZoom = state.zoom;
            const nextZoom = Math.max(0.2, Math.min(4.0, oldZoom + (0.1 * delta)));
            if (Math.abs(nextZoom - oldZoom) < 1e-9) return;
            state.tx = cx - ((cx - state.tx) * (nextZoom / oldZoom));
            state.ty = cy - ((cy - state.ty) * (nextZoom / oldZoom));
            state.zoom = nextZoom;
            applyTransform();
        }}

        function loadScript(url) {{
            return new Promise((resolve, reject) => {{
                const script = document.createElement('script');
                script.src = url;
                script.async = true;
                script.onload = () => resolve(true);
                script.onerror = () => reject(new Error(`Failed to load script: ${{url}}`));
                document.head.appendChild(script);
            }});
        }}

        async function ensureMermaid() {{
            if (window.mermaid) return window.mermaid;
            try {{
                await loadScript({local_mermaid_js});
            }} catch (_localErr) {{
                await loadScript('https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js');
            }}
            if (!window.mermaid) throw new Error('Mermaid runtime not available.');
            return window.mermaid;
        }}

        window.spGetSvg = () => {{
            const svg = currentSvg();
            return svg ? svg.outerHTML : '';
        }};

        window.spSetZoom = (zoom) => {{
            const next = Math.max(0.2, Math.min(4.0, Number(zoom) || 1.0));
            if (!viewport || !currentSvg()) return;
            const rect = viewport.getBoundingClientRect();
            const cx = rect.width / 2;
            const cy = rect.height / 2;
            const old = state.zoom;
            state.zoom = old;
            zoomAbout((next - old) / 0.1, cx, cy);
        }};

        function bindInteractions() {{
            if (!viewport) return;
            viewport.addEventListener('wheel', (event) => {{
                event.preventDefault();
                const rect = viewport.getBoundingClientRect();
                const cx = event.clientX - rect.left;
                const cy = event.clientY - rect.top;
                zoomAbout(event.deltaY < 0 ? 1 : -1, cx, cy);
            }}, {{ passive: false }});

            viewport.addEventListener('mousedown', (event) => {{
                if (event.button !== 2) return;
                state.panning = true;
                state.panX = event.clientX;
                state.panY = event.clientY;
                viewport.style.cursor = 'grabbing';
                event.preventDefault();
            }});

            window.addEventListener('mousemove', (event) => {{
                if (!state.panning) return;
                const dx = event.clientX - state.panX;
                const dy = event.clientY - state.panY;
                state.panX = event.clientX;
                state.panY = event.clientY;
                state.tx += dx;
                state.ty += dy;
                applyTransform();
            }});

            window.addEventListener('mouseup', (event) => {{
                if (event.button !== 2 || !state.panning) return;
                state.panning = false;
                if (viewport) viewport.style.cursor = 'default';
                event.preventDefault();
            }});

            viewport.addEventListener('contextmenu', (event) => {{
                if (state.panning) event.preventDefault();
            }});
        }}

        async function renderDiagram() {{
            const forcedError = {escaped_error};
            if (forcedError) {{
                if (host) host.innerHTML = buildErrorSvg(forcedError);
                state.tx = 0;
                state.ty = 0;
                state.zoom = {initial_zoom};
                applyTransform();
                return;
            }}
            const source = {escaped_source};
            if (!source || !source.trim()) {{
                if (host) host.innerHTML = buildErrorSvg('Enter Mermaid diagram code here...', 'Mermaid Preview');
                state.tx = 0;
                state.ty = 0;
                state.zoom = {initial_zoom};
                applyTransform();
                return;
            }}
            try {{
                const mermaid = await ensureMermaid();
                mermaid.initialize({{ startOnLoad: false, securityLevel: 'loose', theme: {mermaid_theme} }});
                const id = `sp-mermaid-${{Date.now()}}`;
                const rendered = await mermaid.render(id, source);
                if (host) host.innerHTML = rendered.svg;
                applyTransform();
            }} catch (err) {{
                if (host) host.innerHTML = buildErrorSvg(err && err.message ? err.message : String(err));
                state.tx = 0;
                state.ty = 0;
                state.zoom = {initial_zoom};
                applyTransform();
            }}
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            viewport = document.getElementById('viewport');
            host = document.getElementById('diagram-host');
            bindInteractions();
            renderDiagram();
        }});
    </script>
</head>
<body>
    <div id=\"viewport\">
        <div id=\"diagram-host\"></div>
    </div>
</body>
</html>
"""

    def _web_preview_base_url(self) -> QUrl:
        assets_dir = Path(__file__).resolve().parents[2] / "assets"
        return QUrl.fromLocalFile(str(assets_dir) + os.sep)
