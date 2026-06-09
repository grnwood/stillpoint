from __future__ import annotations

from datetime import date, datetime, timedelta
import html
import os
import queue
import re
import threading
import tempfile
import time
from pathlib import Path
from typing import Iterable, Optional

from PySide6.QtCore import QEvent, Qt, Signal, QSize, QTimer, QByteArray, QUrl, QDate, QPoint, QSignalBlocker
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QDesktopServices, QPalette
from PySide6.QtGui import QCursor
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QProgressDialog,
    QStackedWidget,
    QSplitter,
    QDateEdit,
    QHBoxLayout,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QVBoxLayout,
    QWidget,
    QLabel,
    QToolButton,
    QTextBrowser,
    QPushButton,
    QDialog,
    QCalendarWidget,
    QAbstractSpinBox,
    QButtonGroup,
)

from markdown import markdown as render_markdown
from sp.app import config
from sp.app import indexer
from sp.logging_flags import log_enabled
from .theme import apply_menu_theme, theme_color, theme_value
from sp.server.adapters.files import LEGACY_SUFFIX, PAGE_SUFFIX, PAGE_SUFFIXES
from .ai_chat_panel import AIChatPanel, ApiWorker, ServerManager, VectorAPIClient
from .date_insert_dialog import DateInsertDialog
from .path_utils import colon_to_path, path_to_colon
from .screen_positioning import popup_available_geometry, clamp_popup_top_left
from .task_style import (
    contrast_text_color,
    due_colors_from_task,
    priority_brush,
    priority_time_label,
    relative_day_label,
    TaskSemanticColorDelegate,
)

TAG_PATTERN = re.compile(r"(?<![\w.+-])@([A-Za-z0-9_]+)")
TAG_PREFIX_PATTERN = re.compile(r"(?<![\w.+-])@[\w_]*$")
DUE_TOKEN_PATTERN = re.compile(r"<([0-9]{4}-[0-9]{2}-[0-9]{2})")
START_TOKEN_PATTERN = re.compile(r">([0-9]{4}-[0-9]{2}-[0-9]{2})")
PRINT_LINK_PATTERN = re.compile(
    r"(?P<md>\[(?P<md_label>[^\]]+)\]\((?P<md_url>[^\s)]+)\))|"
    r"(?P<wiki>\[(?P<wiki_link>[^\]|]+)\|(?P<wiki_label>[^\]]+)\])"
)


def _active_tag_token(text: str, cursor: int) -> Optional[str]:
    """Return the @tag token currently under the cursor, if any."""
    prefix = text[: max(cursor, 0)]
    match = TAG_PREFIX_PATTERN.search(prefix)
    return match.group(0) if match else None


def _should_suspend_nav_for_tag(text: str, cursor: int, available_tags: set[str]) -> bool:
    """Return True if nav keys should be suspended because a tag is being typed that isn't yet valid."""
    token = _active_tag_token(text, cursor)
    if not token:
        return False
    tag = token.lstrip("@")
    if not tag:
        return False
    normalized = {candidate.lower() for candidate in available_tags}
    # Keep nav suspended while typing partial tags; only release on exact known tag.
    return tag.lower() not in normalized


class DebugTaskTree(QTreeWidget):
    """QTreeWidget that logs mouse events for debugging."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from PySide6.QtCore import QTimer
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._emit_deferred_double_click)
        self._pending_task_data = None
    
    def mouseDoubleClickEvent(self, event):  # type: ignore[override]
        debug = log_enabled("tasks_calendar")
        if debug:
            print(f"[DEBUG_TREE] mouseDoubleClickEvent: button={event.button()}, pos={event.pos()}")
        item = self.itemAt(event.pos())
        column = self.columnAt(event.pos().x())
        if debug:
            print(f"[DEBUG_TREE] Column at click: {column}")
        
        if item and event.button() == Qt.LeftButton:
            if debug:
                print(f"[DEBUG_TREE] Item at pos: {item.text(1)[:50]}")
            # Extract task data immediately before item might become invalid
            task_data = item.data(0, Qt.UserRole)
            if task_data:
                if debug:
                    print(f"[DEBUG_TREE] Task data: {task_data.get('path')}:{task_data.get('line')}")
                self._pending_task_data = task_data
                self._timer.start(0)
                event.accept()
                return
        
        # Only call super if we didn't handle it
        if debug:
            print(f"[DEBUG_TREE] Calling super().mouseDoubleClickEvent()")
        super().mouseDoubleClickEvent(event)
        if debug:
            print(f"[DEBUG_TREE] After super().mouseDoubleClickEvent(), event.isAccepted()={event.isAccepted()}")
    
    def _emit_deferred_double_click(self):
        if self._pending_task_data:
            if log_enabled("tasks_calendar"):
                print(f"[DEBUG_TREE] Emitting task activation for {self._pending_task_data.get('path')}")
            # Find the parent TaskPanel and emit through it
            parent = self.parent()
            while parent and not hasattr(parent, 'taskActivated'):
                parent = parent.parent()
            if parent and hasattr(parent, 'taskActivated'):
                try:
                    parent._mark_activation_source("mouse")
                except Exception:
                    pass
                parent.taskActivated.emit(self._pending_task_data['path'], self._pending_task_data.get('line') or 1)
            self._pending_task_data = None
    
    def mousePressEvent(self, event):  # type: ignore[override]
        if log_enabled("tasks_calendar"):
            print(f"[DEBUG_TREE] mousePressEvent: button={event.button()}, pos={event.pos()}")
        super().mousePressEvent(event)
        if log_enabled("tasks_calendar"):
            print(f"[DEBUG_TREE] After super().mousePressEvent(), event.isAccepted()={event.isAccepted()}")


class TaskPanel(QWidget):
    taskActivated = Signal(str, int)
    focusGained = Signal()
    filterClearRequested = Signal()
    taskDatesWillApply = Signal(list)  # affected page paths
    taskDatesApplied = Signal(list)  # affected page paths
    remoteRequestObserved = Signal(str, float, str)  # state, latency_ms, message

    def __init__(
        self,
        parent=None,
        *,
        font_size_key: str = "task_font_size_tabbed",
        splitter_key: str = "task_splitter_tabbed",
        header_state_key: str = "task_header_tabbed",
        sort_state_key: str = "task_sort_tabbed",
    ) -> None:
        super().__init__(parent)
        self._font_size_key = font_size_key
        self._font_size = config.load_panel_font_size(self._font_size_key, max(8, self.font().pointSize() or 12))
        self._splitter_key = splitter_key
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setInterval(200)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self._header_state_key = header_state_key
        self._sort_state_key = sort_state_key
        self._header_save_timer = QTimer(self)
        self._header_save_timer.setInterval(200)
        self._header_save_timer.setSingleShot(True)
        self._header_save_timer.timeout.connect(self._save_header_state)
        self._allow_filter_clear = True
        self.vault_root = None
        self._ai_enabled = config.load_enable_ai_chats()
        self._ai_worker = None
        self._ai_response_buffer = ""
        self._ai_last_markdown = ""
        self._ai_panel = None
        self._ai_chat_panel = None
        self._ai_summary_panel = None
        self._ai_splitter = None
        self._ai_toggle_btn = None
        self._date_filter_btn = None
        self._date_filter_anchor = None
        self._date_filter_dialog = None
        self._date_filter_start_edit = None
        self._date_filter_end_edit = None
        self._date_filter_calendar_popup = None
        self._date_filter_calendar_target = None
        self._date_filter_preset_group = None
        self._date_filter_active_preset: Optional[str] = None
        self._date_filter_start: Optional[date] = None
        self._date_filter_end: Optional[date] = None
        self._ai_generate_btn = None
        self._ai_delete_btn = None
        self._ai_copy_btn = None
        self._ai_markdown_view = None
        self._ai_title_label = None
        self._task_context_dirty = True
        self._task_index_version = config.get_task_index_version()
        self._task_context_initialized = False
        self._ai_progress = None
        self._http_client = None
        self._vector_api = VectorAPIClient(None)
        self._calendar_feature_enabled = config.load_feature_calendar_enabled()

        self.search = QLineEdit()
        self.search.setPlaceholderText("Search tasks… Esc to clear... enter text or @tag(s)...")
        self.search.textChanged.connect(self._on_search_text_changed)
        self.search.returnPressed.connect(self._trigger_search_refresh_now)
        self.search.installEventFilter(self)
        self._remote_search_debounce_ms = 180
        self._search_commit_next = False
        self._search_refresh_timer = QTimer(self)
        self._search_refresh_timer.setSingleShot(True)
        self._search_refresh_timer.timeout.connect(self._refresh_tasks)

        self.tag_list = QListWidget()
        self.tag_list.setSelectionMode(QAbstractItemView.NoSelection)
        self.tag_list.setFocusPolicy(Qt.NoFocus)
        self.tag_list.itemClicked.connect(self._toggle_tag_selection)
        self.tag_list.viewport().installEventFilter(self)
        self.active_tags: set[str] = set()
        self._available_tags: set[str] = set()

        icon_size = QSize(20, 20)

        def _build_toggle(icon, tooltip, slot):
            toggle = QToolButton(self)
            toggle.setCheckable(True)
            toggle.setIcon(icon)
            toggle.setIconSize(icon_size)
            toggle.setToolTip(tooltip)
            toggle.setAutoRaise(True)
            toggle.setFixedSize(26, 26)
            toggle.toggled.connect(slot)
            # Subtle styling to show pressed/depressed states
            toggle.setStyleSheet(
                f"""
                QToolButton {{
                    border: 1px solid {theme_value('task_panel.toggle.border', 'transparent')};
                    border-radius: 13px;
                    padding: 2px;
                    background: {theme_value('task_panel.toggle.bg', 'transparent')};
                    color: {self._icon_tint_color().name()};
                }}
                QToolButton:hover {{
                    border: 1px solid {theme_value('task_panel.toggle.hover_border', '#666666')};
                    background: {theme_value('task_panel.toggle.hover_bg', 'rgba(255,255,255,0.06)')};
                }}
                QToolButton:checked {{
                    border: 1px solid {theme_value('task_panel.toggle.active_border', '#4a90e2')};
                    background: {theme_value('task_panel.toggle.active_bg', 'rgba(74,144,226,0.22)')};
                }}
                QToolButton:disabled {{
                    border: 1px solid {theme_value('task_panel.toggle.disabled_border', theme_value('task_panel.toggle.border', 'transparent'))};
                    background: {theme_value('task_panel.toggle.disabled_bg', theme_value('task_panel.toggle.bg', 'transparent'))};
                }}
                """
            )
            return toggle

        self.show_completed = _build_toggle(
            self._load_svg_icon("complete-task.svg", icon_size),
            "Include tasks marked as done.",
            self._refresh_tasks,
        )

        self.show_future = _build_toggle(
            self._load_svg_icon("future.svg", icon_size),
            "Include tasks that start in the future (e.g., - [ ] task >YYYY-mm-dd).",
            self._on_show_future_toggled,
        )

        self.show_actionable = _build_toggle(
            self._load_svg_icon("actionable.svg", icon_size),
            self._actionable_tooltip(),
            self._refresh_tasks,
        )
        self._update_actionable_tooltip()

        self.task_tree = DebugTaskTree()
        self.task_tree.setItemDelegate(TaskSemanticColorDelegate(self.task_tree))
        self._show_task_start_column = False
        self._show_task_page_column = False
        self._configure_task_columns(force=True)
        self.task_tree.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.task_tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.task_tree.setRootIsDecorated(True)
        self.task_tree.setAlternatingRowColors(True)
        self.task_tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.task_tree.itemActivated.connect(self._on_task_activated)
        self.task_tree.itemDoubleClicked.connect(self._on_task_double_clicked)
        self.task_tree.itemClicked.connect(self._on_task_item_clicked)
        self.task_tree.itemActivated.connect(lambda *_: self._single_shot_ui(0, self._reset_horizontal_scroll))
        self.task_tree.itemDoubleClicked.connect(lambda *_: self._single_shot_ui(0, self._reset_horizontal_scroll))
        self.task_tree.setSortingEnabled(True)
        self.sort_column = 0
        self.sort_order = Qt.AscendingOrder
        saved_sort = config.load_sort_state(self._sort_state_key)
        if saved_sort:
            try:
                self.sort_column = max(0, int(saved_sort.get("column", 0)))
                default_order = getattr(Qt.AscendingOrder, "value", Qt.AscendingOrder)
                saved_order = int(saved_sort.get("order", default_order))
                self.sort_order = Qt.SortOrder(saved_order)
            except Exception:
                self.sort_column = 0
                self.sort_order = Qt.AscendingOrder
        header = self.task_tree.header()
        header.sectionClicked.connect(self._handle_header_click)
        header.setSortIndicator(self.sort_column, self.sort_order)
        header.setStretchLastSection(False)
        try:
            from PySide6.QtWidgets import QHeaderView
            header.setSectionResizeMode(0, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
        except Exception:
            pass
        self.task_tree.setFocusPolicy(Qt.StrongFocus)
        self.task_tree.installEventFilter(self)
        self.task_tree.setFocusPolicy(Qt.StrongFocus)
        self.task_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.task_tree.customContextMenuRequested.connect(self._open_task_date_context_menu)
        
        # Debug: Log when tree signals fire (if enabled)
        if log_enabled("tasks_calendar"):
            self.task_tree.itemActivated.connect(lambda item: print(f"[TASK_TREE] itemActivated signal fired"))
            self.task_tree.itemDoubleClicked.connect(lambda item, col: print(f"[TASK_TREE] itemDoubleClicked signal fired, col={col}"))
        
        saved_header = config.load_header_state(self._header_state_key)
        if saved_header:
            try:
                self.task_tree.header().restoreState(QByteArray.fromBase64(saved_header.encode("ascii")))
            except Exception:
                pass
        self.task_tree.header().sectionMoved.connect(lambda *_: self._header_save_timer.start())
        self.task_tree.header().sectionResized.connect(lambda *_: self._header_save_timer.start())

        sidebar = QVBoxLayout()
        # Tags row with filter indicators
        tags_row = QHBoxLayout()
        tags_row.addWidget(QLabel("Tags"))
        self.filter_label = QLabel("Filtered")
        self.filter_label.setVisible(False)
        self.filter_label.setCursor(Qt.PointingHandCursor)
        self.filter_label.setToolTip("Click to clear navigation filter")
        self.filter_label.mousePressEvent = lambda event: self._on_filter_label_clicked(event)
        tags_row.addSpacing(6)
        tags_row.addWidget(self.filter_label)
        self.filter_checkbox = QCheckBox()
        self.filter_checkbox.setChecked(True)
        self.filter_checkbox.setVisible(False)
        self.filter_checkbox.setToolTip("Limit tasks to the filtered navigation subtree.")
        self.filter_checkbox.toggled.connect(self._on_filter_checkbox_toggled)
        tags_row.addWidget(self.filter_checkbox)
        self.journal_checkbox = QCheckBox("Journal?")
        self.journal_checkbox.setChecked(True)
        self.journal_checkbox.setVisible(False)
        self.journal_checkbox.setToolTip("Include tasks from the Journal subtree while filtered.")
        self.journal_checkbox.toggled.connect(self._on_journal_checkbox_toggled)
        tags_row.addWidget(self.journal_checkbox)
        tags_row.addStretch(1)
        sidebar.addLayout(tags_row)
        sidebar.addWidget(self.tag_list)
        sidebar_widget = QWidget()
        sidebar_widget.setLayout(sidebar)

        splitter = QSplitter()
        splitter.addWidget(sidebar_widget)
        splitter.addWidget(self.task_tree)
        splitter.setSizes([180, 360])
        self.splitter = splitter
        sizes = config.load_splitter_sizes(self._splitter_key)
        if sizes:
            try:
                self.splitter.setSizes(sizes)
            except Exception:
                pass
        self.splitter.splitterMoved.connect(lambda *_: self._splitter_save_timer.start())

        # Header row with horizontal toggles then search
        header_row = QHBoxLayout()
        for cb in (self.show_completed, self.show_future, self.show_actionable):
            header_row.addWidget(cb)
        self._date_filter_btn = QToolButton()
        self._date_filter_btn.setToolTip("Filter by date range")
        self._date_filter_btn.setAutoRaise(True)
        self._date_filter_btn.setIconSize(QSize(20, 20))
        self._date_filter_btn.clicked.connect(self._open_date_filter_dialog)
        self._update_date_filter_button()
        header_row.addWidget(self._date_filter_btn)
        header_row.addSpacing(6)
        header_row.addWidget(self.search, 1)
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Decrease font size")
        self.zoom_out_btn.setAutoRaise(True)
        self.zoom_out_btn.clicked.connect(lambda: self._adjust_font_size(-1))
        header_row.addWidget(self.zoom_out_btn)
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Increase font size")
        self.zoom_in_btn.setAutoRaise(True)
        self.zoom_in_btn.clicked.connect(lambda: self._adjust_font_size(1))
        header_row.addWidget(self.zoom_in_btn)

        self._print_btn = QToolButton()
        self._print_btn.setToolTip("Print visible tasks to browser")
        self._print_btn.setAutoRaise(True)
        self._print_btn.setIcon(self._load_svg_icon("print.svg", QSize(20, 20)))
        self._print_btn.setIconSize(QSize(20, 20))
        self._print_btn.clicked.connect(self._print_visible_tasks)
        header_row.addWidget(self._print_btn)

        self._copy_btn = QToolButton()
        self._copy_btn.setToolTip("Copy visible tasks as dashed lines")
        self._copy_btn.setAutoRaise(True)
        self._copy_btn.setIcon(self._load_svg_icon("copy.svg", QSize(20, 20)))
        self._copy_btn.setIconSize(QSize(20, 20))
        self._copy_btn.clicked.connect(self._copy_visible_tasks)
        header_row.addWidget(self._copy_btn)

        self._ai_toggle_btn = QToolButton()
        self._ai_toggle_btn.setToolTip("Open task AI insights and chat")
        self._ai_toggle_btn.setAutoRaise(True)
        self._ai_toggle_btn.setCheckable(True)
        self._ai_toggle_btn.setVisible(self._ai_enabled)
        self._ai_toggle_btn.setIcon(self._load_ai_icon())
        self._ai_toggle_btn.setIconSize(QSize(22, 22))
        self._ai_toggle_btn.toggled.connect(self._toggle_ai_panel)
        header_row.addWidget(self._ai_toggle_btn)
        self._apply_header_button_styles()

        self.task_content = QWidget()
        task_content_layout = QVBoxLayout()
        task_content_layout.setContentsMargins(0, 0, 0, 0)
        task_content_layout.addWidget(splitter)
        self.task_content.setLayout(task_content_layout)

        self.content_stack = QStackedWidget()
        self.content_stack.addWidget(self.task_content)
        self.summary_footer = QLabel("")
        self.summary_footer.setStyleSheet(
            "color: "
            f"{theme_value('task_panel.summary_footer.color', '#9aa4ad')}; "
            "padding: 2px 6px;"
        )
        self.summary_footer.setWordWrap(True)
        if self._ai_enabled:
            self._setup_ai_panel()

        layout = QVBoxLayout()
        layout.addLayout(header_row)
        layout.addWidget(self.content_stack, 1)
        layout.addWidget(self.summary_footer)
        self.setLayout(layout)
        
        self._nav_filter_prefix: Optional[str] = None
        self._nav_filter_enabled = True
        self._include_journal = self._default_include_journal()
        self._visible_tasks: list[dict] = []
        self._tag_source_tasks: Optional[list[dict]] = None
        self._last_keyboard_task_id: Optional[str] = None
        self._last_keyboard_task_path: Optional[str] = None
        self._last_keyboard_task_line: Optional[int] = None
        self._suppress_task_activation = False
        self._api_task_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._api_task_cache_ttl = 0.5
        self._remote_mode = False
        self._api_task_inflight: set[tuple] = set()
        self._api_task_error_until: dict[tuple, float] = {}
        self._api_task_result_queue: queue.Queue[tuple[str, tuple, object, float]] = queue.Queue()
        self._api_result_timer = QTimer(self)
        self._api_result_timer.setInterval(75)
        self._api_result_timer.timeout.connect(self._drain_remote_task_results)
        self._api_result_timer.start()
        self._setup_focus_defaults()
        self._update_filter_indicator()
        self._apply_font_size()
        self._last_refresh_signature: Optional[tuple] = None
        self._vault_accent_color: Optional[str] = None
        self._apply_selection_style()

    def _single_shot_ui(self, delay_ms: int, callback) -> None:
        """Schedule a UI callback bound to this widget's lifetime."""
        delay = max(0, int(delay_ms))
        try:
            QTimer.singleShot(delay, self, callback)
        except TypeError:
            QTimer.singleShot(delay, callback)

    def _selection_text_for_background(self, bg_hex: str) -> str:
        return contrast_text_color(QColor(bg_hex)).name()

    def _selection_bg_for_accent(self, bg_hex: str) -> str:
        color = QColor(bg_hex)
        if not color.isValid():
            return bg_hex
        return color.name()

    def _effective_accent_color(self) -> str:
        accent = (self._vault_accent_color or "").strip()
        if accent.startswith("#"):
            return accent
        return QApplication.palette().color(QPalette.Highlight).name()

    def _hover_fill_for_accent(self, accent_hex: str) -> str:
        color = QColor(accent_hex)
        if not color.isValid():
            return theme_value("task_panel.task_tree.hover_bg", "palette(alternate-base)")
        color.setAlpha(48)
        return color.name(QColor.HexArgb)

    def _apply_selection_style(self) -> None:
        accent = self._effective_accent_color()
        hover_bg = self._hover_fill_for_accent(accent)
        self.task_tree.setStyleSheet(
            f"""
            QTreeWidget::item {{
                border: 1px solid transparent;
                border-radius: 6px;
            }}
            QTreeWidget::item:selected {{
                background: transparent;
                border: 1px solid {accent};
            }}
            QTreeWidget::item:selected:active {{
                background: transparent;
                border: 1px solid {accent};
            }}
            QTreeWidget::item:hover {{
                background: {hover_bg};
                border: 1px solid {accent};
            }}
            """
        )

    def set_vault_accent_color(self, color_hex: Optional[str]) -> None:
        candidate = (color_hex or "").strip()
        self._vault_accent_color = candidate if candidate.startswith("#") else None
        self._apply_selection_style()
        self.refresh_theme_visuals()

    def _format_task_text(self, text: str) -> str:
        """Return plain text with link labels (or URLs) inlined, no markup."""
        if not text:
            return ""

        def _replace_md(match: re.Match[str]) -> str:
            label = (match.group("label") or "").strip()
            url = (match.group("url") or "").strip()
            return label or url

        def _replace_wiki(match: re.Match[str]) -> str:
            link = (match.group("link") or "").strip()
            label = (match.group("label") or "").strip()
            return label or link

        rendered = re.sub(
            r"\[(?P<label>[^\]]+)\]\((?P<url>https?://[^\s)]+)\)",
            _replace_md,
            text,
        )
        rendered = re.sub(
            r"\[(?P<link>[^\]|]+)\|(?P<label>[^\]]+)\]",
            _replace_wiki,
            rendered,
        )
        return rendered

    def _safe_print_href(self, url: str) -> str:
        cleaned = (url or "").strip()
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        if lowered.startswith(("javascript:", "data:")):
            return ""
        return html.escape(cleaned, quote=True)

    def _linkify_task_text_html(self, text: str) -> str:
        """Return HTML with link labels rendered as anchors for print views."""
        if not text:
            return ""
        text = text.replace("\n", " ").strip()
        parts: list[str] = []
        last = 0
        for match in PRINT_LINK_PATTERN.finditer(text):
            if match.start() > last:
                parts.append(html.escape(text[last:match.start()]))
            if match.group("md"):
                label = (match.group("md_label") or "").strip()
                url = (match.group("md_url") or "").strip()
            else:
                label = (match.group("wiki_label") or "").strip()
                url = (match.group("wiki_link") or "").strip()
            label = label or url
            safe_href = self._safe_print_href(url)
            if safe_href:
                parts.append(f"<a href=\"{safe_href}\">{html.escape(label)}</a>")
            else:
                parts.append(html.escape(label))
            last = match.end()
        if last < len(text):
            parts.append(html.escape(text[last:]))
        return "".join(parts)

    def _setup_focus_defaults(self) -> None:
        """Ensure sensible default focus inside the Tasks tab."""
        self.search.setFocusPolicy(Qt.StrongFocus)
        self.setFocusPolicy(Qt.StrongFocus)
        self.search.setFocus()
        self.task_tree.setFocusPolicy(Qt.StrongFocus)
        self.search.installEventFilter(self)
        self.task_tree.installEventFilter(self)

    def _default_include_journal(self) -> bool:
        return bool(self._calendar_feature_enabled)

    def set_calendar_feature_enabled(self, enabled: bool) -> None:
        self._calendar_feature_enabled = bool(enabled)
        if not self._nav_filter_prefix:
            self._include_journal = self._default_include_journal()
        self._update_filter_indicator()

    def _on_filter_checkbox_toggled(self, checked: bool) -> None:
        if not self._nav_filter_prefix:
            self.filter_checkbox.blockSignals(True)
            self.filter_checkbox.setChecked(True)
            self.filter_checkbox.blockSignals(False)
            return
        self._nav_filter_enabled = bool(checked)
        self._update_filter_indicator()
        self._refresh_tasks()

    def _on_filter_label_clicked(self, event) -> None:
        """Request clearing the navigation filter when the label is clicked."""
        if not self._allow_filter_clear:
            return
        self.filterClearRequested.emit()

    def _on_journal_checkbox_toggled(self, checked: bool) -> None:
        if not self._nav_filter_prefix:
            self.journal_checkbox.blockSignals(True)
            self.journal_checkbox.setChecked(True)
            self.journal_checkbox.blockSignals(False)
            return
        self._include_journal = bool(checked)
        self._refresh_tasks()

    def _update_filter_indicator(self) -> None:
        active = bool(self._nav_filter_prefix)
        self.filter_label.setVisible(active)
        self.filter_checkbox.setVisible(active)
        self.journal_checkbox.setVisible(active)
        if not active:
            self.filter_label.setStyleSheet("")
            self.filter_checkbox.blockSignals(True)
            self.filter_checkbox.setChecked(True)
            self.filter_checkbox.blockSignals(False)
            self.journal_checkbox.blockSignals(True)
            self.journal_checkbox.setChecked(self._default_include_journal())
            self.journal_checkbox.blockSignals(False)
            self.journal_checkbox.setEnabled(False)
            self._nav_filter_enabled = True
            self._include_journal = self._default_include_journal()
            return
        display_path = path_to_colon(self._nav_filter_prefix) or self._nav_filter_prefix
        if self._allow_filter_clear:
            self.filter_label.setToolTip(f"{display_path} (click to clear)")
            self.filter_label.setCursor(Qt.PointingHandCursor)
        else:
            self.filter_label.setToolTip(display_path)
            self.filter_label.setCursor(Qt.ArrowCursor)
        self.filter_checkbox.blockSignals(True)
        self.filter_checkbox.setChecked(self._nav_filter_enabled)
        self.filter_checkbox.blockSignals(False)
        self.journal_checkbox.blockSignals(True)
        self.journal_checkbox.setChecked(self._include_journal)
        self.journal_checkbox.blockSignals(False)
        self.journal_checkbox.setEnabled(self._nav_filter_enabled)
        if self._nav_filter_enabled:
            self.filter_label.setStyleSheet(
                "color: "
                f"{theme_value('task_panel.filter_badge.text', '#ffffff')}; "
                "background-color: "
                f"{theme_value('task_panel.filter_badge.bg', '#c62828')}; "
                "padding: 1px 6px; border-radius: 4px;"
            )
        else:
            self.filter_label.setStyleSheet("")

    def set_filter_clear_enabled(self, enabled: bool) -> None:
        self._allow_filter_clear = bool(enabled)
        self._update_filter_indicator()

    def _adjust_font_size(self, delta: int) -> None:
        """Bump panel font size (Ctrl +/-) in tabs or popouts."""
        new_size = max(8, min(24, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        self._apply_font_size()
        config.save_panel_font_size(self._font_size_key, self._font_size)

    def adjust_font_size(self, delta: int) -> None:
        """Public wrapper used by parent containers to adjust fonts."""
        self._adjust_font_size(delta)

    def _load_display_preferences(self) -> tuple[bool, bool]:
        show_start = config.load_show_task_start_date()
        show_page = config.load_show_task_page()
        return bool(show_start), bool(show_page)

    def _configure_task_columns(self, force: bool = False) -> None:
        show_start, show_page = self._load_display_preferences()
        if (
            not force
            and show_start == self._show_task_start_column
            and show_page == self._show_task_page_column
        ):
            return
        self._show_task_start_column = show_start
        self._show_task_page_column = show_page
        headers = ["!", "Task", "Due"]
        if show_start:
            headers.append("Start")
        if show_page:
            headers.append("Path")
        self.task_tree.setColumnCount(len(headers))
        self.task_tree.setHeaderLabels(headers)
        self.task_tree.setColumnWidth(0, 70)
        header = self.task_tree.header()
        try:
            from PySide6.QtWidgets import QHeaderView
            header.setSectionResizeMode(0, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            if self._show_task_start_column:
                header.setSectionResizeMode(2, QHeaderView.Interactive)
                header.setSectionResizeMode(3, QHeaderView.Interactive)
                if self._show_task_page_column:
                    header.setSectionResizeMode(4, QHeaderView.Interactive)
            else:
                header.setSectionResizeMode(2, QHeaderView.Interactive)
                if self._show_task_page_column:
                    header.setSectionResizeMode(3, QHeaderView.Interactive)
        except Exception:
            pass

    def _relative_day_label(self, target: date, prefix: str = "") -> str:
        return relative_day_label(target, prefix=prefix)

    def _priority_time_label(self, task: dict) -> tuple[str, bool]:
        return priority_time_label(task)

    def _apply_font_size(self) -> None:
        font = self.font()
        font.setPointSize(self._font_size)
        for widget in (
            self.search,
            self.tag_list,
            self.task_tree,
            self.filter_label,
            self.filter_checkbox,
            self.journal_checkbox,
            self.show_completed,
            self.show_future,
            self.show_actionable,
            self.zoom_in_btn,
            self.zoom_out_btn,
            self._print_btn,
            self._copy_btn,
            self._ai_toggle_btn,
            self._ai_title_label,
            self._ai_delete_btn,
            self._ai_copy_btn,
            self._ai_generate_btn,
            self._ai_markdown_view,
            self.summary_footer,
        ):
            try:
                if widget:
                    widget.setFont(font)
            except Exception:
                pass
        if self._ai_chat_panel:
            try:
                self._ai_chat_panel.set_font_size(self._font_size)
            except Exception:
                pass

    def _load_print_css(self) -> str:
        css_path = Path(__file__).resolve().parents[2] / "server" / "templates" / "print.css"
        try:
            return css_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _iter_task_items(self) -> list[tuple[QTreeWidgetItem, int, dict]]:
        items: list[tuple[QTreeWidgetItem, int, dict]] = []
        iterator = QTreeWidgetItemIterator(self.task_tree, QTreeWidgetItemIterator.All)
        while iterator.value():
            item = iterator.value()
            task = item.data(0, Qt.UserRole) or {}
            if not task:
                iterator += 1
                continue
            depth = 0
            parent = item.parent()
            while parent is not None:
                depth += 1
                parent = parent.parent()
            items.append((item, depth, task))
            iterator += 1
        return items

    def _build_task_print_html(self) -> str:
        css = self._load_print_css()
        extra_css = f"""
        table.task-print {{
            border-collapse: collapse;
            width: 100%;
        }}
        table.task-print th,
        table.task-print td {{
            border: 1px solid var(--border);
            padding: 0.35em 0.5em;
            vertical-align: top;
        }}
        table.task-print th {{
            background: {theme_value('task_panel.print.table_header_bg', '#f0f0f0')};
            font-weight: 600;
        }}
        .task-priority {{
            text-align: center;
            white-space: nowrap;
            width: 4.5em;
        }}
        .task-due,
        .task-start {{
            white-space: nowrap;
        }}
        .task-path {{
            font-family: ui-monospace, Menlo, Consolas, "Courier New", monospace;
            font-size: 0.95em;
        }}
        .task-checkbox {{
            display: inline-block;
            width: 1.1em;
            margin-right: 0.35em;
            text-align: center;
        }}
        .task-text {{
            display: inline-block;
            vertical-align: top;
        }}
        .task-indent {{
            padding-left: var(--task-indent, 0px);
        }}
        .task-done .task-text {{
            text-decoration: line-through;
        }}
        .task-muted td {{
            color: var(--muted);
        }}
        """

        items = self._iter_task_items()
        count = len(items)
        filters: list[str] = []
        search_text = self.search.text().strip()
        if search_text:
            filters.append(f"Search: {html.escape(search_text)}")
        if self.active_tags:
            safe_tags = ", ".join(html.escape(tag) for tag in sorted(self.active_tags))
            filters.append(f"Tags: {safe_tags}")
        if self._nav_filter_prefix and self._nav_filter_enabled:
            filters.append(f"Path: {html.escape(self._present_path(self._nav_filter_prefix))}")
        if not self.show_completed.isChecked():
            filters.append("Hide completed")
        if not self.show_future.isChecked():
            filters.append("Hide future")
        if self.show_actionable.isChecked():
            filters.append("Actionable only")
        if self._date_filter_active():
            start_label = self._date_filter_start.isoformat() if self._date_filter_start else "Any"
            end_label = self._date_filter_end.isoformat() if self._date_filter_end else "Any"
            if self._date_filter_active_preset:
                filters.append(f"Date filter: {self._date_filter_active_preset} ({start_label} → {end_label})")
            else:
                filters.append(f"Date filter: {start_label} → {end_label}")
        meta_bits = [f"{count} task{'s' if count != 1 else ''}"]
        if filters:
            meta_bits.append(" · ".join(filters))
        meta_bits.append(f"Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        meta_line = " · ".join(meta_bits)

        headers = ["!", "Task", "Due"]
        if self._show_task_start_column:
            headers.append("Start")
        if self._show_task_page_column:
            headers.append("Path")

        header_cells = "".join(f"<th>{html.escape(label)}</th>" for label in headers)
        rows_html = []
        for _, depth, task in items:
            priority_level = min(task.get("priority", 0) or 0, 3)
            priority_text, due_overdue = self._priority_time_label(task)
            pri_style = ""
            pri_brush = self._priority_brush(priority_level)
            if pri_brush:
                bg = pri_brush["bg"].name()
                fg = pri_brush["fg"].name()
                pri_style += f"background-color: {bg}; color: {fg};"
            if due_overdue:
                pri_style += "text-decoration: underline;"
            due_style = ""
            due_fg_bg = self._due_colors(task)
            if due_fg_bg:
                fg, bg = due_fg_bg
                if bg:
                    due_style += f"background-color: {bg.name()};"
                if fg:
                    due_style += f"color: {fg.name()};"

            is_done = task.get("status") == "done"
            row_classes = []
            if is_done:
                row_classes.append("task-done")
            if not task.get("actionable", True):
                row_classes.append("task-muted")
            row_class_attr = f" class=\"{' '.join(row_classes)}\"" if row_classes else ""

            checkbox = "☑" if is_done else "☐"
            task_text = self._linkify_task_text_html(task.get("text", "") or "")
            indent_px = max(0, depth) * 18
            task_html = (
                f"<span class=\"task-text task-indent\" style=\"--task-indent: {indent_px}px;\">"
                f"<span class=\"task-checkbox\">{checkbox}</span>{task_text}</span>"
            )

            row_cells = []
            row_cells.append(
                f"<td class=\"task-priority\" style=\"{pri_style}\">{html.escape(priority_text)}</td>"
            )
            row_cells.append(f"<td>{task_html}</td>")
            due_val = html.escape((task.get("due") or "").strip())
            row_cells.append(f"<td class=\"task-due\" style=\"{due_style}\">{due_val}</td>")
            if self._show_task_start_column:
                start_val = html.escape((task.get("starts") or task.get("start") or "").strip())
                row_cells.append(f"<td class=\"task-start\">{start_val}</td>")
            if self._show_task_page_column:
                path_val = html.escape(self._present_path(task.get("path") or ""))
                row_cells.append(f"<td class=\"task-path\">{path_val}</td>")
            rows_html.append(f"<tr{row_class_attr}>" + "".join(row_cells) + "</tr>")

        table_html = (
            "<table class=\"task-print\">"
            f"<thead><tr>{header_cells}</tr></thead>"
            f"<tbody>{''.join(rows_html)}</tbody>"
            "</table>"
        )

        header_html = (
            "<header class=\"stillpoint-header\">"
            "<h1>Tasks</h1>"
            f"<div class=\"meta\">{meta_line}</div>"
            "</header>"
        )

        return (
            "<!doctype html><html lang=\"en\">"
            "<head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>StillPoint Tasks</title>"
            f"<style>{css}\n{extra_css}</style>"
            "</head><body>"
            "<main class=\"stillpoint-print\">"
            f"{header_html}{table_html}"
            "</main></body></html>"
        )

    def _print_visible_tasks(self) -> None:
        if not config.has_active_vault():
            return
        html_doc = self._build_task_print_html()
        if not html_doc:
            return
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
            with tmp:
                tmp.write(html_doc.encode("utf-8"))
            QDesktopServices.openUrl(QUrl.fromLocalFile(tmp.name))
        except Exception:
            return

    def _build_visible_tasks_copy_text(self) -> str:
        lines: list[str] = []
        for item, depth, task in self._iter_task_items():
            if item.isHidden():
                continue
            text = self._format_task_text((task.get("text") or "").strip())
            parts: list[str] = []
            if text:
                parts.append(text)
            priority = "!" * min(max(int(task.get("priority") or 0), 0), 3)
            if priority:
                parts.append(priority)
            due = (task.get("due") or "").strip()
            if due:
                parts.append(f"<{due}")
            starts = (task.get("starts") or task.get("start") or "").strip()
            if starts:
                parts.append(f">{starts}")
            if self._show_task_page_column:
                path = self._present_path(task.get("path") or "")
                if path:
                    parts.append(f":: {path}")
            line = " ".join(part for part in parts if part).strip()
            if not line:
                continue
            lines.append(f"{'  ' * max(0, depth)}- {line}")
        return "\n".join(lines)

    def _copy_visible_tasks(self) -> None:
        try:
            clipboard = QApplication.clipboard()
        except Exception:
            return
        clipboard.setText(self._build_visible_tasks_copy_text())

    def _reset_horizontal_scroll(self) -> None:
        """Force the task list to show the left-most priority column."""
        try:
            bar = self.task_tree.horizontalScrollBar()
            bar.setValue(0)
        except Exception:
            return

    def _save_splitter_sizes(self) -> None:
        try:
            sizes = self.splitter.sizes()
        except Exception:
            return
        config.save_splitter_sizes(self._splitter_key, sizes)

    def _save_header_state(self) -> None:
        try:
            state = bytes(self.task_tree.header().saveState().toBase64()).decode("ascii")
        except Exception:
            return
        config.save_header_state(self._header_state_key, state)

    def _save_sort_state(self) -> None:
        order_value = getattr(self.sort_order, "value", self.sort_order)
        config.save_sort_state(self._sort_state_key, self.sort_column, order_value)

    def _find_asset(self, name: str) -> Optional[Path]:
        candidates = [
            Path(__file__).resolve().parents[2] / "assets" / name,
            Path(__file__).resolve().parents[2] / "sp" / "assets" / name,
        ]
        for path in candidates:
            if path.exists():
                return path
        return None

    def _load_svg_icon(self, name: str, size: QSize) -> QIcon:
        path = self._find_asset(name)
        if not path:
            return QIcon()
        try:
            renderer = QSvgRenderer(str(path))
            pixmap = QPixmap(size)
            pixmap.fill(Qt.transparent)
            painter = QPainter(pixmap)
            renderer.render(painter)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), self._icon_tint_color())
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon()

    def _icon_tint_color(self) -> QColor:
        explicit_dark = theme_value("task_panel.icon.on_dark", None)
        explicit_light = theme_value("task_panel.icon.on_light", None)
        palette = QApplication.palette()
        bg = palette.color(QPalette.Window)
        if bg.lightness() > 128:
            candidate = QColor(str(explicit_light)) if explicit_light is not None else QColor(0, 0, 0)
            return candidate if candidate.isValid() else QColor(0, 0, 0)
        candidate = QColor(str(explicit_dark)) if explicit_dark is not None else QColor(255, 255, 255)
        return candidate if candidate.isValid() else QColor(255, 255, 255)

    def _apply_toggle_button_style(self, button: QToolButton, *, radius: int = 13) -> None:
        button.setStyleSheet(
            f"""
            QToolButton {{
                border: 1px solid {theme_value('task_panel.toggle.border', 'transparent')};
                border-radius: {radius}px;
                padding: 2px;
                background: {theme_value('task_panel.toggle.bg', 'transparent')};
                color: {self._icon_tint_color().name()};
            }}
            QToolButton:hover {{
                border: 1px solid {theme_value('task_panel.toggle.hover_border', '#666666')};
                background: {theme_value('task_panel.toggle.hover_bg', 'rgba(255,255,255,0.06)')};
            }}
            QToolButton:checked {{
                border: 1px solid {theme_value('task_panel.toggle.active_border', '#4a90e2')};
                background: {theme_value('task_panel.toggle.active_bg', 'rgba(74,144,226,0.22)')};
            }}
            QToolButton:disabled {{
                border: 1px solid {theme_value('task_panel.toggle.disabled_border', theme_value('task_panel.toggle.border', 'transparent'))};
                background: {theme_value('task_panel.toggle.disabled_bg', theme_value('task_panel.toggle.bg', 'transparent'))};
            }}
            """
        )

    def refresh_theme_visuals(self) -> None:
        for button, icon_name, size in (
            (getattr(self, "show_completed", None), "complete-task.svg", QSize(20, 20)),
            (getattr(self, "show_future", None), "future.svg", QSize(20, 20)),
            (getattr(self, "show_actionable", None), "actionable.svg", QSize(20, 20)),
        ):
            if button is None:
                continue
            button.setIcon(self._load_svg_icon(icon_name, size))
            self._apply_toggle_button_style(button, radius=13)
        if getattr(self, "_date_filter_btn", None):
            self._update_date_filter_button()
        if getattr(self, "_print_btn", None):
            self._print_btn.setIcon(self._load_svg_icon("print.svg", QSize(20, 20)))
        if getattr(self, "_copy_btn", None):
            self._copy_btn.setIcon(self._load_svg_icon("copy.svg", QSize(20, 20)))
        if getattr(self, "_ai_toggle_btn", None):
            self._ai_toggle_btn.setIcon(self._load_ai_icon())
        self._apply_header_button_styles()

    def _apply_header_button_styles(self) -> None:
        for btn in (
            getattr(self, "_date_filter_btn", None),
            getattr(self, "zoom_out_btn", None),
            getattr(self, "zoom_in_btn", None),
            getattr(self, "_print_btn", None),
            getattr(self, "_copy_btn", None),
            getattr(self, "_ai_toggle_btn", None),
        ):
            if btn is None:
                continue
            self._apply_toggle_button_style(btn, radius=6)

    def _load_ai_icon(self) -> QIcon:
        return self._load_svg_icon("ai.svg", QSize(24, 24))

    def _build_date_filter_icon(self, active: bool) -> QIcon:
        base = self._load_svg_icon("calendar-days.svg", QSize(20, 20))
        if not active:
            return base
        pixmap = base.pixmap(QSize(20, 20))
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setBrush(theme_color("task_panel.priority_bar", "#4a90e2"))
        painter.setPen(Qt.NoPen)
        radius = 3
        painter.drawEllipse(pixmap.width() - 2 * radius - 1, 1, 2 * radius, 2 * radius)
        painter.end()
        return QIcon(pixmap)

    def _date_filter_tooltip(self) -> str:
        if not self._date_filter_active():
            return "Filter by date range"
        start = self._date_filter_start.isoformat() if self._date_filter_start else "Any"
        end = self._date_filter_end.isoformat() if self._date_filter_end else "Any"
        if self._date_filter_active_preset == "unscheduled":
            return "Date filter active: Unscheduled"
        return f"Date filter active: {start} → {end}"

    def _update_date_filter_button(self) -> None:
        if not self._date_filter_btn:
            return
        active = self._date_filter_active()
        self._date_filter_btn.setIcon(self._build_date_filter_icon(active))
        self._date_filter_btn.setToolTip(self._date_filter_tooltip())

    def _date_filter_anchor_widget(self):
        return self._date_filter_anchor or self._date_filter_btn

    def open_date_filter_dialog(self, anchor=None) -> None:
        """Open the date filter dialog anchored to the provided widget."""
        self._date_filter_anchor = anchor
        self._open_date_filter_dialog()

    def _position_popup(self, popup: QDialog, anchor) -> None:
        if not anchor:
            return
        anchor_pos = anchor.mapToGlobal(QPoint(0, anchor.height()))
        anchor_left = anchor.mapToGlobal(QPoint(0, 0)).x()
        anchor_right = anchor.mapToGlobal(QPoint(anchor.width(), 0)).x()
        avail = popup_available_geometry(anchor=anchor_pos, parent=anchor)
        hint = popup.sizeHint()

        space_right = avail.right() - anchor_pos.x()
        space_left = anchor_pos.x() - avail.left()
        if space_right >= hint.width():
            x = anchor_pos.x()
        elif space_left >= hint.width():
            x = anchor_left - hint.width()
        else:
            x = anchor_right - hint.width()
        x = max(avail.left(), min(x, avail.right() - hint.width() + 1))

        y = anchor_pos.y()
        if y + hint.height() > avail.bottom():
            y = anchor.mapToGlobal(QPoint(0, 0)).y() - hint.height()
        popup.move(clamp_popup_top_left(QPoint(x, y), hint, avail))

    def _date_filter_active(self) -> bool:
        if self._date_filter_active_preset == "unscheduled":
            return True
        return bool(self._date_filter_start or self._date_filter_end)

    def _open_date_filter_dialog(self) -> None:
        if not self._date_filter_dialog:
            self._date_filter_dialog = QDialog(self, Qt.Popup)
            self._date_filter_dialog.setObjectName("taskDateFilterPopup")
            layout = QVBoxLayout(self._date_filter_dialog)
            layout.setContentsMargins(10, 10, 10, 10)
            layout.setSpacing(6)
            layout.addWidget(QLabel("<b>Date Filter</b>"))

            preset_row = QHBoxLayout()
            self._date_filter_preset_group = QButtonGroup(self._date_filter_dialog)
            self._date_filter_preset_group.setExclusive(True)
            for label, preset in (
                ("Overdue", "overdue"),
                ("Should Start", "should_start"),
                ("Unscheduled", "unscheduled"),
                ("Today", "today"),
                ("This Week", "week"),
                ("Next 7", "next7"),
                ("This Month", "month"),
            ):
                btn = QToolButton()
                btn.setText(label)
                btn.setCheckable(True)
                btn.setAutoRaise(True)
                btn.setStyleSheet(
                    f"""
                    QToolButton {{
                        border: 1px solid {theme_value('task_panel.date_preset.border', '#444444')};
                        border-radius: 10px;
                        padding: 3px 10px;
                        margin: 2px;
                        background: transparent;
                        color: {theme_value('task_panel.date_preset.text', '#dddddd')};
                    }}
                    QToolButton:hover {{
                        background: {theme_value('task_panel.date_preset.hover_bg', '#333333')};
                    }}
                    QToolButton:checked {{
                        background: {theme_value('task_panel.date_preset.active_bg', '#2b2b2b')};
                        color: {theme_value('task_panel.date_preset.active_text', '#ffffff')};
                        border: 1px solid {theme_value('task_panel.date_preset.active_border', '#2b2b2b')};
                    }}
                    """
                )
                btn.clicked.connect(lambda _, p=preset: self._apply_date_preset(p))
                self._date_filter_preset_group.addButton(btn)
                preset_row.addWidget(btn)
            preset_row.addStretch(1)
            layout.addLayout(preset_row)

            date_row = QHBoxLayout()
            date_row.setSpacing(6)
            date_row.addWidget(QLabel("Start after"))
            self._date_filter_start_edit = QDateEdit()
            self._date_filter_start_edit.setCalendarPopup(False)
            self._date_filter_start_edit.setButtonSymbols(QAbstractSpinBox.NoButtons)
            self._date_filter_start_edit.setDisplayFormat("yyyy-MM-dd")
            self._date_filter_start_edit.setSpecialValueText("Any")
            self._date_filter_start_edit.setDate(QDate.currentDate())
            self._date_filter_start_edit.setMinimumDate(QDate(1900, 1, 1))
            self._date_filter_start_edit.setMaximumDate(QDate(2999, 12, 31))
            date_row.addWidget(self._date_filter_start_edit)
            start_btn = QToolButton()
            start_btn.setIcon(self._load_svg_icon("calendar-days.svg", QSize(16, 16)))
            start_btn.setAutoRaise(True)
            start_btn.setToolTip("Pick start date")
            start_btn.clicked.connect(lambda: self._open_date_calendar("start"))
            date_row.addWidget(start_btn)
            date_row.addSpacing(8)
            date_row.addWidget(QLabel("End before"))
            self._date_filter_end_edit = QDateEdit()
            self._date_filter_end_edit.setCalendarPopup(False)
            self._date_filter_end_edit.setButtonSymbols(QAbstractSpinBox.NoButtons)
            self._date_filter_end_edit.setDisplayFormat("yyyy-MM-dd")
            self._date_filter_end_edit.setSpecialValueText("Any")
            self._date_filter_end_edit.setDate(QDate.currentDate())
            self._date_filter_end_edit.setMinimumDate(QDate(1900, 1, 1))
            self._date_filter_end_edit.setMaximumDate(QDate(2999, 12, 31))
            date_row.addWidget(self._date_filter_end_edit)
            end_btn = QToolButton()
            end_btn.setIcon(self._load_svg_icon("calendar-days.svg", QSize(16, 16)))
            end_btn.setAutoRaise(True)
            end_btn.setToolTip("Pick end date")
            end_btn.clicked.connect(lambda: self._open_date_calendar("end"))
            date_row.addWidget(end_btn)
            layout.addLayout(date_row)

            action_row = QHBoxLayout()
            apply_btn = QPushButton("Apply")
            apply_btn.clicked.connect(self._apply_date_filter_from_dialog)
            clear_btn = QPushButton("Clear")
            clear_btn.clicked.connect(self._clear_date_filter)
            action_row.addStretch(1)
            action_row.addWidget(clear_btn)
            action_row.addWidget(apply_btn)
            layout.addLayout(action_row)

        min_date = QDate(1900, 1, 1)
        if self._date_filter_start_edit:
            if self._date_filter_start:
                self._date_filter_start_edit.setDate(QDate(self._date_filter_start.year, self._date_filter_start.month, self._date_filter_start.day))
            else:
                self._date_filter_start_edit.setDate(min_date)
        if self._date_filter_end_edit:
            if self._date_filter_end:
                self._date_filter_end_edit.setDate(QDate(self._date_filter_end.year, self._date_filter_end.month, self._date_filter_end.day))
            else:
                self._date_filter_end_edit.setDate(min_date)

        anchor = self._date_filter_anchor_widget()
        if anchor:
            self._position_popup(self._date_filter_dialog, anchor)
        self._date_filter_anchor = None
        self._date_filter_dialog.show()

    def _open_date_calendar(self, target: str) -> None:
        self._date_filter_calendar_target = target
        if not self._date_filter_calendar_popup:
            self._date_filter_calendar_popup = QDialog(self, Qt.Popup)
            layout = QVBoxLayout(self._date_filter_calendar_popup)
            layout.setContentsMargins(6, 6, 6, 6)
            calendar = QCalendarWidget()
            calendar.clicked.connect(self._apply_calendar_date)
            layout.addWidget(calendar)
            self._date_filter_calendar_popup.setLayout(layout)
        anchor = self._date_filter_anchor_widget()
        if anchor:
            self._position_popup(self._date_filter_calendar_popup, anchor)
        self._date_filter_calendar_popup.show()

    def _apply_calendar_date(self, qdate: QDate) -> None:
        if self._date_filter_calendar_target == "start" and self._date_filter_start_edit:
            self._date_filter_start_edit.setDate(qdate)
        elif self._date_filter_calendar_target == "end" and self._date_filter_end_edit:
            self._date_filter_end_edit.setDate(qdate)
        if self._date_filter_calendar_popup:
            self._date_filter_calendar_popup.hide()

    def _clear_preset_selection(self) -> None:
        if not self._date_filter_preset_group:
            return
        for button in self._date_filter_preset_group.buttons():
            button.blockSignals(True)
            button.setChecked(False)
            button.blockSignals(False)

    def _apply_date_preset(self, preset: str) -> None:
        today = date.today()
        start: Optional[date] = None
        end: Optional[date] = None
        min_date = QDate(1900, 1, 1)
        if preset == "today":
            start = today
            end = today
        elif preset == "week":
            start = today - timedelta(days=today.weekday())
            end = start + timedelta(days=6)
        elif preset == "next7":
            start = today
            end = today + timedelta(days=6)
        elif preset == "month":
            start = today.replace(day=1)
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            end = next_month - timedelta(days=1)
        elif preset == "overdue":
            end = today - timedelta(days=1)
        elif preset == "should_start":
            end = today - timedelta(days=7)
        elif preset == "unscheduled":
            start = None
            end = None
        else:
            return
        self._date_filter_active_preset = preset
        self._set_date_filter(start, end)
        if self._date_filter_start_edit:
            if start:
                self._date_filter_start_edit.setDate(QDate(start.year, start.month, start.day))
            else:
                self._date_filter_start_edit.setDate(min_date)
        if self._date_filter_end_edit:
            if end:
                self._date_filter_end_edit.setDate(QDate(end.year, end.month, end.day))
            else:
                self._date_filter_end_edit.setDate(min_date)
        if self._date_filter_dialog:
            self._date_filter_dialog.hide()

    def _apply_date_filter_from_dialog(self) -> None:
        start = None
        end = None
        min_date = QDate(1900, 1, 1)
        if self._date_filter_start_edit and self._date_filter_start_edit.date():
            start_qdate = self._date_filter_start_edit.date()
            if start_qdate != min_date:
                start = date(start_qdate.year(), start_qdate.month(), start_qdate.day())
        if self._date_filter_end_edit and self._date_filter_end_edit.date():
            end_qdate = self._date_filter_end_edit.date()
            if end_qdate != min_date:
                end = date(end_qdate.year(), end_qdate.month(), end_qdate.day())
        if start and end and start > end:
            return
        self._clear_preset_selection()
        self._date_filter_active_preset = None
        self._set_date_filter(start, end)
        if self._date_filter_dialog:
            self._date_filter_dialog.hide()

    def _set_date_filter(self, start: Optional[date], end: Optional[date]) -> None:
        self._date_filter_start = start
        self._date_filter_end = end
        self._update_date_filter_button()
        self._refresh_tasks()

    def _clear_date_filter(self) -> None:
        self._date_filter_start = None
        self._date_filter_end = None
        self._clear_preset_selection()
        self._date_filter_active_preset = None
        min_date = QDate(1900, 1, 1)
        if self._date_filter_start_edit:
            self._date_filter_start_edit.setDate(min_date)
        if self._date_filter_end_edit:
            self._date_filter_end_edit.setDate(min_date)
        self._update_date_filter_button()
        if self._date_filter_dialog:
            self._date_filter_dialog.hide()

    def set_date_filter_range(
        self,
        start: Optional[date],
        end: Optional[date],
        preset: Optional[str] = None,
        *,
        refresh: bool = True,
    ) -> None:
        """Set the date filter programmatically and refresh tasks."""
        self._date_filter_active_preset = preset
        self._set_date_filter(start, end)
        if refresh:
            self._refresh_tasks()
        self._refresh_tasks()

    def _actionable_tooltip(self) -> str:
        base = "Show tasks you can act on now (not done, no open subtasks, parents inherit)."
        try:
            raw = config.load_non_actionable_task_tags()
        except Exception:
            raw = ""
        tags = " ".join(raw.split())
        if tags:
            return f"{base}\nNon-actionable tags: {tags}"
        return base

    def _update_actionable_tooltip(self) -> None:
        if not self.show_actionable:
            return
        self.show_actionable.setToolTip(self._actionable_tooltip())

    def _build_ai_summary_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self._ai_title_label = QLabel("AI Insights")
        self._ai_title_label.setStyleSheet(
            "font-weight: "
            f"{theme_value('task_panel.ai.title_weight', 'bold')};"
        )
        self._ai_delete_btn = QToolButton()
        self._ai_delete_btn.setIcon(self._load_svg_icon("icons8-trash.svg", QSize(20, 20)))
        self._ai_delete_btn.setToolTip("Delete AI summary for tasks")
        self._ai_delete_btn.setAutoRaise(True)
        self._ai_delete_btn.clicked.connect(self._delete_ai_summary)
        self._ai_copy_btn = QToolButton()
        self._ai_copy_btn.setIcon(self._load_svg_icon("copy.svg", QSize(20, 20)))
        self._ai_copy_btn.setToolTip("Copy AI summary markdown")
        self._ai_copy_btn.setAutoRaise(True)
        self._ai_copy_btn.clicked.connect(self._copy_ai_markdown)
        self._ai_generate_btn = QToolButton()
        self._ai_generate_btn.setIcon(self._load_ai_icon())
        self._ai_generate_btn.setToolTip("Refresh AI summary for tasks")
        self._ai_generate_btn.setAutoRaise(True)
        self._ai_generate_btn.setIconSize(QSize(28, 28))
        self._ai_generate_btn.clicked.connect(self._on_generate_ai_summary)
        header.addWidget(self._ai_title_label)
        header.addStretch(1)
        header.addWidget(self._ai_delete_btn)
        header.addWidget(self._ai_copy_btn)
        header.addWidget(self._ai_generate_btn)
        self._ai_markdown_view = QTextBrowser()
        self._ai_markdown_view.setOpenExternalLinks(False)
        self._ai_markdown_view.setOpenLinks(False)
        self._ai_markdown_view.anchorClicked.connect(self._on_ai_markdown_link_clicked)
        self._ai_markdown_view.setReadOnly(True)
        self._ai_markdown_view.setStyleSheet(
            "background:"
            f"{theme_value('task_panel.ai.view_bg', '#1f1f1f')}; "
            "color:"
            f"{theme_value('task_panel.ai.view_text', '#f0f0f0')}; "
            "border:1px solid "
            f"{theme_value('task_panel.ai.view_border', '#444444')}; "
            "padding:10px;"
        )
        layout.addLayout(header)
        layout.addWidget(self._ai_markdown_view, 1)
        self._set_ai_markdown("Click button to generate an AI summary.")
        return panel

    def _setup_ai_panel(self) -> None:
        if self._ai_panel:
            return
        self._ai_panel = QWidget()
        layout = QVBoxLayout(self._ai_panel)
        layout.setContentsMargins(0, 0, 0, 0)
        self._ai_summary_panel = self._build_ai_summary_panel()
        self._ai_chat_panel = AIChatPanel(font_size=self._font_size, api_client=self._http_client)
        self._ai_chat_panel.set_preserve_session_on_reset(True, keep_context=True)
        self._ai_chat_panel.chatNavigateRequested.connect(self._on_ai_chat_navigate_requested)
        if self.vault_root:
            try:
                self._ai_chat_panel.set_vault_root(self.vault_root)
            except Exception:
                pass
        self._set_ai_chat_enabled(self._task_context_initialized)
        splitter = QSplitter(Qt.Vertical)
        splitter.addWidget(self._ai_summary_panel)
        splitter.addWidget(self._ai_chat_panel)
        splitter.setSizes([200, 300])
        layout.addWidget(splitter, 1)
        self._ai_splitter = splitter
        self.content_stack.addWidget(self._ai_panel)
        self._apply_font_size()

    def _toggle_ai_panel(self, checked: bool) -> None:
        if not self._ai_enabled:
            if self._ai_toggle_btn:
                self._ai_toggle_btn.setChecked(False)
            return
        if checked:
            if not self._ai_panel:
                self._setup_ai_panel()
            if self._ai_panel:
                self.content_stack.setCurrentWidget(self._ai_panel)
            self._open_ai_panel()
        else:
            self.content_stack.setCurrentWidget(self.task_content)

    def _open_ai_panel(self) -> None:
        if not self._ai_enabled:
            return
        if not config.has_active_vault():
            self._set_ai_markdown("Open a vault to view task insights.")
            return
        self._ensure_task_chat_ready()
        stored = config.load_task_ai_summary() or ""
        if stored.strip():
            self._set_ai_markdown(stored)
            self._task_context_initialized = True
            self._set_ai_chat_enabled(True)
        else:
            self._set_ai_chat_enabled(self._task_context_initialized)
            self._set_ai_markdown("Click AI button to generate AI summary.")

    def _set_ai_markdown(self, text: str) -> None:
        if not self._ai_markdown_view:
            return
        self._ai_last_markdown = text or ""
        self._render_ai_markdown(self._ai_last_markdown)

    def _render_ai_markdown(self, markdown_text: str) -> None:
        if not self._ai_markdown_view:
            return
        try:
            cleaned = self._replace_emoji_with_fallback(markdown_text or "")
            linked = self._linkify_vault_paths(cleaned)
            html = render_markdown(linked, extensions=["extra", "sane_lists", "tables", "fenced_code"])
            font_size = max(6, self._font_size)
            style = f"""
            <style>
            body {{ background:{theme_value('task_panel.ai.html_bg', '#1f1f1f')}; color:{theme_value('task_panel.ai.html_text', '#f0f0f0')}; font-size: {font_size}px;
                   font-family: {theme_value('task_panel.ai.html_font', "'Noto Sans', 'Segoe UI', 'Helvetica', 'Arial', 'Noto Color Emoji', 'Segoe UI Emoji', 'Apple Color Emoji', sans-serif")}; }}
            h1,h2,h3,h4,h5,h6 {{ margin: 0.4em 0 0.2em 0; }}
            ul,ol {{ margin-top: 0.2em; margin-bottom: 0.2em; }}
            </style>
            """
            self._ai_markdown_view.setHtml(style + html)
        except Exception:
            try:
                self._ai_markdown_view.setPlainText(markdown_text)
            except Exception:
                pass

    def _replace_emoji_with_fallback(self, text: str) -> str:
        if not text:
            return text
        replacements = {
            "📝": "✎",
            "✅": "✔",
            "✔️": "✔",
            "📅": "📆",
            "📎": "⎘",
            "🧩": "◆",
            "🔧": "🔧",
            "🧭": "➤",
            "🗒️": "✐",
            "📌": "•",
            "🎯": "◎",
            "📍": "•",
            "🗓️": "📆",
            "🏷️": "⬦",
            "👉": "→",
            "⚡": "⚡",
        }
        for emoji, fallback in replacements.items():
            text = text.replace(emoji, fallback)
        return text

    def _linkify_vault_paths(self, text: str) -> str:
        if not text:
            return text
        fenced = re.split(r"(```.*?```)", text, flags=re.DOTALL)
        for idx, chunk in enumerate(fenced):
            if chunk.startswith("```"):
                continue
            fenced[idx] = self._linkify_inline_paths(chunk)
        return "".join(fenced)

    def _linkify_inline_paths(self, text: str) -> str:
        segments = re.split(r"(`[^`]*`)", text)
        for idx, seg in enumerate(segments):
            if seg.startswith("`"):
                continue
            segments[idx] = self._replace_path_tokens(seg)
        return "".join(segments)

    def _replace_path_tokens(self, text: str) -> str:
        pattern = re.compile(
            rf"(^|[\s\(\[\"'])"
            rf"(/[^\s`<>\"'()\]]+(?:{re.escape(PAGE_SUFFIX)}|{re.escape(LEGACY_SUFFIX)})?)"
        )

        def _replace(match: re.Match[str]) -> str:
            prefix = match.group(1)
            raw_path = match.group(2)
            trimmed = raw_path.rstrip(",:;.!?")
            trailing = raw_path[len(trimmed):]
            if not trimmed:
                return f"{prefix}{raw_path}"
            colon = path_to_colon(trimmed)
            if not colon:
                return f"{prefix}{raw_path}"
            colon = f":{colon}"
            label = Path(trimmed).stem or colon.split(":")[-1]
            safe_label = html.escape(label, quote=False)
            safe_colon = html.escape(colon, quote=True)
            return f"{prefix}<a href=\"{safe_colon}\" title=\"{safe_colon}\">{safe_label}</a>{trailing}"

        return pattern.sub(_replace, text)

    def _normalize_ai_link_target(self, href: str) -> Optional[str]:
        if not href:
            return None
        base = href.split("#", 1)[0].strip()
        if not base:
            return None
        if base.startswith(":"):
            vault_root_name = Path(self.vault_root).name if self.vault_root else ""
            base = colon_to_path(base, vault_root_name) or base
        if not base.startswith("/"):
            base = "/" + base.lstrip("/")
        rel = Path(base.lstrip("/"))
        if rel.suffix.lower() in PAGE_SUFFIXES:
            if rel.suffix.lower() == LEGACY_SUFFIX:
                rel = rel.with_suffix(PAGE_SUFFIX)
                base = "/" + rel.as_posix()
        else:
            name = rel.name or ""
            if name:
                rel = rel / f"{name}{PAGE_SUFFIX}"
                base = "/" + rel.as_posix()
        return base

    def _open_ai_link(self, href: str) -> None:
        target = self._normalize_ai_link_target(href)
        if not target:
            return
        try:
            self._mark_activation_source("mouse")
        except Exception:
            pass
        self.taskActivated.emit(target, 1)

    def _on_ai_markdown_link_clicked(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("http://") or href.startswith("https://"):
            QDesktopServices.openUrl(QUrl(href))
            return
        if href.startswith("/") or href.startswith(":"):
            self._open_ai_link(href)

    def _on_ai_chat_navigate_requested(self, href: str) -> None:
        if not href:
            return
        self._open_ai_link(href)

    def _ensure_task_chat_ready(self) -> None:
        if not self._ai_chat_panel:
            return
        try:
            self._ai_chat_panel.open_named_chat("Tasks", "/")
            self._ai_chat_panel.ensure_context_page_ref("tasks", index=False)
        except Exception:
            return

    def _set_ai_chat_enabled(self, enabled: bool) -> None:
        if not self._ai_chat_panel:
            return
        self._ai_chat_panel.setEnabled(enabled)
        if enabled:
            self._ai_chat_panel.setToolTip("")
        else:
            self._ai_chat_panel.setToolTip("Initialize task AI to enable chat.")

    def _build_task_context_text(self) -> str:
        tasks = self._fetch_tasks_api(
            "",
            [],
            include_done=True,
            include_ancestors=True,
            actionable_only=False,
        )
        lines: list[str] = []
        for task in tasks:
            status = "- [x]" if task.get("status") == "done" else "- [ ]"
            text = (task.get("text") or "").strip()
            priority = "!" * min(max(int(task.get("priority") or 0), 0), 3)
            tags = " ".join(task.get("tags") or [])
            due = (task.get("due") or "").strip()
            starts = (task.get("starts") or task.get("start") or "").strip()
            date_parts = []
            if due:
                date_parts.append(f"<{due}")
            if starts:
                date_parts.append(f">{starts}")
            date_str = " ".join(date_parts)
            parts = [status]
            if text:
                parts.append(text)
            if priority:
                parts.append(priority)
            if tags:
                parts.append(tags)
            if date_str:
                parts.append(date_str)
            line = " ".join(parts).strip()
            path = (task.get("path") or "").strip()
            if path:
                line = f"{line} :: {path}"
            lines.append(line)
        return "\n".join(lines).strip()

    def _build_task_insight_input(self, max_lines: int = 200, max_chars: int = 6000) -> str:
        tasks = self._fetch_tasks_api(
            "",
            [],
            include_done=True,
            include_ancestors=True,
            actionable_only=False,
        )
        tag_counts: list[tuple[str, int]] = []
        counts: dict[str, int] = {}
        for task in tasks:
            tag_set = set(task.get("tags") or [])
            text = task.get("text", "") or ""
            for token in re.findall(r"@[A-Za-z0-9_]+", text):
                tag_set.add(token)
            for tag in tag_set:
                counts[tag] = counts.get(tag, 0) + 1
        if counts:
            tag_counts = sorted(counts.items())
        today = date.today()
        overdue = 0
        upcoming = 0
        future_starts = 0
        done = 0
        priority_counts = {0: 0, 1: 0, 2: 0, 3: 0}
        path_counts: dict[str, int] = {}
        for task in tasks:
            status = task.get("status") or ""
            if status == "done":
                done += 1
            priority = min(max(int(task.get("priority") or 0), 0), 3)
            priority_counts[priority] = priority_counts.get(priority, 0) + 1
            path = (task.get("path") or "").strip()
            if path:
                parent = str(Path(path).parent)
                path_counts[parent] = path_counts.get(parent, 0) + 1
            due_str = (task.get("due") or "").strip()
            if due_str:
                try:
                    due_dt = date.fromisoformat(due_str)
                    if status != "done":
                        if due_dt < today:
                            overdue += 1
                        elif due_dt <= today + timedelta(days=7):
                            upcoming += 1
                except ValueError:
                    pass
            start_str = (task.get("starts") or task.get("start") or "").strip()
            if start_str:
                try:
                    start_dt = date.fromisoformat(start_str)
                    if start_dt > today:
                        future_starts += 1
                except ValueError:
                    pass
        total = len(tasks)
        todo = total - done
        lines: list[str] = []
        lines.append("Task overview:")
        lines.append(f"Total tasks: {total} (open: {todo}, done: {done})")
        lines.append(
            "Priority counts: "
            f"!={priority_counts.get(1, 0)}, "
            f"!!={priority_counts.get(2, 0)}, "
            f"!!!={priority_counts.get(3, 0)}"
        )
        lines.append(f"Overdue open tasks: {overdue}")
        lines.append(f"Upcoming (next 7 days): {upcoming}")
        lines.append(f"Future start tasks: {future_starts}")
        lines.append("")
        lines.append("Top task areas (by parent path):")
        for path, count in sorted(path_counts.items(), key=lambda item: item[1], reverse=True)[:12]:
            lines.append(f"{path}: {count}")
        lines.append("")
        lines.append("Tag counts:")
        if tag_counts:
            for tag, count in tag_counts[:20]:
                lines.append(f"{tag}: {count}")
        else:
            lines.append("None")
        lines.append("")
        lines.append("Tasks (truncated):")
        count = 0
        for task in tasks:
            if count >= max_lines:
                lines.append("[truncated]")
                break
            status = "- [x]" if task.get("status") == "done" else "- [ ]"
            text = (task.get("text") or "").strip()
            priority = "!" * min(max(int(task.get("priority") or 0), 0), 3)
            tags = " ".join(task.get("tags") or [])
            due = (task.get("due") or "").strip()
            starts = (task.get("starts") or task.get("start") or "").strip()
            date_parts = []
            if due:
                date_parts.append(f"<{due}")
            if starts:
                date_parts.append(f">{starts}")
            date_str = " ".join(date_parts)
            parts = [status]
            if text:
                parts.append(text)
            if priority:
                parts.append(priority)
            if tags:
                parts.append(tags)
            if date_str:
                parts.append(date_str)
            line = " ".join(parts).strip()
            path = (task.get("path") or "").strip()
            if path:
                line = f"{line} :: {path}"
            if sum(len(l) + 1 for l in lines) + len(line) + 1 > max_chars:
                lines.append("[truncated]")
                break
            lines.append(line)
            count += 1
        return "\n".join(lines).strip()

    def _ensure_task_context_indexed(self, force: bool) -> bool:
        if not config.has_active_vault():
            return False
        if not self._vector_api or not self._vector_api.available():
            return False
        if not force and not self._task_context_dirty:
            return True
        text = self._build_task_context_text()
        if not text:
            return False
        ok = self._vector_api.index_text("tasks", text, "page", timeout=60.0)
        if ok:
            self._task_context_dirty = False
            self._task_context_initialized = True
        return ok

    def _resolve_ai_server_and_model(self) -> Optional[tuple[dict, str]]:
        try:
            server_mgr = ServerManager()
        except Exception:
            return None
        server_config: dict = {}
        try:
            default_server_name = config.load_default_ai_server()
        except Exception:
            default_server_name = None
        if default_server_name:
            try:
                server_config = server_mgr.get_server(default_server_name) or {}
            except Exception:
                server_config = {}
        if not server_config:
            try:
                active = server_mgr.get_active_server_name()
                if active:
                    server_config = server_mgr.get_server(active) or {}
            except Exception:
                server_config = {}
        if not server_config:
            try:
                servers = server_mgr.load_servers()
                if servers:
                    server_config = servers[0]
            except Exception:
                server_config = {}
        if not server_config:
            return None
        try:
            model = config.load_default_ai_model()
        except Exception:
            model = None
        if not model:
            model = server_config.get("default_model") or "gpt-3.5-turbo"
        return server_config, model

    def _on_generate_ai_summary(self) -> None:
        if not self._ai_enabled:
            return
        if self._ai_worker and self._ai_worker.isRunning():
            try:
                self._ai_worker.request_cancel()
            except Exception:
                pass
        if not config.has_active_vault():
            self._set_ai_markdown("Open a vault to generate a task summary.")
            return
        if self._ai_progress:
            try:
                self._ai_progress.close()
            except Exception:
                pass
        progress = QProgressDialog("Collecting Task Info…", None, 0, 0, self)
        progress.setWindowTitle("Collecting Task Info")
        progress.setCancelButton(None)
        progress.setWindowModality(Qt.WindowModal)
        progress.setMinimumDuration(0)
        progress.setValue(0)
        progress.show()
        QApplication.processEvents()
        self._ai_progress = progress
        prompt_path = Path(__file__).resolve().parents[1] / "task-tab-insight-prompt.txt"
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except Exception:
            if self._ai_progress:
                self._ai_progress.close()
                self._ai_progress = None
            self._set_ai_markdown("Failed to load task summary prompt.")
            return
        server_model = self._resolve_ai_server_and_model()
        if not server_model:
            if self._ai_progress:
                self._ai_progress.close()
                self._ai_progress = None
            self._set_ai_markdown("Configure an AI server to generate a summary.")
            return
        if not self._ensure_task_context_indexed(force=True):
            if self._ai_progress:
                self._ai_progress.close()
                self._ai_progress = None
            self._set_ai_markdown("Unable to index task context.")
            return
        self._set_ai_chat_enabled(True)
        self._ensure_task_chat_ready()
        task_context = self._build_task_insight_input()
        if not task_context.strip():
            if self._ai_progress:
                self._ai_progress.close()
                self._ai_progress = None
            self._set_ai_markdown("No tasks available to summarize.")
            return
        server_config, model = server_model
        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": f"Task list:\n\n{task_context}"},
        ]
        self._ai_response_buffer = ""
        self._set_ai_markdown("Generating AI summary…")
        try:
            if self._ai_generate_btn:
                self._ai_generate_btn.setEnabled(False)
        except Exception:
            pass
        worker = ApiWorker(server_config, messages, model, stream=True)
        self._ai_worker = worker
        worker.chunk.connect(self._on_ai_chunk)
        worker.finished.connect(self._on_ai_finished)
        worker.failed.connect(self._on_ai_failed)
        worker.start()

    def _on_ai_chunk(self, chunk: str) -> None:
        self._ai_response_buffer += chunk or ""
        if self._ai_response_buffer.strip():
            self._ai_last_markdown = self._ai_response_buffer
            self._render_ai_markdown(self._ai_last_markdown)

    def _on_ai_finished(self, content: str) -> None:
        try:
            if self._ai_generate_btn:
                self._ai_generate_btn.setEnabled(True)
        except Exception:
            pass
        if self._ai_progress:
            try:
                self._ai_progress.close()
            except Exception:
                pass
            self._ai_progress = None
        final = content or self._ai_response_buffer
        self._ai_response_buffer = final
        if not final.strip():
            self._set_ai_markdown("AI returned no content.")
        else:
            self._set_ai_markdown(final)
            config.save_task_ai_summary(final)
        if self._ai_worker:
            try:
                self._ai_worker.deleteLater()
            except Exception:
                pass
            self._ai_worker = None

    def _on_ai_failed(self, message: str) -> None:
        try:
            if self._ai_generate_btn:
                self._ai_generate_btn.setEnabled(True)
        except Exception:
            pass
        if self._ai_progress:
            try:
                self._ai_progress.close()
            except Exception:
                pass
            self._ai_progress = None
        if not message:
            message = "Failed to generate AI summary."
        self._set_ai_markdown(message)
        if self._ai_worker:
            try:
                self._ai_worker.deleteLater()
            except Exception:
                pass
            self._ai_worker = None

    def _copy_ai_markdown(self) -> None:
        if not self._ai_enabled:
            return
        try:
            clipboard = QApplication.clipboard()
        except Exception:
            return
        payload = self._ai_last_markdown or ""
        clipboard.setText(payload)

    def _delete_ai_summary(self) -> None:
        if not self._ai_enabled:
            return
        config.delete_task_ai_summary()
        self._set_ai_markdown("Click button to generate an AI summary.")

    @staticmethod
    def _normalize_task_path(path: Optional[str]) -> str:
        if not path:
            return ""
        norm = path.replace("\\", "/")
        if not norm.startswith("/"):
            norm = "/" + norm.lstrip("/")
        if norm.lower().endswith(LEGACY_SUFFIX):
            norm = norm[: -len(LEGACY_SUFFIX)] + PAGE_SUFFIX
        return norm

    def _task_matches_filter(self, task_path: str, prefix: str) -> bool:
        if not task_path or not prefix:
            return False
        if prefix == "/":
            return True
        if prefix.endswith(tuple(PAGE_SUFFIXES)):
            return task_path == prefix
        base = prefix.rstrip("/")
        if not base:
            return True
        if task_path.startswith(base + "/"):
            return True
        file_target = base + PAGE_SUFFIX
        return task_path == file_target

    def _is_journal_path(self, task_path: str) -> bool:
        if not task_path:
            return False
        norm = task_path
        journal_root = "/Journal"
        if norm == journal_root:
            return True
        if norm in (journal_root + PAGE_SUFFIX, journal_root + LEGACY_SUFFIX):
            return True
        return norm.startswith(journal_root + "/")

    def _apply_nav_filter(self, tasks: list[dict]) -> list[dict]:
        if not tasks:
            return []
        if not self._nav_filter_prefix or not self._nav_filter_enabled:
            return tasks
        prefix = self._normalize_task_path(self._nav_filter_prefix)
        include_journal = self._include_journal
        filtered: list[dict] = []
        seen_ids: set = set()
        for task in tasks:
            task_path = self._normalize_task_path(task.get("path"))
            task_id = task.get("id") or task_path
            if self._task_matches_filter(task_path, prefix):
                if task_id not in seen_ids:
                    filtered.append(task)
                    seen_ids.add(task_id)
                continue
            if include_journal and self._is_journal_path(task_path):
                if task_id not in seen_ids:
                    filtered.append(task)
                    seen_ids.add(task_id)
        return filtered

    def focusInEvent(self, event):  # type: ignore[override]
        super().focusInEvent(event)
        # Don't auto-focus search - let user click what they want
        # Auto-focusing search was interfering with task tree double-clicks
        try:
            self.focusGained.emit()
        except Exception:
            pass

    def focus_search(self) -> None:
        """Public helper to focus the task search field."""
        try:
            self.search.setFocus(Qt.OtherFocusReason)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if getattr(self, "tag_list", None):
            viewport = self.tag_list.viewport()
            if obj is viewport and event.type() == QEvent.MouseButtonRelease:
                # Handle empty-area clear only on release so a valid click can activate a tag on first try.
                pos = event.pos()
                idx = self.tag_list.indexAt(pos)
                if not idx.isValid():
                    # Some styles only mark the text/icon region as hittable for indexAt(x, y).
                    # Retry with x near the left edge so clicks anywhere on the row still count.
                    idx = self.tag_list.indexAt(QPoint(1, pos.y()))
                if not idx.isValid():
                    if self.active_tags:
                        self.active_tags.clear()
                        self._refresh_tasks()
                    return True
        if obj in (self.search, self.task_tree) and event.type() == QEvent.KeyPress:
            mods = event.modifiers() & ~Qt.KeypadModifier
            if obj is self.search and _should_suspend_nav_for_tag(
                self.search.text(), self.search.cursorPosition(), self._available_tags
            ):
                return super().eventFilter(obj, event)
            if obj is self.task_tree and event.key() in (Qt.Key_Return, Qt.Key_Enter):
                current = self.task_tree.currentItem()
                if current:
                    keep_panel = mods == Qt.ShiftModifier
                    if keep_panel:
                        self._mark_activation_source("keyboard_keep_panel")
                    else:
                        self._mark_activation_source("keyboard")
                    self._emit_task_activation(current)
                    event.accept()
                    return True
            if obj is self.task_tree and event.text() == "@":
                if self.task_tree.currentItem():
                    # Reset other filters before jumping into tag search
                    self.active_tags.clear()
                    self.search.clear()
                    self.search.setFocus(Qt.ShortcutFocusReason)
                    cursor_pos = self.search.cursorPosition()
                    if cursor_pos < 0:
                        cursor_pos = len(self.search.text())
                    self.search.setCursorPosition(cursor_pos)
                    self.search.insert("@")
                    event.accept()
                    return True
            if (
                obj is self.task_tree
                and event.text() == "/"
                and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier))
            ):
                # Vim-style quick focus for plain-text task search.
                self.active_tags.clear()
                self.search.clear()
                self.search.setFocus(Qt.ShortcutFocusReason)
                self.search.setCursorPosition(len(self.search.text()))
                event.accept()
                return True
            if self._handle_task_nav_key(event):
                return True
        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            current = self.task_tree.currentItem()
            if current:
                mods = event.modifiers() & ~Qt.KeypadModifier
                keep_panel = mods == Qt.ShiftModifier
                if keep_panel:
                    self._mark_activation_source("keyboard_keep_panel")
                else:
                    self._mark_activation_source("keyboard")
                self._emit_task_activation(current)
                event.accept()
                return
        if self._handle_task_nav_key(event):
            return
        if event.key() == Qt.Key_Escape:
            self.active_tags.clear()
            self.search.clear()
            self._clear_date_filter()
            self._refresh_tasks()
            event.accept()
            return
        super().keyPressEvent(event)

    def _handle_task_nav_key(self, event) -> bool:
        """Handle up/down navigation (including vi j/k) within the task list."""
        key = event.key()
        if key == Qt.Key_J and not self._is_vi_mode():
            return False
        if key in (Qt.Key_J, Qt.Key_Down):
            self._cycle_task_selection(1)
            event.accept()
            return True
        if key == Qt.Key_K and not self._is_vi_mode():
            return False
        if key in (Qt.Key_K, Qt.Key_Up):
            self._cycle_task_selection(-1)
            event.accept()
            return True
        return False

    def _is_vi_mode(self) -> bool:
        """Check if vi mode is enabled in the parent main window."""
        parent = self.parent()
        while parent:
            if hasattr(parent, "_vi_enabled"):
                return bool(parent._vi_enabled)
            parent = parent.parent()
        try:
            return bool(config.load_vi_mode_enabled())
        except Exception:
            return False

    def _cycle_task_selection(self, direction: int) -> None:
        """Move selection up/down with wrap-around in the task list."""
        items = self._visible_items()
        if not items:
            return
        current_item = self.task_tree.currentItem()
        if current_item not in items:
            target_index = 0 if direction > 0 else len(items) - 1
        else:
            current_index = items.index(current_item)
            target_index = (current_index + direction) % len(items)
        target_item = items[target_index]
        if target_item:
            self.task_tree.setCurrentItem(target_item)
            self.task_tree.scrollToItem(target_item)
            self.task_tree.setFocus(Qt.OtherFocusReason)

    def _visible_items(self) -> list[QTreeWidgetItem]:
        items: list[QTreeWidgetItem] = []
        iterator = QTreeWidgetItemIterator(self.task_tree, QTreeWidgetItemIterator.All)
        while iterator.value():
            item = iterator.value()
            if not item.isHidden():
                items.append(item)
            iterator += 1
        return items

    def _parse_search_tags(
        self,
        text: str,
        *,
        commit_active_tag: bool = False,
    ) -> tuple[str, list[str], Optional[str]]:
        """Extract @tags and return (query_without_tags, exact_tokens, active_partial_token)."""
        tokens = [token.strip() for token in TAG_PATTERN.findall(text) if token.strip()]
        exact_tokens: list[str] = []
        seen_tokens: set[str] = set()
        for token in tokens:
            lowered = token.lower()
            if lowered in seen_tokens:
                continue
            seen_tokens.add(lowered)
            exact_tokens.append(token)
        partial_token: Optional[str] = None
        active_token = _active_tag_token(text, self.search.cursorPosition())
        if active_token:
            stripped = active_token.lstrip("@")
            if stripped:
                lowered = stripped.lower()
                if not commit_active_tag:
                    # While typing an in-progress tag token, avoid treating it as committed.
                    exact_tokens = [tok for tok in exact_tokens if tok.lower() != lowered]
                    partial_token = stripped
                elif lowered not in seen_tokens:
                    partial_token = stripped
        # Remove tags from the free-text portion
        query = TAG_PATTERN.sub(" ", text)
        if active_token:
            query = query.replace(active_token, " ")
        query = re.sub(r"\s{2,}", " ", query).strip()
        return query, exact_tokens, partial_token

    def _resolve_tag_groups(
        self,
        exact_tokens: list[str],
        partial_token: Optional[str] = None,
    ) -> tuple[list[set[str]], set[str], set[str]]:
        """Return (tag_groups, matched_tags_flat, missing_tokens) from tag tokens.

        Exact tokens are treated as exact matches. A partial token (currently edited by the cursor)
        is treated as a prefix group when possible.
        """
        groups: list[set[str]] = []
        matched: set[str] = set()
        missing: set[str] = set()
        for token in exact_tokens:
            token = token.strip()
            if not token:
                continue
            exact = {tag for tag in self._available_tags if tag.lower() == token.lower()}
            if exact:
                groups.append(exact)
                matched.update(exact)
                continue
            # Preserve explicit tag filters even when the sidebar tag list is stale.
            groups.append({token})
            matched.add(token)
            missing.add(token)
        if partial_token:
            token = partial_token.strip()
            if token:
                matches = {tag for tag in self._available_tags if tag.lower().startswith(token.lower())}
                if matches:
                    groups.append(matches)
                    matched.update(matches)
                else:
                    groups.append({token})
                    matched.add(token)
                    missing.add(token)
        return groups, matched, missing

    def _apply_search_tag_feedback(self, tokens_present: bool, has_matches: bool, has_missing: bool) -> None:
        """Color the search field based on tag validity or presence."""
        if not tokens_present:
            self.search.setStyleSheet("")
            return
        if has_matches:
            self.search.setStyleSheet(
                "color: "
                f"{theme_value('task_panel.search.match_color', '#00b33c')};"
            )
        elif has_missing:
            self.search.setStyleSheet(
                "color: "
                f"{theme_value('task_panel.search.no_match_color', '#c62828')};"
            )  # red when nothing matches
        else:
            self.search.setStyleSheet("")

    def _parse_task_date(self, value: Optional[str]) -> Optional[date]:
        if not value:
            return None
        try:
            return date.fromisoformat(value.strip())
        except Exception:
            return None

    def _apply_date_filter(self, tasks: list[dict]) -> list[dict]:
        if not self._date_filter_active():
            return tasks
        start_bound = self._date_filter_start
        end_bound = self._date_filter_end
        filtered: list[dict] = []
        for task in tasks:
            start_value = self._parse_task_date(task.get("starts") or task.get("start"))
            end_value = self._parse_task_date(task.get("due"))
            preset = self._date_filter_active_preset
            if preset == "unscheduled":
                if not (start_value or end_value):
                    filtered.append(task)
                continue
            if preset == "overdue":
                if not end_value or not end_bound or end_value > end_bound:
                    continue
                filtered.append(task)
                continue
            if preset == "should_start":
                if not start_value or not end_bound or start_value > end_bound:
                    continue
                filtered.append(task)
                continue
            candidates = [value for value in (start_value, end_value) if value]
            if not candidates:
                continue
            if start_bound and end_bound:
                if not any(start_bound <= value <= end_bound for value in candidates):
                    continue
            elif start_bound:
                if not any(value >= start_bound for value in candidates):
                    continue
            elif end_bound:
                if not any(value <= end_bound for value in candidates):
                    continue
            filtered.append(task)
        return filtered

    def _toggle_tag_selection(self, item: QListWidgetItem) -> None:
        tag = item.data(Qt.UserRole)
        if not tag:
            return
        # Prevent a pending debounced keystroke refresh from immediately overriding click-filter results.
        self._search_refresh_timer.stop()
        before = set(self.active_tags)
        if tag in self.active_tags:
            self.active_tags.remove(tag)
        else:
            self.active_tags.add(tag)
        if log_enabled("tasks_calendar"):
            print(
                f"[TASK_PANEL] tag click tag={tag!r} before={sorted(before)} after={sorted(self.active_tags)} "
                f"remote_mode={self._remote_mode} has_http_client={bool(self._http_client)}"
            )
        self._refresh_tasks()

    def _on_search_text_changed(self, _text: str) -> None:
        """Debounce remote search typing to reduce repeated API calls."""
        if self._remote_mode:
            self._search_refresh_timer.start(self._remote_search_debounce_ms)
            return
        self._search_refresh_timer.stop()
        self._refresh_tasks()

    def _trigger_search_refresh_now(self) -> None:
        """Force an immediate search refresh (e.g., Enter key)."""
        self._search_refresh_timer.stop()
        self._commit_active_search_tag()
        self._search_commit_next = True
        self._refresh_tasks()

    def _commit_active_search_tag(self) -> None:
        """Commit in-progress @tag token by inserting a separator so one Enter applies it."""
        text = self.search.text()
        cursor = self.search.cursorPosition()
        active_token = _active_tag_token(text, cursor)
        if not active_token:
            return
        if cursor < len(text) and text[cursor].isspace():
            return
        updated = f"{text[:cursor]} {text[cursor:]}"
        blocker = QSignalBlocker(self.search)
        self.search.setText(updated)
        self.search.setCursorPosition(cursor + 1)
        del blocker

    def refresh(self) -> None:
        if not config.has_active_vault():
            self._last_refresh_signature = None
            self.clear()
            return
        signature = self._refresh_signature()
        if signature == self._last_refresh_signature:
            if log_enabled("tasks_calendar"):
                print("[TASK_PANEL] refresh skipped (unchanged signature)")
            return
        self._last_refresh_signature = signature
        self._refresh_tasks()

    def _refresh_signature(self) -> tuple:
        current_version = config.get_task_index_version()
        return (
            str(self.vault_root or ""),
            bool(self._remote_mode),
            bool(self.show_completed.isChecked()),
            bool(self.show_future.isChecked()),
            bool(self.show_actionable.isChecked()),
            str(self.search.text() or ""),
            tuple(sorted(self.active_tags)),
            str(self._nav_filter_prefix or ""),
            bool(self._nav_filter_enabled),
            bool(self._include_journal),
            str(self._date_filter_active_preset or ""),
            self._date_filter_start.isoformat() if self._date_filter_start else "",
            self._date_filter_end.isoformat() if self._date_filter_end else "",
            int(current_version),
        )

    def clear(self) -> None:
        self._search_refresh_timer.stop()
        self.active_tags.clear()
        self._api_task_cache.clear()
        self._api_task_inflight.clear()
        self._api_task_error_until.clear()
        self._last_refresh_signature = None
        self.tag_list.clear()
        self.task_tree.clear()
        self._visible_tasks = []
        self._tag_source_tasks = None
        self._nav_filter_prefix = None
        self._nav_filter_enabled = True
        self._include_journal = True
        self._last_keyboard_task_id = None
        self._last_keyboard_task_path = None
        self._last_keyboard_task_line = None
        self._task_context_dirty = True
        self._task_context_initialized = False
        self._update_filter_indicator()

    def _refresh_tags(self) -> None:
        self.tag_list.blockSignals(True)
        self.tag_list.clear()
        include_done = self.show_completed.isChecked()

        def _count_tags(tasks: list[dict]) -> list[tuple[str, int]]:
            counts: dict[str, int] = {}
            for task in tasks:
                if not include_done and task.get("status") == "done":
                    continue
                tag_set = set(task.get("tags", []))
                text = task.get("text", "") or ""
                for token in re.findall(r"@[A-Za-z0-9_]+", text):
                    tag_set.add(token)
                for tag in tag_set:
                    counts[tag] = counts.get(tag, 0) + 1
            return sorted(counts.items())

        if self._tag_source_tasks is not None:
            tag_items = _count_tags(self._tag_source_tasks)
        elif self._nav_filter_prefix and self._nav_filter_enabled:
            tag_items = _count_tags(self._visible_tasks)
        else:
            try:
                all_tasks = self._fetch_tasks_api(
                    "",
                    [],
                    include_done=include_done,
                    include_ancestors=True,
                    actionable_only=False,
                )
                tag_items = _count_tags(list(all_tasks))
            except Exception:
                tag_items = []
        self._available_tags = {tag for tag, _ in tag_items}
        before_active = set(self.active_tags)
        # Drop active tags only when we have positive tag evidence.
        # For API-backed mode (including local UI+server), an empty tag set can be
        # a transient refresh state while requests are in-flight; pruning here causes
        # a clicked tag to be cleared and an immediate unfiltered reload.
        if self._http_client or self._remote_mode:
            can_prune_active_tags = bool(tag_items)
        else:
            can_prune_active_tags = True
        if self.active_tags and can_prune_active_tags:
            unavailable = {tag for tag in self.active_tags if tag not in self._available_tags}
            if unavailable:
                self.active_tags.difference_update(unavailable)
        if log_enabled("tasks_calendar"):
            print(
                "[TASK_PANEL] refresh_tags "
                f"tag_items={len(tag_items)} available={sorted(self._available_tags)} "
                f"before_active={sorted(before_active)} after_active={sorted(self.active_tags)} "
                f"can_prune={can_prune_active_tags} inflight={len(self._api_task_inflight)} "
                f"remote_mode={self._remote_mode} has_http_client={bool(self._http_client)}"
            )
        for tag, count in tag_items:
            item = QListWidgetItem(f"{tag} ({count})")
            item.setData(Qt.UserRole, tag)
            active = tag in self.active_tags
            brush_bg = self.palette().highlight() if active else self.palette().base()
            brush_fg = self.palette().highlightedText() if active else self.palette().text()
            item.setBackground(brush_bg)
            item.setForeground(brush_fg)
            self.tag_list.addItem(item)
        self.tag_list.blockSignals(False)

    def _refresh_tasks(self) -> None:
        commit_active_tag = bool(self._search_commit_next)
        self._search_commit_next = False
        current_item = self.task_tree.currentItem()
        if current_item:
            task = current_item.data(0, Qt.UserRole)
            if task:
                self._remember_task_selection(task)
        current_version = config.get_task_index_version()
        if current_version != self._task_index_version:
            self._task_index_version = current_version
            self._task_context_dirty = True
        self._update_actionable_tooltip()
        self._update_date_filter_button()
        self._configure_task_columns()
        raw_text = self.search.text().strip()
        query, tokens, partial_token = self._parse_search_tags(
            raw_text,
            commit_active_tag=commit_active_tag,
        )
        self._tag_source_tasks = None
        exact_tag_groups, exact_matched_tags, exact_missing_tokens = self._resolve_tag_groups(tokens)
        tag_groups, matched_tags, missing_tokens = self._resolve_tag_groups(tokens, partial_token=partial_token)
        tokens_present = bool(tokens) or bool(partial_token)
        has_matches = bool(tag_groups)
        self._apply_search_tag_feedback(tokens_present, has_matches, bool(missing_tokens))
        # If the search explicitly specifies tags, let those drive the active set
        if tokens:
            self.active_tags = set(exact_matched_tags)
        if log_enabled("tasks_calendar"):
            print(
                "[TASK_PANEL] refresh_tasks "
                f"query={query!r} raw={raw_text!r} tokens={tokens} partial={partial_token!r} "
                f"active_tags={sorted(self.active_tags)} matched={sorted(matched_tags)} "
                f"missing={sorted(missing_tokens)} remote_mode={self._remote_mode} "
                f"has_http_client={bool(self._http_client)}"
            )
        effective_tag_groups: list[set[str]] = []
        if tokens:
            effective_tag_groups = exact_tag_groups or ([set()] if exact_missing_tokens else [])
        elif self.active_tags:
            effective_tag_groups = [{tag} for tag in sorted(self.active_tags)]
        include_done = self.show_completed.isChecked()
        searching = bool(query) or bool(effective_tag_groups) or bool(tokens)
        actionable_toggle = self.show_actionable.isChecked()
        use_sql_tags = bool(effective_tag_groups) and all(len(group) == 1 for group in effective_tag_groups)
        sql_tags = sorted(next(iter(group)) for group in effective_tag_groups) if use_sql_tags else []
        impossible_tag_filter = any(len(group) == 0 for group in effective_tag_groups)
        if impossible_tag_filter:
            tasks = []
        else:
            actionable_only = actionable_toggle or (not include_done and not searching)
            used_degraded_fallback = False
            fallback_actionable_only = actionable_only
            tasks = self._fetch_tasks_api(
                query,
                sql_tags,
                include_done=include_done,
                include_ancestors=True,
                actionable_only=actionable_only,
            )
            if self._remote_mode and not tasks and not tokens_present and not query:
                fallback_variants: list[tuple[bool, bool]] = []
                fallback_variants.append((False, actionable_only))
                if actionable_only:
                    fallback_variants.append((False, False))
                for fb_include_ancestors, fb_actionable_only in fallback_variants:
                    if fb_include_ancestors and fb_actionable_only == actionable_only:
                        continue
                    fallback_tasks = self._fetch_tasks_api(
                        query,
                        sql_tags,
                        include_done=include_done,
                        include_ancestors=fb_include_ancestors,
                        actionable_only=fb_actionable_only,
                    )
                    if fallback_tasks:
                        tasks = fallback_tasks
                        used_degraded_fallback = True
                        fallback_actionable_only = fb_actionable_only
                        if log_enabled("tasks_calendar"):
                            print(
                                "[TASK_PANEL] using degraded remote fallback "
                                f"include_ancestors={fb_include_ancestors} actionable_only={fb_actionable_only}"
                            )
                        break
            if (
                not tasks
                and actionable_only
                and not actionable_toggle
                and not searching
            ):
                tasks = self._fetch_tasks_api(
                    query,
                    sql_tags,
                    include_done=include_done,
                    include_ancestors=True,
                    actionable_only=False,
                )
            if used_degraded_fallback and actionable_toggle and not fallback_actionable_only:
                tasks = [task for task in tasks if bool(task.get("actionable", True))]
            tasks = self._apply_nav_filter(tasks)
            if effective_tag_groups and not use_sql_tags:
                tasks = self._filter_tasks_to_tag_groups(tasks, effective_tag_groups)
            tasks = self._apply_date_filter(tasks)

        if include_done:
            self._tag_source_tasks = list(tasks)
        else:
            if impossible_tag_filter:
                self._tag_source_tasks = []
                extra_tasks = []
            elif self._remote_mode and tokens_present:
                # While remote tag typing is in progress, avoid extra fetch variants.
                self._tag_source_tasks = list(tasks)
                extra_tasks = []
            else:
                extra_tasks = self._fetch_tasks_api(
                    query,
                    sql_tags,
                    include_done=True,
                    include_ancestors=True,
                    actionable_only=False,
                )
                if self._nav_filter_prefix and self._nav_filter_enabled:
                    extra_tasks = self._apply_nav_filter(extra_tasks)
                if effective_tag_groups and not use_sql_tags:
                    extra_tasks = self._filter_tasks_to_tag_groups(extra_tasks, effective_tag_groups)
                extra_tasks = self._apply_date_filter(extra_tasks)
                self._tag_source_tasks = []
            if not self._tag_source_tasks:
                tag_source_map = {task.get("id") or task.get("path"): task for task in extra_tasks}
                for task in tasks:
                    tag_source_map.setdefault(task.get("id") or task.get("path"), task)
                self._tag_source_tasks = list(tag_source_map.values())

        self.task_tree.clear()
        self._visible_tasks = []
        if not tasks:
            self._refresh_tags()
            self._update_summary_footer([])
            return
        task_map = {task["id"]: task for task in tasks}
        visible_ids: set[str] = set()

        def _mark_visible(task_id: str) -> None:
            if task_id in visible_ids:
                return
            visible_ids.add(task_id)
            parent_id = task_map.get(task_id, {}).get("parent")
            if parent_id and parent_id in task_map:
                _mark_visible(parent_id)

        for task in tasks:
            if self.show_future.isChecked() or not self._is_future_task(task):
                _mark_visible(task["id"])

        items_by_id: dict[str, QTreeWidgetItem] = {}
        visible_tasks: list[dict] = []
        for task in sorted(tasks, key=self._task_sort_key):
            if task["id"] not in visible_ids:
                continue
            visible_tasks.append(task)
            try:
                task_level = max(0, int(task.get("level") or 0))
            except Exception:
                task_level = 0
            priority_level = min(task.get("priority", 0) or 0, 3)
            due = task.get("due", "") or ""
            start = (task.get("starts") or task.get("start") or "").strip()
            text = task["text"]
            display_text = self._format_task_text(text)
            # QTree indentation renders in column 0; mirror nesting in task text column.
            if task_level:
                display_text = ("  " * task_level) + display_text
            display_path = self._present_path(task["path"])
            priority_text, due_overdue = self._priority_time_label(task)
            due_idx = 2
            start_idx = 3 if self._show_task_start_column else None
            path_idx = 3 if (not self._show_task_start_column and self._show_task_page_column) else 4
            row = [""] * self.task_tree.columnCount()
            row[0] = priority_text
            row[1] = display_text
            row[due_idx] = due
            if self._show_task_start_column and start_idx is not None:
                row[start_idx] = start
            if self._show_task_page_column:
                row[path_idx] = display_path
            item = QTreeWidgetItem(row)
            item.setToolTip(1, text)
            item.setData(0, Qt.UserRole, task)
            item.setToolTip(1, text)
            due_fg_bg = self._due_colors(task)
            pri_brush = self._priority_brush(priority_level)
            if pri_brush:
                item.setBackground(0, pri_brush["bg"])
                item.setForeground(0, pri_brush["fg"])
            if due_fg_bg:
                fg, bg = due_fg_bg
                if bg:
                    item.setBackground(due_idx, bg)
                    item.setBackground(0, bg)
                if fg:
                    item.setForeground(due_idx, fg)
                    item.setForeground(0, fg)
            if due_overdue:
                font = item.font(0)
                font.setUnderline(True)
                item.setFont(0, font)
            if task.get("status") == "done":
                font = item.font(1)
                font.setStrikeOut(True)
                for col in range(item.columnCount()):
                    item.setFont(col, font)
            elif not task.get("actionable", True):
                muted = theme_color("task_panel.muted_text", "#666666")
                for col in range(item.columnCount()):
                    item.setForeground(col, muted)
            parent_id = task.get("parent")
            if parent_id and parent_id in items_by_id:
                items_by_id[parent_id].addChild(item)
            else:
                self.task_tree.addTopLevelItem(item)
            items_by_id[task["id"]] = item
        self._visible_tasks = visible_tasks
        self.task_tree.expandAll()
        self.task_tree.sortItems(self.sort_column, self.sort_order)
        self._restore_last_keyboard_selection(items_by_id)
        self._refresh_tags()
        self._update_summary_footer(visible_tasks)
        self._single_shot_ui(0, self._reset_horizontal_scroll)

    def _filter_tasks_to_tag_groups(self, tasks: list[dict], tag_groups: list[set[str]]) -> list[dict]:
        """Apply tag filtering for OR-within-prefix semantics."""
        if not tasks or not tag_groups:
            return tasks
        tasks_by_id = {task.get("id"): task for task in tasks if task.get("id")}
        matching_ids: set[str] = set()
        for task in tasks:
            tag_set = set(task.get("tags") or [])
            if all(any(tag in tag_set for tag in group) for group in tag_groups):
                task_id = task.get("id")
                if task_id:
                    matching_ids.add(task_id)
        if not matching_ids:
            return []
        keep_ids = set(matching_ids)
        for task_id in list(matching_ids):
            current = tasks_by_id.get(task_id, {}).get("parent")
            while current and current not in keep_ids:
                keep_ids.add(current)
                current = tasks_by_id.get(current, {}).get("parent")
        return [task for task in tasks if task.get("id") in keep_ids]

    def _handle_header_click(self, column: int) -> None:
        if column == self.sort_column:
            self.sort_order = Qt.DescendingOrder if self.sort_order == Qt.AscendingOrder else Qt.AscendingOrder
        else:
            self.sort_column = column
            self.sort_order = Qt.AscendingOrder
        self.task_tree.header().setSortIndicator(self.sort_column, self.sort_order)
        self.task_tree.sortItems(self.sort_column, self.sort_order)
        self._save_sort_state()

    def set_active_tags(self, tags: Iterable[str]) -> None:
        self.active_tags = set(tags)
        self._refresh_tasks()

    def set_navigation_filter(self, prefix: Optional[str], refresh: bool = True) -> None:
        normalized = self._normalize_task_path(prefix) if prefix else None
        # Default Journal subtree inclusion follows whether Calendar is effectively enabled.
        if normalized and normalized != self._nav_filter_prefix:
            self._include_journal = self._default_include_journal()
        if not normalized:
            self._include_journal = self._default_include_journal()
        self._nav_filter_prefix = normalized
        self._nav_filter_enabled = True
        self._update_filter_indicator()
        if refresh:
            self._refresh_tasks()
        self._last_activation_source: Optional[str] = None

    def _due_colors(self, task: dict) -> Optional[tuple[QColor | None, QColor | None]]:
        """Return (fg, bg) for due column with red/orange/yellow emphasis."""
        return due_colors_from_task(task, include_tomorrow=True)

    def _priority_brush(self, level: int) -> Optional[dict]:
        """Return background/foreground for priority level."""
        return priority_brush(level)

    def _contrast_text_color(self, bg: QColor) -> QColor:
        """Return a readable text color for the given background."""
        return contrast_text_color(bg)

    def _update_summary_footer(self, tasks: list[dict]) -> None:
        total = len(tasks)
        done = sum(1 for task in tasks if task.get("status") == "done")
        open_count = total - done
        overdue = 0
        upcoming = 0
        needs_date = 0
        today = date.today()
        for task in tasks:
            due_str = (task.get("due") or "").strip()
            start_str = (task.get("starts") or task.get("start") or "").strip()
            if not due_str and not start_str:
                needs_date += 1
            if due_str and task.get("status") != "done":
                try:
                    due_dt = date.fromisoformat(due_str)
                except ValueError:
                    continue
                if due_dt < today:
                    overdue += 1
                elif due_dt <= today + timedelta(days=7):
                    upcoming += 1
        self.summary_footer.setText(
            f"{total} tasks • open {open_count} • overdue {overdue} • due next 7d {upcoming} • needs date {needs_date}"
        )

    def _task_sort_key(self, task: dict) -> tuple:
        """Sort tasks to ensure parents are created before children."""
        return (task.get("path") or "", task.get("line") or 0, task.get("level") or 0)

    def _emit_task_activation(self, item: QTreeWidgetItem) -> None:
        task = item.data(0, Qt.UserRole)
        if not task:
            if log_enabled("tasks_calendar"):
                print(f"[TASK_PANEL] _emit_task_activation: no task data on item")
            return
        self._remember_task_selection(task)
        if log_enabled("tasks_calendar"):
            print(f"[TASK_PANEL] _emit_task_activation: emitting signal for {task['path']}:{task.get('line') or 1}")
        if not self._last_activation_source:
            self._last_activation_source = "unknown"
        self.taskActivated.emit(task["path"], task.get("line") or 1)

    def _toggle_task_checkbox(self, task: dict) -> None:
        """Toggle the checkbox state of a task by modifying its source file."""
        if not task:
            return
        is_done = task.get("status") == "done"
        path = task.get("path") or ""
        if not path:
            return
        if not str(path).startswith("/"):
            path = "/" + str(path).lstrip("/")
        line = task.get("line") or 1
        try:
            line_num = int(line)
        except (TypeError, ValueError):
            line_num = 1
        self._set_tasks_completed([{"path": str(path), "line": line_num, "task": task}], not is_done)

    def _update_task_line_checkbox(self, line: str, done: bool) -> str:
        newline = "\n" if line.endswith("\n") else ""
        base = line.rstrip("\n")
        symbol_match = re.match(r"^(?P<indent>\s*)(?P<box>[☐☑])", base)
        if symbol_match:
            indent = symbol_match.group("indent") or ""
            new_box = "☑" if done else "☐"
            return indent + new_box + base[len(indent) + 1:] + newline
        md_match = re.match(r"^(?P<prefix>\s*[-*]\s*\[)(?P<state>[ xX])(?P<suffix>\])", base)
        if md_match:
            new_state = "x" if done else " "
            prefix = md_match.group("prefix")
            suffix = md_match.group("suffix")
            rest = base[md_match.end():]
            return prefix + new_state + suffix + rest + newline
        return line

    def _set_tasks_completed(self, targets: list[dict], done: bool) -> None:
        if not config.has_active_vault():
            return
        vault_root_val = config.get_active_vault()
        if not vault_root_val:
            return
        vault_root = Path(vault_root_val)
        affected_paths = sorted(
            {
                "/" + str(t.get("path") or "").strip().lstrip("/")
                for t in targets
                if str(t.get("path") or "").strip()
            }
        )
        if affected_paths:
            self.taskDatesWillApply.emit(affected_paths)
        targets_by_path: dict[str, list[dict]] = {}
        for target in targets:
            path = target.get("path")
            if not path:
                continue
            targets_by_path.setdefault(str(path), []).append(target)
        changed_paths: set[str] = set()
        for rel_path, items in targets_by_path.items():
            file_path = vault_root / rel_path.lstrip("/")
            if not file_path.exists():
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
            except Exception:
                try:
                    lines = file_path.read_text(errors="ignore").splitlines(keepends=True)
                except Exception:
                    continue
            changed = False
            for target in items:
                line_num = target.get("line") or 1
                try:
                    line_idx = int(line_num) - 1
                except (TypeError, ValueError):
                    line_idx = 0
                if line_idx < 0 or line_idx >= len(lines):
                    continue
                original = lines[line_idx]
                updated = self._update_task_line_checkbox(original, done)
                if updated != original:
                    lines[line_idx] = updated
                    changed = True
            if not changed:
                continue
            try:
                new_content = "".join(lines)
                file_path.write_text(new_content, encoding="utf-8")
            except Exception:
                continue
            try:
                indexer.index_page(rel_path if rel_path.startswith("/") else f"/{rel_path}", new_content)
            except Exception:
                pass
            changed_paths.add("/" + str(rel_path).lstrip("/"))
        if changed_paths:
            self.taskDatesApplied.emit(sorted(changed_paths))
        self._single_shot_ui(100, self._refresh_tasks)

    def _open_task_date_quick_menu(self, role: str, targets: list[dict], anchor: QPoint) -> None:
        menu = QMenu(self)
        apply_menu_theme(menu, self.task_tree)
        for label in ("Today", "Tomorrow", "Yesterday"):
            act = menu.addAction(label)
            act.triggered.connect(lambda _, l=label: self._apply_task_date_choice(role, l, targets))
        menu.addSeparator()
        for label in ("This Week", "Next Week", "End of Week", "This Weekend", "Next Weekend"):
            act = menu.addAction(label)
            act.triggered.connect(lambda _, l=label: self._apply_task_date_choice(role, l, targets))
        menu.addSeparator()
        menu.addAction("Date...").triggered.connect(
            lambda: self._open_task_date_picker(role, targets, anchor)
        )
        if self._targets_have_date(role, targets):
            menu.addSeparator()
            menu.addAction("Clear Date").triggered.connect(
                lambda: self._clear_task_date_choice(role, targets)
            )
        menu.exec(anchor)
        self._suppress_task_activation = False

    def _collect_task_targets(self) -> list[dict]:
        targets: list[dict] = []
        for item in self.task_tree.selectedItems():
            task = item.data(0, Qt.UserRole) or {}
            path = task.get("path") or ""
            line = task.get("line") or 1
            if not path:
                continue
            if not str(path).startswith("/"):
                path = "/" + str(path).lstrip("/")
            try:
                line_num = int(line)
            except (TypeError, ValueError):
                line_num = 1
            targets.append({"path": str(path), "line": line_num, "task": task})
        return targets

    def _apply_task_date_choice(self, role: str, label: str, targets: list[dict]) -> None:
        target_date = self._resolve_quick_date(label, role)
        if not target_date:
            return
        if role == "start":
            self._update_tasks_with_dates(targets, target_date, None, apply_start=True, apply_due=False)
        else:
            self._update_tasks_with_dates(targets, None, target_date, apply_start=False, apply_due=True)

    def _clear_task_date_choice(self, role: str, targets: list[dict]) -> None:
        if role == "start":
            self._update_tasks_with_dates(targets, None, None, apply_start=True, apply_due=False)
        else:
            self._update_tasks_with_dates(targets, None, None, apply_start=False, apply_due=True)

    def _open_task_date_picker(self, role: str, targets: list[dict], anchor: Optional[QPoint] = None) -> None:
        anchor_pos = anchor or self._task_date_anchor()
        dlg = DateInsertDialog(
            self,
            anchor_pos=anchor_pos,
            accept_on_double_click=True,
            accept_on_enter=True,
            allow_nav_keys=False,
            use_vi_keys=False,
            keep_edit_focus=True,
        )
        if dlg.exec() != QDialog.Accepted:
            return
        value = dlg.selected_date_text()
        if not value:
            return
        if role == "start":
            self._update_tasks_with_dates(targets, value, None, apply_start=True, apply_due=False)
        else:
            self._update_tasks_with_dates(targets, None, value, apply_start=False, apply_due=True)

    def _task_date_anchor(self) -> QPoint:
        items = self.task_tree.selectedItems()
        if items:
            rect = self.task_tree.visualItemRect(items[0])
            return self.task_tree.viewport().mapToGlobal(rect.topRight() + QPoint(0, 4))
        return QCursor.pos()

    def _resolve_quick_date(self, label: str, role: str) -> Optional[str]:
        today = date.today()
        weekday = today.weekday()
        week_start = today - timedelta(days=weekday)
        week_end = week_start + timedelta(days=6)
        next_week_start = week_start + timedelta(days=7)
        next_week_end = next_week_start + timedelta(days=6)
        this_weekend_start = week_start + timedelta(days=5)
        this_weekend_end = week_start + timedelta(days=6)
        next_weekend_start = next_week_start + timedelta(days=5)
        next_weekend_end = next_week_start + timedelta(days=6)

        if label == "Today":
            target = today
        elif label == "Tomorrow":
            target = today + timedelta(days=1)
        elif label == "Yesterday":
            target = today - timedelta(days=1)
        elif label == "This Week":
            target = week_start if role == "start" else week_end
        elif label == "Next Week":
            target = next_week_start if role == "start" else next_week_end
        elif label == "End of Week":
            target = week_end
        elif label == "This Weekend":
            target = this_weekend_start if role == "start" else this_weekend_end
        elif label == "Next Weekend":
            target = next_weekend_start if role == "start" else next_weekend_end
        else:
            return None
        return target.isoformat()

    def _targets_have_date(self, role: str, targets: list[dict]) -> bool:
        for target in targets:
            task = target.get("task") or {}
            if role == "start":
                value = (task.get("starts") or task.get("start") or "").strip()
            else:
                value = (task.get("due") or "").strip()
            if value:
                return True
        return False

    def _update_tasks_with_dates(
        self,
        targets: list[dict],
        start_value: Optional[str],
        due_value: Optional[str],
        *,
        apply_start: bool,
        apply_due: bool,
    ) -> None:
        affected_paths = sorted(
            {
                "/" + str(t.get("path") or "").strip().lstrip("/")
                for t in targets
                if str(t.get("path") or "").strip()
            }
        )
        if affected_paths:
            self.taskDatesWillApply.emit(affected_paths)
        if self._http_client:
            payload = {
                "targets": [
                    {
                        "path": (t.get("path") or ""),
                        "line": int(t.get("line") or 1),
                    }
                    for t in targets
                    if t.get("path")
                ],
                "start_value": start_value,
                "due_value": due_value,
                "apply_start": apply_start,
                "apply_due": apply_due,
                "clear_start": False,
                "clear_due": False,
            }
            try:
                resp = self._http_client.post("/api/tasks/update-dates", json=payload)
                resp.raise_for_status()
            except Exception as exc:
                print(f"[TASK_PANEL] Failed to update task dates via API: {exc}")
                self._single_shot_ui(150, self._refresh_tasks)
                return
            if affected_paths:
                self.taskDatesApplied.emit(affected_paths)
            self._single_shot_ui(150, self._refresh_tasks)
            return
        if not config.has_active_vault():
            return
        vault_root_val = config.get_active_vault()
        if not vault_root_val:
            return
        vault_root = Path(vault_root_val)
        targets_by_path: dict[str, list[dict]] = {}
        for target in targets:
            path = target.get("path")
            if not path:
                continue
            targets_by_path.setdefault(path, []).append(target)
        for rel_path, items in targets_by_path.items():
            file_path = vault_root / rel_path.lstrip("/")
            if not file_path.exists():
                continue
            try:
                lines = file_path.read_text(encoding="utf-8").splitlines(keepends=True)
            except Exception:
                try:
                    lines = file_path.read_text(errors="ignore").splitlines(keepends=True)
                except Exception:
                    continue
            changed = False
            for target in items:
                line_num = target.get("line") or 1
                line_idx = line_num - 1
                if line_idx < 0 or line_idx >= len(lines):
                    continue
                original = lines[line_idx]
                updated = self._update_task_line_dates(
                    original,
                    start_value=start_value,
                    due_value=due_value,
                    apply_start=apply_start,
                    apply_due=apply_due,
                )
                if updated != original:
                    lines[line_idx] = updated
                    changed = True
            if changed:
                try:
                    new_content = "".join(lines)
                    file_path.write_text(new_content, encoding="utf-8")
                except Exception:
                    pass
                try:
                    indexer.index_page(rel_path if rel_path.startswith("/") else f"/{rel_path}", new_content)
                except Exception:
                    pass
        if affected_paths:
            self.taskDatesApplied.emit(affected_paths)
        self._single_shot_ui(150, self._refresh_tasks)

    def _update_task_line_dates(
        self,
        line: str,
        *,
        start_value: Optional[str],
        due_value: Optional[str],
        apply_start: bool,
        apply_due: bool,
    ) -> str:
        newline = "\n" if line.endswith("\n") else ""
        base = line.rstrip("\n")
        existing_start = None
        existing_due = None
        start_match = START_TOKEN_PATTERN.search(base)
        if start_match:
            existing_start = start_match.group(1)
        due_match = DUE_TOKEN_PATTERN.search(base)
        if due_match:
            existing_due = due_match.group(1)
        final_start = existing_start
        final_due = existing_due
        if apply_start:
            final_start = start_value
        if apply_due:
            final_due = due_value
        cleaned = re.sub(r"\s*[<>][0-9]{4}-[0-9]{2}-[0-9]{2}", "", base).rstrip()
        if final_start:
            cleaned += f" >{final_start}"
        if final_due:
            cleaned += f" <{final_due}"
        return cleaned + newline

    def _on_task_double_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        # If double-clicking column 0 (priority/checkbox), toggle the task instead of opening it
        if col == 0:
            task = item.data(0, Qt.UserRole)
            if task:
                self._toggle_task_checkbox(task)
            return
        due_idx = 2
        start_idx = 3 if self._show_task_start_column else None
        if col == due_idx or (start_idx is not None and col == start_idx):
            self._suppress_task_activation = True
            self._open_task_date_picker_for_column(item, col)
            return
        
        self._mark_activation_source("mouse")
        self._emit_task_activation(item)

    def _on_task_activated(self, item: QTreeWidgetItem, *_args) -> None:
        if self._suppress_task_activation:
            self._suppress_task_activation = False
            return
        # itemActivated can fire for mouse or keyboard; default to unknown unless set elsewhere
        if not self._last_activation_source:
            self._last_activation_source = "unknown"
        self._emit_task_activation(item)

    def _on_task_item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        due_idx = 2
        start_idx = 3 if self._show_task_start_column else None
        if col == due_idx or (start_idx is not None and col == start_idx):
            self._suppress_task_activation = True
            self._open_task_date_picker_for_column(item, col)

    def _open_task_date_picker_for_column(self, item: QTreeWidgetItem, col: int) -> None:
        if item and not item.isSelected():
            self.task_tree.clearSelection()
            item.setSelected(True)
        targets = self._collect_task_targets()
        if not targets:
            return
        task = item.data(0, Qt.UserRole) or {}
        path = task.get("path") or ""
        line = task.get("line") or 1
        if not path:
            return
        if not str(path).startswith("/"):
            path = "/" + str(path).lstrip("/")
        try:
            line_num = int(line)
        except (TypeError, ValueError):
            line_num = 1
        role = "start" if (self._show_task_start_column and col == 3) else "due"
        anchor = self._task_date_anchor_for_item(item, col)
        self._open_task_date_quick_menu(role, targets, anchor)

    def _task_date_anchor_for_item(self, item: QTreeWidgetItem, col: int) -> QPoint:
        rect = self.task_tree.visualItemRect(item)
        header = self.task_tree.header()
        try:
            col_x = header.sectionViewportPosition(col)
        except Exception:
            col_x = rect.left()
        anchor = QPoint(col_x + rect.left(), rect.bottom() + 2)
        return self.task_tree.viewport().mapToGlobal(anchor)

    def _open_task_date_context_menu(self, pos) -> None:
        col = self.task_tree.columnAt(pos.x())
        if col < 0:
            return
        item = self.task_tree.itemAt(pos)
        if not item:
            return
        if not item.isSelected():
            self.task_tree.clearSelection()
            item.setSelected(True)
        due_idx = 2
        start_idx = 3 if self._show_task_start_column else None
        if col == due_idx or (start_idx is not None and col == start_idx):
            targets = self._collect_task_targets()
            if not targets:
                return
            role = "start" if (self._show_task_start_column and col == 3) else "due"
            anchor = self.task_tree.viewport().mapToGlobal(pos)
            self._open_task_date_quick_menu(role, targets, anchor)
            return
        task = item.data(0, Qt.UserRole) or {}
        if not task:
            return
        targets = self._collect_task_targets()
        if not targets:
            return
        any_done = any((t.get("task") or {}).get("status") == "done" for t in targets)
        any_open = any((t.get("task") or {}).get("status") != "done" for t in targets)
        menu = QMenu(self)
        apply_menu_theme(menu, self.task_tree)
        if any_open:
            menu.addAction("Mark Complete").triggered.connect(
                lambda: self._set_tasks_completed(targets, True)
            )
        if any_done:
            menu.addAction("Reopen Task").triggered.connect(
                lambda: self._set_tasks_completed(targets, False)
            )
        if menu.actions():
            menu.exec(self.task_tree.viewport().mapToGlobal(pos))

    def _mark_activation_source(self, source: str) -> None:
        self._last_activation_source = source

    def consume_activation_source(self) -> Optional[str]:
        src = self._last_activation_source
        self._last_activation_source = None
        return src

    def _remember_task_selection(self, task: dict) -> None:
        """Keep track of the last keyboard-activated task for later restoration."""
        self._last_keyboard_task_id = task.get("id")
        self._last_keyboard_task_path = task.get("path")
        line = task.get("line")
        try:
            self._last_keyboard_task_line = int(line) if line is not None else None
        except Exception:
            self._last_keyboard_task_line = None

    def _restore_last_keyboard_selection(self, items_by_id: dict[str, QTreeWidgetItem]) -> None:
        """Re-select the last keyboard-activated task if it is still visible."""
        if not (self._last_keyboard_task_id or self._last_keyboard_task_path):
            return
        target = items_by_id.get(self._last_keyboard_task_id) if self._last_keyboard_task_id else None
        if not target and self._last_keyboard_task_path:
            desired_line = self._last_keyboard_task_line
            for item in items_by_id.values():
                task = item.data(0, Qt.UserRole) or {}
                if task.get("path") != self._last_keyboard_task_path:
                    continue
                if desired_line and task.get("line") and task.get("line") != desired_line:
                    continue
                target = item
                break
        if target:
            self.task_tree.setCurrentItem(target)
            self.task_tree.scrollToItem(target)

    def _present_path(self, path: str) -> str:
        return path_to_colon(path)

    def _fetch_tasks_api(
        self,
        query: str,
        tags: list[str],
        *,
        include_done: bool,
        include_ancestors: bool,
        actionable_only: bool,
    ) -> list[dict]:
        if self._http_client:
            cache_key = (
                query,
                tuple(tags),
                bool(include_done),
                bool(include_ancestors),
                bool(actionable_only),
            )
            cached = self._api_task_cache.get(cache_key)
            now = time.monotonic()
            if cached and (now - cached[0]) <= self._api_task_cache_ttl:
                return cached[1]
            params: dict = {
                "query": query,
                "include_done": include_done,
                "include_ancestors": include_ancestors,
                "actionable_only": actionable_only,
            }
            if tags:
                params["tags"] = tags
            error_until = self._api_task_error_until.get(cache_key, 0.0)
            if now < error_until:
                return cached[1] if cached else []
            if cache_key in self._api_task_inflight:
                return cached[1] if cached else []
            self._queue_remote_task_fetch(cache_key, params)
            return cached[1] if cached else []
        return config.fetch_tasks(
            query,
            tags,
            include_done=include_done,
            include_ancestors=include_ancestors,
            actionable_only=actionable_only,
        )
    
    def set_vault_root(self, vault_root: str) -> None:
        """Set vault root for task filtering preferences."""
        self.vault_root = vault_root
        self._apply_show_future_preference()
        self._task_context_dirty = True
        self._task_context_initialized = False
        self._last_refresh_signature = None
        self._api_task_cache.clear()
        self._api_task_inflight.clear()
        self._api_task_error_until.clear()
        self._set_ai_chat_enabled(False)
        if self._ai_chat_panel:
            try:
                self._ai_chat_panel.set_vault_root(vault_root)
            except Exception:
                pass

    def set_http_client(self, http_client) -> None:
        self._last_refresh_signature = None
        self._api_task_cache.clear()
        self._api_task_inflight.clear()
        self._api_task_error_until.clear()
        self._api_task_result_queue = queue.Queue()
        self._http_client = http_client
        self._vector_api = VectorAPIClient(http_client)
        if self._ai_chat_panel:
            try:
                self._ai_chat_panel.set_api_client(http_client)
            except Exception:
                pass

    def set_remote_mode(self, remote_mode: bool) -> None:
        changed = self._remote_mode != bool(remote_mode)
        self._remote_mode = bool(remote_mode)
        if changed:
            self._search_refresh_timer.stop()
            self._last_refresh_signature = None
            self._api_task_cache.clear()
            self._api_task_inflight.clear()
            self._api_task_error_until.clear()

    def _queue_remote_task_fetch(self, cache_key: tuple, params: dict) -> None:
        if not self._http_client or cache_key in self._api_task_inflight:
            return
        self._api_task_inflight.add(cache_key)
        client = self._http_client
        args = dict(params)

        def _worker() -> None:
            started = time.perf_counter()
            try:
                if log_enabled("tasks_calendar"):
                    print(
                        f"[TASK_PANEL] dispatch /api/tasks "
                        f"query={args.get('query', '')!r} tags={args.get('tags', [])} "
                        f"include_done={args.get('include_done')} "
                        f"include_ancestors={args.get('include_ancestors')} "
                        f"actionable_only={args.get('actionable_only')} "
                        f"remote_mode={self._remote_mode}"
                    )
                resp = client.get("/api/tasks", params=args)
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("items", [])
                latency_ms = (time.perf_counter() - started) * 1000.0
                self._api_task_result_queue.put(("ok", cache_key, items, latency_ms))
            except Exception as exc:
                latency_ms = (time.perf_counter() - started) * 1000.0
                exc_name = exc.__class__.__name__.lower()
                error_text = str(exc)
                if "readtimeout" in exc_name:
                    error_text = (
                        "Client read timeout waiting for /api/tasks response "
                        "(request may not appear in server logs)"
                    )
                self._api_task_result_queue.put(("error", cache_key, error_text, latency_ms))

        threading.Thread(target=_worker, daemon=True).start()

    def _drain_remote_task_results(self) -> None:
        had_success = False
        while True:
            try:
                state, cache_key_obj, payload, latency_ms_obj = self._api_task_result_queue.get_nowait()
            except queue.Empty:
                break
            cache_key = tuple(cache_key_obj)
            self._api_task_inflight.discard(cache_key)
            latency_ms = float(latency_ms_obj)
            if state == "ok":
                items = payload if isinstance(payload, list) else []
                self._api_task_cache[cache_key] = (time.monotonic(), items)
                self._api_task_error_until.pop(cache_key, None)
                if self._remote_mode:
                    self.remoteRequestObserved.emit("ok", latency_ms, "GET /api/tasks")
                had_success = True
            else:
                error_text = str(payload or "unknown error")
                now = time.monotonic()
                # Back off longer on remote task endpoint failures to avoid request storms.
                self._api_task_error_until[cache_key] = now + 5.0
                if self._remote_mode:
                    if "504" in error_text:
                        error_text = "Task endpoint timed out (HTTP 504)"
                    self.remoteRequestObserved.emit("error", latency_ms, f"/api/tasks failed: {error_text}")
        if had_success:
            self._single_shot_ui(0, self._refresh_tasks)

    def set_ai_enabled(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._ai_enabled:
            return
        self._ai_enabled = enabled
        if self._ai_toggle_btn:
            self._ai_toggle_btn.setVisible(enabled)
            if not enabled:
                self._ai_toggle_btn.setChecked(False)
        if enabled:
            self._setup_ai_panel()
        else:
            self.content_stack.setCurrentWidget(self.task_content)

    def _on_show_future_toggled(self, checked: bool) -> None:
        if config.has_active_vault():
            config.save_show_future_tasks(checked)
        self._refresh_tasks()

    def _is_future_task(self, task: dict) -> bool:
        """Return True if task has a start date in the future."""
        start_str = (task.get("starts") or "").strip()
        if not start_str:
            return False
        try:
            start_dt = date.fromisoformat(start_str)
        except ValueError:
            return False
        return start_dt > date.today()

    def _apply_show_future_preference(self) -> None:
        """Sync the checkbox with saved preference and refresh the list."""
        if not config.has_active_vault():
            return
        saved = config.load_show_future_tasks()
        self.show_future.blockSignals(True)
        self.show_future.setChecked(saved)
        self.show_future.blockSignals(False)
        self._refresh_tasks()
