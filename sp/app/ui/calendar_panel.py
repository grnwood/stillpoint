from __future__ import annotations

from pathlib import Path
import html
import tempfile
import re
import os
import calendar
import time
from datetime import date as Date, datetime, timedelta
from typing import Optional, Callable

from PySide6.QtCore import Qt, Signal, QDate, QEvent, QTimer, QByteArray, QRect, QMimeData, QUrl, QPoint
from PySide6.QtGui import QFont, QTextCharFormat, QKeyEvent, QColor, QIcon, QPainter, QPixmap, QPalette, QBrush, QDrag, QDesktopServices
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import (
    QApplication,
    QCalendarWidget,
    QTableView,
    QAbstractItemView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
    QMenu,
    QLabel,
    QMessageBox,
    QHBoxLayout,
    QSplitter,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QCheckBox,
    QSizePolicy,
    QToolButton,
    QTextBrowser,
    QStyle,
    QTabWidget,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QDialog,
)
from PySide6.QtCore import QSize
from PySide6.QtSvg import QSvgRenderer
from shiboken6 import Shiboken

from sp.server.adapters.files import LEGACY_SUFFIX, PAGE_SUFFIX, PAGE_SUFFIXES
from sp.app import config
from sp.app import indexer
from .path_utils import path_to_colon
from .task_style import (
    contrast_text_color,
    due_colors_from_due_str,
    priority_brush,
    priority_time_label,
    relative_day_label,
)
from markdown import markdown as render_markdown
from .ai_chat_panel import ApiWorker, ServerManager
from .date_insert_dialog import DateInsertDialog


PATH_ROLE = Qt.UserRole + 1
LINE_ROLE = Qt.UserRole + 2
RECENT_ACTION_ROLE = Qt.UserRole + 50
TAG_PATTERN = re.compile(r"(?<![\w.+-])@([A-Za-z0-9_]+)")
DUE_TOKEN_PATTERN = re.compile(r"<([0-9]{4}-[0-9]{2}-[0-9]{2})")
START_TOKEN_PATTERN = re.compile(r">([0-9]{4}-[0-9]{2}-[0-9]{2})")
PRINT_LINK_PATTERN = re.compile(
    r"(?P<md>\[(?P<md_label>[^\]]+)\]\((?P<md_url>[^\s)]+)\))|"
    r"(?P<wiki>\[(?P<wiki_link>[^\]|]+)\|(?P<wiki_label>[^\]]+)\])"
)


class MultiSelectCalendarDelegate(QStyledItemDelegate):
    """Custom delegate to paint multi-selected dates with highlighting."""
    
    def __init__(self, parent=None, calendar_widget=None):
        super().__init__(parent)
        self.multi_selected_dates = set()
        self.highlight_color = QColor("#4A90E2")
        self.text_color = QColor("#FFFFFF")
        self.calendar_widget = calendar_widget

    def _date_for_index(self, index) -> QDate:
        date_val = index.data(Qt.UserRole)
        if isinstance(date_val, QDate) and date_val.isValid():
            return date_val
        day_val = index.data(Qt.DisplayRole)
        if not isinstance(day_val, int):
            return QDate()
        if not self.calendar_widget:
            return QDate()
        year = self.calendar_widget.yearShown()
        month = self.calendar_widget.monthShown()
        first = QDate(year, month, 1)
        if not first.isValid():
            return QDate()
        row = index.row()
        if row == 0 and day_val > 20:
            prev = first.addMonths(-1)
            return QDate(prev.year(), prev.month(), day_val)
        if row >= 4 and day_val < 15:
            nxt = first.addMonths(1)
            return QDate(nxt.year(), nxt.month(), day_val)
        return QDate(year, month, day_val)
    
    def paint(self, painter, option, index):
        # Try multiple ways to get the date from this cell
        date_val = self._date_for_index(index)
        
        # Check if this EXACT date (year, month, day) is in the multi-selection
        is_multi_selected = False
        if isinstance(date_val, QDate) and date_val.isValid():
            # Check if date matches any in multi_selected_dates (exact match: year, month, day)
            for sel_date in self.multi_selected_dates:
                if (sel_date.isValid() and 
                    sel_date.year() == date_val.year() and 
                    sel_date.month() == date_val.month() and 
                    sel_date.day() == date_val.day()):
                    is_multi_selected = True
                    break
        
        if is_multi_selected:
            # Paint base without selection state
            opt = QStyleOptionViewItem(option)
            opt.state &= ~QStyle.State_Selected
            super().paint(painter, opt, index)
            
            # Overlay our custom highlight
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing)
            
            # Paint background
            rect = option.rect.adjusted(2, 2, -2, -2)
            painter.setBrush(QBrush(self.highlight_color))
            painter.setPen(Qt.NoPen)
            painter.drawRoundedRect(rect, 4, 4)
            
            # Draw text
            painter.setPen(self.text_color)
            font = QFont(option.font)
            font.setBold(True)
            font.setWeight(QFont.Bold)
            painter.setFont(font)
            
            text = str(index.data(Qt.DisplayRole))
            if text:
                painter.drawText(option.rect, Qt.AlignCenter, text)
            
            painter.restore()
        else:
            # Use default painting
            super().paint(painter, option, index)


class InsightDragList(QListWidget):
    """QListWidget that drags page paths into the editor."""

    def startDrag(self, supportedActions):  # type: ignore[override]
        item = self.currentItem()
        if not item:
            selected = self.selectedItems()
            item = selected[0] if selected else None
        if not item:
            return super().startDrag(supportedActions)
        path = item.data(PATH_ROLE)
        if not path:
            return super().startDrag(supportedActions)
        mime = QMimeData()
        mime.setText(str(path))
        mime.setData("application/x-stillpoint-path", str(path).encode("utf-8"))
        label = item.text()
        path_text = str(path)
        if "#" not in path_text:
            try:
                label = Path(path_text).stem or label
            except Exception:
                pass
        if label:
            mime.setData("application/x-stillpoint-label", label.encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class CalendarPanel(QWidget):
    """Calendar tab with a journal-focused navigation tree."""

    dateActivated = Signal(int, int, int)  # year, month, day
    pageActivated = Signal(str)  # relative path to a page
    taskActivated = Signal(str, int)  # path, line number
    tasksUpdated = Signal()
    openInWindowRequested = Signal(str)
    pageAboutToBeDeleted = Signal(str)  # emitted BEFORE page deletion (for editor unload)
    pageDeleted = Signal(str)  # emitted AFTER page is deleted

    def __init__(
        self,
        parent=None,
        *,
        font_size_key: str = "calendar_font_size_tabbed",
        splitter_key: str = "calendar_splitter_tabbed",
        header_state_key: str = "calendar_tasks_header_tabbed",
        http_client=None,
        api_base: Optional[str] = None,
    ) -> None:
        super().__init__(parent)
        self.http = http_client
        self.api_base = api_base or os.getenv("ZIMX_API_BASE", "http://127.0.0.1:8734")
        self._font_size_key = font_size_key
        self._font_size = config.load_panel_font_size(self._font_size_key, max(8, self.font().pointSize() or 12))
        self._splitter_key = splitter_key
        self._splitter_save_timer = QTimer(self)
        self._splitter_save_timer.setInterval(200)
        self._splitter_save_timer.setSingleShot(True)
        self._splitter_save_timer.timeout.connect(self._save_splitter_sizes)
        self._header_state_key = header_state_key
        self._header_save_timer = QTimer(self)
        self._header_save_timer.setInterval(200)
        self._header_save_timer.setSingleShot(True)
        self._header_save_timer.timeout.connect(self._save_header_state)
        self._due_task_count: int = 0
        self._ai_enabled = config.load_enable_ai_chats()
        self._ai_worker: ApiWorker | None = None
        self._ai_response_buffer: str = ""
        self._page_text_provider: Optional[Callable[[Optional[str]], str]] = None
        self._ai_last_markdown: str = ""
        self._recent_fetch_guard: int = 0
        self._recent_pending_params: Optional[tuple[str, str, Optional[str]]] = None
        self._recent_fetching: bool = False
        self._recent_data_loaded: bool = False
        self._task_date_filter_opener: Optional[Callable[[Optional[QWidget]], None]] = None
        self._task_date_filter_setter: Optional[Callable[[Optional[Date], Optional[Date], Optional[str]], None]] = None
        self._task_date_dialog: QDialog | None = None
        self._task_date_start_cal: QCalendarWidget | None = None
        self._task_date_due_cal: QCalendarWidget | None = None
        self._task_date_apply_start: QCheckBox | None = None
        self._task_date_apply_due: QCheckBox | None = None
        self._task_date_clear_start = False
        self._task_date_clear_due = False
        self._task_date_targets: list[dict] = []
        self._suppress_task_activation = False
        self._api_task_cache: dict[tuple, tuple[float, list[dict]]] = {}
        self._api_task_cache_ttl = 0.5

        self.calendar = QCalendarWidget()
        self.calendar.setGridVisible(True)
        self.calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        # Determine light vs dark mode
        palette = QApplication.palette()
        is_light = palette.color(QPalette.Window).lightness() > 128
        base_bg = palette.color(QPalette.Base)
        alt_bg = palette.color(QPalette.AlternateBase)
        text_fg = palette.color(QPalette.Text)
        
        # Prominent selected day colors
        selected_bg = "#2D7FF9"
        selected_text = "#FFFFFF"
        self._calendar_selected_bg = QColor(selected_bg)
        self._calendar_selected_text = QColor(selected_text)
        
        # Friendly calendar styling
        grid_color = "#DDDDDD" if is_light else "#555555"
        header_bg = alt_bg.name() if alt_bg.isValid() else ("#3A3A3A" if not is_light else "#F5F5F5")
        
        self.calendar.setStyleSheet(
            f"""
            QCalendarWidget QWidget#qt_calendar_navigationbar {{
                background-color: {header_bg};
            }}
            QCalendarWidget QWidget {{
                alternate-background-color: palette(base);
            }}
            QCalendarWidget QToolButton {{
                padding: 6px 8px;
                font-weight: bold;
                border-radius: 4px;
                background-color: {header_bg};
            }}
            QCalendarWidget QToolButton:hover {{
                background-color: {selected_bg};
                color: {selected_text};
            }}
            QCalendarWidget QMenu {{
                background-color: palette(base);
            }}
            QCalendarWidget QSpinBox {{
                border-radius: 4px;
                padding: 4px;
            }}
            QCalendarWidget QTableView {{
                selection-background-color: {selected_bg};
                selection-color: {selected_text};
                gridline-color: {grid_color};
                border-radius: 6px;
            }}
            QCalendarWidget QTableView::item {{
                border: 1px solid {grid_color};
                padding: 6px;
                border-radius: 4px;
            }}
            QCalendarWidget QTableView::item:selected {{
                background-color: {selected_bg};
                color: {selected_text};
                font-weight: bold;
                border: 2px solid {selected_bg};
            }}
            QCalendarWidget QTableView::item:hover {{
                background-color: {selected_bg};
                color: {selected_text};
            }}
            """
        )
        self.calendar.clicked.connect(self._on_date_clicked)
        self.calendar.currentPageChanged.connect(self._on_month_changed)
        self.calendar.selectionChanged.connect(self._update_today_visibility)
        self.calendar.setSelectedDate(QDate.currentDate())
        self.calendar.setFocusPolicy(Qt.StrongFocus)
        # Install event filter on calendar itself to capture modifier keys
        self.calendar.installEventFilter(self)
        self._update_today_visibility()
        self.calendar_view: QTableView | None = None
        self._suppress_next_click = False
        self._pending_shift_click = False
        self.multi_selected_dates: set[QDate] = {self.calendar.selectedDate()}
        self._selection_anchor: QDate | None = self.calendar.selectedDate()
        
        # Create custom delegate for multi-selection highlighting
        self.calendar_delegate = MultiSelectCalendarDelegate(calendar_widget=self.calendar)
        self.calendar_delegate.multi_selected_dates = self.multi_selected_dates
        # Determine colors based on theme
        palette = QApplication.palette()
        is_light = palette.color(QPalette.Window).lightness() > 128
        self.calendar_delegate.highlight_color = self._calendar_selected_bg
        self.calendar_delegate.text_color = self._calendar_selected_text

        self.prev_calendar = QCalendarWidget()
        self.prev_calendar.setGridVisible(True)
        self.prev_calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.prev_calendar.setStyleSheet(self.calendar.styleSheet())
        self.prev_calendar.setStyleSheet(
            self.calendar.styleSheet()
            + "\nQCalendarWidget { color: #a0a0a0; }"
            + "\nQCalendarWidget QTableView::item { color: #a0a0a0; }"
            + "\nQCalendarWidget QTableView::item:selected { background: transparent; color: #a0a0a0; border: none; }"
        )
        try:
            self.prev_calendar.setNavigationBarVisible(False)
        except Exception:
            pass
        try:
            self.prev_calendar.setSelectionMode(QCalendarWidget.NoSelection)
        except Exception:
            pass
        self.prev_calendar.setEnabled(False)
        self.prev_calendar.setFocusPolicy(Qt.NoFocus)
        self.prev_calendar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.prev_month_label = QLabel("")
        self.prev_month_label.setAlignment(Qt.AlignCenter)
        self.prev_month_label.setStyleSheet("color: #9a9a9a; font-weight: 600; padding: 2px 0;")
        self.prev_calendar_container = QWidget()
        prev_layout = QVBoxLayout(self.prev_calendar_container)
        prev_layout.setContentsMargins(0, 0, 0, 0)
        prev_layout.setSpacing(4)
        prev_layout.addWidget(self.prev_month_label)
        prev_layout.addWidget(self.prev_calendar)
        self.prev_calendar_container.setVisible(False)

        self.next_calendar = QCalendarWidget()
        self.next_calendar.setGridVisible(True)
        self.next_calendar.setVerticalHeaderFormat(QCalendarWidget.NoVerticalHeader)
        self.next_calendar.setStyleSheet(self.calendar.styleSheet())
        self.next_calendar.setStyleSheet(
            self.calendar.styleSheet()
            + "\nQCalendarWidget { color: #a0a0a0; }"
            + "\nQCalendarWidget QTableView::item { color: #a0a0a0; }"
            + "\nQCalendarWidget QTableView::item:selected { background: transparent; color: #a0a0a0; border: none; }"
        )
        try:
            self.next_calendar.setNavigationBarVisible(False)
        except Exception:
            pass
        try:
            self.next_calendar.setSelectionMode(QCalendarWidget.NoSelection)
        except Exception:
            pass
        self.next_calendar.setEnabled(False)
        self.next_calendar.setFocusPolicy(Qt.NoFocus)
        self.next_calendar.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.next_month_label = QLabel("")
        self.next_month_label.setAlignment(Qt.AlignCenter)
        self.next_month_label.setStyleSheet("color: #9a9a9a; font-weight: 600; padding: 2px 0;")
        self.next_calendar_container = QWidget()
        next_layout = QVBoxLayout(self.next_calendar_container)
        next_layout.setContentsMargins(0, 0, 0, 0)
        next_layout.setSpacing(4)
        next_layout.addWidget(self.next_month_label)
        next_layout.addWidget(self.next_calendar)
        self.next_calendar_container.setVisible(False)

        self._show_three_calendars = False
        self._syncing_calendars = False
        self._hide_insights_tabs = False
        self._main_splitter_sizes_before_hide: Optional[list[int]] = None

        self._attach_calendar_view()
        self.day_insights = QWidget()
        self.day_insights.setMinimumWidth(180)
        self.day_insights_layout = QVBoxLayout(self.day_insights)
        self.day_insights_layout.setContentsMargins(8, 8, 8, 8)
        self.day_insights_layout.setSpacing(6)
        self.insight_title = QLabel("No date selected")
        self.insight_title.setStyleSheet(
            "font-weight: bold; background:#30475e; color:white; padding:4px 8px; border-radius:4px;"
        )
        # Title row with an optional Clear button when multiple days are selected
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)
        title_row.setSpacing(6)
        self.filter_btn = QPushButton("Clear")
        self.filter_btn.setVisible(False)
        self.filter_btn.setStyleSheet("background:#e53935; color:white; font-weight:bold; padding:2px 6px;")
        self.filter_btn.setCursor(self.insight_title.cursor())
        self.filter_btn.clicked.connect(self._clear_filter)
        title_row.addWidget(self.insight_title)
        title_row.addStretch(1)
        title_row.addWidget(self.filter_btn)
        self.insight_counts = QLabel("")
        self.insight_tags = QLabel("")
        # Keep the date label on one line; allow counts and tags to wrap if needed.
        self.insight_title.setWordWrap(False)
        for lbl in (self.insight_counts, self.insight_tags):
            lbl.setWordWrap(True)
        # Title row widget (label + optional clear button)
        title_container = QWidget()
        title_container.setLayout(title_row)
        self.zoom_out_btn = QToolButton()
        self.zoom_out_btn.setText("−")
        self.zoom_out_btn.setToolTip("Decrease font size")
        self.zoom_out_btn.setAutoRaise(True)
        self.zoom_out_btn.setFixedSize(26, 26)
        self.zoom_out_btn.clicked.connect(lambda: self._adjust_font_size(-1))
        self.zoom_in_btn = QToolButton()
        self.zoom_in_btn.setText("+")
        self.zoom_in_btn.setToolTip("Increase font size")
        self.zoom_in_btn.setAutoRaise(True)
        self.zoom_in_btn.setFixedSize(26, 26)
        self.zoom_in_btn.clicked.connect(lambda: self._adjust_font_size(1))
        cal_zoom_row = QHBoxLayout()
        cal_zoom_row.setContentsMargins(0, 0, 0, 0)
        cal_zoom_row.setSpacing(6)
        cal_zoom_row.addStretch(1)
        cal_zoom_row.addWidget(self.zoom_out_btn)
        cal_zoom_row.addWidget(self.zoom_in_btn)
        self.day_insights_layout.addWidget(self.insight_counts)
        self.day_insights_layout.addWidget(self.insight_tags)

        self.subpage_list = InsightDragList()
        self.subpage_list.itemActivated.connect(self._open_insight_link)
        self.subpage_list.itemClicked.connect(self._open_insight_link)
        self.subpage_list.setDragEnabled(True)
        self.subpage_list.setAlternatingRowColors(True)
        self.subpage_list.setStyleSheet(
            """
            QListWidget { background: #2f2f2f; color: #f0f0f0; }
            QListWidget::item { padding: 2px 4px; background: #2f2f2f; }
            QListWidget::item:alternate { background: #3a3a3a; }
            """
        )
        # Ensure items do not wrap (single-line, elide) and use uniform sizing
        try:
            self.subpage_list.setWordWrap(False)
            self.subpage_list.setUniformItemSizes(True)
        except Exception:
            pass

        self.headings_list = InsightDragList()
        self.headings_list.itemActivated.connect(self._open_insight_link)
        self.headings_list.itemClicked.connect(self._open_insight_link)
        self.headings_list.setDragEnabled(True)
        self.headings_list.setAlternatingRowColors(True)
        self.headings_list.setStyleSheet(
            """
            QListWidget { background: #2f2f2f; color: #f0f0f0; }
            QListWidget::item { padding: 2px 4px; background: #2f2f2f; }
            QListWidget::item:alternate { background: #363636; }
            """
        )
        try:
            self.headings_list.setWordWrap(False)
            self.headings_list.setUniformItemSizes(True)
        except Exception:
            pass
        # Pages and headings: split into two columns (Headings | Sub Pages)
        pages_headings_container = QWidget()
        ph_layout = QHBoxLayout()
        ph_layout.setContentsMargins(0, 0, 0, 0)
        ph_layout.setSpacing(6)

        headings_col = QWidget()
        self._headings_col = headings_col
        headings_col_layout = QVBoxLayout()
        headings_col_layout.setContentsMargins(0, 0, 0, 0)
        headings_col_layout.setSpacing(4)
        headings_label = QLabel("Headings:")
        self._headings_label = headings_label
        headings_label.setStyleSheet("font-weight: bold;")
        headings_col_layout.addWidget(headings_label)
        headings_col_layout.addWidget(self.headings_list, 1)
        headings_col.setLayout(headings_col_layout)

        subpages_col = QWidget()
        self._subpages_col = subpages_col
        subpages_col_layout = QVBoxLayout()
        subpages_col_layout.setContentsMargins(0, 0, 0, 0)
        subpages_col_layout.setSpacing(4)
        subpages_label = QLabel("Sub Pages:")
        self._subpages_label = subpages_label
        subpages_label.setStyleSheet("font-weight: bold;")
        subpages_col_layout.addWidget(subpages_label)
        subpages_col_layout.addWidget(self.subpage_list, 1)
        subpages_col.setLayout(subpages_col_layout)

        ph_layout.addWidget(headings_col, 1)
        ph_layout.addWidget(subpages_col, 1)
        pages_headings_container.setLayout(ph_layout)
        print_row = QHBoxLayout()
        print_row.setContentsMargins(0, 0, 0, 0)
        print_row.setSpacing(6)
        print_row.addStretch(1)
        self._print_btn = QToolButton()
        self._print_btn.setToolTip("Print calendar view to browser")
        self._print_btn.setAutoRaise(True)
        self._print_btn.setIcon(self._load_svg_icon("print.svg", QSize(20, 20)))
        self._print_btn.setIconSize(QSize(20, 20))
        self._print_btn.clicked.connect(self._print_calendar_view)
        print_row.addWidget(self._print_btn)

        self.day_insights_layout.addLayout(print_row)
        self.day_insights_layout.addWidget(pages_headings_container, 1)
        recent_row = QHBoxLayout()
        recent_row.setContentsMargins(0, 0, 0, 0)
        recent_row.setSpacing(6)
        recent_label = QLabel("Edited Pages:")
        recent_label.setStyleSheet("font-weight: bold;")
        self.recent_journal_checkbox = QCheckBox("Journal?")
        self.recent_journal_checkbox.setChecked(False)
        self.recent_journal_checkbox.stateChanged.connect(lambda _: self._update_insights_for_selection())
        recent_row.addWidget(recent_label)
        recent_row.addStretch(1)
        recent_row.addWidget(self.recent_journal_checkbox)
        self.recent_list = QListWidget()
        self.recent_list.setAlternatingRowColors(True)
        self.recent_list.setSelectionMode(QAbstractItemView.SingleSelection)
        self.recent_list.itemActivated.connect(self._on_recent_item_activated)
        self.recent_list.itemClicked.connect(self._on_recent_item_activated)
        try:
            self.recent_list.setWordWrap(False)
            self.recent_list.setUniformItemSizes(True)
        except Exception:
            pass
        try:
            self.recent_list.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            row_h = self.recent_list.sizeHintForRow(0) or (self.recent_list.fontMetrics().height() + 6)
            row_h = max(20, row_h)
            # Start collapsed to 1 row, will expand to 4 when data is loaded
            self.recent_list.setMinimumHeight(row_h * 1)
            self.recent_list.setMaximumHeight(row_h * 1 + 12)
        except Exception:
            pass
        self.day_insights_layout.addLayout(recent_row)
        self.day_insights_layout.addWidget(self.recent_list)
        self.tasks_due_list = QTreeWidget()
        self._show_task_start_column = False
        self._show_task_page_column = False
        self._configure_task_columns(force=True)
        self.tasks_due_list.setRootIsDecorated(True)
        self.tasks_due_list.setItemsExpandable(True)
        self.tasks_due_list.setExpandsOnDoubleClick(True)
        self.tasks_due_list.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tasks_due_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tasks_due_list.setAlternatingRowColors(True)
        self.tasks_due_list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.tasks_due_list.itemActivated.connect(self._open_task_item)
        self.tasks_due_list.itemDoubleClicked.connect(self._open_task_item)
        self.tasks_due_list.itemClicked.connect(self._on_task_item_clicked)
        self.tasks_due_list.setSortingEnabled(True)
        self.tasks_due_list.setColumnWidth(0, 70)
        self.tasks_due_list.setColumnWidth(2, 90)
        header = self.tasks_due_list.header()
        header.setSortIndicatorShown(True)
        header.setStretchLastSection(False)
        try:
            from PySide6.QtWidgets import QHeaderView
            header.setSectionResizeMode(0, QHeaderView.Interactive)
            header.setSectionResizeMode(1, QHeaderView.Interactive)
            header.setSectionResizeMode(2, QHeaderView.Interactive)
        except Exception:
            pass
        saved_header = config.load_header_state(self._header_state_key)
        if saved_header:
            try:
                self.tasks_due_list.header().restoreState(QByteArray.fromBase64(saved_header.encode("ascii")))
            except Exception:
                pass
        self.tasks_due_list.header().sectionMoved.connect(lambda *_: self._header_save_timer.start())
        self.tasks_due_list.header().sectionResized.connect(lambda *_: self._header_save_timer.start())
        self.tasks_due_list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tasks_due_list.customContextMenuRequested.connect(self._open_task_date_context_menu)

        due_row = QWidget()
        due_row_layout = QHBoxLayout()
        due_row_layout.setContentsMargins(0, 0, 0, 0)
        due_row_layout.setSpacing(6)
        due_label = QLabel("Tasks")
        self.overdue_checkbox = QToolButton()
        self.overdue_checkbox.setCheckable(True)
        self.overdue_checkbox.setChecked(True)
        self.overdue_checkbox.setIcon(self._load_svg_icon("late.svg", QSize(18, 18)))
        self.overdue_checkbox.setIconSize(QSize(18, 18))
        self.overdue_checkbox.setFixedSize(26, 26)
        self.overdue_checkbox.setAutoRaise(True)
        self.overdue_checkbox.setToolTip("Include overdue tasks")
        self.overdue_checkbox.setStyleSheet(
            """
            QToolButton {
                border: 1px solid transparent;
                border-radius: 13px;
                background: transparent;
                padding: 2px;
            }
            QToolButton:hover {
                border: 1px solid #666666;
                background: rgba(255,255,255,0.06);
            }
            QToolButton:checked {
                border: 1px solid #4a90e2;
                background: rgba(74,144,226,0.22);
            }
            """
        )
        self.overdue_checkbox.toggled.connect(lambda _: self._update_insights_for_selection())
        due_row_layout.addWidget(due_label)
        due_row_layout.addStretch(1)
        self.date_filter_btn = QToolButton()
        self.date_filter_btn.setAutoRaise(True)
        self.date_filter_btn.setIcon(self._load_svg_icon("calendar-days.svg", QSize(18, 18)))
        self.date_filter_btn.setIconSize(QSize(18, 18))
        self.date_filter_btn.setToolTip("Filter tasks by date range")
        self.date_filter_btn.clicked.connect(self._open_task_date_filter)
        self.date_filter_btn.setEnabled(False)
        self.date_filter_btn.setFixedSize(26, 26)
        self.date_filter_btn.setStyleSheet(
            """
            QToolButton {
                border: 1px solid transparent;
                border-radius: 13px;
                padding: 2px;
                background: transparent;
            }
            QToolButton:hover {
                border: 1px solid #666666;
                background: rgba(255,255,255,0.06);
            }
            QToolButton:pressed {
                border: 1px solid #4a90e2;
                background: rgba(74,144,226,0.22);
            }
            """
        )
        due_row_layout.addWidget(self.date_filter_btn)
        # Future checkbox (shows future-starting tasks); checked by default
        self.future_checkbox = QToolButton()
        self.future_checkbox.setCheckable(True)
        self.future_checkbox.setChecked(True)
        self.future_checkbox.setIcon(self._load_svg_icon("future.svg", QSize(18, 18)))
        self.future_checkbox.setIconSize(QSize(18, 18))
        self.future_checkbox.setFixedSize(26, 26)
        self.future_checkbox.setAutoRaise(True)
        self.future_checkbox.setToolTip("Include future-starting tasks in this month")
        self.future_checkbox.setStyleSheet(
            """
            QToolButton {
                border: 1px solid transparent;
                border-radius: 13px;
                background: transparent;
                padding: 2px;
            }
            QToolButton:hover {
                border: 1px solid #666666;
                background: rgba(255,255,255,0.06);
            }
            QToolButton:checked {
                border: 1px solid #4a90e2;
                background: rgba(74,144,226,0.22);
            }
            """
        )
        self.future_checkbox.toggled.connect(lambda _: self._update_insights_for_selection())
        due_row_layout.addWidget(self.future_checkbox)
        due_row_layout.addWidget(self.overdue_checkbox)
        due_row.setLayout(due_row_layout)

        tasks_panel = QWidget()
        tasks_layout = QVBoxLayout(tasks_panel)
        tasks_layout.setContentsMargins(8, 8, 8, 8)
        tasks_layout.setSpacing(6)
        tasks_layout.addWidget(due_row)
        self.tasks_due_list.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        tasks_layout.addWidget(self.tasks_due_list, 1)

        self.ai_insights_panel: QWidget | None = self._build_ai_summary_panel() if self._ai_enabled else None
        self.journal_tabs = QTabWidget()
        self.journal_tabs.addTab(tasks_panel, "Tasks")
        if self.ai_insights_panel:
            self.journal_tabs.addTab(self.ai_insights_panel, "AI Insights")
        if self.journal_tabs.count() == 1:
            try:
                self.journal_tabs.tabBar().setVisible(False)
            except Exception:
                pass

        self.journal_tree = QTreeWidget()
        self.journal_tree.setHeaderHidden(True)
        self.journal_tree.setColumnCount(1)
        self.journal_tree.setAlternatingRowColors(True)
        self.journal_tree.itemClicked.connect(self._on_tree_activated)
        self.journal_tree.itemActivated.connect(self._on_tree_activated)
        self.journal_tree.setFocusPolicy(Qt.StrongFocus)
        self.journal_tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.journal_tree.customContextMenuRequested.connect(self._open_context_menu)

        # Wrap calendar with a top-aligned zoom row
        cal_container = QWidget()
        cal_layout = QVBoxLayout()
        cal_layout.setContentsMargins(0, 0, 0, 0)
        cal_layout.setSpacing(4)
        zoom_row = QHBoxLayout()
        zoom_row.setContentsMargins(0, 0, 0, 0)
        zoom_row.setSpacing(6)
        zoom_row.addWidget(title_container)
        zoom_row.addStretch(1)
        self.today_btn = QToolButton()
        self.today_btn.setText("Today")
        self.today_btn.setToolTip("Jump to today's date")
        self.today_btn.setAutoRaise(False)
        try:
            btn_font = self.today_btn.font()
            btn_font.setBold(True)
            self.today_btn.setFont(btn_font)
        except Exception:
            pass
        self.today_btn.setMinimumHeight(28)
        self.today_btn.setStyleSheet(
            """
            QToolButton {
                padding: 4px 10px;
                border-radius: 6px;
                border: 1px solid #2b6cb0;
                background: #2b6cb0;
                color: #ffffff;
            }
            QToolButton:hover {
                background: #2f76c6;
            }
            QToolButton:pressed {
                background: #255a92;
            }
            """
        )
        self.today_btn.clicked.connect(lambda: self.set_calendar_date(QDate.currentDate().year(), QDate.currentDate().month(), QDate.currentDate().day()))
        zoom_row.addWidget(self.today_btn)
        zoom_row.addWidget(self.zoom_out_btn)
        zoom_row.addWidget(self.zoom_in_btn)
        cal_layout.addLayout(zoom_row)
        self.calendar_row = QWidget()
        self.calendar_row.installEventFilter(self)
        self.calendar_row_layout = QHBoxLayout(self.calendar_row)
        self.calendar_row_layout.setContentsMargins(0, 0, 0, 0)
        self.calendar_row_layout.setSpacing(8)
        self.calendar_row_layout.addWidget(self.prev_calendar_container)
        self.calendar_row_layout.addWidget(self.calendar)
        self.calendar_row_layout.addWidget(self.next_calendar_container)
        cal_layout.addWidget(self.calendar_row)
        cal_container.setLayout(cal_layout)

        # Horizontal splitter between insights and task/ai tabs (bottom row)
        self.main_splitter = QSplitter(Qt.Horizontal)
        self.main_splitter.addWidget(self.day_insights)
        self.main_splitter.addWidget(self.journal_tabs)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 1)
        sizes = config.load_splitter_sizes(self._splitter_key)
        if sizes:
            try:
                self.main_splitter.setSizes(sizes)
            except Exception:
                pass
        self.main_splitter.splitterMoved.connect(lambda *_: self._splitter_save_timer.start())
        self.main_splitter.splitterMoved.connect(lambda *_: self._enforce_calendar_min_width())
        self.main_splitter.splitterMoved.connect(lambda *_: self._update_calendar_layout())
        self.main_splitter.splitterMoved.connect(lambda *_: self._update_insights_layout_visibility())
        self._apply_font_size()
        self._update_calendar_layout()

        # Vertical splitter for calendar (top) + bottom row
        self.top_splitter = QSplitter(Qt.Vertical)
        self._calendar_container = cal_container
        self.top_splitter.addWidget(cal_container)
        self.top_splitter.addWidget(self.main_splitter)
        self.top_splitter.setStretchFactor(0, 0)
        self.top_splitter.setStretchFactor(1, 1)

        root_layout = QHBoxLayout()
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        root_layout.addWidget(self.top_splitter)
        self.setLayout(root_layout)

        self.vault_root: Optional[str] = None
        self.setFocusPolicy(Qt.StrongFocus)

    def set_page_text_provider(self, provider: Callable[[Optional[str]], str]) -> None:
        """Allow caller to supply live editor text for a given page path (relative, with leading slash)."""
        self._page_text_provider = provider

    def set_task_date_filter_opener(self, opener: Optional[Callable[[Optional[QWidget]], None]]) -> None:
        """Allow parent to provide a date filter opener for the Task panel."""
        self._task_date_filter_opener = opener
        if getattr(self, "date_filter_btn", None):
            self.date_filter_btn.setEnabled(bool(opener))

    def _open_task_date_filter(self) -> None:
        if self._task_date_filter_opener:
            self._task_date_filter_opener(self.date_filter_btn)

    def set_task_date_filter_setter(
        self,
        setter: Optional[Callable[[Optional[Date], Optional[Date], Optional[str]], None]],
    ) -> None:
        """Allow parent to provide a date filter setter for the Task panel."""
        self._task_date_filter_setter = setter

    def showEvent(self, event):  # type: ignore[override]
        """Ensure we hook the calendar view after widget is shown."""
        super().showEvent(event)
        self._attach_calendar_view()
        self._apply_multi_selection_formats()
        self._update_today_visibility()
        self._update_calendar_layout()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._update_calendar_layout()

    def eventFilter(self, obj, event):  # type: ignore[override]
        if obj is getattr(self, "calendar_row", None) and event.type() in (QEvent.Resize, QEvent.Show):
            QTimer.singleShot(0, self._update_calendar_layout)
        return super().eventFilter(obj, event)
        self._enforce_calendar_min_width()
        self.ensure_splitter_visible()

    def resizeEvent(self, event):  # type: ignore[override]
        super().resizeEvent(event)
        self._enforce_calendar_min_width()

    def set_vault_root(self, vault_root: Optional[str]) -> None:
        """Set vault root for calendar and tree data."""
        self.vault_root = vault_root
        self.refresh()

    def refresh(self) -> None:
        """Refresh the journal tree and calendar highlights."""
        self._populate_tree()
        self._update_calendar_dates()
        self._update_insights_from_calendar()
        self._update_today_visibility()

    def set_calendar_date(self, year: int, month: int, day: int) -> None:
        """Move the calendar to a specific date and expand the tree."""
        target = QDate(year, month, day)
        self.calendar.setSelectedDate(target)
        self._set_single_selection(target)
        self._update_calendar_dates(year, month)
        self._expand_to_date(target)
        self._update_day_listing(target)
        self._apply_multi_selection_formats()
        self._update_insights_for_selection()
        self._update_today_visibility()

    def set_current_page(self, rel_path: Optional[str]) -> None:
        """Sync calendar and tree based on an opened journal page."""
        # If a multi-day filter is active, do not change the calendar selection
        if len(self.multi_selected_dates) > 1:
            # only update insight selection highlight
            self._update_insights_for_selection(rel_path)
            return
        if not rel_path or "Journal" not in rel_path:
            return
        parts = Path(rel_path.lstrip("/")).parts
        # Expect /Journal/YYYY/MM/DD[/Sub]/file.md
        try:
            idx = parts.index("Journal")
        except ValueError:
            return
        if len(parts) < idx + 4:
            return
        year, month, day = parts[idx + 1 : idx + 4]
        try:
            y, m, d = int(year), int(month), int(day)
        except ValueError:
            return
        self.set_calendar_date(y, m, d)
        # If subpage, defer selection slightly to ensure tree is populated
        if len(parts) > idx + 4:
            # Handle both folder-based and flat subpages
            sub_name = Path(parts[-1]).stem
            if len(parts) > idx + 5:
                sub_name = parts[idx + 4]
            # Defer selection to ensure day listing is populated
            from PySide6.QtCore import QTimer
            QTimer.singleShot(10, lambda: self._select_subpage_item(y, m, d, sub_name, rel_path))
        # Update insights list selection
        self._update_insights_for_selection(rel_path)

    def _adjust_font_size(self, delta: int) -> None:
        """Adjust panel font size (Ctrl +/-) in tabs or popup windows."""
        new_size = max(8, min(24, self._font_size + delta))
        if new_size == self._font_size:
            return
        self._font_size = new_size
        self._apply_font_size()
        config.save_panel_font_size(self._font_size_key, self._font_size)

    def adjust_font_size(self, delta: int) -> None:
        """Public wrapper to allow parent containers to forward zoom shortcuts."""
        self._adjust_font_size(delta)

    def set_base_font_size(self, size: int) -> None:
        """Align calendar/journal/insights fonts to the editor font size."""
        if config.has_global_config_key(self._font_size_key):
            return
        clamped = max(6, min(48, int(size or self._font_size)))
        if clamped == self._font_size:
            return
        self._font_size = clamped
        self._apply_font_size()

    def _apply_font_size(self) -> None:
        font = QFont(self.font())
        font.setPointSize(self._font_size)
        for widget in (
            self.calendar,
            self.prev_calendar,
            self.next_calendar,
            self.insight_title,
            self.insight_counts,
            self.insight_tags,
            self.subpage_list,
            self.headings_list,
            self.tasks_due_list,
            self.journal_tree,
            self.overdue_checkbox,
            self.future_checkbox,
            getattr(self, "date_filter_btn", None),
            self.filter_btn,
            self.zoom_in_btn,
            self.zoom_out_btn,
            getattr(self, "_print_btn", None),
            getattr(self, "ai_title_label", None),
            getattr(self, "ai_delete_btn", None),
            getattr(self, "ai_generate_btn", None),
            getattr(self, "ai_copy_btn", None),
        ):
            try:
                widget.setFont(font)
            except Exception:
                pass
        
        # Apply font to calendar's internal table view for date cells
        if self.calendar_view:
            try:
                self.calendar_view.setFont(font)
            except Exception:
                pass
        
        # Apply font to all calendar child widgets (buttons, headers, etc.)
        for cal in (self.calendar, self.prev_calendar, self.next_calendar):
            try:
                for child in cal.findChildren(QWidget):
                    try:
                        child.setFont(font)
                    except Exception:
                        pass
            except Exception:
                pass
        
        if getattr(self, "ai_markdown_view", None):
            try:
                self.ai_markdown_view.setFont(font)
            except Exception:
                pass
        self._enforce_calendar_min_width()

    def _calendar_min_width(self) -> int:
        fm = self.calendar.fontMetrics()
        cell_w = fm.horizontalAdvance("88") + 18
        base = cell_w * 7 + 28
        try:
            base = max(base, self.calendar.minimumSizeHint().width())
        except Exception:
            pass
        try:
            base = max(base, self.calendar.sizeHint().width())
        except Exception:
            pass
        return max(240, base)

    def _enforce_calendar_min_width(self) -> None:
        min_w = self._calendar_min_width()
        try:
            self.calendar.setMinimumWidth(min_w)
        except Exception:
            pass
        for cal in (self.prev_calendar, self.next_calendar):
            try:
                cal.setMinimumWidth(min_w)
            except Exception:
                pass
        try:
            if getattr(self, "_calendar_container", None):
                total = min_w
                if getattr(self, "_show_three_calendars", False) and getattr(self, "calendar_row_layout", None):
                    total = min_w * 3 + self.calendar_row_layout.spacing() * 2
                self._calendar_container.setMinimumWidth(total)
        except Exception:
            pass

    def ensure_splitter_visible(self, min_left: int = 180) -> None:
        if not getattr(self, "main_splitter", None):
            return
        try:
            sizes = self.main_splitter.sizes()
        except Exception:
            return
        if len(sizes) < 2:
            return
        total = sum(sizes)
        if total <= 0:
            return
        min_right = min(self._calendar_min_width(), total)
        target_left = min_left if total >= (min_left + min_right) else max(0, total - min_right)
        if sizes[0] >= target_left:
            return
        sizes[0] = target_left
        sizes[1] = max(0, total - target_left)
        try:
            self.main_splitter.blockSignals(True)
            self.main_splitter.setSizes(sizes)
        finally:
            self.main_splitter.blockSignals(False)
        self._splitter_save_timer.start()

    def _load_print_css(self) -> str:
        css_path = Path(__file__).resolve().parents[2] / "server" / "templates" / "print.css"
        try:
            return css_path.read_text(encoding="utf-8")
        except Exception:
            return ""

    def _calendar_table_html(self) -> tuple[str, str]:
        year = self.calendar.yearShown()
        month = self.calendar.monthShown()
        month_label = QDate(year, month, 1).toString("MMMM yyyy")
        cal = calendar.Calendar(firstweekday=0)
        weeks = cal.monthdayscalendar(year, month)
        today = QDate.currentDate()
        selected_dates = self.multi_selected_dates or {self.calendar.selectedDate()}
        selected = {(d.year(), d.month(), d.day()) for d in selected_dates if d and d.isValid()}

        headings = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        head_cells = "".join(f"<th>{h}</th>" for h in headings)
        rows = []
        for week in weeks:
            cells = []
            for day in week:
                if day == 0:
                    cells.append("<td class=\"calendar-day empty\"></td>")
                    continue
                classes = ["calendar-day"]
                if today.year() == year and today.month() == month and today.day() == day:
                    classes.append("today")
                if (year, month, day) in selected:
                    classes.append("selected")
                class_attr = " ".join(classes)
                cells.append(f"<td class=\"{class_attr}\">{day}</td>")
            rows.append("<tr>" + "".join(cells) + "</tr>")
        table_html = (
            "<table class=\"calendar-print\">"
            f"<thead><tr>{head_cells}</tr></thead>"
            f"<tbody>{''.join(rows)}</tbody>"
            "</table>"
        )
        return month_label, table_html

    def _read_ai_summary_html(self) -> str:
        if not self.vault_root:
            return ""
        dates = sorted(self.multi_selected_dates or {self.calendar.selectedDate()}, key=lambda d: d.toJulianDay())
        if len(dates) != 1:
            return ""
        path = self._ai_summary_path_for_date(dates[0])
        if not path or not path.exists():
            return ""
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                return ""
        if not text.strip():
            return ""
        cleaned = self._replace_emoji_with_fallback(text.strip())
        try:
            return render_markdown(cleaned, extensions=["extra", "sane_lists", "tables", "fenced_code"])
        except Exception:
            return "<pre>" + html.escape(cleaned) + "</pre>"

    def _safe_print_href(self, url: str) -> str:
        cleaned = (url or "").strip()
        if not cleaned:
            return ""
        lowered = cleaned.lower()
        if lowered.startswith(("javascript:", "data:")):
            return ""
        return html.escape(cleaned, quote=True)

    def _linkify_task_text_html(self, text: str) -> str:
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

    def _build_due_tasks_table(self) -> str:
        header = self.tasks_due_list.headerItem()
        headers = []
        if header:
            for i in range(self.tasks_due_list.columnCount()):
                headers.append(header.text(i))
        header_cells = "".join(f"<th>{html.escape(h)}</th>" for h in headers)
        rows = []
        def _append_task_row(item: QTreeWidgetItem) -> None:
            task = item.data(0, Qt.UserRole) or {}
            priority_level = min(task.get("priority", 0) or 0, 3)
            _, due_overdue = self._priority_time_label(task)
            pri_style = ""
            pri_brush = self._priority_brush(priority_level)
            if pri_brush:
                pri_style += f"background-color: {pri_brush['bg'].name()}; color: {pri_brush['fg'].name()};"
            if due_overdue:
                pri_style += "text-decoration: underline;"
            due_style = ""
            due_colors = self._due_colors(task.get("due") or "")
            if due_colors:
                fg, bg = due_colors
                due_style += f"color: {fg.name()}; background-color: {bg.name()};"
            row_cells = []
            for col in range(self.tasks_due_list.columnCount()):
                text = item.text(col)
                if col == 1:
                    safe = self._linkify_task_text_html(text or "")
                else:
                    safe = html.escape(text or "")
                cell_style = ""
                if col == 0 and pri_style:
                    cell_style = pri_style
                if col == 2 and due_style:
                    cell_style = due_style
                class_name = "task-cell"
                if col == 1:
                    class_name = "task-text"
                row_cells.append(f"<td class=\"{class_name}\" style=\"{cell_style}\">{safe}</td>")
            rows.append("<tr>" + "".join(row_cells) + "</tr>")

        for idx in range(self.tasks_due_list.topLevelItemCount()):
            item = self.tasks_due_list.topLevelItem(idx)
            if item.childCount():
                title = item.text(0) or item.text(1)
                safe_title = html.escape(title or "")
                rows.append(
                    f"<tr><td class=\"task-section\" colspan=\"{self.tasks_due_list.columnCount()}\">{safe_title}</td></tr>"
                )
                for child_idx in range(item.childCount()):
                    _append_task_row(item.child(child_idx))
            else:
                _append_task_row(item)
        return "<table class=\"task-print\"><thead><tr>" + header_cells + "</tr></thead><tbody>" + "".join(rows) + "</tbody></table>"

    def _build_calendar_print_html(self) -> str:
        css = self._load_print_css()
        month_label, calendar_table = self._calendar_table_html()
        title = html.escape(self.insight_title.text() or "Calendar")
        counts = html.escape(self.insight_counts.text() or "")
        tags = html.escape(self.insight_tags.text() or "")
        generated = datetime.now().strftime("%Y-%m-%d %H:%M")

        headings = [self.headings_list.item(i).text() for i in range(self.headings_list.count())] if self.headings_list.isVisible() else []
        subpages = [self.subpage_list.item(i).text() for i in range(self.subpage_list.count())]
        recent_pages = [self.recent_list.item(i).text() for i in range(self.recent_list.count())]

        ai_html = self._read_ai_summary_html()

        overdue_on = "On" if self.overdue_checkbox.isChecked() else "Off"
        future_on = "On" if self.future_checkbox.isChecked() else "Off"
        due_filters = f"Overdue: {overdue_on} · Future: {future_on}"

        extra_css = """
        .calendar-print {
            width: 100%;
            border-collapse: collapse;
            margin: 0.5em 0 1.25em;
        }
        .calendar-print th,
        .calendar-print td {
            border: 1px solid var(--border);
            text-align: center;
            padding: 0.35em;
            width: 14.28%;
        }
        .calendar-day.today {
            background: #4A90E2;
            color: #fff;
            font-weight: 700;
        }
        .calendar-day.selected {
            background: #d9e9ff;
            font-weight: 600;
        }
        .calendar-day.empty {
            background: #fafafa;
        }
        .section {
            margin: 1em 0 1.4em;
        }
        .section h2 {
            margin: 0 0 0.4em;
            font-size: 1.2em;
        }
        .section ul {
            margin: 0.2em 0 0.2em 1.2em;
        }
        table.task-print {
            border-collapse: collapse;
            width: 100%;
        }
        table.task-print th,
        table.task-print td {
            border: 1px solid var(--border);
            padding: 0.35em 0.5em;
            vertical-align: top;
        }
        table.task-print th {
            background: #f0f0f0;
            font-weight: 600;
        }
        .task-text {
            white-space: normal;
        }
        """

        def _list_html(items: list[str]) -> str:
            if not items:
                return "<p>—</p>"
            return "<ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in items) + "</ul>"

        sections = []
        sections.append(
            "<section class=\"section\">"
            f"<h2>Calendar — {html.escape(month_label)}</h2>"
            f"{calendar_table}"
            "</section>"
        )
        if headings:
            sections.append(
                "<section class=\"section\"><h2>Headings</h2>" + _list_html(headings) + "</section>"
            )
        sections.append(
            "<section class=\"section\"><h2>Subpages</h2>" + _list_html(subpages) + "</section>"
        )
        if ai_html:
            sections.append(
                "<section class=\"section\"><h2>AI Summary</h2>" + ai_html + "</section>"
            )
        if recent_pages:
            sections.append(
                "<section class=\"section\"><h2>Edited Pages</h2>" + _list_html(recent_pages) + "</section>"
            )
        if self.tasks_due_list.topLevelItemCount():
            sections.append(
                "<section class=\"section\"><h2>Due Tasks</h2>"
                f"<p>{html.escape(due_filters)}</p>"
                f"{self._build_due_tasks_table()}</section>"
            )

        header_html = (
            "<header class=\"stillpoint-header\">"
            f"<h1>{title}</h1>"
            f"<div class=\"meta\">{counts}</div>"
            f"<div class=\"meta\">{tags}</div>"
            f"<div class=\"meta\">Generated {generated}</div>"
            "</header>"
        )

        return (
            "<!doctype html><html lang=\"en\">"
            "<head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            "<title>StillPoint Calendar</title>"
            f"<style>{css}\n{extra_css}</style>"
            "</head><body>"
            "<main class=\"stillpoint-print\">"
            f"{header_html}{''.join(sections)}"
            "</main></body></html>"
        )

    def _print_calendar_view(self) -> None:
        if not config.has_active_vault():
            return
        html_doc = self._build_calendar_print_html()
        if not html_doc:
            return
        try:
            tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".html")
            with tmp:
                tmp.write(html_doc.encode("utf-8"))
            QDesktopServices.openUrl(QUrl.fromLocalFile(tmp.name))
        except Exception:
            return

    def _save_splitter_sizes(self) -> None:
        try:
            sizes = self.main_splitter.sizes()
        except Exception:
            return
        config.save_splitter_sizes(self._splitter_key, sizes)

    def _save_header_state(self) -> None:
        try:
            state = bytes(self.tasks_due_list.header().saveState().toBase64()).decode("ascii")
        except Exception:
            return
        config.save_header_state(self._header_state_key, state)

    def _build_ai_summary_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(6)
        self.ai_title_label = QLabel("AI Insights")
        self.ai_title_label.setStyleSheet("font-weight: bold;")
        self.ai_delete_btn = QToolButton()
        self.ai_delete_btn.setIcon(self._load_svg_icon("icons8-trash.svg", QSize(20, 20)))
        self.ai_delete_btn.setToolTip("Delete AI summary for this day")
        self.ai_delete_btn.setAutoRaise(True)
        self.ai_delete_btn.clicked.connect(self._delete_ai_summary)
        self.ai_copy_btn = QToolButton()
        self.ai_copy_btn.setIcon(self._load_svg_icon("copy.svg", QSize(20, 20)))
        self.ai_copy_btn.setToolTip("Copy AI summary markdown")
        self.ai_copy_btn.setAutoRaise(True)
        self.ai_copy_btn.clicked.connect(self._copy_ai_markdown)
        self.ai_generate_btn = QToolButton()
        self.ai_generate_btn.setIcon(self._load_ai_icon())
        self.ai_generate_btn.setToolTip("Generate AI summary for this day")
        self.ai_generate_btn.setAutoRaise(True)
        self.ai_generate_btn.setIconSize(QSize(28, 28))
        self.ai_generate_btn.clicked.connect(self._on_generate_ai_summary)
        header.addWidget(self.ai_title_label)
        header.addStretch(1)
        header.addWidget(self.ai_delete_btn)
        header.addWidget(self.ai_copy_btn)
        header.addWidget(self.ai_generate_btn)
        self.ai_markdown_view = QTextBrowser()
        self.ai_markdown_view.setOpenExternalLinks(True)
        self.ai_markdown_view.setReadOnly(True)
        self.ai_markdown_view.setStyleSheet("background:#1f1f1f; color:#f0f0f0; border:1px solid #444; padding:10px;")
        layout.addLayout(header)
        layout.addWidget(self.ai_markdown_view, 1)
        self._set_ai_markdown("Click buton to generate a AI summary")
        return panel

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
            painter.fillRect(pixmap.rect(), Qt.white)
            painter.end()
            return QIcon(pixmap)
        except Exception:
            return QIcon()

    def _load_ai_icon(self) -> QIcon:
        return self._load_svg_icon("ai.svg", QSize(28, 28))

    def _set_ai_markdown(self, text: str) -> None:
        if not getattr(self, "ai_markdown_view", None):
            return
        self._ai_last_markdown = text or ""
        self._render_ai_markdown(self._ai_last_markdown)

    def _render_ai_markdown(self, markdown_text: str) -> None:
        if not getattr(self, "ai_markdown_view", None):
            return
        try:
            cleaned = self._replace_emoji_with_fallback(markdown_text or "")
            html = render_markdown(cleaned, extensions=["extra", "sane_lists", "tables", "fenced_code"])
            font_size = max(6, self._font_size)
            style = f"""
            <style>
            body {{ background:#1f1f1f; color:#f0f0f0; font-size: {font_size}px;
                   font-family: 'Noto Sans', 'Segoe UI', 'Helvetica', 'Arial',
                   'Noto Color Emoji', 'Segoe UI Emoji', 'Apple Color Emoji', sans-serif; }}
            h1,h2,h3,h4,h5,h6 {{ margin: 0.4em 0 0.2em 0; }}
            ul,ol {{ margin-top: 0.2em; margin-bottom: 0.2em; }}
            </style>
            """
            self.ai_markdown_view.setHtml(style + html)
        except Exception:
            try:
                self.ai_markdown_view.setPlainText(markdown_text)
            except Exception:
                pass

    def _replace_emoji_with_fallback(self, text: str) -> str:
        """Replace emoji with monochrome fallbacks so they render even without emoji fonts."""
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

    def _ai_summary_path_for_date(self, qdate: QDate) -> Optional[Path]:
        if not self.vault_root or not qdate or not qdate.isValid():
            return None
        base_dir = Path(self.vault_root) / "Journal" / f"{qdate.year():04d}" / f"{qdate.month():02d}" / f"{qdate.day():02d}"
        return base_dir / "AISummary" / f"AISummary{PAGE_SUFFIX}"

    def _update_ai_summary_for_selection(self, dates: list[QDate]) -> None:
        if not self._ai_enabled:
            return
        if not getattr(self, "ai_markdown_view", None):
            return
        if not self.vault_root or not config.has_active_vault():
            self._set_ai_markdown("Open a vault to view AI summaries.")
            return
        if len(dates) != 1:
            self._set_ai_markdown("Select a single day to view or generate a AI summary.")
            return
        date = dates[0]
        if not date or not date.isValid():
            self._set_ai_markdown("Select a single day to view or generate a AI summary.")
            return
        self._load_ai_summary_for_date(date)

    def _load_ai_summary_for_date(self, qdate: QDate) -> None:
        path = self._ai_summary_path_for_date(qdate)
        if not path:
            self._set_ai_markdown("Click buton to generate a AI summary")
            return
        if not path.exists():
            self._set_ai_markdown("Click buton to generate a AI summary")
            return
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            try:
                text = path.read_text(errors="ignore")
            except Exception:
                self._set_ai_markdown("Click buton to generate a AI summary")
                return
        self._set_ai_markdown(text.strip() or "Click buton to generate a AI summary")

    def _read_day_text(self, qdate: QDate) -> str:
        if not self.vault_root or not qdate or not qdate.isValid():
            return ""
        base_dir = Path(self.vault_root) / "Journal" / f"{qdate.year():04d}" / f"{qdate.month():02d}" / f"{qdate.day():02d}"
        if not base_dir.exists():
            return ""
        parts: list[str] = []
        day_page = base_dir / f"{base_dir.name}{PAGE_SUFFIX}"
        main_rel: Optional[str] = None
        try:
            main_rel = "/" + day_page.relative_to(self.vault_root).as_posix()
        except Exception:
            main_rel = None
        editor_text = ""
        if self._page_text_provider and main_rel:
            try:
                editor_text = self._page_text_provider(main_rel) or ""
            except Exception:
                editor_text = ""
        if editor_text.strip():
            parts.append(editor_text)
        elif day_page.exists():
            try:
                parts.append(day_page.read_text(encoding="utf-8"))
            except Exception:
                try:
                    parts.append(day_page.read_text(errors="ignore"))
                except Exception:
                    pass
        for _, rel in self._list_day_subpages(base_dir):
            target = Path(self.vault_root) / rel.lstrip("/")
            if not target.exists():
                continue
            try:
                text = target.read_text(encoding="utf-8")
            except Exception:
                try:
                    text = target.read_text(errors="ignore")
                except Exception:
                    continue
            parts.append(f"## {Path(rel).stem}\n{text}")
        return "\n\n".join(parts).strip()

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
        if not self.vault_root or not config.has_active_vault():
            self._set_ai_markdown("Open a vault to generate a AI summary.")
            return
        dates = sorted(self.multi_selected_dates or {self.calendar.selectedDate()}, key=lambda d: d.toJulianDay())
        if len(dates) != 1:
            self._set_ai_markdown("Select a single day to generate a AI summary.")
            return
        date = dates[0]
        if not date or not date.isValid():
            self._set_ai_markdown("Select a single day to generate a AI summary.")
            return
        day_text = self._read_day_text(date)
        if not day_text.strip():
            self._set_ai_markdown("No journal entry found for this date to summarize.")
            return
        prompt_path = Path(__file__).resolve().parents[1] / "calendar-day-insight-prompt.txt"
        try:
            prompt_text = prompt_path.read_text(encoding="utf-8")
        except Exception:
            self._set_ai_markdown("Failed to load AI summary prompt.")
            return
        prompt_text = prompt_text.replace("{{date}}", self._pretty_date_label(date))
        server_model = self._resolve_ai_server_and_model()
        if not server_model:
            self._set_ai_markdown("Configure an AI server to generate a summary.")
            return
        server_config, model = server_model
        messages = [
            {"role": "system", "content": prompt_text},
            {"role": "user", "content": f"Daily journal for {self._pretty_date_label(date)}:\n\n{day_text}"},
        ]
        self._ai_response_buffer = ""
        self._set_ai_markdown("Generating AI summary…")
        try:
            self.ai_generate_btn.setEnabled(False)
        except Exception:
            pass
        worker = ApiWorker(server_config, messages, model, stream=True)
        self._ai_worker = worker
        worker.chunk.connect(self._on_ai_chunk)
        worker.finished.connect(lambda full, d=date: self._on_ai_finished(d, full))
        worker.failed.connect(self._on_ai_failed)
        worker.start()

    def _on_ai_chunk(self, chunk: str) -> None:
        self._ai_response_buffer += chunk or ""
        if self._ai_response_buffer.strip():
            self._ai_last_markdown = self._ai_response_buffer
            self._render_ai_markdown(self._ai_last_markdown)

    def _on_ai_finished(self, date: QDate, content: str) -> None:
        try:
            self.ai_generate_btn.setEnabled(True)
        except Exception:
            pass
        final = content or self._ai_response_buffer
        self._ai_response_buffer = final
        if not final.strip():
            self._set_ai_markdown("AI returned no content.")
        else:
            self._set_ai_markdown(final)
            self._write_ai_summary(date, final)
            # Refresh insights so the new summary shows as a subpage if applicable
            self._update_insights_for_selection()
        if self._ai_worker:
            try:
                self._ai_worker.deleteLater()
            except Exception:
                pass
            self._ai_worker = None

    def _on_ai_failed(self, message: str) -> None:
        try:
            self.ai_generate_btn.setEnabled(True)
        except Exception:
            pass
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
        dates = sorted(self.multi_selected_dates or {self.calendar.selectedDate()}, key=lambda d: d.toJulianDay())
        if len(dates) != 1:
            self._set_ai_markdown("Select a single day to delete AI summary.")
            return
        date = dates[0]
        path = self._ai_summary_path_for_date(date)
        if not path:
            self._set_ai_markdown("cick to generate")
            return
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
        self._set_ai_markdown("cick to generate")

    def _write_ai_summary(self, date: QDate, content: str) -> None:
        path = self._ai_summary_path_for_date(date)
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception:
            pass

    def _on_month_changed(self, year: int, month: int) -> None:
        self._update_calendar_dates(year, month)
        self._update_day_listing(self.calendar.selectedDate())
        self._apply_multi_selection_formats()
        self._sync_aux_calendars()
        self._update_insights_for_selection()
        # Also update the due-tasks panel to reflect the visible month range
        try:
            first = QDate(year, month, 1)
            last_day = first.daysInMonth()
            last = QDate(year, month, last_day)
            self._update_due_tasks([first, last])
        except Exception:
            pass
        try:
            if self._task_date_filter_setter:
                start = Date(year, month, 1)
                end = Date(year, month, calendar.monthrange(year, month)[1])
                self._task_date_filter_setter(start, end, "month")
        except Exception:
            pass
        self._update_today_visibility()

    def _set_single_selection(self, date: QDate) -> None:
        if not date or not date.isValid():
            return
        self.multi_selected_dates = {date}
        self._selection_anchor = date

    def _set_range_selection(self, start: QDate, end: QDate) -> None:
        if not start or not start.isValid() or not end or not end.isValid():
            return
        start_j = start.toJulianDay()
        end_j = end.toJulianDay()
        if end_j < start_j:
            start_j, end_j = end_j, start_j
        self.multi_selected_dates = {QDate.fromJulianDay(j) for j in range(start_j, end_j + 1)}

    def _on_date_clicked(self, date: QDate) -> None:
        """Emit selected date and sync the tree."""
        if self._suppress_next_click:
            self._suppress_next_click = False
            return
        
        # Check if shift key was detected in eventFilter or is currently held
        is_shift = self._pending_shift_click or (QApplication.keyboardModifiers() & Qt.ShiftModifier)
        if is_shift:
            # Shift+Click: select range from anchor to this date
            anchor = self._selection_anchor if self._selection_anchor and self._selection_anchor.isValid() else date
            self._set_range_selection(anchor, date)
            print(f"[CALENDAR] _on_date_clicked Shift+Click: Range {anchor.toString('yyyy-MM-dd')} -> {date.toString('yyyy-MM-dd')}, total selected: {len(self.multi_selected_dates)}")
            self._pending_shift_click = False
        else:
            # Regular click: select only this date (clear previous selection)
            self._set_single_selection(date)
            print(f"[CALENDAR] _on_date_clicked Click: Selected only {date.toString('yyyy-MM-dd')}")
        
        self._apply_multi_selection_formats()
        self._expand_to_date(date)
        self._update_day_listing(date)
        self._update_insights_for_selection()
        self._update_today_visibility()
        self._sync_aux_calendars()
        if not is_shift:
            self.dateActivated.emit(date.year(), date.month(), date.day())

    def _update_today_visibility(self) -> None:
        if not hasattr(self, "today_btn"):
            return
        today = QDate.currentDate()
        self.today_btn.setVisible(self.calendar.selectedDate() != today)

    def _update_calendar_layout(self) -> None:
        if not getattr(self, "calendar_row_layout", None):
            return
        available = 0
        try:
            available = self.calendar_row.width()
        except Exception:
            available = 0
        if available <= 0:
            try:
                available = self._calendar_container.width()
            except Exception:
                available = 0
        if available <= 0:
            return
        cal_min = self._calendar_min_width()
        spacing = self.calendar_row_layout.spacing()
        needed = cal_min * 3 + spacing * 2 + 6
        show_three = available >= needed
        if show_three != self._show_three_calendars:
            self._show_three_calendars = show_three
            self.prev_calendar_container.setVisible(show_three)
            self.next_calendar_container.setVisible(show_three)
        self._enforce_calendar_min_width()
        self._sync_aux_calendars()
        self.calendar.update()
        self.prev_calendar.update()
        self.next_calendar.update()
        if self.calendar_row:
            self.calendar_row.update()

    def update_calendar_layout(self) -> None:
        """Public hook for parent containers to force layout recalculation."""
        self._update_calendar_layout()
        self._update_insights_layout_visibility()

    def _update_insights_layout_visibility(self) -> None:
        if not getattr(self, "main_splitter", None):
            return
        total = self.main_splitter.width()
        if total <= 0:
            try:
                total = sum(self.main_splitter.sizes())
            except Exception:
                total = 0
        if total <= 0:
            return
        left_min = max(180, self.day_insights.minimumWidth() or 0)
        right_hint = 0
        try:
            right_hint = self.journal_tabs.minimumSizeHint().width()
        except Exception:
            right_hint = 0
        right_min = max(260, right_hint)
        should_hide = total < (left_min + right_min)
        if should_hide == self._hide_insights_tabs:
            return
        self._hide_insights_tabs = should_hide
        if should_hide:
            try:
                self._main_splitter_sizes_before_hide = self.main_splitter.sizes()
            except Exception:
                self._main_splitter_sizes_before_hide = None
            self.journal_tabs.setVisible(False)
            try:
                self.main_splitter.setSizes([total, 0])
            except Exception:
                pass
        else:
            self.journal_tabs.setVisible(True)
            if self._main_splitter_sizes_before_hide and len(self._main_splitter_sizes_before_hide) >= 2:
                try:
                    self.main_splitter.setSizes(self._main_splitter_sizes_before_hide)
                except Exception:
                    pass
            else:
                try:
                    self.main_splitter.setSizes([left_min, max(1, total - left_min)])
                except Exception:
                    pass

    def _sync_aux_calendars(self) -> None:
        if not getattr(self, "_show_three_calendars", False):
            return
        if self._syncing_calendars:
            return
        self._syncing_calendars = True
        try:
            year = self.calendar.yearShown()
            month = self.calendar.monthShown()
            base = QDate(year, month, 1)
            prev = base.addMonths(-1)
            next_m = base.addMonths(1)
            self.prev_calendar.setCurrentPage(prev.year(), prev.month())
            self.next_calendar.setCurrentPage(next_m.year(), next_m.month())
            self.prev_month_label.setText(prev.toString("MMMM yyyy"))
            self.next_month_label.setText(next_m.toString("MMMM yyyy"))
            try:
                self.prev_calendar.setSelectedDate(QDate())
                self.next_calendar.setSelectedDate(QDate())
            except Exception:
                pass
        finally:
            self._syncing_calendars = False

    def _on_aux_calendar_clicked(self, date: QDate) -> None:
        if self._syncing_calendars:
            return
        self.calendar.setSelectedDate(date)
        self._on_date_clicked(date)

    def _on_aux_calendar_navigate(self, year: int, month: int, offset: int) -> None:
        if self._syncing_calendars:
            return
        base = QDate(year, month, 1)
        target = base.addMonths(1 if offset < 0 else -1)
        self._syncing_calendars = True
        try:
            self.calendar.setCurrentPage(target.year(), target.month())
        finally:
            self._syncing_calendars = False

    def _populate_tree(self) -> None:
        """Build a tree rooted at Journal with year/month/day nodes."""
        had_tree = self.journal_tree.topLevelItemCount() > 0
        expanded_paths = self._capture_expanded_paths()
        selected_path = self._capture_selected_path()

        self.journal_tree.clear()
        root_item = QTreeWidgetItem(["Journal"])
        root_item.setData(0, Qt.UserRole, None)
        root_item.setData(0, PATH_ROLE, "Journal")
        root_item.setExpanded("Journal" in expanded_paths or not had_tree)
        self.journal_tree.addTopLevelItem(root_item)

        if not self.vault_root:
            return

        journal_path = Path(self.vault_root) / "Journal"
        if not journal_path.exists():
            return

        self._add_children(root_item, journal_path)

        if expanded_paths:
            self._restore_expanded_paths(root_item, expanded_paths)
        if selected_path:
            self._restore_selection(selected_path)
        self._update_day_listing(self.calendar.selectedDate())
        self._update_insights_from_calendar()

    def _update_calendar_dates(self, year: Optional[int] = None, month: Optional[int] = None) -> None:
        """Bold dates with saved journal entries for the visible month."""
        if not self.vault_root:
            return

        current = self.calendar.selectedDate()
        year = year or current.year()
        month = month or current.month()

        journal_path = Path(self.vault_root) / "Journal" / str(year) / f"{month:02d}"
        days_in_month = QDate(year, month, 1).daysInMonth()

        default_format = QTextCharFormat()
        bold_format = QTextCharFormat()
        bold_font = QFont()
        bold_font.setBold(True)
        bold_font.setWeight(QFont.Black)
        bold_format.setFont(bold_font)

        for day in range(1, days_in_month + 1):
            self.calendar.setDateTextFormat(QDate(year, month, day), default_format)

        if not journal_path.exists():
            self._apply_multi_selection_formats()
            return

        for day_dir in journal_path.iterdir():
            if not day_dir.is_dir() or not day_dir.name.isdigit():
                continue
            day_num = int(day_dir.name)
            day_file = day_dir / f"{day_dir.name}{PAGE_SUFFIX}"
            if day_file.exists():
                self.calendar.setDateTextFormat(QDate(year, month, day_num), bold_format)
        self._apply_multi_selection_formats()

    def _apply_multi_selection_formats(self) -> None:
        """Highlight all currently multi-selected dates."""
        # Update the delegate
        self.calendar_delegate.multi_selected_dates = self.multi_selected_dates.copy()
        
        # Also use QTextCharFormat for reliable highlighting
        highlight_color = self._calendar_selected_bg
        text_color = self._calendar_selected_text
        
        def apply_for_calendar(cal: QCalendarWidget, *, allow_selection: bool) -> None:
            year = cal.yearShown()
            month = cal.monthShown()
            default_format = QTextCharFormat()
            for month_offset in [-1, 0, 1]:
                check_date = QDate(year, month, 1).addMonths(month_offset)
                check_year = check_date.year()
                check_month = check_date.month()
                days_in_month = check_date.daysInMonth()
                for day in range(1, days_in_month + 1):
                    day_date = QDate(check_year, check_month, day)
                    cal.setDateTextFormat(day_date, default_format)

            if allow_selection:
                highlight_format = QTextCharFormat()
                highlight_format.setBackground(QBrush(highlight_color))
                highlight_format.setForeground(QBrush(text_color))
                bold_font = QFont()
                bold_font.setBold(True)
                bold_font.setWeight(QFont.Bold)
                highlight_format.setFont(bold_font)
                for date in self.multi_selected_dates:
                    if date.isValid():
                        cal.setDateTextFormat(date, highlight_format)

                today = QDate.currentDate()
                if today.isValid() and today not in self.multi_selected_dates:
                    today_format = QTextCharFormat()
                    today_format.setFontWeight(QFont.Bold)
                    today_format.setForeground(text_color)
                    today_format.setUnderlineStyle(QTextCharFormat.SingleUnderline)
                    today_format.setUnderlineColor(text_color)
                    cal.setDateTextFormat(today, today_format)

        apply_for_calendar(self.calendar, allow_selection=True)
        apply_for_calendar(self.prev_calendar, allow_selection=False)
        apply_for_calendar(self.next_calendar, allow_selection=False)
        
        # Force repaint
        if self.calendar_view and Shiboken.isValid(self.calendar_view):
            if self.calendar_view.viewport():
                self.calendar_view.viewport().update()
            self.calendar_view.viewport().update(self.calendar_view.viewport().rect())
        self.calendar.update()

    def _attach_calendar_view(self) -> None:
        """Find and attach to the internal calendar view for mouse tracking."""
        if self.calendar_view and Shiboken.isValid(self.calendar_view) and self.calendar_view.viewport():
            self.calendar_view.viewport().removeEventFilter(self)

        view = (
            self.calendar.findChild(QTableView, "qt_calendar_calendarview")
            or next(iter(self.calendar.findChildren(QTableView)), None)
        )
        self.calendar_view = view
        if self.calendar_view and Shiboken.isValid(self.calendar_view) and self.calendar_view.viewport():
            self.calendar_view.setSelectionMode(QAbstractItemView.NoSelection)
            self.calendar_view.viewport().installEventFilter(self)
            self.calendar_view.viewport().setMouseTracking(True)
            # Install the custom delegate for multi-selection highlighting
            self.calendar_view.setItemDelegate(self.calendar_delegate)

    def _on_tree_activated(self, item: QTreeWidgetItem, column: int | None = None) -> None:  # noqa: ARG002
        """Sync calendar to the activated tree item and open pages."""
        date_value = item.data(0, Qt.UserRole)
        path_value = item.data(0, PATH_ROLE)

        if isinstance(date_value, QDate):
            self.calendar.setSelectedDate(date_value)
            self._update_calendar_dates(date_value.year(), date_value.month())
            # Only trigger journal-date open for day-level nodes (directories), not child pages
            path_obj = Path(self.vault_root) / str(path_value).lstrip("/") if path_value and self.vault_root else None
            if not path_obj or path_obj.is_dir():
                self.dateActivated.emit(date_value.year(), date_value.month(), date_value.day())

        if path_value and self.vault_root:
            page_path = Path(self.vault_root) / str(path_value).lstrip("/")
            # For folder nodes, prefer the matching .md inside that folder if it exists
            if page_path.is_dir():
                candidate_md = page_path / f"{page_path.name}{PAGE_SUFFIX}"
                candidate_txt = page_path / f"{page_path.name}{LEGACY_SUFFIX}"
                if candidate_md.exists():
                    page_path = candidate_md
                elif candidate_txt.exists():
                    page_path = candidate_txt
            if page_path.is_file():
                rel_path = "/" + page_path.relative_to(self.vault_root).as_posix()
                self.pageActivated.emit(rel_path)

    def _expand_to_date(self, date: QDate) -> None:
        """Expand and select the tree path for the given date."""
        target_year = f"{date.year()}"
        target_month = f"{date.month():02d}"
        target_day = f"{date.day():02d}"

        root = self.journal_tree.topLevelItem(0)
        if not root:
            return

        year_item = self._find_child_by_text(root, target_year)
        if not year_item:
            return
        self.journal_tree.expandItem(year_item)

        month_item = self._find_child_by_text(year_item, target_month)
        if not month_item:
            return
        self.journal_tree.expandItem(month_item)

        day_item = self._find_child_by_text(month_item, target_day)
        if not day_item:
            return

        self.journal_tree.setCurrentItem(day_item)
        self.journal_tree.scrollToItem(day_item)
        self._update_day_listing(date)
        self._update_insights_for_selection()

    def _find_child_by_text(self, parent: QTreeWidgetItem, text: str) -> Optional[QTreeWidgetItem]:
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child and child.text(0) == text:
                return child
        return None

    def _select_subpage_item(self, year: int, month: int, day: int, sub_name: str, rel_path: Optional[str] = None) -> None:
        """Select a subpage row in the day listing if present."""
        for i in range(self.journal_tree.topLevelItemCount()):
            top = self.journal_tree.topLevelItem(i)
            if not top:
                continue
            if top.data(0, Qt.UserRole) and isinstance(top.data(0, Qt.UserRole), QDate):
                if top.data(0, Qt.UserRole) == QDate(year, month, day):
                    for j in range(top.childCount()):
                        child = top.child(j)
                        if not child:
                            continue
                        child_path = child.data(0, PATH_ROLE) or ""
                        label_match = child.text(0).endswith(sub_name)
                        path_match = rel_path and str(rel_path).endswith(child_path)
                        if label_match or path_match:
                            self.journal_tree.setCurrentItem(child)
                            self.journal_tree.scrollToItem(child)
                            return

    def keyPressEvent(self, event):  # type: ignore[override]
        """Allow arrow keys and vi-style nav to move within the journal tree."""
        key_map = {
            Qt.Key_H: Qt.Key_Left,
            Qt.Key_L: Qt.Key_Right,
            Qt.Key_J: Qt.Key_Down,
            Qt.Key_K: Qt.Key_Up,
        }
        target_key = key_map.get(event.key(), event.key())
        if event.key() in (Qt.Key_H, Qt.Key_J, Qt.Key_K, Qt.Key_L) and not self._is_vi_mode():
            super().keyPressEvent(event)
            return
        if target_key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
            self.journal_tree.setFocus(Qt.OtherFocusReason)
            forwarded = QKeyEvent(event.type(), target_key, event.modifiers())
            QApplication.sendEvent(self.journal_tree, forwarded)
            event.accept()
            return
        super().keyPressEvent(event)

    def _is_vi_mode(self) -> bool:
        """Check if vi mode is enabled in the parent main window."""
        parent = self.parent()
        while parent:
            if hasattr(parent, "_vi_enabled"):
                return bool(parent._vi_enabled)
            parent = parent.parent()
        return False

    def eventFilter(self, obj, event):  # type: ignore[override]
        # Handle calendar widget events to detect shift-click
        if obj is self.calendar:
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                if event.modifiers() & Qt.ShiftModifier:
                    self._pending_shift_click = True
                    print(f"[CALENDAR] Shift key detected on calendar click")
                else:
                    self._pending_shift_click = False
        
        if (
            self.calendar_view
            and Shiboken.isValid(self.calendar_view)
            and self.calendar_view.viewport()
            and obj is self.calendar_view.viewport()
        ):
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                date = self._date_from_pos(event.pos())
                if date.isValid():
                    is_shift = bool(event.modifiers() & Qt.ShiftModifier)
                    if is_shift:
                        # Shift+Click: select range from anchor to this date
                        anchor = self._selection_anchor if self._selection_anchor and self._selection_anchor.isValid() else date
                        self._set_range_selection(anchor, date)
                        print(f"[CALENDAR] Viewport Shift+Click: Range {anchor.toString('yyyy-MM-dd')} -> {date.toString('yyyy-MM-dd')}, total selected: {len(self.multi_selected_dates)}")
                    else:
                        # Regular click: select only this date (clear previous selection)
                        self._set_single_selection(date)
                        print(f"[CALENDAR] Viewport Click: Selected only {date.toString('yyyy-MM-dd')}")
                    
                    self.calendar.setSelectedDate(date)
                    self._suppress_next_click = True
                    self._apply_multi_selection_formats()
                    self._update_day_listing(date)
                    self._update_insights_for_selection()
                    if not is_shift:
                        self.dateActivated.emit(date.year(), date.month(), date.day())
                    return True
            # Double-click: open/create day's page and remove any multi-day filter
            if event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                date = self._date_from_pos(event.pos())
                if date.isValid():
                    # Clear multi-selection filter and select this date
                    try:
                        self._set_single_selection(date)
                        self.calendar.setSelectedDate(date)
                        if hasattr(self, "filter_btn"):
                            self.filter_btn.setVisible(False)
                        self._apply_multi_selection_formats()
                        self._update_day_listing(date)
                        self._update_insights_for_selection()
                    except Exception:
                        pass
                    # Ensure day page exists and open it
                    try:
                        rel = self._ensure_day_page_exists(date)
                        if rel:
                            self.pageActivated.emit(rel)
                    except Exception:
                        pass
                    return True
        return super().eventFilter(obj, event)

    def _date_from_pos(self, pos) -> QDate:
        if not self.calendar_view or not Shiboken.isValid(self.calendar_view):
            return QDate()
        idx = self.calendar_view.indexAt(pos)
        if not idx.isValid():
            return QDate()
        model = idx.model()
        if model:
            val = idx.data(Qt.UserRole)
            if isinstance(val, QDate) and val.isValid():
                return val
            day_val = idx.data(Qt.DisplayRole)
            if isinstance(day_val, int):
                return self._resolve_day_from_index(idx.row(), idx.column(), day_val)
        return QDate()

    def _resolve_day_from_index(self, row: int, col: int, day: int) -> QDate:
        """Best-effort mapping from table index to a real date."""
        year = self.calendar.yearShown()
        month = self.calendar.monthShown()
        # Heuristic: top rows with large day numbers belong to previous month,
        # bottom rows with small day numbers belong to next month.
        if row == 0 and day > 7:
            month -= 1
            if month == 0:
                month = 12
                year -= 1
        elif row >= 4 and day <= 14:
            month += 1
            if month == 13:
                month = 1
                year += 1
        date = QDate(year, month, day)
        if date.isValid():
            return date
        return QDate()

    def _capture_expanded_paths(self) -> set[str]:
        """Remember which nodes are expanded so refreshes don't collapse them."""
        paths: set[str] = set()

        def _walk(item: QTreeWidgetItem) -> None:
            path = item.data(0, PATH_ROLE)
            if path and item.isExpanded():
                paths.add(path)
            for i in range(item.childCount()):
                child = item.child(i)
                if child:
                    _walk(child)

        root = self.journal_tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            if child:
                _walk(child)
        return paths

    def _capture_selected_path(self) -> Optional[str]:
        current = self.journal_tree.currentItem()
        if not current:
            return None
        return current.data(0, PATH_ROLE)

    def _update_day_listing(self, date: QDate) -> None:
        """Render the selected day's page and its subpages as children of the day item."""
        if not self.vault_root:
            return
        day_item = self._find_item_by_path(f"Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}")
        if not day_item:
            return
        day_item.takeChildren()
        base_dir = Path(self.vault_root) / "Journal" / f"{date.year():04d}" / f"{date.month():02d}" / f"{date.day():02d}"
        if not base_dir.exists():
            return
        subpages = self._list_day_subpages(base_dir)
        # Main day page first: create a main node and group headings under it
        main_path = f"/Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}/{date.day():02d}{PAGE_SUFFIX}"
        main = QTreeWidgetItem([f"{date.year():04d}-{date.month():02d}-{date.day():02d} (day)"])
        main.setData(0, Qt.UserRole, date)
        main.setData(0, PATH_ROLE, main_path)
        day_item.addChild(main)

        # Parse headings from the main day page (preserve order)
        heading_texts: list[str] = []
        try:
            main_file = Path(self.vault_root) / main_path.lstrip("/")
            if main_file.exists():
                text = main_file.read_text(encoding="utf-8", errors="ignore")
                heading_texts = self._parse_headings_from_text(text)
        except Exception:
            heading_texts = []

        # Create heading nodes under the main node
        heading_nodes: dict[str, QTreeWidgetItem] = {}
        for h in heading_texts:
            hn = QTreeWidgetItem([h])
            hn.setData(0, Qt.UserRole, date)
            hn.setData(0, PATH_ROLE, None)
            main.addChild(hn)
            heading_nodes[h.lower()] = hn

        # 'Other' bucket for subpages that don't map to a heading
        other_node = QTreeWidgetItem(["Other"])
        other_node.setData(0, Qt.UserRole, date)
        other_node.setData(0, PATH_ROLE, None)
        main.addChild(other_node)

        # Add subpages and attempt to associate them with headings by searching content
        for label, rel_path in subpages:
            child_label = Path(rel_path).stem
            child_item = QTreeWidgetItem([child_label])
            child_item.setData(0, Qt.UserRole, date)
            child_item.setData(0, PATH_ROLE, rel_path)

            # Try to read subpage and find a heading match
            placed = False
            try:
                target = Path(self.vault_root) / rel_path.lstrip("/")
                if target.exists():
                    sub_text = target.read_text(encoding="utf-8", errors="ignore")
                    sub_headings = self._parse_headings_from_text(sub_text)
                    # If any heading in subpage matches a main heading, attach there
                    for sh in sub_headings:
                        key = sh.strip().lower()
                        if key in heading_nodes:
                            heading_nodes[key].addChild(child_item)
                            placed = True
                            break
                    # Otherwise, try searching page content for main heading tokens
                    if not placed and heading_texts:
                        txt_low = sub_text.lower()
                        for h in heading_texts:
                            if h.lower() in txt_low:
                                heading_nodes[h.lower()].addChild(child_item)
                                placed = True
                                break
            except Exception:
                placed = False

            if not placed:
                other_node.addChild(child_item)

        day_item.setExpanded(True)

    def _list_day_subpages(self, base_dir: Path) -> list[tuple[str, str]]:
        """Return (label, rel_path) for subpages under a journal day (recursive)."""

        entries: list[tuple[str, str]] = []

        def add_from_dir(directory: Path, prefix: str = "") -> None:
            try:
                children = sorted(directory.iterdir())
            except OSError:
                return
            for entry in children:
                if entry.is_dir():
                    add_from_dir(entry, f"{prefix}{entry.name}/")
                elif entry.is_file() and entry.suffix.lower() in PAGE_SUFFIXES:
                    if entry.suffix.lower() == LEGACY_SUFFIX and entry.with_suffix(PAGE_SUFFIX).exists():
                        continue
                    # Skip the root day's own file; everything else is a subpage
                    if directory == base_dir and entry.stem == base_dir.name:
                        continue
                    label = f"{prefix}{entry.stem}".rstrip("/")
                    rel = "/" + entry.relative_to(self.vault_root).as_posix()
                    entries.append((label, rel))

        add_from_dir(base_dir)
        return entries

    def _update_insights_from_calendar(self) -> None:
        self._update_insights_for_selection()

    def _update_insights_for_selection(self, current_path: Optional[str] = None) -> None:
        """Update insights based on the current multi-selection."""
        # Reset recent data loaded flag so user has to click to load each time
        self._recent_data_loaded = False
        
        dates_for_tasks: list[QDate] = []
        if self.multi_selected_dates:
            dates = sorted(self.multi_selected_dates, key=lambda d: d.toJulianDay())
            dates_for_tasks = dates
        else:
            date = self.calendar.selectedDate()
            dates_for_tasks = [date]
        # Update the due-tasks list first so insight counts reflect the visible rows
        self._update_due_tasks(dates_for_tasks)
        if self._ai_enabled:
            self._update_ai_summary_for_selection(dates_for_tasks)
        if self.multi_selected_dates:
            dates = sorted(self.multi_selected_dates, key=lambda d: d.toJulianDay())
            if len(dates) == 1:
                self._update_insights(dates[0], current_path)
            else:
                # For multi-day selection, show only subpages and hide headings
                try:
                    self.headings_list.setVisible(False)
                    if getattr(self, "_headings_col", None):
                        self._headings_col.setVisible(False)
                    if getattr(self, "_headings_label", None):
                        self._headings_label.setVisible(False)
                except Exception:
                    pass
                self._update_insights_multi(dates, current_path)
        else:
            date = self.calendar.selectedDate()
            self._update_insights(date, current_path)

    def _update_insights_multi(self, dates: list[QDate], current_path: Optional[str] = None) -> None:
        if not self.vault_root:
            self.insight_title.setText("No date selected")
            self.insight_counts.setText("")
            self.insight_tags.setText("")
            self.subpage_list.clear()
            try:
                self.headings_list.clear()
            except Exception:
                pass
            return
        tags: list[str] = []
        total_files: list[Path] = []
        day_entries = 0
        self.subpage_list.clear()
        try:
            self.headings_list.clear()
        except Exception:
            pass
        self.recent_list.clear()
        for date in dates:
            base_dir = Path(self.vault_root) / "Journal" / f"{date.year():04d}" / f"{date.month():02d}" / f"{date.day():02d}"
            date_label = date.toString("yyyy-MM-dd")
            if not base_dir.exists():
                continue
            day_page = base_dir / f"{base_dir.name}{PAGE_SUFFIX}"
            if day_page.exists():
                total_files.append(day_page)
                day_entries += 1
                self._add_insight_item(f"{date_label} (day)", "/" + day_page.relative_to(self.vault_root).as_posix())
            subpages = self._list_day_subpages(base_dir)
            for label, rel in subpages:
                target = Path(self.vault_root) / rel.lstrip("/")
                if target.exists():
                    total_files.append(target)
                # Show only the page name for subpage entries
                try:
                    short = Path(rel).stem
                except Exception:
                    short = label
                self._add_insight_item(f"{date_label} • {short}", rel)
        for file in total_files:
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue
            tags.extend(TAG_PATTERN.findall(text))
        unique_tags = sorted(set(tags))
        entries_count = len(total_files)
        subpages_count = max(0, entries_count - day_entries)
        self.insight_title.setText(f"Selected {len(dates)} days")
        # Show the filtered indicator so user can clear the multi-day filter
        try:
            self.filter_btn.setVisible(True)
        except Exception:
            pass
        self.insight_counts.setText(f"Entries: {entries_count}  •  Subpages: {subpages_count}  •  Tasks: {self._due_task_count}")
        self.insight_tags.setText("Tags: " + (", ".join(unique_tags[:8]) if unique_tags else "—"))
        # Populate recently edited for selected dates
        self._populate_recent_modified(dates, current_path=current_path, expand_single=False)
        if current_path:
            for idx in range(self.subpage_list.count()):
                it = self.subpage_list.item(idx)
                if it and current_path.endswith(str(it.data(PATH_ROLE))):
                    self.subpage_list.setCurrentItem(it)
                    break

    def _add_insight_item(self, label: str, rel_path: str) -> None:
        item = QListWidgetItem(label)
        item.setData(PATH_ROLE, rel_path)
        # Tooltip shows full label; item text should remain single-line in the UI
        try:
            item.setToolTip(label)
        except Exception:
            pass
        self.subpage_list.addItem(item)

    def _clear_due_tasks(self, message: Optional[str] = None) -> None:
        self._configure_task_columns()
        self.tasks_due_list.clear()
        self._due_task_count = 0
        if message:
            row = QTreeWidgetItem([""] * self.tasks_due_list.columnCount())
            row.setText(0, message)
            row.setFirstColumnSpanned(True)
            row.setFlags(Qt.NoItemFlags)
            self.tasks_due_list.addTopLevelItem(row)

    def _add_task_section(self, title: str) -> QTreeWidgetItem:
        row = QTreeWidgetItem([""] * self.tasks_due_list.columnCount())
        row.setText(0, title)
        row.setFirstColumnSpanned(True)
        row.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        font = row.font(0)
        font.setBold(True)
        row.setFont(0, font)
        header_bg = QColor("#30475e")
        header_fg = QColor("#FFFFFF")
        for col in range(self.tasks_due_list.columnCount()):
            row.setBackground(col, header_bg)
            row.setForeground(col, header_fg)
        self.tasks_due_list.addTopLevelItem(row)
        row.setExpanded(True)
        return row

    def _add_task_row(self, task: dict, *, parent: Optional[QTreeWidgetItem] = None) -> None:
        path = str(task.get("path") or "")
        if not path.startswith("/"):
            path = "/" + path.lstrip("/")
        line = task.get("line") or 1
        due_idx = 2
        start_idx = 3 if self._show_task_start_column else None
        path_idx = 3 if (not self._show_task_start_column and self._show_task_page_column) else 4
        start_value = (task.get("starts") or task.get("start") or "").strip()
        priority_txt, is_overdue = self._priority_time_label(task)
        row_values = [""] * self.tasks_due_list.columnCount()
        row_values[0] = priority_txt
        row_values[1] = task.get("text") or "(task)"
        row_values[due_idx] = task.get("due") or ""
        if self._show_task_start_column and start_idx is not None:
            row_values[start_idx] = start_value
        if self._show_task_page_column:
            row_values[path_idx] = path_to_colon(path)
        row = QTreeWidgetItem(row_values)
        row.setData(0, Qt.UserRole, task)
        row.setData(0, PATH_ROLE, path)
        row.setData(0, LINE_ROLE, line)
        tooltip_parts = []
        if due_str := (task.get("due") or "").strip():
            tooltip_parts.append(f"Due: {due_str}")
        if start_str := start_value:
            tooltip_parts.append(f"Start: {start_str}")
        if tooltip_parts:
            row.setToolTip(1, " • ".join(tooltip_parts))
        pri_brush = self._priority_brush(int(task.get("priority") or 0))
        if pri_brush:
            if pri_brush.get("bg"):
                row.setBackground(0, pri_brush["bg"])
            if pri_brush.get("fg"):
                row.setForeground(0, pri_brush["fg"])
        if is_overdue:
            font = row.font(0)
            font.setUnderline(True)
            row.setFont(0, font)
        due_colors = self._due_colors(task.get("due") or "")
        if due_colors:
            fg, bg = due_colors
            row.setForeground(due_idx, fg)
            row.setBackground(due_idx, bg)
        if parent:
            parent.addChild(row)
        else:
            self.tasks_due_list.addTopLevelItem(row)

    @staticmethod
    def _pretty_date_label(qdate: QDate) -> str:
        """Return a friendly date string like 'Wed Jan 7th 2025'."""
        if not qdate.isValid():
            return ""
        day = qdate.day()
        suffix = "th"
        if day % 10 == 1 and day != 11:
            suffix = "st"
        elif day % 10 == 2 and day != 12:
            suffix = "nd"
        elif day % 10 == 3 and day != 13:
            suffix = "rd"
        return f"{qdate.toString('ddd')} {qdate.toString('MMM')} {day}{suffix} {qdate.year()}"

    def _populate_recent_modified(self, dates: list[QDate], *, current_path: Optional[str], expand_single: bool) -> None:
        """Populate recent_list using the modified-files API."""
        self.recent_list.clear()
        if not self.vault_root or not dates:
            return
        
        # Show "Click to load..." link instead of auto-loading
        if not self._recent_data_loaded:
            load_item = QListWidgetItem("Click to load...")
            load_item.setData(RECENT_ACTION_ROLE, "load")
            load_item.setForeground(QColor("#0066CC"))
            try:
                load_item.setToolTip("Click to load recently edited pages")
            except Exception:
                pass
            self.recent_list.addItem(load_item)
            # Store parameters for later loading
            self._recent_pending_params = (dates, current_path, expand_single)
            return
        
        # Show "Fetching data..." while loading
        if self._recent_fetching:
            fetch_item = QListWidgetItem("Fetching data...")
            fetch_item.setForeground(QColor("#666666"))
            self.recent_list.addItem(fetch_item)
            return
        
        if expand_single and len(dates) == 1:
            d = dates[0]
            span = [d.addDays(-1), d, d.addDays(1)]
            dates = span
        # Derive min/max ISO date strings
        try:
            start = min(dates, key=lambda d: d.toJulianDay())
            end = max(dates, key=lambda d: d.toJulianDay())
            start_str = start.toString("yyyy-MM-dd")
            end_str = end.toString("yyyy-MM-dd")
        except Exception:
            return
        try:
            resp = self.http.post(f"{self.api_base}/api/files/modified", json={"start_date": start_str, "end_date": end_str})
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
        except Exception:
            return
        for entry in items:
            rel = entry.get("path", "")
            if not rel or (current_path and rel == current_path):
                continue
            if not self.recent_journal_checkbox.isChecked() and rel.startswith("/Journal/"):
                continue
            label = Path(rel).stem
            item = QListWidgetItem(label)
            item.setData(PATH_ROLE, rel)
            try:
                item.setToolTip(rel)
            except Exception:
                pass
            self.recent_list.addItem(item)

    def _priority_brush(self, level: int) -> Optional[dict]:
        """Return background/foreground for priority level."""
        return priority_brush(level)

    def _contrast_text_color(self, bg: QColor) -> QColor:
        """Return a readable text color for the given background."""
        return contrast_text_color(bg)

    def _due_colors(self, due_str: str) -> Optional[tuple]:
        """Return (fg, bg) for due column with red/orange/yellow emphasis."""
        return due_colors_from_due_str(due_str, include_tomorrow=True)

    def _load_task_display_prefs(self) -> tuple[bool, bool]:
        show_start = config.load_show_task_start_date()
        show_page = config.load_show_task_page()
        return bool(show_start), bool(show_page)

    def _configure_task_columns(self, force: bool = False) -> None:
        show_start, show_page = self._load_task_display_prefs()
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
        self.tasks_due_list.setColumnCount(len(headers))
        self.tasks_due_list.setHeaderLabels(headers)
        header = self.tasks_due_list.header()
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
        self._ensure_task_column_widths()

    def _ensure_task_column_widths(self) -> None:
        header = self.tasks_due_list.header()
        try:
            if header.sectionSize(0) < 40:
                header.resizeSection(0, 70)
            if header.sectionSize(1) < 80:
                header.resizeSection(1, 260)
            if header.sectionSize(2) < 60:
                header.resizeSection(2, 90)
        except Exception:
            pass

    def _relative_day_label(self, target: Date, prefix: str = "") -> str:
        return relative_day_label(target, prefix=prefix)

    def _priority_time_label(self, task: dict) -> tuple[str, bool]:
        return priority_time_label(task)

    def _fetch_tasks_api(
        self,
        *,
        include_done: bool,
        include_ancestors: bool,
        actionable_only: bool,
    ) -> list[dict]:
        if self.http:
            cache_key = (
                "",
                (),
                bool(include_done),
                bool(include_ancestors),
                bool(actionable_only),
            )
            cached = self._api_task_cache.get(cache_key)
            now = time.monotonic()
            if cached and (now - cached[0]) <= self._api_task_cache_ttl:
                return cached[1]
            params = {
                "query": "",
                "include_done": include_done,
                "include_ancestors": include_ancestors,
                "actionable_only": actionable_only,
            }
            try:
                resp = self.http.get("/api/tasks", params=params)
                resp.raise_for_status()
                payload = resp.json()
                items = payload.get("items", [])
                self._api_task_cache[cache_key] = (now, items)
                return items
            except Exception as exc:
                print(f"[CALENDAR] Failed to fetch tasks via API: {exc}")
                return []
        return config.fetch_tasks(
            include_done=include_done,
            include_ancestors=include_ancestors,
            actionable_only=actionable_only,
        )

    @staticmethod
    def _parse_date(value: str) -> Optional[Date]:
        try:
            return Date.fromisoformat(value.strip())
        except Exception:
            return None

    def _update_due_tasks(self, dates: list[QDate]) -> None:
        """List tasks due on any of the selected dates."""
        self._configure_task_columns()
        if not dates or not config.has_active_vault():
            self._clear_due_tasks("No due tasks for selection")
            return
        valid_dates = [d for d in dates if d and d.isValid()]
        if not valid_dates:
            self._clear_due_tasks("No due tasks for selection")
            return
        start_dt = min(valid_dates, key=lambda d: d.toJulianDay())
        end_dt = max(valid_dates, key=lambda d: d.toJulianDay())
        range_start = Date(start_dt.year(), start_dt.month(), start_dt.day())
        range_end = Date(end_dt.year(), end_dt.month(), end_dt.day())
        # If 'future' checkbox is enabled and a single date is selected,
        # extend the end of the range to the end of that month to show
        # future-starting tasks for the month.
        try:
            if getattr(self, "future_checkbox", None) and self.future_checkbox.isChecked() and len(valid_dates) == 1:
                y = range_start.year
                m = range_start.month
                last = calendar.monthrange(y, m)[1]
                range_end = Date(y, m, last)
        except Exception:
            pass
        try:
            tasks = self._fetch_tasks_api(include_done=False, include_ancestors=False, actionable_only=False)
        except Exception:
            tasks = []
        if os.getenv("ZIMX_DEBUG_TASKS_API", "0") not in ("0", "false", "False", ""):
            print(f"[CALENDAR] fetched tasks count={len(tasks)}")
        overdue_tasks: list[dict] = []
        due_tasks: list[dict] = []
        start_tasks: list[dict] = []
        unscheduled_tasks: list[dict] = []
        for task in tasks:
            path = task.get("path") or ""
            if not path:
                continue
            due_str = (task.get("due") or "").strip()
            start_str = (task.get("starts") or task.get("start") or "").strip()
            due_dt = self._parse_date(due_str)
            start_dt_val = self._parse_date(start_str)
            is_overdue = bool(due_dt and due_dt < range_start)
            is_due_in_range = bool(due_dt and range_start <= due_dt <= range_end)
            starts_in_range = bool(start_dt_val and range_start <= start_dt_val <= range_end)
            # Respect overdue checkbox: if unchecked, exclude all overdue items
            show_overdue = bool(getattr(self, "overdue_checkbox", True) and self.overdue_checkbox.isChecked())
            if is_overdue and not show_overdue:
                continue
            if is_overdue:
                overdue_tasks.append(task)
                continue
            if is_due_in_range:
                due_tasks.append(task)
                continue
            if starts_in_range:
                start_tasks.append(task)
                continue
            if not due_dt and not start_dt_val:
                unscheduled_tasks.append(task)
        self.tasks_due_list.clear()
        total_count = 0
        max_unscheduled = 50
        unscheduled_total = len(unscheduled_tasks)
        sort_key = lambda t: (
            t.get("due") or t.get("start") or t.get("starts") or "",
            t.get("path") or "",
            t.get("line") or 0,
        )
        if unscheduled_total > max_unscheduled:
            unscheduled_tasks = sorted(unscheduled_tasks, key=sort_key)[:max_unscheduled]
        section_defs = [
            ("Overdue", overdue_tasks),
            ("Due", due_tasks),
            ("Starts", start_tasks),
            (
                "Unscheduled"
                if unscheduled_total <= max_unscheduled
                else f"Unscheduled (showing {max_unscheduled} of {unscheduled_total})",
                unscheduled_tasks,
            ),
        ]
        if not any(section for _, section in section_defs):
            self._clear_due_tasks("No tasks for selection")
            return
        for title, items in section_defs:
            if not items:
                continue
            section_item = self._add_task_section(title)
            for task in sorted(items, key=sort_key):
                self._add_task_row(task, parent=section_item)
                total_count += 1
        self._due_task_count = total_count
        if total_count:
            self._ensure_task_column_widths()
        if os.getenv("ZIMX_DEBUG_TASKS_API", "0") not in ("0", "false", "False", ""):
            print(
                f"[CALENDAR] sections overdue={len(overdue_tasks)} due={len(due_tasks)} "
                f"start={len(start_tasks)} unscheduled={len(unscheduled_tasks)} total={total_count}"
            )

    def _open_task_date_popup(self, pos) -> None:
        if self.tasks_due_list:
            item = self.tasks_due_list.itemAt(pos)
            if item and not item.isSelected():
                self.tasks_due_list.clearSelection()
                item.setSelected(True)
        targets = self._collect_task_date_targets()
        if not targets:
            return
        self._task_date_targets = targets
        if not self._task_date_dialog:
            self._task_date_dialog = QDialog(self, Qt.Popup)
            layout = QVBoxLayout(self._task_date_dialog)
            layout.setContentsMargins(8, 8, 8, 8)
            layout.setSpacing(6)

            calendars_row = QHBoxLayout()
            calendars_row.setContentsMargins(0, 0, 0, 0)
            calendars_row.setSpacing(8)

            start_col = QVBoxLayout()
            start_label = QLabel("Start on")
            self._task_date_start_cal = QCalendarWidget()
            self._task_date_start_cal.setGridVisible(True)
            self._task_date_start_cal.clicked.connect(self._on_task_start_date_clicked)
            self._task_date_apply_start = QCheckBox("Set start")
            self._task_date_apply_start.setChecked(True)
            clear_start_btn = QPushButton("Clear start")
            clear_start_btn.clicked.connect(self._clear_task_start_date)
            start_col.addWidget(start_label)
            start_col.addWidget(self._task_date_start_cal)
            start_col.addWidget(self._task_date_apply_start)
            start_col.addWidget(clear_start_btn)

            due_col = QVBoxLayout()
            due_label = QLabel("Due on")
            self._task_date_due_cal = QCalendarWidget()
            self._task_date_due_cal.setGridVisible(True)
            self._task_date_due_cal.clicked.connect(self._on_task_due_date_clicked)
            self._task_date_apply_due = QCheckBox("Set due")
            self._task_date_apply_due.setChecked(True)
            clear_due_btn = QPushButton("Clear due")
            clear_due_btn.clicked.connect(self._clear_task_due_date)
            due_col.addWidget(due_label)
            due_col.addWidget(self._task_date_due_cal)
            due_col.addWidget(self._task_date_apply_due)
            due_col.addWidget(clear_due_btn)

            calendars_row.addLayout(start_col, 1)
            calendars_row.addLayout(due_col, 1)
            layout.addLayout(calendars_row)

            buttons = QHBoxLayout()
            buttons.addStretch(1)
            apply_btn = QPushButton("Apply")
            apply_btn.clicked.connect(self._apply_task_date_popup)
            cancel_btn = QPushButton("Cancel")
            cancel_btn.clicked.connect(self._close_task_date_popup)
            buttons.addWidget(cancel_btn)
            buttons.addWidget(apply_btn)
            layout.addLayout(buttons)

        base_date = self.calendar.selectedDate() if self.calendar else QDate.currentDate()
        start_date = self._task_date_from_targets("start") or base_date
        due_date = self._task_date_from_targets("due") or base_date
        if self._task_date_start_cal:
            self._task_date_start_cal.setSelectedDate(start_date)
        if self._task_date_due_cal:
            self._task_date_due_cal.setSelectedDate(due_date)
        if self._task_date_apply_start:
            self._task_date_apply_start.setChecked(True)
        if self._task_date_apply_due:
            self._task_date_apply_due.setChecked(True)
        self._task_date_clear_start = False
        self._task_date_clear_due = False

        if self.tasks_due_list and self.tasks_due_list.viewport():
            global_pos = self.tasks_due_list.viewport().mapToGlobal(pos)
            hint = self._task_date_dialog.sizeHint()
            self._task_date_dialog.move(self._smart_popup_pos(global_pos, hint))
        self._task_date_dialog.show()

    def _open_task_date_quick_menu(self, role: str, targets: list[dict], anchor: QPoint) -> None:
        menu = QMenu(self)
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
        menu.exec(anchor)
        self._suppress_task_activation = False

    def _apply_task_date_choice(self, role: str, label: str, targets: list[dict]) -> None:
        target_date = self._resolve_quick_date(label, role)
        if not target_date:
            return
        if role == "start":
            self._update_tasks_with_dates(
                targets,
                target_date,
                None,
                apply_start=True,
                apply_due=False,
                clear_start=False,
                clear_due=False,
            )
        else:
            self._update_tasks_with_dates(
                targets,
                None,
                target_date,
                apply_start=False,
                apply_due=True,
                clear_start=False,
                clear_due=False,
            )
        QTimer.singleShot(200, self._update_insights_for_selection)

    def _open_task_date_picker(self, role: str, targets: list[dict], anchor: Optional[QPoint] = None) -> None:
        anchor_pos = anchor or QCursor.pos()
        dlg = DateInsertDialog(self, anchor_pos=None)
        dlg.move(self._smart_popup_pos(anchor_pos, dlg.sizeHint()))
        try:
            dlg.calendar.clicked.connect(lambda *_: dlg.accept())
        except Exception:
            pass
        if dlg.exec() != QDialog.Accepted:
            return
        value = dlg.selected_date_text()
        if not value:
            return
        if role == "start":
            self._update_tasks_with_dates(
                targets,
                value,
                None,
                apply_start=True,
                apply_due=False,
                clear_start=False,
                clear_due=False,
            )
        else:
            self._update_tasks_with_dates(
                targets,
                None,
                value,
                apply_start=False,
                apply_due=True,
                clear_start=False,
                clear_due=False,
            )
        QTimer.singleShot(200, self._update_insights_for_selection)

    def _task_date_anchor(self) -> QPoint:
        items = self.tasks_due_list.selectedItems()
        if items:
            rect = self.tasks_due_list.visualItemRect(items[0])
            return self.tasks_due_list.viewport().mapToGlobal(rect.topRight() + QPoint(0, 4))
        return QCursor.pos()

    def _smart_popup_pos(self, anchor: QPoint, hint: QSize) -> QPoint:
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        avail = screen.availableGeometry() if screen else self.geometry()
        x = anchor.x()
        y = anchor.y()
        if x + hint.width() > avail.right():
            x = anchor.x() - hint.width()
        if y + hint.height() > avail.bottom():
            y = anchor.y() - hint.height()
        x = max(avail.left(), min(x, avail.right() - hint.width()))
        y = max(avail.top(), min(y, avail.bottom() - hint.height()))
        return QPoint(x, y)

    def _resolve_quick_date(self, label: str, role: str) -> Optional[str]:
        today = Date.today()
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

    def _collect_task_date_targets(self) -> list[dict]:
        targets: list[dict] = []
        for item in self.tasks_due_list.selectedItems():
            path = item.data(0, PATH_ROLE)
            line = item.data(0, LINE_ROLE)
            task = item.data(0, Qt.UserRole) or {}
            if not path or not line:
                continue
            try:
                line_num = int(line)
            except (TypeError, ValueError):
                line_num = 1
            targets.append({"path": str(path), "line": line_num, "task": task})
        return targets

    def _task_date_from_targets(self, field: str) -> Optional[QDate]:
        if not self._task_date_targets:
            return None
        task = self._task_date_targets[0].get("task") or {}
        value = ""
        if field == "start":
            value = (task.get("starts") or task.get("start") or "").strip()
        elif field == "due":
            value = (task.get("due") or "").strip()
        if not value:
            return None
        try:
            parsed = Date.fromisoformat(value)
        except Exception:
            return None
        return QDate(parsed.year, parsed.month, parsed.day)

    def _on_task_start_date_clicked(self, qdate: QDate) -> None:
        self._task_date_clear_start = False
        if self._task_date_apply_start:
            self._task_date_apply_start.setChecked(True)

    def _on_task_due_date_clicked(self, qdate: QDate) -> None:
        self._task_date_clear_due = False
        if self._task_date_apply_due:
            self._task_date_apply_due.setChecked(True)

    def _clear_task_start_date(self) -> None:
        self._task_date_clear_start = True
        if self._task_date_apply_start:
            self._task_date_apply_start.setChecked(True)

    def _clear_task_due_date(self) -> None:
        self._task_date_clear_due = True
        if self._task_date_apply_due:
            self._task_date_apply_due.setChecked(True)

    def _close_task_date_popup(self) -> None:
        if self._task_date_dialog:
            self._task_date_dialog.hide()

    def _apply_task_date_popup(self) -> None:
        if not self._task_date_targets:
            self._close_task_date_popup()
            return
        apply_start = bool(self._task_date_apply_start and self._task_date_apply_start.isChecked())
        apply_due = bool(self._task_date_apply_due and self._task_date_apply_due.isChecked())
        start_value = None
        due_value = None
        if apply_start and not self._task_date_clear_start and self._task_date_start_cal:
            start_value = self._task_date_start_cal.selectedDate().toString("yyyy-MM-dd")
        if apply_due and not self._task_date_clear_due and self._task_date_due_cal:
            due_value = self._task_date_due_cal.selectedDate().toString("yyyy-MM-dd")
        self._update_tasks_with_dates(
            self._task_date_targets,
            start_value,
            due_value,
            apply_start=apply_start,
            apply_due=apply_due,
            clear_start=self._task_date_clear_start,
            clear_due=self._task_date_clear_due,
        )
        self._close_task_date_popup()
        QTimer.singleShot(200, self._update_insights_for_selection)

    def _update_tasks_with_dates(
        self,
        targets: list[dict],
        start_value: Optional[str],
        due_value: Optional[str],
        *,
        apply_start: bool,
        apply_due: bool,
        clear_start: bool,
        clear_due: bool,
    ) -> None:
        if self.http:
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
                "clear_start": clear_start,
                "clear_due": clear_due,
            }
            try:
                resp = self.http.post("/api/tasks/update-dates", json=payload)
                resp.raise_for_status()
            except Exception as exc:
                print(f"[CALENDAR] Failed to update task dates via API: {exc}")
            QTimer.singleShot(200, self._update_insights_for_selection)
            self.tasksUpdated.emit()
            return
        if not self.vault_root:
            return
        targets_by_path: dict[str, list[dict]] = {}
        for target in targets:
            path = target.get("path")
            if not path:
                continue
            targets_by_path.setdefault(path, []).append(target)
        for rel_path, items in targets_by_path.items():
            file_path = Path(self.vault_root) / rel_path.lstrip("/")
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
                    clear_start=clear_start,
                    clear_due=clear_due,
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
        QTimer.singleShot(200, self._update_insights_for_selection)
        self.tasksUpdated.emit()

    def _update_task_line_dates(
        self,
        line: str,
        *,
        start_value: Optional[str],
        due_value: Optional[str],
        apply_start: bool,
        apply_due: bool,
        clear_start: bool,
        clear_due: bool,
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
        if apply_start or clear_start:
            final_start = None if clear_start else start_value
        if apply_due or clear_due:
            final_due = None if clear_due else due_value
        cleaned = re.sub(r"\s*[<>][0-9]{4}-[0-9]{2}-[0-9]{2}", "", base).rstrip()
        if final_start:
            cleaned += f" >{final_start}"
        if final_due:
            cleaned += f" <{final_due}"
        return cleaned + newline

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
        if not self.vault_root:
            return
        targets_by_path: dict[str, list[dict]] = {}
        for target in targets:
            path = target.get("path")
            if not path:
                continue
            rel_path = str(path)
            if not rel_path.startswith("/"):
                rel_path = "/" + rel_path.lstrip("/")
            targets_by_path.setdefault(rel_path, []).append(target)
        for rel_path, items in targets_by_path.items():
            file_path = Path(self.vault_root) / rel_path.lstrip("/")
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
                indexer.index_page(rel_path, new_content)
            except Exception:
                pass
        QTimer.singleShot(200, self._update_insights_for_selection)
        self.tasksUpdated.emit()

    def _update_insights(self, date: QDate, current_path: Optional[str] = None) -> None:
        if not self.vault_root or not date.isValid():
            self.insight_title.setText("No date selected")
            self.insight_counts.setText("")
            self.insight_tags.setText("")
            self.recent_list.clear()
            self.subpage_list.clear()
            try:
                self.headings_list.clear()
            except Exception:
                pass
            self.tasks_due_list.clear()
            return
        base_dir = Path(self.vault_root) / "Journal" / f"{date.year():04d}" / f"{date.month():02d}" / f"{date.day():02d}"
        if not base_dir.exists():
            self.insight_title.setText(self._pretty_date_label(date))
            self.insight_counts.setText("No journal entry.")
            self.insight_tags.setText("")
            self.recent_list.clear()
            self.subpage_list.clear()
            try:
                self.headings_list.clear()
            except Exception:
                pass
            return
        day_page = base_dir / f"{base_dir.name}{PAGE_SUFFIX}"
        subpages = self._list_day_subpages(base_dir)
        files = [day_page] if day_page.exists() else []
        for _, rel_path in subpages:
            target = Path(self.vault_root) / rel_path.lstrip("/")
            if target.exists():
                files.append(target)
        tags = []
        for file in files:
            try:
                text = file.read_text(encoding="utf-8")
            except Exception:
                continue
            tags.extend(TAG_PATTERN.findall(text))
        unique_tags = sorted(set(tags))
        subpages_count = max(0, len(files) - 1)
        self.insight_title.setText(self._pretty_date_label(date))
        # Hide filter when viewing a single day
        try:
            self.filter_btn.setVisible(False)
        except Exception:
            pass
        self.insight_counts.setText(f"Entries: {len(files)}  •  Subpages: {subpages_count}  •  Tasks: {self._due_task_count}")
        self.insight_tags.setText("Tags: " + (", ".join(unique_tags[:8]) if unique_tags else "—"))
        # Populate pages + headings list
        self.subpage_list.clear()
        self.recent_list.clear()
        try:
            self.headings_list.clear()
        except Exception:
            pass
        # Headings only relevant for single-day view
        try:
            self.headings_list.setVisible(True)
            if getattr(self, "_headings_col", None):
                self._headings_col.setVisible(True)
            if getattr(self, "_headings_label", None):
                self._headings_label.setVisible(True)
        except Exception:
            pass
        main_path = f"/Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}/{date.day():02d}{PAGE_SUFFIX}"
        # Add headings from the main page (in order)
        try:
            main_file = Path(self.vault_root) / main_path.lstrip("/")
            main_text = main_file.read_text(encoding="utf-8", errors="ignore") if main_file.exists() else ""
        except Exception:
            main_text = ""
        headings = self._parse_headings_from_text(main_text)
        # Add heading items (anchor to main page with slug) into the Headings column
        for h in headings:
            slug = self._slugify(h)
            item = QListWidgetItem(h)
            item.setData(PATH_ROLE, f"{main_path}#{slug}")
            try:
                item.setToolTip(h)
            except Exception:
                pass
            self.headings_list.addItem(item)
        # Then add subpages (only the page name shown) into the Sub Pages column
        for label, rel in subpages:
            try:
                short = Path(rel).stem
            except Exception:
                short = label
            item = QListWidgetItem(short)
            item.setData(PATH_ROLE, rel)
            self.subpage_list.addItem(item)
        # Recently edited pages for the selected day (±1 day window)
        self._populate_recent_modified([date], current_path=current_path, expand_single=True)
        # Highlight current page if provided
        if current_path:
            # Try headings first
            for idx in range(self.headings_list.count()):
                it = self.headings_list.item(idx)
                if it and current_path.endswith(str(it.data(PATH_ROLE))):
                    self.headings_list.setCurrentItem(it)
                    break
            else:
                for idx in range(self.subpage_list.count()):
                    it = self.subpage_list.item(idx)
                    if it and current_path.endswith(str(it.data(PATH_ROLE))):
                        self.subpage_list.setCurrentItem(it)
                        break

    def _open_insight_link(self, item: QListWidgetItem) -> None:
        if not item:
            return
        path = item.data(PATH_ROLE)
        if path:
            self.pageActivated.emit(str(path))

    def _on_recent_item_activated(self, item: QListWidgetItem) -> None:
        """Handle clicks on recent list items, including the load action."""
        if not item:
            return
        
        # Check if this is the "Click to load..." action item
        action = item.data(RECENT_ACTION_ROLE)
        if action == "load":
            self._load_recent_data()
            return
        
        # Regular item - open the page
        path = item.data(PATH_ROLE)
        if path:
            self.pageActivated.emit(str(path))
    
    def _load_recent_data(self) -> None:
        """Load the recent edited pages data."""
        if self._recent_fetching or not self._recent_pending_params:
            return
        
        # Expand to 4 rows when loading data
        try:
            row_h = self.recent_list.sizeHintForRow(0) or (self.recent_list.fontMetrics().height() + 6)
            row_h = max(20, row_h)
            self.recent_list.setMinimumHeight(row_h * 4)
            self.recent_list.setMaximumHeight(row_h * 4 + 12)
        except Exception:
            pass
        
        # Mark as loading and show "Fetching data..."
        self._recent_fetching = True
        self._recent_data_loaded = True
        dates, current_path, expand_single = self._recent_pending_params
        
        # Clear and show fetching message
        self.recent_list.clear()
        fetch_item = QListWidgetItem("Fetching data...")
        fetch_item.setForeground(QColor("#666666"))
        self.recent_list.addItem(fetch_item)
        
        # Use QTimer to allow UI to update before blocking call
        QTimer.singleShot(0, lambda: self._do_fetch_recent(dates, current_path, expand_single))
    
    def _do_fetch_recent(self, dates: list[QDate], current_path: Optional[str], expand_single: bool) -> None:
        """Actually fetch the recent data (called via timer to avoid blocking UI)."""
        self.recent_list.clear()
        
        if not self.vault_root or not dates:
            self._recent_fetching = False
            return
        
        if expand_single and len(dates) == 1:
            d = dates[0]
            span = [d.addDays(-1), d, d.addDays(1)]
            dates = span
        
        # Derive min/max ISO date strings
        try:
            start = min(dates, key=lambda d: d.toJulianDay())
            end = max(dates, key=lambda d: d.toJulianDay())
            start_str = start.toString("yyyy-MM-dd")
            end_str = end.toString("yyyy-MM-dd")
        except Exception:
            self._recent_fetching = False
            return
        
        try:
            resp = self.http.post(f"{self.api_base}/api/files/modified", json={"start_date": start_str, "end_date": end_str})
            resp.raise_for_status()
            data = resp.json()
            items = data.get("items", [])
        except Exception:
            self._recent_fetching = False
            return
        
        for entry in items:
            rel = entry.get("path", "")
            if not rel or (current_path and rel == current_path):
                continue
            if not self.recent_journal_checkbox.isChecked() and rel.startswith("/Journal/"):
                continue
            label = Path(rel).stem
            item = QListWidgetItem(label)
            item.setData(PATH_ROLE, rel)
            try:
                item.setToolTip(rel)
            except Exception:
                pass
            self.recent_list.addItem(item)
        
        self._recent_fetching = False
    
    def _open_recent_link(self, item: QListWidgetItem) -> None:
        if not item:
            return
        path = item.data(PATH_ROLE)
        if path:
            self.pageActivated.emit(str(path))

    def _slugify(self, text: str) -> str:
        """Create a simple slug for headings to be used as anchor targets."""
        if not text:
            return ""
        s = text.strip().lower()
        s = re.sub(r"[^a-z0-9\s-]", "", s)
        s = re.sub(r"\s+", "-", s)
        s = re.sub(r"-+", "-", s)
        return s.strip("-")

    def _clear_filter(self) -> None:
        """Clear the multi-day filter and select the calendar's current date."""
        try:
            cur = self.calendar.selectedDate()
            self._set_single_selection(cur)
            self.filter_btn.setVisible(False)
            self._apply_multi_selection_formats()
            self._update_insights_for_selection()
        except Exception:
            pass

    def _ensure_day_page_exists(self, date: QDate) -> Optional[str]:
        """Ensure the journal day page file exists; create it if necessary and return rel path."""
        if not self.vault_root or not date or not date.isValid():
            return None
        year = f"{date.year():04d}"
        month = f"{date.month():02d}"
        day = f"{date.day():02d}"
        day_dir = Path(self.vault_root) / "Journal" / year / month / day
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
        except Exception:
                self._add_insight_item(f"{date_label} (day)", "/" + day_page.relative_to(self.vault_root).as_posix())
                # Add headings from the day's main page into the headings column
                try:
                    text = day_page.read_text(encoding="utf-8", errors="ignore")
                    day_headings = self._parse_headings_from_text(text)
                    main_path = f"/Journal/{date.year():04d}/{date.month():02d}/{date.day():02d}/{date.day():02d}{PAGE_SUFFIX}"
                    for dh in day_headings:
                        slug = self._slugify(dh)
                        label = f"{date_label}: {dh}"
                        hi = QListWidgetItem(label)
                        hi.setData(PATH_ROLE, f"{main_path}#{slug}")
                        try:
                            hi.setToolTip(label)
                        except Exception:
                            pass
                        self.headings_list.addItem(hi)
                except Exception:
                    pass
        day_file = day_dir / f"{day}{PAGE_SUFFIX}"
        if not day_file.exists():
            try:
                # Try to create from the repository template `templates/JournalDay.txt` if available
                content = None
                try:
                    template_path = Path(__file__).resolve().parents[2] / "templates" / "JournalDay.txt"
                    if template_path.exists():
                        tmpl = template_path.read_text(encoding="utf-8", errors="ignore")
                        # Use QDate formatting for localized names
                        dow = date.toString("dddd")
                        month_name = date.toString("MMMM")
                        dd = date.toString("dd")
                        yyyy = date.toString("yyyy")
                        content = (
                            tmpl.replace("{{DOW}}", dow)
                            .replace("{{Month}}", month_name)
                            .replace("{{dd}}", dd)
                            .replace("{{YYYY}}", yyyy)
                        )
                except Exception:
                    content = None

                # Fallback: a simple ISO date heading
                if content is None:
                    content = f"# {date.toString('yyyy-MM-dd')}\n\n"

                day_file.write_text(content, encoding="utf-8")
            except Exception:
                return None
        try:
            return "/" + day_file.relative_to(self.vault_root).as_posix()
        except Exception:
            return None

    def _parse_headings_from_text(self, text: str) -> list[str]:
        """Return a list of headings (text only) in order from markdown text."""
        if not text:
            return []
        out: list[str] = []
        for line in text.splitlines():
            m = re.match(r"^(#{1,6})\s+(.*)", line)
            if m:
                h = m.group(2).strip()
                if h:
                    out.append(h)
        return out

    def _open_task_item(self, item) -> None:
        """Open a due task's page at its line."""
        if self._suppress_task_activation:
            self._suppress_task_activation = False
            return
        if not item:
            return
        path = item.data(0, PATH_ROLE) if hasattr(item, "data") else None
        line = item.data(0, LINE_ROLE) if hasattr(item, "data") else None
        if not path:
            return
        try:
            line_num = int(line or 1)
        except (TypeError, ValueError):
            line_num = 1
        norm = str(path)
        if not norm.startswith("/"):
            norm = "/" + norm.lstrip("/")
        self.taskActivated.emit(norm, max(1, line_num))

    def _on_task_item_clicked(self, item: QTreeWidgetItem, col: int) -> None:
        if not item or not item.data(0, PATH_ROLE):
            return
        due_idx = 2
        start_idx = 3 if self._show_task_start_column else None
        if col == due_idx or (start_idx is not None and col == start_idx):
            self._suppress_task_activation = True
            self._open_task_date_picker_for_column(item, col)

    def _open_task_date_picker_for_column(self, item: QTreeWidgetItem, col: int) -> None:
        if item and not item.isSelected():
            self.tasks_due_list.clearSelection()
            item.setSelected(True)
        targets = self._collect_task_date_targets()
        if not targets:
            return
        path = item.data(0, PATH_ROLE)
        line = item.data(0, LINE_ROLE)
        task = item.data(0, Qt.UserRole) or {}
        if not path or not line:
            return
        try:
            line_num = int(line)
        except (TypeError, ValueError):
            line_num = 1
        role = "start" if (self._show_task_start_column and col == 3) else "due"
        anchor = QCursor.pos()
        self._open_task_date_quick_menu(role, targets, anchor)

    def _task_date_anchor_for_item(self, item: QTreeWidgetItem, col: int) -> QPoint:
        rect = self.tasks_due_list.visualItemRect(item)
        header = self.tasks_due_list.header()
        try:
            col_x = header.sectionViewportPosition(col)
        except Exception:
            col_x = rect.left()
        anchor = QPoint(col_x + rect.left(), rect.bottom() + 2)
        return self.tasks_due_list.viewport().mapToGlobal(anchor)

    def _open_task_date_context_menu(self, pos) -> None:
        col = self.tasks_due_list.columnAt(pos.x())
        if col < 0:
            return
        due_idx = 2
        start_idx = 3 if self._show_task_start_column else None
        item = self.tasks_due_list.itemAt(pos)
        if not item:
            return
        if not item.isSelected():
            self.tasks_due_list.clearSelection()
            item.setSelected(True)
        if col == due_idx or (start_idx is not None and col == start_idx):
            targets = self._collect_task_date_targets()
            if not targets:
                return
            role = "start" if (self._show_task_start_column and col == 3) else "due"
            anchor = self.tasks_due_list.viewport().mapToGlobal(pos)
            self._open_task_date_quick_menu(role, targets, anchor)
            return
        task = item.data(0, Qt.UserRole) or {}
        if not task:
            return
        targets = self._collect_task_date_targets()
        if not targets:
            return
        any_done = any((t.get("task") or {}).get("status") == "done" for t in targets)
        any_open = any((t.get("task") or {}).get("status") != "done" for t in targets)
        menu = QMenu(self)
        if any_open:
            menu.addAction("Mark Complete").triggered.connect(
                lambda: self._set_tasks_completed(targets, True)
            )
        if any_done:
            menu.addAction("Reopen Task").triggered.connect(
                lambda: self._set_tasks_completed(targets, False)
            )
        if menu.actions():
            menu.exec(self.tasks_due_list.viewport().mapToGlobal(pos))

    def _restore_expanded_paths(self, root: QTreeWidgetItem, expanded_paths: set[str]) -> None:
        def _walk(item: QTreeWidgetItem) -> None:
            path = item.data(0, PATH_ROLE)
            if path in expanded_paths:
                item.setExpanded(True)
            for i in range(item.childCount()):
                child = item.child(i)
                if child:
                    _walk(child)

        _walk(root)

    def _restore_selection(self, path: str) -> None:
        item = self._find_item_by_path(path)
        if item:
            self.journal_tree.setCurrentItem(item)
            self.journal_tree.scrollToItem(item)

    def _resolve_page_relpath(self, rel_path: str) -> Optional[str]:
        """Return a file relpath for deletion if it exists."""
        if not self.vault_root or not rel_path:
            return None
        path_obj = Path(self.vault_root) / rel_path.lstrip("/")
        if path_obj.is_file():
            return rel_path
        if path_obj.is_dir():
            candidate = path_obj / f"{path_obj.name}{PAGE_SUFFIX}"
            if candidate.exists() and candidate.is_file():
                return "/" + candidate.relative_to(self.vault_root).as_posix()
        return None

    def _delete_page(self, rel_path: str) -> None:
        """Delete a journal page after confirmation."""
        if not self.vault_root or not rel_path:
            return
        abs_path = Path(self.vault_root) / rel_path.lstrip("/")
        if not abs_path.exists() or not abs_path.is_file():
            return
        
        # Suppress focus border updates during deletion to prevent crashes
        main_window = self.window()
        if hasattr(main_window, '_suppress_focus_borders'):
            main_window._suppress_focus_borders = True
        
        try:
            confirm = QMessageBox.question(
                self,
                "Delete Page",
                f"Delete page:\n{path_to_colon(rel_path)}?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if confirm != QMessageBox.Yes:
                return
            
            # Emit signal BEFORE deletion so main window can unload editor
            # Use QTimer to defer and avoid focus change issues during signal processing
            from PySide6.QtCore import QTimer
            QTimer.singleShot(0, lambda: self.pageAboutToBeDeleted.emit(rel_path))
            
            # Give the signal handler time to process
            from PySide6.QtWidgets import QApplication
            QApplication.processEvents()
            
            try:
                abs_path.unlink()
            except Exception:
                QMessageBox.warning(self, "Delete Page", "Failed to delete the page.")
                return
            
            # Emit signal to notify that page was deleted
            self.pageDeleted.emit(rel_path)
            
            # Clean up empty parent folders up to Journal
            try:
                parent = abs_path.parent
                journal_root = Path(self.vault_root) / "Journal"
                while parent != journal_root and parent.is_dir():
                    if any(parent.iterdir()):
                        break
                    parent.rmdir()
                    parent = parent.parent
            except Exception:
                pass
            
            self.refresh()
        finally:
            # Restore focus border updates
            if hasattr(main_window, '_suppress_focus_borders'):
                main_window._suppress_focus_borders = False

    def _open_context_menu(self, pos) -> None:
        item = self.journal_tree.itemAt(pos)
        menu = QMenu(self)
        if item:
            path_value = item.data(0, PATH_ROLE)
            if path_value:
                rel_path = str(path_value)
                if not rel_path.startswith("/"):
                    rel_path = "/" + rel_path
                file_rel = self._resolve_page_relpath(rel_path)
                open_win = menu.addAction("Open in Editor Window")
                open_win.triggered.connect(lambda: self.openInWindowRequested.emit(rel_path))
                if file_rel:
                    delete_action = menu.addAction("Delete Page")
                    delete_action.triggered.connect(lambda: self._delete_page(file_rel))
                menu.addSeparator()
        refresh = menu.addAction("Refresh")
        refresh.triggered.connect(self.refresh)
        global_pos = self.journal_tree.viewport().mapToGlobal(pos)
        menu.exec(global_pos)

    def _divider(self) -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFrameShadow(QFrame.Sunken)
        return line

    def _find_item_by_path(self, path: str) -> Optional[QTreeWidgetItem]:
        def _walk(item: QTreeWidgetItem) -> Optional[QTreeWidgetItem]:
            if item.data(0, PATH_ROLE) == path:
                return item
            for i in range(item.childCount()):
                child = item.child(i)
                if not child:
                    continue
                found = _walk(child)
                if found:
                    return found
            return None

        root = self.journal_tree.invisibleRootItem()
        for i in range(root.childCount()):
            child = root.child(i)
            if child:
                found = _walk(child)
                if found:
                    return found
        return None

    def _add_children(self, parent_item: QTreeWidgetItem, path: Path, inherited_date: Optional[QDate] = None) -> None:
        """Recursively add directories and files under the Journal root."""
        try:
            entries = sorted(path.iterdir(), key=lambda p: p.name)
        except OSError:
            return

        for entry in entries:
            if entry.is_dir():
                child_date = inherited_date
                parts = entry.parts[-3:]
                if len(parts) == 3 and all(part.isdigit() for part in parts):
                    try:
                        year, month, day = map(int, parts)
                        child_date = QDate(year, month, day)
                    except ValueError:
                        pass

                item = QTreeWidgetItem([entry.name])
                item.setData(0, Qt.UserRole, child_date)
                item.setData(0, PATH_ROLE, entry.relative_to(self.vault_root).as_posix() if self.vault_root else entry.name)
                parent_item.addChild(item)
                self._add_children(item, entry, child_date)
            # Mirror left nav: only directories, no individual .txt nodes
