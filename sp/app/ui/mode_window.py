from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from PySide6.QtCore import Qt, QEvent, QPoint, QTimer, Signal, QPropertyAnimation
from PySide6.QtGui import (
    QColor,
    QFont,
    QKeySequence,
    QPainter,
    QPen,
    QShortcut,
    QTextCursor,
    QTextFormat,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from .markdown_editor import MarkdownEditor
from .date_insert_dialog import DateInsertDialog
from .find_replace_bar import FindReplaceBar


class _ClickableLabel(QLabel):
    clicked = Signal()

    def mousePressEvent(self, event):  # type: ignore[override]
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class _CursorHalo(QWidget):
    """Lightweight halo that tracks the editor caret without stealing focus."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WA_NoSystemBackground)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self._diameter = 88
        self.resize(self._diameter, self._diameter)
        self.hide()
        self._mode = "line"

    def set_center(self, center: QPoint) -> None:
        half = self._diameter // 2
        self.move(center.x() - half, center.y() - half)
        self.update()

    def toggle_mode(self) -> None:
        self._mode = "line" if self._mode == "circle" else "circle"
        self.update()

    def set_mode(self, mode: str) -> None:
        if mode in {"line", "circle"}:
            self._mode = mode
        self.update()

    def paintEvent(self, event):  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        color = QColor("#6fc1ff")
        color.setAlpha(60)
        painter.setBrush(color if self._mode == "circle" else Qt.NoBrush)
        painter.setPen(QPen(QColor("#b7e2ff"), 2))
        if self._mode == "circle":
            radius = self._diameter / 2
            painter.drawEllipse(2, 2, int(radius * 2) - 4, int(radius * 2) - 4)
        else:
            painter.drawRoundedRect(0, self.height() // 2 - 12, self.width(), 24, 8, 8)
        painter.end()


class ModeWindow(QMainWindow):
    """Full-screen overlay window for Focus and Audience modes."""

    closed = Signal(str, int)  # Emits the mode label and cursor position when dismissed
    ready = Signal()  # Emits when the overlay has fully initialized

    _HIGHLIGHT_KEY = 7021

    def __init__(
        self,
        mode: str,
        base_editor: MarkdownEditor,
        *,
        vault_root: Optional[str],
        page_path: Optional[str],
        read_only: bool,
        heading_provider: Callable[[], list[dict]],
        settings: Optional[dict] = None,
        initial_cursor: Optional[int] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.mode = mode.lower()
        self._base_editor = base_editor
        self._base_wrap_mode = base_editor.lineWrapMode()
        try:
            self._base_text_wrap = base_editor.document().defaultTextOption().wrapMode()
        except Exception:
            self._base_text_wrap = QTextOption.WrapAtWordBoundaryOrAnywhere
        self._settings = settings or {}
        base_font = base_editor.font()
        self._base_font_family = base_font.family()
        self._base_font_size = float(self._settings.get("font_size", config.load_default_markdown_font_size()))
        try:
            self._doc_default_font = QFont(base_editor.document().defaultFont())
        except Exception:
            self._doc_default_font = QFont(base_font)
        self._base_min_width = base_editor.minimumWidth()
        self._base_max_width = base_editor.maximumWidth()
        try:
            # Qt's default max is QWIDGETSIZE_MAX (typically 16777215); use it as a sane reset value.
            temp_widget = QWidget()
            self._default_max_width = temp_widget.maximumWidth()
            temp_widget.deleteLater()
        except Exception:
            self._default_max_width = 16777215
        self._vault_root = vault_root
        self._page_path = page_path
        self._heading_provider = heading_provider
        self._tools_timer = QTimer(self)
        self._tools_timer.setInterval(1600)
        self._tools_timer.setSingleShot(True)
        self._tools_timer.timeout.connect(self._show_tools)
        self._font_scale = float(self._settings.get("font_scale", 1.0))
        self._line_height_scale = float(self._settings.get("line_height_scale", 1.0))
        self._halo_rect_mode = True
        self._initial_cursor = initial_cursor
        self._scroll_anim: Optional[QPropertyAnimation] = None
        self._ready = False
        self._pending_close = False
        self._shortcuts: list[QShortcut] = []
        # Heading picker state
        self._popup_items: list = []
        self._popup_index: int = -1
        self._popup_mode: Optional[str] = None
        self._heading_popup: Optional[QWidget] = None
        self._heading_popup_label: Optional[QLabel] = None
        self._heading_popup_list: Optional[QListWidget] = None
        try:
            # Suppress link scanning in both overlay and base editors during startup.
            self.editor._suppress_link_scan = True
            self._base_editor._suppress_link_scan = True
            self.editor._suppress_vi_cursor = True
            self._base_editor._suppress_vi_cursor = True
            self.editor._cursor_events_blocked = True
            self._base_editor._cursor_events_blocked = True
            try:
                type(self.editor)._LOAD_GUARD_DEPTH += 1
            except Exception:
                pass
            self.editor._overlay_transition = True
            self._base_editor._overlay_transition = True
            self.editor._overlay_active = True
            self._base_editor._overlay_active = True
        except Exception:
            pass
        QTimer.singleShot(0, lambda: self._flash_cursor_line(self.editor))

        self.setWindowFlag(Qt.FramelessWindowHint, True)
        self.setWindowFlag(Qt.WindowStaysOnTopHint, True)
        self.setWindowModality(Qt.ApplicationModal)
        self.setAttribute(Qt.WA_DeleteOnClose, True)

        self._build_ui(read_only)
        self._wire_shortcuts()
        self._apply_mode_styling()
        self._position_overlays()
        
        # Install event filter to catch Control key release for popup navigation
        self.installEventFilter(self)
        
        QTimer.singleShot(0, self.showFullScreen)
        QTimer.singleShot(0, self._mark_ready)

    # ------------------------------------------------------------------ UI
    def _build_ui(self, read_only: bool) -> None:
        container = QWidget()
        root = QVBoxLayout(container)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(10)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        title_label = QLabel(self._page_title())
        title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        header.addWidget(title_label, 0, Qt.AlignVCenter)
        header.addStretch(1)
        
        # Add audience tools to header if in audience mode
        if self.mode == "audience" and self._settings.get("show_floating_tools", True):
            self._build_audience_tools_in_header(header)
        
        self._header_mode_badge = QLabel(self.mode.upper())
        self._header_mode_badge.setStyleSheet(
            "background: #28384a; color: #e8f1ff; padding: 6px 10px; border-radius: 8px; font-weight: bold;"
        )
        header.addWidget(self._header_mode_badge, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._vi_badge = QLabel("INS")
        self._vi_badge.setVisible(False)
        self._vi_badge.setStyleSheet(
            "border: 1px solid #666; padding: 2px 6px; border-radius: 3px; margin-right: 6px; background: #ffd54d; color: #000;"
        )
        header.addWidget(self._vi_badge, 0, Qt.AlignRight | Qt.AlignVCenter)
        self._close_button = QToolButton()
        self._close_button.setText("✕")
        self._close_button.setToolTip("Exit mode")
        self._close_button.setStyleSheet(
            "QToolButton { background: #1c1f24; color: #f0f3f8; border: 1px solid #3b4251; padding: 6px 10px; border-radius: 10px; }"
            "QToolButton:hover { background: #2a303c; }"
        )
        self._close_button.setEnabled(False)
        self._close_button.clicked.connect(self._request_close)
        header.addWidget(self._close_button, 0, Qt.AlignRight)
        root.addLayout(header)

        self._find_bar = FindReplaceBar(self)
        root.addWidget(self._find_bar)

        editor_host = QWidget()
        editor_host.setObjectName("modeEditorHost")
        host_layout = QVBoxLayout(editor_host)
        host_layout.setContentsMargins(0, 0, 0, 0)
        host_layout.setSpacing(6)

        self.editor = MarkdownEditor()
        # Share the live document so edits stay in sync with the main window.
        self.editor.setDocument(self._base_editor.document())
        self.editor.set_context(self._vault_root, self._page_path)
        try:
            # Detach the overlay highlighter so we don't replace the shared document's highlighter.
            self.editor.highlighter.setDocument(None)
        except Exception:
            pass
        # Drop expensive per-editor textChanged handlers on the overlay to avoid re-entrant processing
        try:
            self.editor.textChanged.disconnect(self.editor._enforce_display_symbols)
            self.editor.textChanged.disconnect(self.editor._schedule_heading_outline)
        except Exception:
            pass
        # Keep lightweight symbol enforcement (bullet/checkbox/heading line transforms) active in the overlay
        try:
            self.editor.textChanged.connect(self.editor._enforce_display_symbols)
        except Exception:
            pass
        self.editor.set_read_only_mode(read_only)
        self.editor.set_vi_mode_enabled(config.load_vi_mode_enabled())
        self.editor.set_vi_block_cursor_enabled(config.load_vi_block_cursor_enabled())
        self._sync_vi_insert_from_base()
        # Custom context menu with Edit operations only
        self.editor.setContextMenuPolicy(Qt.CustomContextMenu)
        self.editor.customContextMenuRequested.connect(self._show_editor_context_menu)
        self.editor.viewport().installEventFilter(self)
        self.editor.cursorPositionChanged.connect(self._on_cursor_moved)
        self.editor.textChanged.connect(self._on_editor_text_changed)
        self.editor.insertDateRequested.connect(self._insert_date)
        self.editor.findBarRequested.connect(self._on_editor_find_requested)
        try:
            self.editor.headingPickerRequested.connect(self._handle_heading_picker_request)
        except Exception:
            pass
        try:
            self.editor.viInsertModeChanged.connect(self._update_vi_badge)
        except Exception:
            pass
        self.editor.setFocusPolicy(Qt.StrongFocus)
        self.editor.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        host_layout.addWidget(self.editor, 1)

        root.addWidget(editor_host, 1)

        # Mode indicator footer
        footer = QHBoxLayout()
        footer.addStretch(1)
        self._mode_indicator = _ClickableLabel(self.mode.upper())
        self._mode_indicator.setToolTip("Exit this mode")
        self._mode_indicator.setStyleSheet(
            "padding: 6px 10px; background: rgba(15, 19, 26, 0.9); color: #eaf1ff; "
            "border: 1px solid rgba(120, 140, 170, 0.7); border-radius: 10px; font-weight: bold;"
        )
        self._mode_indicator.clicked.connect(self._request_close)
        footer.addWidget(self._mode_indicator, 0, Qt.AlignRight)
        root.addLayout(footer)

        self._cursor_halo = _CursorHalo(self.editor.viewport())
        self._cursor_halo.set_mode("line")
        self._find_bar.findNextRequested.connect(self._on_find_next_requested)
        self._find_bar.replaceRequested.connect(self._on_replace_requested)
        self._find_bar.replaceAllRequested.connect(self._on_replace_all_requested)
        self._find_bar.closed.connect(lambda: self.editor.setFocus(Qt.ShortcutFocusReason))

        self.setCentralWidget(container)

    def _build_audience_tools_in_header(self, header: QHBoxLayout) -> None:
        """Add audience mode tools directly to the header bar."""
        def _btn(text: str, tooltip: str, handler):
            btn = QToolButton()
            btn.setText(text)
            btn.setToolTip(tooltip)
            btn.setFocusPolicy(Qt.NoFocus)
            btn.setStyleSheet(
                "QToolButton { padding: 4px 8px; color: #e9eef8; background: rgba(40, 56, 74, 0.7); border: 1px solid #3b4555; border-radius: 6px; font-weight: 600; margin-right: 4px; } "
                "QToolButton:hover { background: rgba(60, 80, 100, 0.9); }"
            )
            btn.clicked.connect(handler)
            header.addWidget(btn, 0, Qt.AlignRight | Qt.AlignVCenter)
            return btn

        _btn("A+", "Increase text size (Ctrl+Alt+=)", lambda: self._adjust_font_scale(0.05))
        _btn("A-", "Decrease text size (Ctrl+Alt+-)", lambda: self._adjust_font_scale(-0.05))
        _btn("#", "Jump to heading", self._jump_to_heading)
        self._highlight_btn = _btn("H", "Toggle paragraph highlight (Ctrl+Alt+H)", self._toggle_paragraph_highlight)
        self._scroll_btn = _btn("S", "Toggle soft auto-scroll (Ctrl+Alt+S)", self._toggle_soft_scroll)

    def _wire_shortcuts(self) -> None:
        def _add_shortcut(seq: QKeySequence | Qt.Key, handler) -> QShortcut:
            sc = QShortcut(QKeySequence(seq), self)
            sc.setContext(Qt.ApplicationShortcut)
            sc.setEnabled(False)  # Enabled once overlay reports ready
            sc.activated.connect(handler)
            self._shortcuts.append(sc)
            return sc

        # Close by repeating the toggle key
        if self.mode == "focus":
            seq = "Ctrl+Alt+F"
        else:
            seq = "Ctrl+Alt+A"
        _add_shortcut(seq, self._request_close)

        if self.mode == "audience":
            _add_shortcut("Ctrl+Alt+=", lambda: self._adjust_font_scale(0.05))
            _add_shortcut("Ctrl+Alt+-", lambda: self._adjust_font_scale(-0.05))
            _add_shortcut("Ctrl+Alt+H", self._toggle_paragraph_highlight)
            _add_shortcut("Ctrl+Alt+S", self._toggle_soft_scroll)
            _add_shortcut("Ctrl+Alt+C", self._toggle_halo_mode)
            _add_shortcut(Qt.Key_Control, self._toggle_halo_mode)
        _add_shortcut("Ctrl+D", self._insert_date)
        _add_shortcut("Ctrl+F", lambda: self._show_find_bar(replace=False, backwards=False))
        _add_shortcut("Ctrl+H", lambda: self._show_find_bar(replace=True, backwards=False))
        _add_shortcut("Ctrl+Shift+Tab", lambda: self._cycle_popup("heading", reverse=False))
        _add_shortcut("Ctrl+Shift+Backtab", lambda: self._cycle_popup("heading", reverse=True))

    # ------------------------------------------------------------------ Behavior
    def _apply_mode_styling(self) -> None:
        base_font: QFont = self._base_editor.font()
        palette_bg = "#0e121a" if self.mode == "focus" else "#0c1017"
        text_color = "#cbd4e6"
        padding = "28px 40px" if self.mode == "focus" else "38px 72px"
        line_height_pct = int(self._line_height_scale * 100)
        scaled_size = int(self._base_font_size * self._font_scale)
        self.editor.setStyleSheet(
            f"QTextEdit {{ background: {palette_bg}; color: {text_color}; padding: {padding};"
            f" border: none; selection-background-color: #2f4c74; line-height: {line_height_pct}%;"
            f" font-family: '{self._base_font_family}'; font-size: {scaled_size}pt; }}"
        )
        self._update_vi_badge(self.editor._vi_insert_mode if hasattr(self.editor, "_vi_insert_mode") else False)
        if self._initial_cursor is not None:
            try:
                cursor = self.editor.textCursor()
                cursor.setPosition(max(0, int(self._initial_cursor)))
                self.editor.setTextCursor(cursor)
                # Defer scroll until after window is fully shown and layout is complete
                QTimer.singleShot(100, lambda: self.editor.ensureCursorVisible())
            except Exception:
                pass

        if self.mode == "focus" and self._settings.get("center_column", True):
            self._apply_center_column(int(self._settings.get("max_column_width_chars", 80)))
        if self.mode == "audience" and self._settings.get("center_column", True):
            self._apply_center_column(int(self._settings.get("max_column_width_chars", 120)))
        if self.mode == "focus" and self._settings.get("paragraph_focus", False):
            self._update_paragraph_highlight()
        if self.mode == "audience" and self._settings.get("paragraph_highlight", True):
            self._update_paragraph_highlight()
        if self.mode == "audience" and self._settings.get("cursor_spotlight", True):
            self._update_cursor_halo()
        else:
            self._cursor_halo.hide()

    def _sync_vi_insert_from_base(self) -> None:
        if not config.load_vi_mode_enabled():
            return
        if not hasattr(self._base_editor, "_vi_insert_mode"):
            return
        if getattr(self._base_editor, "_read_only_mode", False):
            return
        insert_active = bool(getattr(self._base_editor, "_vi_insert_mode", False))
        if insert_active:
            try:
                self.editor._enter_vi_insert_mode()
                return
            except Exception:
                pass
        try:
            self.editor._enter_vi_navigation_mode()
        except Exception:
            try:
                self.editor._handle_vi_escape()
            except Exception:
                pass

    def _sync_vi_insert_to_base(self) -> None:
        if not config.load_vi_mode_enabled():
            return
        if not hasattr(self.editor, "_vi_insert_mode"):
            return
        insert_active = bool(getattr(self.editor, "_vi_insert_mode", False))
        if insert_active and not getattr(self._base_editor, "_read_only_mode", False):
            try:
                self._base_editor._enter_vi_insert_mode()
                return
            except Exception:
                pass
        try:
            self._base_editor._enter_vi_navigation_mode()
        except Exception:
            try:
                self._base_editor._handle_vi_escape()
            except Exception:
                pass

    def _apply_center_column(self, max_chars: int) -> None:
        max_chars = max(40, max_chars)
        metrics = self.editor.fontMetrics()
        width = int(metrics.horizontalAdvance("M") * max_chars) + 48
        self.editor.setMaximumWidth(width)
        self.editor.setMinimumWidth(min(width, 760))
        try:
            self.editor.parentWidget().layout().setAlignment(self.editor, Qt.AlignHCenter)
        except Exception:
            pass

    def _update_paragraph_highlight(self) -> None:
        if not getattr(self, "_ready", False) or self._should_block_highlight():
            return
        # Prevent recursion
        if getattr(self, '_highlight_guard', False):
            return
        self._highlight_guard = True
        enable = (
            (self.mode == "focus" and self._settings.get("paragraph_focus", False))
            or (self.mode == "audience" and self._settings.get("paragraph_highlight", True))
        )
        selections = [s for s in self.editor.extraSelections() if s.format.property(self._HIGHLIGHT_KEY) is None]
        if not enable:
            self.editor.setExtraSelections(selections)
            return
        cursor = self.editor.textCursor()
        try:
            cursor.select(QTextCursor.BlockUnderCursor)
        except Exception:
            return
        extra = QTextEdit.ExtraSelection()
        extra.cursor = cursor
        color = QColor("#24466b") if self.mode == "focus" else QColor("#1d4b80")
        color.setAlpha(110 if self.mode == "audience" else 90)
        extra.format.setForeground(QColor("#f4f7ff"))
        extra.format.setBackground(color)
        extra.format.setProperty(self._HIGHLIGHT_KEY, True)
        try:
            extra.format.setProperty(QTextFormat.FullWidthSelection, True)
        except Exception:
            pass
        selections.append(extra)
        try:
            self.editor.setExtraSelections(selections)
        finally:
            self._highlight_guard = False

    def _update_cursor_halo(self) -> None:
        if self.mode != "audience" or not self._settings.get("cursor_spotlight", True):
            self._cursor_halo.hide()
            return
        rect = self.editor.cursorRect()
        center = rect.center()
        if self._halo_rect_mode:
            half = self._cursor_halo._diameter // 2
            self._cursor_halo.resize(self.editor.viewport().width(), 32)
            self._cursor_halo.move(0, rect.center().y() - 16)
        else:
            self._cursor_halo.resize(self._cursor_halo._diameter, self._cursor_halo._diameter)
            self._cursor_halo.set_center(center)
        self._cursor_halo.show()
        self._cursor_halo.raise_()

    def _should_block_highlight(self) -> bool:
        try:
            if getattr(self.editor, "_cursor_events_blocked", False):
                return True
            if getattr(type(self.editor), "_LOAD_GUARD_DEPTH", 0) > 0:
                return True
        except Exception:
            return True
        return False

    def _show_find_bar(self, *, replace: bool, backwards: bool, seed: Optional[str] = None) -> None:
        query = (seed or "").strip()
        if not query:
            query = self.editor.last_search_query()
        self._find_bar.show_bar(replace=replace, query=query or "", backwards=backwards)

    def _on_editor_find_requested(self, replace_mode: bool, backwards: bool, seed_query: str) -> None:
        self._show_find_bar(replace=replace_mode, backwards=backwards, seed=seed_query)

    def _on_find_next_requested(self, query: str, backwards: bool, case_sensitive: bool) -> None:
        search_query = (query or "").strip() or (self.editor.last_search_query() or "").strip()
        if not search_query:
            self._status_message("Enter text to find.", 2000)
            self._find_bar.focus_query()
            return
        self._find_bar.query_edit.setText(search_query)
        self.editor.search_find_next(search_query, backwards=backwards, wrap=True, case_sensitive=case_sensitive)

    def _on_replace_requested(self, replacement: str) -> None:
        self.editor.search_replace_current(replacement)

    def _on_replace_all_requested(self, query: str, replacement: str, case_sensitive: bool) -> None:
        search_query = query.strip() or self.editor.last_search_query()
        if not search_query:
            self._status_message("Enter text to find.", 2000)
            self._find_bar.focus_query()
            return
        self.editor.search_replace_all(search_query, replacement, case_sensitive=case_sensitive)

    def _soft_autoscroll(self) -> None:
        if not self._ready or self.mode != "audience" or not self._settings.get("soft_autoscroll", True):
            return
        sb = self.editor.verticalScrollBar()
        viewport = self.editor.viewport()
        rect = self.editor.cursorRect()
        if not sb or not viewport:
            return
        if self._scroll_anim and self._scroll_anim.state() == QPropertyAnimation.Running:
            return
        height = viewport.height()
        top_threshold_px = max(2, int(height * 0.05))
        bottom_margin_px = 48  # Keep in sync with MarkdownEditor._ensure_cursor_margin
        bottom_threshold_px = max(top_threshold_px, bottom_margin_px + 4)
        target_value = None
        top_edge = rect.top()
        bottom_edge = height - rect.bottom()
        if bottom_edge < bottom_threshold_px or top_edge < top_threshold_px:
            desired_top = height // 2 - rect.height() // 2
            delta = int(rect.top() - desired_top)
            target_value = sb.value() + delta
        if target_value is None:
            return
        target_value = max(sb.minimum(), min(sb.maximum(), int(target_value)))
        if target_value == sb.value():
            return
        if self._scroll_anim is None:
            self._scroll_anim = QPropertyAnimation(sb, b"value", self)
            self._scroll_anim.setDuration(220)
        elif self._scroll_anim.state() == QPropertyAnimation.Running:
            self._scroll_anim.stop()
        self._scroll_anim.setStartValue(sb.value())
        self._scroll_anim.setEndValue(target_value)
        self._scroll_anim.start()

    def _typewriter_scroll(self) -> None:
        if not self._ready or self.mode != "focus" or not self._settings.get("typewriter_scrolling", False):
            return
        sb = self.editor.verticalScrollBar()
        viewport = self.editor.viewport()
        rect = self.editor.cursorRect()
        if not sb or not viewport:
            return
        target_center = viewport.height() // 2
        delta = rect.center().y() - target_center
        if abs(delta) > 6:
            sb.setValue(max(sb.minimum(), min(sb.maximum(), sb.value() + delta)))

    def _adjust_font_scale(self, delta: float) -> None:
        self._font_scale = max(1.0, min(2.5, self._font_scale + delta))
        self._apply_mode_styling()
        if self.mode == "audience":
            settings = config.load_audience_mode_settings()
            settings["font_scale"] = self._font_scale
            config.save_audience_mode_settings(settings)

    def _toggle_paragraph_highlight(self) -> None:
        if self.mode == "focus":
            self._settings["paragraph_focus"] = not bool(self._settings.get("paragraph_focus", False))
            config.save_focus_mode_settings(self._settings)
        else:
            self._settings["paragraph_highlight"] = not bool(self._settings.get("paragraph_highlight", True))
            config.save_audience_mode_settings(self._settings)
        self._update_paragraph_highlight()

    def _toggle_soft_scroll(self) -> None:
        if self.mode != "audience":
            return
        self._settings["soft_autoscroll"] = not bool(self._settings.get("soft_autoscroll", True))
        config.save_audience_mode_settings(self._settings)
        if hasattr(self, "_scroll_btn"):
            self._scroll_btn.setText("S*" if self._settings["soft_autoscroll"] else "S")

    def _jump_to_heading(self) -> None:
        headings = self._heading_provider() or []
        if not headings:
            return
        # Choose the closest heading after the current cursor
        cursor_pos = self.editor.textCursor().position()
        candidates = sorted(headings, key=lambda h: abs(h.get("position", 0) - cursor_pos))
        target = candidates[0]
        pos = int(target.get("position", cursor_pos))
        cursor = self.editor.textCursor()
        cursor.setPosition(max(0, pos))
        self.editor.setTextCursor(cursor)
        self.editor.setFocus(Qt.ShortcutFocusReason)

    def _show_editor_context_menu(self, pos) -> None:
        """Show context menu with Edit operations matching main window."""
        from PySide6.QtWidgets import QMenu
        
        # Get standard context menu (Undo, Redo, Cut, Copy, Paste, Delete, Select All)
        base_menu = self.editor.createStandardContextMenu()
        
        # Install custom copy actions (sanitized Copy + Copy As Markdown)
        try:
            if hasattr(self.editor, '_install_copy_actions'):
                self.editor._install_copy_actions(base_menu)
        except Exception:
            pass
        
        # Show the menu
        base_menu.exec(self.editor.mapToGlobal(pos))

    # ------------------------------------------------------------------ Events
    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._position_overlays()

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is self.editor.viewport() and event.type() == QEvent.Resize:
            self._position_overlays()
        return super().eventFilter(obj, event)

    def _position_overlays(self) -> None:
        if self._cursor_halo.isVisible():
            self._update_cursor_halo()

    def _on_cursor_moved(self) -> None:
        if getattr(self, "_ready", False) and not self._should_block_highlight():
            self._update_paragraph_highlight()
            self._update_cursor_halo()
            self._soft_autoscroll()
            self._typewriter_scroll()

    def _on_editor_text_changed(self) -> None:
        if self.mode == "audience" and self._settings.get("paragraph_highlight", True):
            if getattr(self, "_ready", False) and not self._should_block_highlight():
                self._update_paragraph_highlight()

    def _show_tools(self) -> None:
        # Tools are now always visible in header - just update halo
        if self.mode == "audience" and self._halo_rect_mode:
            self._update_cursor_halo()

    def _handle_heading_picker_request(self, global_point, prefer_above: bool) -> None:
        """Handle Ctrl+Alt+T heading picker request from editor - show filterable picker."""
        self._show_filterable_heading_picker(global_point, prefer_above)

    def _show_filterable_heading_picker(self, global_pos, prefer_above: bool = False) -> None:
        """Show a filterable heading picker near the cursor (vi 't')."""
        headings = self._heading_popup_candidates()
        if not headings:
            return
        # Dispose any existing picker
        if hasattr(self, "_heading_picker") and self._heading_picker:
            try:
                self._heading_picker.close()
            except Exception:
                pass
        popup = QWidget(self, Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        popup.setStyleSheet(
            "QWidget { background: rgba(32,32,32,240); border: 1px solid #666; border-radius: 6px; }"
            "QLineEdit { border: 1px solid #777; border-radius: 4px; padding: 4px 6px; }"
            "QListWidget { background: transparent; color: #f5f5f5; border: none; }"
            "QListWidget::item { padding: 4px 6px; }"
            "QListWidget::item:selected { background: rgba(90,161,255,80); }"
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

        def activate_current() -> None:
            item = list_widget.currentItem()
            if not item:
                popup.close()
                return
            data = item.data(Qt.UserRole) or {}
            try:
                pos = int(data.get("position", 0))
            except Exception:
                pos = 0
            if pos <= 0:
                try:
                    line = int(data.get("line", 1))
                except Exception:
                    line = 1
                block = self.editor.document().findBlockByNumber(max(0, line - 1))
                if block.isValid():
                    pos = block.position()
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, pos))
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self._flash_heading_selection(cursor)
            popup.close()
            QTimer.singleShot(0, lambda: self.editor.setFocus(Qt.OtherFocusReason))

        filter_edit.textChanged.connect(populate)
        list_widget.itemDoubleClicked.connect(lambda *_: activate_current())
        list_widget.itemActivated.connect(lambda *_: activate_current())

        editor_ref = self.editor

        from PySide6.QtCore import QObject
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

    def _heading_popup_candidates(self) -> list[dict]:
        """Return headings for current page (excluding horizontal rules)."""
        headings = self._heading_provider() or []
        return [h for h in headings if h and h.get("type") != "hr"]

    def _ensure_heading_popup(self) -> None:
        """Create heading popup widget if it doesn't exist."""
        if self._heading_popup is None:
            popup = QWidget(self, Qt.Tool | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
            popup.setAttribute(Qt.WA_TransparentForMouseEvents, True)
            popup.setStyleSheet(
                "background: rgba(30,30,30,220); border: 1px solid #888; border-radius: 6px; padding: 8px;"
            )
            layout = QVBoxLayout(popup)
            layout.setContentsMargins(12, 8, 12, 8)
            self._heading_popup_label = QLabel(popup)
            self._heading_popup_label.setStyleSheet("color: #f5f5f5; font-weight: bold;")
            layout.addWidget(self._heading_popup_label)
            self._heading_popup_list = QListWidget(popup)
            self._heading_popup_list.setStyleSheet(
                "QListWidget { background: transparent; color: #f5f5f5; border: none; }"
                "QListWidget::item { padding: 4px 6px; }"
                "QListWidget::item:selected { background: rgba(255,255,255,40); }"
            )
            layout.addWidget(self._heading_popup_list)
            self._heading_popup_list.itemActivated.connect(self._activate_heading_popup_selection)
            self._heading_popup = popup

    def _show_heading_popup(self) -> None:
        """Display the heading popup with current items and selection."""
        self._ensure_heading_popup()
        if not self._heading_popup or not self._heading_popup_label or not self._heading_popup_list:
            return
        self._heading_popup_list.clear()
        if self._popup_mode == "heading":
            for heading in self._popup_items:
                title = heading.get("title") or "(heading)"
                line = heading.get("line", 1)
                level = max(1, min(5, int(heading.get("level", 1))))
                indent = "    " * (level - 1)
                item = QListWidgetItem(f"{indent}{title}  (line {line})")
                self._heading_popup_list.addItem(item)
            label = "Headings"
        else:
            return
        if 0 <= self._popup_index < self._heading_popup_list.count():
            self._heading_popup_list.setCurrentRow(self._popup_index)
        self._heading_popup_label.setText(label)
        editor_rect = self.editor.rect()
        top_left = self.editor.mapToGlobal(editor_rect.topLeft())
        popup_width = max(self._heading_popup.sizeHint().width(), editor_rect.width() // 3)
        min_height = int(editor_rect.height() * 0.5)
        popup_height = max(self._heading_popup.sizeHint().height(), min_height)
        x = top_left.x() + editor_rect.width() // 2 - popup_width // 2
        y = top_left.y() + 24
        self._heading_popup.resize(popup_width, popup_height)
        self._heading_popup.move(x, y)
        self._heading_popup.show()
        self._heading_popup.raise_()

    def _cycle_popup(self, mode: str, reverse: bool = False) -> None:
        """Cycle through heading popup items."""
        if mode == "heading":
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
        self._show_heading_popup()

    def _activate_heading_popup_selection(self) -> None:
        """Navigate to selected heading and hide popup."""
        if not self._popup_items or self._popup_index < 0 or not self._popup_mode:
            self._hide_heading_popup()
            return
        target = self._popup_items[self._popup_index]
        mode = self._popup_mode
        self._hide_heading_popup()
        if mode == "heading" and target:
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
            cursor = self.editor.textCursor()
            cursor.setPosition(max(0, pos))
            self.editor.setTextCursor(cursor)
            self.editor.ensureCursorVisible()
            self._flash_heading_selection(cursor)

    def _hide_heading_popup(self) -> None:
        """Hide the heading popup and reset state."""
        self._popup_items = []
        self._popup_index = -1
        self._popup_mode = None
        if self._heading_popup:
            self._heading_popup.hide()

    def _flash_heading_selection(self, cursor: QTextCursor) -> None:
        """Briefly highlight the heading line with yellow flash."""
        try:
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.cursor.clearSelection()
            sel.format.setBackground(QColor("#ffd54f"))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.format.setProperty(QTextFormat.UserProperty, 9992)  # Different from _flash_cursor_line
            current = self.editor.extraSelections()
            self.editor.setExtraSelections(current + [sel])

            def clear_flash() -> None:
                try:
                    keep = [
                        s
                        for s in self.editor.extraSelections()
                        if s.format.property(QTextFormat.UserProperty) != 9992
                    ]
                    self.editor.setExtraSelections(keep)
                except Exception:
                    pass

            QTimer.singleShot(220, clear_flash)
        except Exception:
            pass

    def eventFilter(self, obj, event):  # type: ignore[override]
        """Handle Ctrl key release to activate heading popup selection."""
        if event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Tab and (event.modifiers() & Qt.ControlModifier):
                if event.modifiers() & Qt.ShiftModifier:
                    reverse = event.key() == Qt.Key_Backtab
                    self._cycle_popup("heading", reverse=reverse)
                    return True
        elif event.type() == QEvent.KeyRelease:
            if event.key() == Qt.Key_Control and self._popup_items:
                self._activate_heading_popup_selection()
                return True
        # Check if filtering the editor viewport for resize events
        if obj is self.editor.viewport() and event.type() == QEvent.Resize:
            self._position_overlays()
            return False
        return super().eventFilter(obj, event)

    def _page_title(self) -> str:
        if not self._page_path:
            return "Untitled"
        return Path(self._page_path).name or self._page_path

    def closeEvent(self, event):  # type: ignore[override]
        if not getattr(self, "_ready", True):
            self._pending_close = True
            event.ignore()
            return
        self._sync_vi_insert_to_base()
        # Detach overlay editor from the shared document before teardown to avoid
        # stale pointers while the base editor reloads.
        try:
            self.editor.setDocument(self._base_editor.document())
        except Exception:
            try:
                from PySide6.QtGui import QTextDocument
                self.editor.setDocument(QTextDocument())
            except Exception:
                pass
        self._pending_close = False
        try:
            pos = 0
            try:
                pos = int(self.editor.textCursor().position())
            except Exception:
                pos = 0
            self.closed.emit(self.mode, pos)
        except Exception:
            pass
        try:
            self.editor._suppress_link_scan = False
            self._base_editor._suppress_link_scan = False
            self.editor._suppress_vi_cursor = False
            self._base_editor._suppress_vi_cursor = False
            QTimer.singleShot(0, lambda: setattr(self.editor, "_cursor_events_blocked", False))
            QTimer.singleShot(0, lambda: setattr(self._base_editor, "_cursor_events_blocked", False))
            QTimer.singleShot(0, lambda: setattr(type(self.editor), "_LOAD_GUARD_DEPTH", max(0, getattr(type(self.editor), "_LOAD_GUARD_DEPTH", 0) - 1)))
            self.editor._overlay_transition = False
            self._base_editor._overlay_transition = False
            self.editor._overlay_active = False
            self._base_editor._overlay_active = False
        except Exception:
            pass
        # Restore wrap state on the base editor to avoid leaking overlay changes.
        try:
            self._base_editor.setLineWrapMode(self._base_wrap_mode)
        except Exception:
            pass
        try:
            opt = self._base_editor.document().defaultTextOption()
            opt.setWrapMode(self._base_text_wrap)
            self._base_editor.document().setDefaultTextOption(opt)
        except Exception:
            pass
        try:
            doc = self._base_editor.document()
            doc.setDefaultFont(self._doc_default_font)
        except Exception:
            pass
        try:
            self.editor.document().setDefaultFont(self._doc_default_font)
        except Exception:
            pass
        try:
            self.editor._suppress_link_scan = False
            self._base_editor._suppress_link_scan = False
            self.editor._suppress_vi_cursor = False
            self._base_editor._suppress_vi_cursor = False
        except Exception:
            pass
        try:
            self._base_editor.set_font_point_size(self._base_font_size)
            base_font = self._base_editor.font()
            base_font.setFamily(self._base_font_family)
            self._base_editor.setFont(base_font)
            self._base_editor.setMinimumWidth(self._base_min_width)
            # Always restore to an unconstrained width so the main editor isn't left narrow.
            self._base_editor.setMaximumWidth(getattr(self, "_default_max_width", 16777215))
            self._base_editor.updateGeometry()
        except Exception:
            pass
        super().closeEvent(event)
        # Flash the main editor cursor line after returning without scrolling.
        QTimer.singleShot(20, lambda: self._flash_cursor_line(self._base_editor))

    def _request_close(self) -> None:
        """Delay closing until the overlay is fully initialized."""
        if not self._ready:
            self._pending_close = True
            return
        self.close()

    def _mark_ready(self) -> None:
        """Mark the window as ready; allow close actions queued during init."""
        self._ready = True
        if hasattr(self, "_close_button") and self._close_button:
            self._close_button.setEnabled(True)
        if self._shortcuts:
            for sc in self._shortcuts:
                try:
                    sc.setEnabled(True)
                except Exception:
                    continue
        try:
            self.ready.emit()
        except Exception:
            pass
        try:
            self.editor._suppress_link_scan = False
            self._base_editor._suppress_link_scan = False
            self.editor._suppress_vi_cursor = False
            self._base_editor._suppress_vi_cursor = False
            QTimer.singleShot(0, lambda: setattr(self.editor, "_cursor_events_blocked", False))
            QTimer.singleShot(0, lambda: setattr(self._base_editor, "_cursor_events_blocked", False))
            QTimer.singleShot(0, lambda: setattr(type(self.editor), "_LOAD_GUARD_DEPTH", max(0, getattr(type(self.editor), "_LOAD_GUARD_DEPTH", 0) - 1)))
            self.editor._overlay_transition = False
            self._base_editor._overlay_transition = False
        except Exception:
            pass
        if self._pending_close:
            self._pending_close = False
            QTimer.singleShot(0, self.close)

    def _update_vi_badge(self, insert_active: bool) -> None:
        if not hasattr(self, "_vi_badge"):
            return
        if not config.load_vi_mode_enabled():
            self._vi_badge.hide()
            return
        self._vi_badge.show()
        style = "border: 1px solid #666; padding: 2px 6px; border-radius: 3px; margin-right: 6px;"
        if insert_active:
            style += " background: #ffd54d; color: #000;"
        else:
            style += " background: transparent; color: #cbd4e6;"
        self._vi_badge.setStyleSheet(style)

    def _toggle_halo_mode(self) -> None:
        if self.mode != "audience":
            return
        self._halo_rect_mode = not self._halo_rect_mode
        try:
            if self._halo_rect_mode:
                self._cursor_halo.set_mode("line")
            else:
                self._cursor_halo.set_mode("circle")
        except Exception:
            pass
        self._update_cursor_halo()

    def _scroll_cursor_center(self, tolerance_px: int = 12, animated: bool = True) -> None:
        """Scroll so the existing cursor stays selected but is centered in the viewport."""
        sb = self.editor.verticalScrollBar()
        viewport = self.editor.viewport()
        if not sb or not viewport:
            return
        rect = self.editor.cursorRect()
        target_center = viewport.height() // 2
        delta = rect.center().y() - target_center
        if abs(delta) <= tolerance_px:
            return
        target_value = max(sb.minimum(), min(sb.maximum(), sb.value() + delta))
        if not animated:
            sb.setValue(target_value)
            return
        duration = 260 if self.mode == "audience" else 200
        if self._scroll_anim is None:
            self._scroll_anim = QPropertyAnimation(sb, b"value", self)
        else:
            try:
                self._scroll_anim.stop()
                self._scroll_anim.setTargetObject(sb)
            except Exception:
                pass
        try:
            self._scroll_anim.setDuration(duration)
            self._scroll_anim.setStartValue(sb.value())
            self._scroll_anim.setEndValue(target_value)
            self._scroll_anim.start()
        except Exception:
            sb.setValue(target_value)
        # Cursor/selection already maintained by QTextEdit when scrolling via scrollbar.

    def _flash_cursor_line(self, editor: Optional[MarkdownEditor]) -> None:
        """Briefly flash the line under the cursor to guide the eye."""
        if not editor:
            return
        try:
            cursor = editor.textCursor()
            sel = QTextEdit.ExtraSelection()
            sel.cursor = cursor
            sel.cursor.clearSelection()
            sel.format.setBackground(QColor("#ffd54f"))
            sel.format.setProperty(QTextFormat.FullWidthSelection, True)
            sel.format.setProperty(QTextFormat.UserProperty, 9991)
            current = editor.extraSelections()
            editor.setExtraSelections(current + [sel])

            def clear_flash() -> None:
                try:
                    keep = [s for s in editor.extraSelections() if s.format.property(QTextFormat.UserProperty) != 9991]
                    editor.setExtraSelections(keep)
                except Exception:
                    pass

            QTimer.singleShot(220, clear_flash)
        except Exception:
            pass

    def _insert_date(self) -> None:
        """Show calendar/date dialog and insert selected date in the overlay editor."""
        if not self._vault_root:
            self._alert_parent("Select a vault before inserting dates.")
            return
        cursor = self.editor.textCursor()
        saved_cursor_pos = cursor.position()
        saved_anchor_pos = cursor.anchor()
        cursor_rect = self.editor.cursorRect()
        anchor = self.editor.viewport().mapToGlobal(cursor_rect.bottomRight() + QPoint(0, 4))
        dlg = DateInsertDialog(self, anchor_pos=anchor)
        result = dlg.exec()
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
                self._status_message(f"Inserted date: {text}", 3000)

    def _status_message(self, msg: str, duration: int = 2000) -> None:
        parent = self.parent()
        try:
            if parent and hasattr(parent, "statusBar"):
                parent.statusBar().showMessage(msg, duration)
        except Exception:
            pass

    def _alert_parent(self, msg: str) -> None:
        parent = self.parent()
        try:
            if parent and hasattr(parent, "_alert"):
                parent._alert(msg)
        except Exception:
            pass
