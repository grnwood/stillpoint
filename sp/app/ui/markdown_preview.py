"""Token-free, in-process reading view for Markdown editors."""
from __future__ import annotations

import json
import os
import re
import sys
import weakref
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
    QEvent,
    QPoint,
    QRect,
    QSize,
    QPropertyAnimation,
    QTimer,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QIcon,
    QFont,
    QFontMetricsF,
    QFontDatabase,
    QImageReader,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPixmap,
    QPalette,
    QResizeEvent,
    QShortcut,
    QTextCursor,
    QTextBlockFormat,
    QTextFormat,
    QTextDocument,
    QTextTable,
    QTextCharFormat,
)
from PySide6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QApplication,
    QHBoxLayout,
    QLabel,
    QMenu,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from sp.app.ui.keyboard_shortcuts import is_vi_navigation_chord
from sp.app.ui.theme import theme_value
from sp.app.ui.heading_utils import HEADING_SIZE_MULTIPLIERS
from sp.app.ui.markdown_clipboard import clipboard_from_html, clipboard_from_markdown
from sp.app.ui.reading_options import ReadingOptionsPopup, reading_source_blocks
from sp.app.ui.toc_widget import TableOfContentsWidget
from sp.app.ui.screen_positioning import popup_available_geometry, clamp_popup_top_left
from sp.markdown_html import decode_vault_link_href, nearest_source_anchor, render_markdown_html


def _reading_table_border(paper: str, header: str, border: str) -> str:
    """Keep dark table grids distinct from both body and header backgrounds."""
    backgrounds = [QColor(paper), QColor(header)]
    original = QColor(border)
    if not all(color.isValid() for color in [*backgrounds, original]):
        return border
    if backgrounds[0].lightnessF() >= 0.5:
        return border

    def luminance(color: QColor) -> float:
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [
            value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            for value in channels
        ]
        return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))

    for step in range(21):
        amount = step / 20
        candidate = QColor.fromRgbF(*(
            value + (1 - value) * amount
            for value in (original.redF(), original.greenF(), original.blueF())
        ))
        foreground = luminance(candidate)
        if all(
            (max(foreground, luminance(bg)) + 0.05)
            / (min(foreground, luminance(bg)) + 0.05) >= 3
            for bg in backgrounds
        ):
            return candidate.name()
    return candidate.name()


READING_CURSOR_GUTTER = 24


class _ReadingBrowser(QTextBrowser):
    """Selectable HTML reader with optional vi-style scrolling."""

    searchRequested = Signal()
    headingPickerRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._clipboard_view = parent
        self.vi_enabled = False
        self.vi_cursor_style = "line"
        self._pending_g = False
        self._cursor_sync_pending = False
        self._preserve_cursor_during_animation = False
        self._reading_cursor = QTextCursor()
        self._navigation_cursor_visible = False
        self._source_positions: list[tuple[int, int]] = []
        self._requested_source_line = 1
        self._scroll_animation = QPropertyAnimation(
            self.verticalScrollBar(), b"value", self
        )
        self._scroll_animation.setDuration(120)
        self._scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_animation.finished.connect(self._finish_scroll_animation)
        self.verticalScrollBar().valueChanged.connect(self._schedule_cursor_sync)

    def _selection_mime(self, mode="formatted"):
        cursor = self.textCursor()
        owner = self._clipboard_view
        source = owner.clipboard_source_selection(cursor) if owner is not None else None
        if source is not None:
            return clipboard_from_markdown(source, mode=mode)
        html = cursor.selection().toHtml()
        if self.document().findBlock(cursor.selectionStart()).blockFormat().nonBreakableLines():
            # Qt exports the first partial preformatted line as a paragraph.
            # Restore its code context before stripping presentation styles.
            html = re.sub(r"<p(\s[^>]*)?>(.*?)</p>", r"<pre\1>\2</pre>", html, count=1, flags=re.DOTALL)
        return clipboard_from_html(html, mode=mode)

    def createMimeDataFromSelection(self):  # type: ignore[override]
        return self._selection_mime()

    def copy(self):
        if self.textCursor().hasSelection():
            QApplication.clipboard().setMimeData(self._selection_mime())

    def _copy_with_mode(self, mode):
        if self.textCursor().hasSelection():
            QApplication.clipboard().setMimeData(self._selection_mime(mode))

    def createStandardContextMenu(self, *args, **kwargs):  # type: ignore[override]
        menu = super().createStandardContextMenu(*args, **kwargs)
        for label, mode in (("Copy as Plain Text", "plain"), ("Copy as Markdown", "markdown")):
            action = menu.addAction(label, lambda checked=False, mode=mode: self._copy_with_mode(mode))
            action.setEnabled(self.textCursor().hasSelection())
        return menu

    def contextMenuEvent(self, event):  # type: ignore[override]
        menu = self.createStandardContextMenu(event.pos())
        menu.exec(event.globalPos())
        menu.deleteLater()

    def _finish_scroll_animation(self) -> None:
        self._preserve_cursor_during_animation = False
        self._schedule_cursor_sync()

    def _schedule_cursor_sync(self, *_args) -> None:
        if self._cursor_sync_pending:
            return
        self._cursor_sync_pending = True
        QTimer.singleShot(0, self, self._run_scheduled_cursor_sync)

    def _run_scheduled_cursor_sync(self) -> None:
        self._cursor_sync_pending = False
        self._sync_cursor_to_viewport()

    def set_vi_state(self, enabled: bool, cursor_style: str = "line") -> None:
        self.vi_enabled = bool(enabled)
        self._navigation_cursor_visible = self.vi_enabled
        self.setCursorWidth(0 if self.vi_enabled else 1)
        normalized = str(cursor_style or "").strip().lower()
        self.vi_cursor_style = normalized if normalized in {"block", "line"} else "line"
        self._sync_cursor_to_viewport()

    def refresh_reading_cursor(self) -> None:
        self.setExtraSelections([])
        if (
            not (self.vi_enabled or self._navigation_cursor_visible)
            or self.document().isEmpty()
        ):
            self.viewport().update()
            return
        cursor = QTextCursor(self._reading_cursor)
        if cursor.isNull():
            cursor = self.content_cursor_at_y(self.viewport().height() // 2)
        cursor = self._nearest_content_cursor(cursor)
        cursor.clearSelection()
        self._reading_cursor = QTextCursor(cursor)
        self.viewport().update()

    def vi_rail_rect(self) -> QRect:
        """Return the compact viewport marker used for the reading position."""
        if (
            not (self.vi_enabled or self._navigation_cursor_visible)
            or self._reading_cursor.isNull()
        ):
            return QRect()
        cursor_rect = self.cursorRect(self._reading_cursor)
        line_height = min(cursor_rect.height(), self.fontMetrics().height() + 4)
        height = max(8, min(20, round(line_height * 0.7)))
        return QRect(8, cursor_rect.top() + (line_height - height) // 2, 4, height)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        marker = self.vi_rail_rect()
        if marker.isEmpty() or not marker.intersects(self.viewport().rect()):
            return
        color = QColor(
            getattr(
                self,
                "_folder_navigator_vi_cursor_color",
                theme_value(
                    "markdown_preview.cursor.rail_bg",
                    theme_value("markdown_editor.vi_block_cursor.bg", "#b259ff"),
                ),
            )
        )
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(Qt.NoPen)
        glow = QColor(color)
        glow.setAlpha(35)
        painter.setBrush(glow)
        painter.drawRoundedRect(QRect(marker).adjusted(-3, -3, 3, 3), 5, 5)
        color.setAlpha(220)
        painter.setBrush(color)
        painter.drawRoundedRect(marker, 2, 2)
        painter.end()

    @staticmethod
    def _nearest_content_cursor(
        cursor: QTextCursor, *, prefer_forward: bool = True
    ) -> QTextCursor:
        """Skip the empty blocks emitted for source-position HTML anchors."""
        origin = cursor.block()
        if origin.isValid() and origin.text().replace("\u200b", "").strip():
            return cursor
        directions = (
            (origin.next, lambda block: block.next()),
            (origin.previous, lambda block: block.previous()),
        )
        if not prefer_forward:
            directions = tuple(reversed(directions))
        for first, advance in directions:
            block = first()
            while block.isValid():
                if block.text().replace("\u200b", "").strip():
                    return QTextCursor(block)
                block = advance(block)
        return cursor

    def content_cursor_at_y(self, y: int) -> QTextCursor:
        """Hit an actual table cell rather than the empty space beside it."""
        document = self.document()
        x = round(document.documentMargin()) + 8
        document_y = y + self.verticalScrollBar().value()
        for frame in document.rootFrame().childFrames():
            if isinstance(frame, QTextTable):
                rect = document.documentLayout().frameBoundingRect(frame)
                if rect.top() <= document_y <= rect.bottom():
                    x = round(rect.left() + frame.format().cellPadding() + 4) - self.horizontalScrollBar().value()
                    break
        x = max(1, min(self.viewport().width() - 1, x))
        return self.cursorForPosition(QPoint(x, y))

    def _sync_cursor_to_viewport(self) -> None:
        if self.document().isEmpty():
            self.refresh_reading_cursor()
            return
        if self._preserve_cursor_during_animation:
            self.refresh_reading_cursor()
            return
        viewport = self.viewport().rect()
        if not self._reading_cursor.isNull():
            cursor_rect = self.cursorRect(self._reading_cursor)
            if (
                cursor_rect.bottom() >= viewport.top()
                and cursor_rect.top() <= viewport.bottom()
            ):
                self.refresh_reading_cursor()
                return
            y = (
                4
                if cursor_rect.bottom() < viewport.top()
                else max(4, viewport.height() - 5)
            )
        else:
            y = 4
        self._reading_cursor = self._nearest_content_cursor(
            self.content_cursor_at_y(y)
        )
        self.refresh_reading_cursor()

    def _scroll_cursor_into_view(self, cursor: QTextCursor) -> None:
        """Reveal ``cursor`` with the smallest smooth viewport adjustment."""
        cursor_rect = self.cursorRect(cursor)
        viewport_height = self.viewport().height()
        margin = max(6, self.fontMetrics().height() // 2)
        scrollbar = self.verticalScrollBar()
        target = scrollbar.value()
        available_height = max(1, viewport_height - (2 * margin))
        if cursor_rect.height() > available_height:
            target += cursor_rect.top() - margin
        elif cursor_rect.top() < margin:
            target += cursor_rect.top() - margin
        elif cursor_rect.bottom() > viewport_height - margin:
            target += cursor_rect.bottom() - (viewport_height - margin)
        target = max(scrollbar.minimum(), min(scrollbar.maximum(), target))
        if target == scrollbar.value():
            self.refresh_reading_cursor()
            return
        distance = abs(target - scrollbar.value())
        self._scroll_animation.stop()
        self._preserve_cursor_during_animation = True
        self._scroll_animation.setDuration(max(80, min(220, round(distance * 0.4))))
        self._scroll_animation.setStartValue(scrollbar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def page_scroll(self, direction: int, *, fraction: float = 0.85) -> None:
        scrollbar = self.verticalScrollBar()
        step = max(1, round(scrollbar.pageStep() * fraction))
        target = max(
            scrollbar.minimum(),
            min(scrollbar.maximum(), scrollbar.value() + (1 if direction > 0 else -1) * step),
        )
        self._scroll_animation.stop()
        self._preserve_cursor_during_animation = False
        self._scroll_animation.setDuration(120)
        self._scroll_animation.setStartValue(scrollbar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.start()

    def line_scroll(self, direction: int) -> None:
        self._navigation_cursor_visible = True
        if self._reading_cursor.isNull():
            self._sync_cursor_to_viewport()
        cursor = QTextCursor(self._reading_cursor)
        operation = QTextCursor.Down if direction > 0 else QTextCursor.Up
        if not cursor.movePosition(operation):
            cursor.movePosition(QTextCursor.End if direction > 0 else QTextCursor.Start)
        cursor = self._nearest_content_cursor(
            cursor, prefer_forward=direction > 0
        )
        cursor.clearSelection()
        self._reading_cursor = cursor
        self._scroll_cursor_into_view(cursor)

    def jump_to_edge(self, *, end: bool) -> None:
        self._scroll_animation.stop()
        self._preserve_cursor_during_animation = False
        scrollbar = self.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum() if end else scrollbar.minimum())
        cursor = QTextCursor(self.document())
        cursor.movePosition(QTextCursor.End if end else QTextCursor.Start)
        self._reading_cursor = self._nearest_content_cursor(cursor)
        self.refresh_reading_cursor()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        super().mousePressEvent(event)
        if self.vi_enabled:
            self._reading_cursor = self._nearest_content_cursor(
                self.cursorForPosition(event.position().toPoint())
            )
            self.refresh_reading_cursor()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        modifiers = event.modifiers() & ~Qt.KeypadModifier
        if modifiers == Qt.NoModifier and event.key() == Qt.Key_T:
            self.headingPickerRequested.emit()
            event.accept()
            return
        if (
            self.vi_enabled
            and is_vi_navigation_chord(modifiers)
            and event.key() in (Qt.Key_J, Qt.Key_K)
        ):
            self.page_scroll(1 if event.key() == Qt.Key_J else -1)
            self._pending_g = False
            event.accept()
            return
        if modifiers == Qt.NoModifier and event.key() in (Qt.Key_Down, Qt.Key_Up):
            self.line_scroll(1 if event.key() == Qt.Key_Down else -1)
            self._pending_g = False
            event.accept()
            return
        if (
            self.vi_enabled
            and modifiers == Qt.ShiftModifier
            and event.key() == Qt.Key_G
        ):
            self.jump_to_edge(end=True)
            self._pending_g = False
            event.accept()
            return
        if self.vi_enabled and modifiers == Qt.NoModifier:
            scrollbar = self.verticalScrollBar()
            key = event.key()
            if key == Qt.Key_Slash:
                self.searchRequested.emit()
                self._pending_g = False
                event.accept()
                return
            if key in (Qt.Key_J, Qt.Key_K):
                direction = 1 if key == Qt.Key_J else -1
                self.line_scroll(direction)
                self._pending_g = False
                event.accept()
                return
            if key == Qt.Key_G:
                if self._pending_g:
                    self.jump_to_edge(end=False)
                    self._pending_g = False
                else:
                    self._pending_g = True
                event.accept()
                return
            self._pending_g = False
        if self.vi_enabled and modifiers == Qt.ControlModifier and event.key() in (Qt.Key_D, Qt.Key_U):
            direction = 1 if event.key() == Qt.Key_D else -1
            self.page_scroll(direction, fraction=0.5)
            event.accept()
            return
        if modifiers == Qt.NoModifier and event.key() in (Qt.Key_PageDown, Qt.Key_PageUp):
            self.page_scroll(1 if event.key() == Qt.Key_PageDown else -1)
            event.accept()
            return
        self._pending_g = False
        super().keyPressEvent(event)


class MarkdownReadingView(QWidget):
    """A shared read-only Markdown surface used by both desktop editors."""

    ZOOM_DEBOUNCE_MS = 100
    _instances: weakref.WeakSet = weakref.WeakSet()

    editRequested = Signal()
    linkActivated = Signal(str)

    def clipboard_source_selection(self, cursor):
        """Use exact source for complete blocks; retain partial selections as-is."""
        document = self.browser.document()
        start, end = cursor.selectionStart(), cursor.selectionEnd()
        if start == 0 and end == document.characterCount() - 1:
            return self.last_markdown
        if not cursor.hasSelection() or not self._source_positions:
            return None
        first = document.findBlock(start)
        last = document.findBlock(max(start, end - 1))
        # Cursor positions are UTF-16 offsets, so use QTextCursor to inspect
        # the prefix rather than indexing Python text by Qt positions.
        prefix = QTextCursor(first)
        prefix.setPosition(start, QTextCursor.KeepAnchor)
        if prefix.selectedText().replace("\u200b", "").strip():
            return None
        if end < last.position() + last.length() - 1:
            return None
        first_cursor, last_cursor = QTextCursor(first), QTextCursor(last)
        for candidate in (first_cursor, last_cursor):
            table = candidate.currentTable()
            if table is not None and (start > table.firstCursorPosition().position() + 1
                                      or end < table.lastCursorPosition().position()):
                return None
        preceding = [(position, line) for position, line in self._source_positions if position <= start]
        if not preceding:
            return None
        position = max(position for position, _line in preceding)
        source_start = min(line for candidate, line in preceding if candidate == position)
        following = [(position, line) for position, line in self._source_positions if position >= end]
        source_end = min(following)[1] - 1 if following else len(self.last_markdown.splitlines())
        for source in self._source_blocks:
            if source.kind == "code" and source.line <= source_start <= source.end_line:
                block_position = self._source_line_positions.get(source.line)
                if block_position != first.position() or source_end < source.end_line:
                    return None
                source_start = source.line
            if source.kind == "code" and source.line <= source_end < source.end_line:
                return None
        return "\n".join(self.last_markdown.splitlines()[source_start - 1:source_end])
    backgroundChanged = Signal(str)

    def __init__(self, parent=None, *, preference_scope=None, enable_table_preview=True):
        super().__init__(parent)
        self.preference_scope = preference_scope
        self.last_markdown = ""
        self.last_html = ""
        self.base_directory: Path | None = None
        self._reading_background = config.load_reading_view_background()
        self._use_theme_colors = self._reading_background == "theme"
        self._host_theme_palette: QPalette | None = None
        self.reading_options = config.load_reading_options()
        if preference_scope in {"focus", "audience"}:
            preferences = config.load_mode_reading_preferences(preference_scope)
            self._reading_background = preferences["background"]
            self._use_theme_colors = self._reading_background == "theme"
            self.reading_options = preferences["options"]
        self._source_blocks = []
        self._copy_buttons = []
        self._positioning_reader_tools = False
        self._rendering_markdown = False
        self._pending_render_request = None
        self._render_request_timer = QTimer(self)
        self._render_request_timer.setSingleShot(True)
        self._render_request_timer.setInterval(0)
        self._render_request_timer.timeout.connect(self._render_pending_request)
        self._reader_tools_timer = QTimer(self)
        self._reader_tools_timer.setSingleShot(True)
        self._reader_tools_timer.setInterval(0)
        self._reader_tools_timer.timeout.connect(self._position_reader_tools)
        self._focus_targets = None
        self._focus_restore = []
        self._focus_splitters = []
        self._source_line_positions = {}
        self._source_positions = []
        self._appearance_changed = False
        self._scroll_request_id = 0
        self._pending_source_position = None
        self._base_font_point_size = 12.0
        self._editor_font_family = "sans-serif"
        self._code_font_family = self._fixed_width_font_family()
        self._editor_document_margin = 18.0
        self._editor_line_spacing: float | None = None
        self._heading_weight = 700
        self._editor_syntax_colors = {}
        self._handoff_scroll_value = None
        self._handoff_source_line = None
        self._zoom_steps = 0
        self._zoom_css_dirty = False
        self._zoom_render_timer = QTimer(self)
        self._zoom_render_timer.setSingleShot(True)
        self._zoom_render_timer.setInterval(self.ZOOM_DEBOUNCE_MS)
        self._zoom_render_timer.timeout.connect(self._apply_pending_zoom)
        self._editor_zoom_delta = 0
        self._editor_zoom_callback = None
        self._editor_zoom_timer = QTimer(self)
        self._editor_zoom_timer.setSingleShot(True)
        self._editor_zoom_timer.setInterval(60)
        self._editor_zoom_timer.timeout.connect(self.flush_editor_zoom)
        self._image_layout: dict[int, tuple[int, float, float, float]] = {}
        self._image_resize_timer = QTimer(self)
        self._image_resize_timer.setSingleShot(True)
        self._image_resize_timer.setInterval(self.ZOOM_DEBOUNCE_MS)
        self._image_resize_timer.timeout.connect(self._fit_images_to_viewport)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(self)
        self.header = header
        header.setObjectName("markdownReadingHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(10, 4, 8, 4)
        title = QLabel("Reading View", header)
        title.setAccessibleName("Reading View")
        header_layout.addWidget(title)
        self.theme_toggle = QToolButton(header)
        self.theme_toggle.setObjectName("readingThemeToggle")
        self.theme_toggle.setCheckable(True)
        self.theme_toggle.setChecked(self._use_theme_colors)
        background_menu = QMenu(self.theme_toggle)
        self._background_actions = {}
        for name, color in (("theme", "#303840"), ("white", "#ffffff"),
                            ("paper", "#f5f0e6"), ("mist", "#e5e7eb"),
                            ("dusk", "#252a30")):
            swatch = QPixmap(18, 18)
            swatch.fill(QColor(color))
            action = background_menu.addAction(QIcon(swatch), name.title())
            action.setCheckable(True)
            action.setChecked(name == self._reading_background)
            action.triggered.connect(lambda checked=False, value=name: self._background_selected(value))
            self._background_actions[name] = action
        self.theme_toggle.setMenu(background_menu)
        self.theme_toggle.setPopupMode(QToolButton.MenuButtonPopup)
        self.theme_toggle.setMinimumSize(40, 24)
        self.theme_toggle.setIconSize(QSize(18, 18))
        self.theme_toggle.setAccessibleName("Reading background")
        self._update_theme_toggle_tooltip()
        header_layout.addWidget(self.theme_toggle)
        self.options_button = self._reader_button(header, "reading-options.svg", "Reading options")
        self.options_button.clicked.connect(self.show_reading_options)
        header_layout.addWidget(self.options_button)
        self.focus_button = self._reader_button(header, "focus-mode.svg", "Enter reading focus mode")
        self.focus_button.setCheckable(True)
        self.focus_button.toggled.connect(self.set_focus_mode)
        self.focus_button.hide()
        header_layout.addWidget(self.focus_button)
        header_layout.addStretch()
        self.find_button = QToolButton(header)
        self.find_button.setText("Find")
        self.find_button.setToolTip("Find in Reading View")
        self.find_button.clicked.connect(self.show_find)
        header_layout.addWidget(self.find_button)
        self.edit_button = QPushButton("Edit", header)
        self.edit_button.setToolTip("Return to the Markdown editor")
        self.edit_button.clicked.connect(self.editRequested)
        header_layout.addWidget(self.edit_button)
        layout.addWidget(header)

        self.find_bar = QFrame(self)
        find_layout = QHBoxLayout(self.find_bar)
        find_layout.setContentsMargins(8, 4, 8, 4)
        self.find_query = QLineEdit(self.find_bar)
        self.find_query.setPlaceholderText("Find in preview")
        self.find_query.returnPressed.connect(self.find_next)
        find_layout.addWidget(self.find_query, 1)
        previous = QToolButton(self.find_bar)
        previous.setText("Previous")
        previous.clicked.connect(self.find_previous)
        find_layout.addWidget(previous)
        following = QToolButton(self.find_bar)
        following.setText("Next")
        following.clicked.connect(self.find_next)
        find_layout.addWidget(following)
        close = QToolButton(self.find_bar)
        close.setText("Close")
        close.clicked.connect(self.hide_find)
        find_layout.addWidget(close)
        self.find_bar.hide()

        self.browser = _ReadingBrowser(self)
        self.browser.setObjectName("markdownReadingBrowser")
        self.browser.setAccessibleName("Rendered Markdown reading view")
        self.browser.setOpenLinks(False)
        self.browser.setOpenExternalLinks(False)
        self.browser.setTextInteractionFlags(
            Qt.TextSelectableByMouse
            | Qt.TextSelectableByKeyboard
            | Qt.LinksAccessibleByMouse
            | Qt.LinksAccessibleByKeyboard
        )
        self.browser.anchorClicked.connect(
            lambda url: self.linkActivated.emit(
                decode_vault_link_href(url.toString(QUrl.FullyEncoded))
                if url.scheme() == "stillpoint-page" else url.toString()
            )
        )
        self.browser.searchRequested.connect(self.show_find)
        self.reading_body = QWidget(self)
        self.reading_body.setObjectName("markdownReadingBody")
        self._column_layout = QHBoxLayout(self.reading_body)
        self._column_layout.setContentsMargins(0, 0, 0, 0)
        self._column_layout.setSpacing(0)
        self._column_layout.addWidget(self.browser, 1)
        layout.addWidget(self.reading_body, 1)
        layout.addWidget(self.find_bar)
        self.toc_widget = TableOfContentsWidget(self.reading_body)
        self.toc_widget.set_collapsed(config.load_toc_collapsed())
        self.toc_widget.collapsedChanged.connect(self._toc_collapsed)
        self.toc_widget.headingActivated.connect(self._toc_activated)
        self.toc_widget.hide()
        self.options_popup = ReadingOptionsPopup(self)
        self.options_popup.set_options(self.reading_options)
        self.options_popup.optionsChanged.connect(self.set_reading_options)
        self.browser.verticalScrollBar().valueChanged.connect(self._position_reader_tools)
        self.browser.horizontalScrollBar().valueChanged.connect(self._position_reader_tools)
        self.browser.document().documentLayout().documentSizeChanged.connect(self._schedule_reader_tools)
        self.browser.viewport().installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        self.setFocusProxy(self.browser)
        self.theme_toggle.clicked.connect(self._cycle_background)
        self._instances.add(self)

        find_shortcut = QShortcut(QKeySequence.Find, self)
        find_shortcut.setContext(Qt.WidgetWithChildrenShortcut)
        find_shortcut.activated.connect(self.show_find)
        self._find_shortcut = find_shortcut
        close_find = QShortcut(QKeySequence(Qt.Key_Escape), self.find_bar)
        close_find.setContext(Qt.WidgetWithChildrenShortcut)
        close_find.activated.connect(self.hide_find)
        self._close_find_shortcut = close_find
        self._page_shortcuts = []
        page_sequences = [
            ("Ctrl+Shift+J", 1),
            ("Ctrl+Shift+K", -1),
        ]
        if sys.platform == "darwin":
            page_sequences.extend((("Meta+Shift+J", 1), ("Meta+Shift+K", -1)))
        for sequence, direction in page_sequences:
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(Qt.WidgetWithChildrenShortcut)
            shortcut.activated.connect(
                lambda step=direction: self.page_scroll(step)
            )
            self._page_shortcuts.append(shortcut)
        self._apply_palette()

        if enable_table_preview:
            from .table_hover_preview import TableHoverPreview
            self._table_hover_preview = TableHoverPreview(
                self.browser, source_at_point=self._table_source_at_point
            )

    def _table_source_at_point(self, point):
        table = self.browser.cursorForPosition(point).currentTable()
        if table is None:
            return None
        rect = self.browser.document().documentLayout().frameBoundingRect(table)
        document_point = QPoint(
            point.x() + self.browser.horizontalScrollBar().value(),
            point.y() + self.browser.verticalScrollBar().value(),
        )
        if not rect.contains(document_point):
            return None
        for source in self._source_blocks:
            if source.kind != "table":
                continue
            position = self._source_line_positions.get(source.line)
            if position is not None and table.firstPosition() <= position <= table.lastPosition():
                return source.markdown, source.line - 1
        return None

    def _asset_icon(self, name: str, color: QColor | None = None) -> QIcon:
        path = Path(__file__).resolve().parents[2] / "assets" / name
        if not path.exists() and getattr(sys, "_MEIPASS", None):
            root = Path(sys._MEIPASS)
            path = next((p for p in (root / "sp" / "assets" / name, root / "assets" / name) if p.exists()), path)
        pixmap = QIcon(str(path)).pixmap(QSize(16, 16))
        if not pixmap.isNull():
            painter = QPainter(pixmap)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            palette = self._host_theme_palette if self._host_theme_palette is not None else self.palette()
            painter.fillRect(pixmap.rect(), color if color is not None else palette.color(QPalette.Text))
            painter.end()
        return QIcon(pixmap)

    @staticmethod
    def _fixed_width_font_family():
        """Resolve a real installed font; Qt's generic alias can be proportional."""
        system_family = QFontDatabase.systemFont(QFontDatabase.FixedFont).family()
        available = QFontDatabase.families()
        names = {family.casefold(): family for family in available}
        candidates = [system_family, "Menlo", "Consolas", "DejaVu Sans Mono",
                      "Liberation Mono", "Monaco", "Courier New", *available]
        for candidate in candidates:
            family = names.get(candidate.casefold())
            if family and QFontDatabase.isFixedPitch(family):
                return family
        return system_family or "monospace"

    def _update_focus_button_icon(self):
        background = self._focus_checked_background
        checked_text = QColor("#111111" if background.lightnessF() >= 0.5 else "#ffffff")
        icon = QIcon()
        for state, tinted in (
            (QIcon.Off, self._asset_icon("focus-mode.svg")),
            (QIcon.On, self._asset_icon("focus-mode.svg", checked_text)),
        ):
            pixmap = tinted.pixmap(QSize(16, 16))
            for mode in (QIcon.Normal, QIcon.Active, QIcon.Selected):
                icon.addPixmap(pixmap, mode, state)
        self.focus_button.setIcon(icon)

    def _style_copy_button(self, button, *, copied=False):
        button.setIcon(self._asset_icon("accept.svg" if copied else "copy.svg", self._text_color))
        button.setStyleSheet(
            "QToolButton { padding: 2px 4px; border-radius: 4px; "
            f"color: {self._text_color.name()}; background: {self._paper_color.name()}; "
            f"border: 1px solid {self._copy_button_border.name()}; }}"
            "QToolButton:hover, QToolButton:focus {"
            f"background: {self._copy_button_hover.name()}; border-color: {self._text_color.name()}; }}"
        )

    def _reader_button(self, parent, asset, label):
        button = QToolButton(parent)
        button.setAutoRaise(True)
        button.setFocusPolicy(Qt.NoFocus)
        button.setFixedSize(24, 22)
        button.setIconSize(QSize(16, 16))
        button.setIcon(self._asset_icon(asset))
        button.setAccessibleName(label)
        button.setToolTip(label)
        button.setStyleSheet(
            "QToolButton { border: none; padding: 2px 4px; background: transparent; }"
            "QToolButton:hover, QToolButton:checked { background: palette(alternate-base); border-radius: 3px; }"
        )
        return button

    def show_reading_options(self):
        self.options_popup.set_options(self.reading_options)
        self.options_popup.adjustSize()
        anchor = self.options_button.mapToGlobal(QPoint(0, 0))
        desired = anchor - QPoint(0, self.options_popup.height() + 6)
        available = popup_available_geometry(anchor=anchor, parent=self)
        self.options_popup.move(clamp_popup_top_left(desired, self.options_popup.size(), available))
        self.options_popup.show()
        self.options_popup.width_choice.setFocus()

    def set_reading_options(self, options):
        options = config.normalize_reading_options(options)
        if self.preference_scope:
            config.save_mode_reading_preferences(self.preference_scope, options=options)
        else:
            config.save_reading_options(options)
        for view in list(self._instances):
            # WeakSet can retain Python wrappers briefly after Qt deletes them.
            import shiboken6
            if (not shiboken6.isValid(view) or not shiboken6.isValid(view.browser)
                    or view.preference_scope != self.preference_scope):
                continue
            view._apply_reading_options(options)

    def _apply_reading_options(self, options):
        if self.reading_options == options:
            return
        source_line, screen_y = self.reading_view_position() if self.last_markdown else (1, None)
        self.reading_options = dict(options)
        self.options_popup.set_options(options)
        self._appearance_changed = True
        self._apply_palette()
        if self.last_markdown:
            self.set_markdown(self.last_markdown, base_directory=self.base_directory,
                              source_line=source_line, anchor_global_y=screen_y)

    def _apply_column_width(self):
        characters = {"narrow": 60, "comfortable": 80, "wide": 100}.get(self.reading_options["width"])
        if characters is None:
            maximum = 16777215
        else:
            font = QFont(self.browser.font())
            font.setPointSizeF(self._base_font_point_size * self.reading_options["size"] / 100 * 1.1 ** self._zoom_steps)
            maximum = round(QFontMetricsF(font).averageCharWidth() * characters + 2 * self._editor_document_margin + 24)
        if self.browser.maximumWidth() != maximum:
            self.browser.setMaximumWidth(maximum)
        if self.browser.minimumWidth() != 0:
            self.browser.setMinimumWidth(0)
        margin = max(0, (self.reading_body.width() - maximum) // 2)
        current = self._column_layout.contentsMargins()
        if (current.left(), current.top(), current.right(), current.bottom()) != (margin, 0, margin, 0):
            self._column_layout.setContentsMargins(margin, 0, margin, 0)

    def configure_focus_mode(self, targets):
        """targets returns chrome widgets to hide; the reader stays in place."""
        self._focus_targets = targets
        self.focus_button.show()

    def set_focus_mode(self, enabled):
        enabled = bool(enabled)
        if enabled == bool(self._focus_restore):
            return
        source_line, screen_y = self.reading_view_position()
        if enabled and self._focus_targets is not None:
            from PySide6.QtWidgets import QSplitter
            window = self.window()
            self._focus_splitters = [(splitter, splitter.sizes()) for splitter in window.findChildren(QSplitter)]
            for widget in self._focus_targets():
                if widget is not None:
                    self._focus_restore.append((widget, not widget.isHidden()))
                    widget.hide()
        else:
            for widget, visible in self._focus_restore:
                widget.setVisible(visible)
            self._focus_restore = []
            for splitter, sizes in self._focus_splitters:
                splitter.setSizes(sizes)
            self._focus_splitters = []
        blocked = self.focus_button.blockSignals(True)
        self.focus_button.setChecked(bool(self._focus_restore))
        self.focus_button.blockSignals(blocked)
        self.focus_button.setToolTip("Exit reading focus mode (Esc)" if self._focus_restore else "Enter reading focus mode")
        if self.last_markdown:
            self.scroll_to_source_line(source_line, anchor_global_y=screen_y)

    def eventFilter(self, obj, event):
        if obj is getattr(self, "browser", None) or obj is getattr(getattr(self, "browser", None), "viewport", lambda: None)():
            if event.type() == QEvent.Resize:
                self._schedule_reader_tools()
        if self._focus_restore and event.type() == QEvent.KeyPress and event.key() == Qt.Key_Escape:
            if self.window().isActiveWindow() and QApplication.activePopupWidget() is None and not self.find_bar.isVisible():
                self.set_focus_mode(False)
                event.accept()
                return True
        return super().eventFilter(obj, event)

    def _toc_collapsed(self, collapsed):
        config.save_toc_collapsed(collapsed)
        self._position_reader_tools()

    def _toc_activated(self, source_line):
        self.scroll_to_source_line(source_line)
        self.focus_reader()

    def _schedule_reader_tools(self, *_args):
        if not self._reader_tools_timer.isActive():
            self._reader_tools_timer.start()

    def _position_reader_tools(self, *_args):
        self._reader_tools_timer.stop()
        if self._positioning_reader_tools or self._rendering_markdown:
            return
        self._positioning_reader_tools = True
        try:
            self._layout_reader_tools()
        finally:
            self._positioning_reader_tools = False

    def _layout_reader_tools(self):
        if not hasattr(self, "toc_widget"):
            return
        self._apply_column_width()
        enough = len(self.toc_widget._headings) > 1
        self.toc_widget.setVisible(config.load_toc_widget_enabled() and enough and self.browser.verticalScrollBar().maximum() > 0)
        self.toc_widget.move(max(0, self.reading_body.width() - self.toc_widget.width() - 12), 12)
        self.toc_widget.raise_()
        viewport = self.browser.viewport()
        for button, position, kind in self._copy_buttons:
            cursor = QTextCursor(self.browser.document())
            cursor.setPosition(min(position, self.browser.document().characterCount() - 1))
            rect = self.browser.cursorRect(cursor)
            y = rect.top() - 26
            end_cursor = QTextCursor(self.browser.document())
            end_cursor.setPosition(min(button._reading_block_end, self.browser.document().characterCount() - 1))
            bottom = self.browser.cursorRect(end_cursor).bottom()
            x = viewport.width() - button.width() - 8
            if kind == "table" and cursor.currentTable():
                table = cursor.currentTable()
                layout = self.browser.document().documentLayout()
                first = table.cellAt(0, 0)
                last_column = table.cellAt(0, table.columns() - 1)
                first_format = first.format().toTableCellFormat()
                last_format = last_column.format().toTableCellFormat()
                # Qt's frame bounds include table margins. Cell bounds locate
                # the visible grid, keeping the control beside its actual edge.
                top = layout.blockBoundingRect(first.firstCursorPosition().block()).top() - first_format.topPadding()
                right = layout.blockBoundingRect(last_column.firstCursorPosition().block()).right() + last_format.rightPadding()
                last_row = [table.cellAt(table.rows() - 1, column) for column in range(table.columns())]
                table_bottom = max(layout.blockBoundingRect(cell.lastCursorPosition().block()).bottom()
                                   + cell.format().toTableCellFormat().bottomPadding() for cell in last_row)
                y = round(top) - self.browser.verticalScrollBar().value() - 26
                bottom = round(table_bottom) - self.browser.verticalScrollBar().value()
                x = min(x, round(right) - self.browser.horizontalScrollBar().value() - button.width())
            visible = bottom >= button.height() + 4 and y < viewport.height()
            y = min(max(4, y), bottom - button.height(), viewport.height() - button.height() - 4)
            candidate = QRect(viewport.mapTo(self.reading_body, QPoint(max(0, x), y)), button.size())
            if self.toc_widget.isVisible() and candidate.intersects(self.toc_widget.geometry()):
                x = min(x, viewport.mapFrom(self.reading_body, self.toc_widget.pos()).x() - button.width() - 8)
            button.move(max(0, x), y)
            button.setVisible(visible)
            button.raise_()

    def set_header_visible(self, visible: bool) -> None:
        """Show shared reader controls when the host does not provide its own."""
        self.header.setVisible(bool(visible))

    @staticmethod
    def _theme_icon_path() -> Path | None:
        candidates = [Path(__file__).resolve().parents[2] / "assets" / "dark-light.svg"]
        bundled = getattr(sys, "_MEIPASS", None)
        if bundled:
            root = Path(bundled)
            candidates.extend(
                (root / "sp" / "assets" / "dark-light.svg", root / "assets" / "dark-light.svg")
            )
        return next((path for path in candidates if path.exists()), None)

    def _update_theme_toggle_icon(self) -> None:
        path = self._theme_icon_path()
        if path is None:
            self.theme_toggle.setIcon(QIcon())
            return
        pixmap = QIcon(str(path)).pixmap(QSize(18, 18))
        painter = QPainter(pixmap)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(pixmap.rect(), self.palette().color(QPalette.ButtonText))
        painter.end()
        self.theme_toggle.setIcon(QIcon(pixmap))

    def _update_theme_toggle_tooltip(self) -> None:
        self.theme_toggle.setToolTip(
            f"Reading background: {self._reading_background.title()}. Click to cycle; open the menu to choose."
        )

    def _cycle_background(self) -> None:
        names = ("white", "theme", "paper", "mist", "dusk")
        self._background_selected(names[(names.index(self._reading_background) + 1) % len(names)])

    def _background_selected(self, background: str) -> None:
        if self.preference_scope:
            config.save_mode_reading_preferences(self.preference_scope, background=background)
        else:
            config.save_reading_view_background(background)
            config.save_reading_view_use_theme_colors(background == "theme")
        for view in list(self._instances):
            import shiboken6
            if (shiboken6.isValid(view) and shiboken6.isValid(view.theme_toggle)
                    and view.preference_scope == self.preference_scope):
                view._apply_background(background)
                view.backgroundChanged.emit(background)

    def _theme_mode_toggled(self, enabled: bool) -> None:
        self._background_selected("theme" if enabled else "white")

    def _apply_theme_mode(self, enabled: bool) -> None:
        self._apply_background("theme" if enabled else "white")

    def _apply_background(self, background: str) -> None:
        source_line, screen_y = self.reading_view_position() if self.last_markdown else (1, None)
        self._reading_background = background
        self._use_theme_colors = background == "theme"
        for name, action in self._background_actions.items():
            action.setChecked(name == background)
        blocked = self.theme_toggle.blockSignals(True)
        self.theme_toggle.setChecked(self._use_theme_colors)
        self.theme_toggle.blockSignals(blocked)
        self._update_theme_toggle_tooltip()
        self._apply_palette()
        if self.last_markdown:
            self.set_markdown(
                self.last_markdown,
                base_directory=self.base_directory,
                source_line=source_line,
                anchor_global_y=screen_y,
            )

    def _apply_palette(self) -> None:
        # Application-wide QWidget styles can replace the effective widget
        # palette after polishing. Keep the host's explicit content colors
        # independent of that stylesheet so later layout/zoom refreshes agree.
        palette = self._host_theme_palette if self._host_theme_palette is not None else self.palette()
        scale = 1.1 ** self._zoom_steps * self.reading_options["size"] / 100

        def points(value: float) -> str:
            return f"{value * scale:.2f}pt"

        body_size = points(self._base_font_point_size)
        heading_sizes = [
            points(self._base_font_point_size * multiplier)
            for multiplier in HEADING_SIZE_MULTIPLIERS
        ]
        if self._use_theme_colors:
            configured_paper = str(
                theme_value("markdown_preview.paper_bg", "#ffffff")
            )
            configured_text = str(theme_value("markdown_preview.text", "#111111"))
            custom_preview_palette = (
                configured_paper.casefold() != "#ffffff"
                or configured_text.casefold() != "#111111"
            )
            if custom_preview_palette and self._host_theme_palette is None:
                paper = configured_paper
                text = configured_text
                muted = str(theme_value("markdown_preview.muted", "#666666"))
                border = str(theme_value("markdown_preview.border", "#c6cbd1"))
                table_header = str(
                    theme_value("markdown_preview.table_header_bg", "#e9edf2")
                )
                code_bg = str(theme_value("markdown_preview.code_bg", "#f6f8fa"))
                highlight = str(theme_value("markdown_preview.link", "#1a5fb4"))
            else:
                # The bundled reading palette is deliberately paper-white. In
                # theme mode, use the live palette unless a theme explicitly
                # replaces that reading palette. This follows vault overrides
                # that have already been applied to the application.
                paper = palette.color(QPalette.Base).name()
                text = palette.color(QPalette.Text).name()
                muted = palette.color(QPalette.Mid).name()
                border = palette.color(QPalette.Mid).name()
                table_header = palette.color(QPalette.AlternateBase).name()
                code_bg = palette.color(QPalette.AlternateBase).name()
                highlight = palette.color(QPalette.Link).name()
        else:
            paper = "#ffffff"
            text = "#111111"
            muted = "#666666"
            border = "#c6cbd1"
            table_header = "#e9edf2"
            code_bg = "#f6f8fa"
            highlight = "#1a5fb4"
            if self._reading_background in {"paper", "mist", "dusk"}:
                paper, text = {
                    "paper": ("#f5f0e6", "#302c25"),
                    "mist": ("#e5e7eb", "#25282d"),
                    "dusk": ("#252a30", "#dee2e7"),
                }[self._reading_background]
                dark = self._reading_background == "dusk"
                muted = "#aeb7c2" if dark else "#62605a"
                border = "#59636f" if dark else "#b8b6b0"
                table_header = code_bg = "#333b45" if dark else QColor(paper).darker(105).name()
                highlight = "#8fc5ff" if dark else "#1a5fb4"
        table_border = _reading_table_border(paper, table_header, border)
        self._paper_color = QColor(paper)
        self._text_color = QColor(text)
        self._code_background = QColor(code_bg)
        self._focus_checked_background = QColor(table_header)

        def tint(amount):
            return QColor.fromRgbF(*(
                base * (1 - amount) + foreground * amount
                for base, foreground in zip(
                    (self._paper_color.redF(), self._paper_color.greenF(), self._paper_color.blueF()),
                    (self._text_color.redF(), self._text_color.greenF(), self._text_color.blueF()),
                )
            ))

        # Prefer the attached editor's actual highlighter colors. Standalone
        # readers use the same theme keys and light/dark defaults as the editor.
        light = self._paper_color.lightness() >= 128
        def syntax(name, light_default, dark_default):
            return self._editor_syntax_colors.get(name) or str(theme_value(
                f"markdown_editor.syntax.{name}", light_default if light else dark_default
            ))

        heading = syntax("heading", "#8b5cf6", "#6cb4ff")
        bold = syntax("bold", "#92400e", "#ffd479")
        italic = syntax("italic", "#9d174d", "#ffa7c4")
        bold_italic = syntax("bold_italic", "#7c2d12", "#ffb8d1")
        code_text = syntax("code_text", "#166534", "#a3ffab")
        inline_code_bg = syntax("code_bg", "#ecfdf5", "#2a2a2a")
        code_bg = syntax("code_block_bg", "#f3f4f6", "#2a2a2a")
        code_block_text = syntax("code_block_text", "#166534", "#a3ffab")
        self._code_background = QColor(code_bg)
        self._code_text_color = QColor(code_block_text)
        heading_inline_css = "\n".join(
            f"h{level}, h{level} strong, h{level} em, h{level} b, h{level} i {{ color: {self._editor_syntax_colors.get(f'heading{level}', heading)}; }}"
            for level in range(1, 7)
        )

        self._copy_button_border = tint(0.30)
        self._copy_button_hover = tint(0.12)
        self._css = f"""
            html, body {{ color: {text}; background: {paper}; }}
            body {{ font-family: {self._editor_font_family}; font-size: {body_size}; line-height: 1.35;
                    margin: {self._editor_document_margin}px;
                    margin-left: {self._editor_document_margin + READING_CURSOR_GUTTER}px; }}
            h1, h2, h3, h4, h5, h6 {{ color: {heading}; margin-top: 6px;
                   margin-bottom: 3px; font-weight: {self._heading_weight}; }}
            h1 {{ font-size: {heading_sizes[0]}; }}
            h2 {{ font-size: {heading_sizes[1]}; }}
            h3 {{ font-size: {heading_sizes[2]}; }}
            h4 {{ font-size: {heading_sizes[3]}; }}
            h5 {{ font-size: {heading_sizes[4]}; }}
            h6 {{ font-size: {heading_sizes[4]}; }}
            p, li, th, td, blockquote, pre, code {{ font-size: {body_size}; }}
            p, ul, ol, li, th, td {{ color: {text}; }}
            p {{ margin-top: 0.25em; margin-bottom: 0.25em; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 0.55em;
                     margin-bottom: 0.55em; border: 2px solid {table_border}; }}
            th, td {{ border: 1px solid {table_border}; padding: 5px 8px; text-align: left; }}
            th {{ background: {table_header}; color: {text}; font-weight: 700; }}
            table.reading-rule {{ width: 100%; border: none;
                                  margin-top: 2px; margin-bottom: 3px; }}
            table.reading-rule td {{ border: none; border-top: 1px solid {border};
                                     padding: 0; height: 1px; font-size: 1px; }}
            blockquote {{ color: {muted}; border-left: 4px solid {border};
                          margin-left: 0; padding-left: 1em; }}
            strong, b {{ color: {bold}; }}
            em, i {{ color: {italic}; }}
            strong em, em strong, b i, i b {{ color: {bold_italic}; }}
            {heading_inline_css}
            pre, code {{ font-family: {json.dumps(self._code_font_family)}; }}
            code {{ color: {code_text}; background: {inline_code_bg}; white-space: pre-wrap; }}
            pre, pre code {{ color: {code_block_text}; background: {code_bg}; }}
            pre {{ border: 1px solid {border}; padding: 7px; }}
            a {{ color: {highlight}; text-decoration: none; }}
            img {{ max-width: 100%; margin-top: 0.2em; margin-bottom: 0.2em; }}
            mark {{ color: {text}; background: transparent; text-decoration: underline; }}
            .md-checkbox {{ margin-right: 0.35em; }}
            .md-checkbox--checked {{ color: {highlight}; }}
        """
        self.setStyleSheet(
            "QFrame#markdownReadingHeader {"
            f"background: {palette.color(QPalette.AlternateBase).name()}; "
            f"color: {palette.color(QPalette.Text).name()}; "
            f"border-bottom: 1px solid {palette.color(QPalette.Mid).name()};"
            "} QTextBrowser#markdownReadingBrowser {"
            f"background: {paper}; color: {text}; border: none;"
            "} QWidget#markdownReadingBody {"
            f"background: {paper};"
            "}"
        )
        self._update_theme_toggle_icon()
        self.options_button.setIcon(self._asset_icon("reading-options.svg"))
        self._update_focus_button_icon()
        for button, _position, _kind in self._copy_buttons:
            self._style_copy_button(button, copied=bool(button.text()))
        button_style = (
            f"QToolButton {{ border: none; padding: 2px 4px; background: transparent; color: {text}; }}"
            f"QToolButton:hover, QToolButton:checked {{ background: {table_header}; border-radius: 3px; }}"
        )
        self.options_button.setStyleSheet(button_style)
        self.focus_button.setStyleSheet(button_style)
        self.options_popup.setStyleSheet(
            f"QFrame#readingOptionsPopup {{ background: {paper}; color: {text}; border: 1px solid {table_border}; border-radius: 6px; }}"
            f"QFrame#readingOptionsPopup QWidget {{ background: {paper}; color: {text}; }}"
            f"QFrame#readingOptionsPopup QComboBox, QFrame#readingOptionsPopup QPushButton, QFrame#readingOptionsPopup QToolButton {{ background: {table_header}; color: {text}; padding: 4px; border: 1px solid {table_border}; border-radius: 3px; }}"
        )
        self._apply_column_width()
        self.browser.refresh_reading_cursor()

    def set_vi_enabled(self, enabled: bool) -> None:
        self.browser.set_vi_state(enabled, self.browser.vi_cursor_style)

    def set_theme_palette(self, palette: QPalette) -> None:
        """Use the host's local colors and refresh any rendered theme content."""
        self._host_theme_palette = QPalette(palette)
        self.setPalette(palette)
        self._apply_background(self._reading_background)

    def set_vi_cursor_style(self, style: str) -> None:
        self.browser.set_vi_state(self.browser.vi_enabled, style)

    def set_folder_navigator_vi_cursor_color(self, color: str | None) -> None:
        candidate = QColor(str(color or ""))
        self.browser._folder_navigator_vi_cursor_color = (
            candidate.name() if candidate.isValid() else None
        )
        self.browser.refresh_reading_cursor()

    def set_base_font_point_size(self, size: float) -> None:
        try:
            value = max(6.0, float(size))
        except (TypeError, ValueError):
            value = 12.0
        if abs(value - self._base_font_point_size) < 0.01:
            return
        self._base_font_point_size = value
        self._apply_palette()

    def match_editor_layout(self, editor) -> None:
        """Keep text widths and typography familiar when switching from edit mode."""
        self._zoom_render_timer.stop()
        self._zoom_css_dirty = False
        self._editor_font_family = json.dumps(editor.font().family())
        self._editor_document_margin = editor.document().documentMargin()
        self._editor_line_spacing = editor.fontMetrics().height()
        self.browser.setFont(editor.font())
        self.browser.document().setDefaultFont(editor.document().defaultFont())
        highlighter = getattr(editor, "highlighter", None)
        if highlighter is not None:
            self._heading_weight = highlighter.heading_format.fontWeight()
            colors = {}
            for name, fmt in (
                ("heading", highlighter.heading_format),
                ("bold", highlighter.bold_format),
                ("italic", highlighter.italic_format),
                ("bold_italic", highlighter.bold_italic_format),
                ("code_text", highlighter.code_format),
                ("code_block_text", highlighter.code_block),
            ):
                colors[name] = fmt.foreground().color().name()
            colors["code_bg"] = highlighter.code_format.background().color().name()
            colors["code_block_bg"] = highlighter.code_block.background().color().name()
            for level, fmt in enumerate(highlighter.heading_styles, 1):
                colors[f"heading{level}"] = fmt.foreground().color().name()
            self._editor_syntax_colors = colors
        self.browser.setViewportMargins(editor.viewportMargins())
        self.set_base_font_point_size(editor.font().pointSizeF())
        self._apply_palette()

    def refresh_editor_layout(self, editor) -> None:
        source_line, screen_y = self.reading_view_position() if self.last_markdown else (1, None)
        self.match_editor_layout(editor)
        if self.last_markdown:
            self.set_markdown(
                self.last_markdown, base_directory=self.base_directory,
                source_line=source_line, anchor_global_y=screen_y,
            )

    def defer_editor_zoom(self, delta, callback, *, current=None, minimum=None, maximum=None) -> None:
        """Coalesce a wheel gesture before touching either text document."""
        self._editor_zoom_delta += int(delta)
        if current is not None:
            target = max(minimum, min(maximum, current + self._editor_zoom_delta))
            self._editor_zoom_delta = target - current
        self._editor_zoom_callback = callback
        self._editor_zoom_timer.start()

    def flush_editor_zoom(self) -> None:
        self._editor_zoom_timer.stop()
        delta, callback = self._editor_zoom_delta, self._editor_zoom_callback
        self._editor_zoom_delta = 0
        self._editor_zoom_callback = None
        if delta and callback is not None:
            callback(delta)

    def _match_source_spacing(self) -> None:
        """Represent source blank lines instead of HTML's compact block margins."""
        lines = self.last_markdown.splitlines()
        for position, source_line in self._source_positions:
            cursor = QTextCursor(self.browser.document())
            cursor.setPosition(position)
            # Tables own multiple rendered blocks; preserve their internal
            # layout rather than treating them as paragraphs.
            if cursor.currentTable() is not None or cursor.blockFormat().nonBreakableLines():
                continue
            blank_lines = 0
            index = source_line - 2
            while index >= 0 and not lines[index].strip():
                blank_lines += 1
                index -= 1
            fmt = cursor.blockFormat()
            fmt.setTopMargin(blank_lines * (self._editor_line_spacing or self.browser.fontMetrics().height()))
            fmt.setBottomMargin(0)
            spacing = {"compact": 90, "normal": 100, "relaxed": 135}[self.reading_options["spacing"]]
            fmt.setLineHeight(spacing, QTextBlockFormat.ProportionalHeight.value)
            cursor.setBlockFormat(fmt)
        # Wrapped paragraphs and list continuations also need the reader's
        # spacing; tables and code retain their compact internal layout.
        block = self.browser.document().begin()
        while block.isValid():
            cursor = QTextCursor(block)
            if cursor.currentTable() is None and not block.blockFormat().nonBreakableLines():
                fmt = block.blockFormat()
                fmt.setLineHeight(
                    {"compact": 90, "normal": 100, "relaxed": 135}[self.reading_options["spacing"]],
                    QTextBlockFormat.ProportionalHeight.value,
                )
                cursor.setBlockFormat(fmt)
            block = block.next()

    @staticmethod
    def editor_view_position(editor) -> tuple[int, int]:
        cursor = editor.textCursor()
        if not editor.viewport().rect().intersects(editor.cursorRect(cursor)):
            cursor = editor.cursorForPosition(QPoint(12, 4))
        block_cursor = QTextCursor(cursor.block())
        screen_y = editor.viewport().mapToGlobal(editor.cursorRect(block_cursor).topLeft()).y()
        return cursor.blockNumber() + 1, screen_y

    def reading_view_position(self) -> tuple[int, int | None]:
        if self._rendering_markdown:
            # Theme/options may arrive from a layout signal while the document
            # is being replaced. Do not query cursors in that partial layout.
            return getattr(self, "_requested_source_line", 1), None
        if self._pending_source_position is not None:
            line, screen_y = self._pending_source_position
            if screen_y is None:
                screen_y = self.browser.viewport().mapToGlobal(QPoint(0, self.browser.viewport().height() // 2)).y()
            return self.source_block_line(line), screen_y
        cursor = self.browser._reading_cursor
        if cursor.isNull():
            cursor = self.browser.content_cursor_at_y(4)
            self.browser._reading_cursor = QTextCursor(cursor)
        screen_y = self.browser.viewport().mapToGlobal(self.browser.cursorRect(cursor).topLeft()).y()
        return self.current_source_line(), screen_y

    def handoff_position_changed(self) -> bool:
        return self._handoff_scroll_value is not None and (
            self._appearance_changed
            or
            self.browser.verticalScrollBar().value() != self._handoff_scroll_value
            or self.current_source_line() != self._handoff_source_line
        )

    def page_scroll(self, direction: int) -> None:
        self.browser.page_scroll(direction)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self.last_html:
            self._image_resize_timer.start()
            self._schedule_reader_tools()

    def _image_source_size(self, source: str) -> tuple[float, float] | None:
        source_url = QUrl(source)
        resolved = self.browser.document().baseUrl().resolved(source_url)
        if not resolved.isLocalFile():
            return None
        reader = QImageReader(resolved.toLocalFile())
        size = reader.size()
        if not size.isValid() or size.width() <= 0 or size.height() <= 0:
            return None
        return float(size.width()), float(size.height())

    def _fit_images_to_viewport(self, *, reset: bool = False) -> None:
        """Give Qt explicit responsive image boxes so visual and layout sizes agree."""
        available_width = max(64.0, float(self.browser.viewport().width() - 80 - READING_CURSOR_GUTTER))
        document = self.browser.document()
        if reset:
            layouts: dict[int, tuple[int, float, float, float]] = {}
            block = document.begin()
            while block.isValid():
                iterator = block.begin()
                while not iterator.atEnd():
                    fragment = iterator.fragment()
                    if fragment.isValid() and fragment.charFormat().isImageFormat():
                        image_format = fragment.charFormat().toImageFormat()
                        source_size = self._image_source_size(image_format.name())
                        if source_size is not None:
                            natural_width, natural_height = source_size
                            requested_width = image_format.width()
                            maximum_width = (
                                requested_width
                                if requested_width > 0
                                else natural_width
                            )
                            layouts[fragment.position()] = (
                                fragment.length(),
                                natural_width,
                                natural_height,
                                maximum_width,
                            )
                    iterator += 1
                block = block.next()
            self._image_layout = layouts

        if not self._image_layout:
            return
        reading_cursor = QTextCursor(self.browser._reading_cursor)
        cursor_y = (
            self.browser.cursorRect(reading_cursor).top()
            if not reading_cursor.isNull()
            else None
        )
        for position, (length, natural_width, natural_height, maximum_width) in (
            self._image_layout.items()
        ):
            target_width = max(1.0, min(maximum_width, available_width))
            target_height = max(1.0, target_width * natural_height / natural_width)
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.KeepAnchor)
            image_format = cursor.charFormat().toImageFormat()
            if (
                abs(image_format.width() - target_width) < 0.5
                and abs(image_format.height() - target_height) < 0.5
            ):
                continue
            image_format.setWidth(target_width)
            image_format.setHeight(target_height)
            cursor.setCharFormat(image_format)

        if cursor_y is not None and not reading_cursor.isNull():
            new_y = self.browser.cursorRect(reading_cursor).top()
            scrollbar = self.browser.verticalScrollBar()
            scrollbar.setValue(scrollbar.value() + new_y - cursor_y)
        self.browser.refresh_reading_cursor()

    def set_markdown(
        self,
        markdown_text: str,
        *,
        base_directory: str | Path | None = None,
        source_line: int = 1,
        anchor_global_y: int | None = None,
    ) -> None:
        request = (markdown_text, base_directory, source_line, anchor_global_y)
        if self._rendering_markdown:
            # Theme, width, spacing, font and navigation all enter here. Keep
            # only the latest request; never replace a document mid-layout.
            self._pending_render_request = request
            return
        self._render_request_timer.stop()
        self._pending_render_request = None
        self._rendering_markdown = True
        updates_enabled = self.browser.updatesEnabled()
        self.browser.setUpdatesEnabled(False)
        try:
            self._render_markdown_document(
                markdown_text, base_directory=base_directory,
                source_line=source_line, anchor_global_y=anchor_global_y,
            )
        finally:
            self._rendering_markdown = False
            self.browser.setUpdatesEnabled(updates_enabled)
            if self._pending_render_request is not None:
                self._render_request_timer.start()
            self._schedule_reader_tools()

    def _render_pending_request(self):
        request = self._pending_render_request
        self._pending_render_request = None
        if request is not None:
            source, directory, line, screen_y = request
            self.set_markdown(source, base_directory=directory,
                              source_line=line, anchor_global_y=screen_y)

    def _render_markdown_document(
        self,
        markdown_text: str,
        *,
        base_directory: str | Path | None = None,
        source_line: int = 1,
        anchor_global_y: int | None = None,
    ) -> None:
        if self._zoom_css_dirty:
            self._zoom_render_timer.stop()
            self._apply_palette()
            self._zoom_css_dirty = False
        self.last_markdown = markdown_text or ""
        self._source_line_positions = {}
        self._source_blocks = reading_source_blocks(self.last_markdown)
        self.base_directory = Path(base_directory).resolve() if base_directory else None
        if self.base_directory is not None:
            base_url = QUrl.fromLocalFile(str(self.base_directory) + os.sep)
            self.browser.document().setBaseUrl(base_url)
        else:
            self.browser.document().setBaseUrl(QUrl())
        body = render_markdown_html(self.last_markdown, source_anchors=True)
        body = re.sub(
            r'<hr\s+id="(?P<anchor>[^"]+)"\s*/>\s*'
            r'(?P<opening><(?P<tag>(?!hr\b)[a-zA-Z0-9]+)\b[^>]*>)',
            lambda match: (
                '<hr />\n'
                + match.group("opening")
                + f'<span id="{match.group("anchor")}">&#8203;</span>'
            ),
            body,
        )
        body = re.sub(
            r'<hr(?:\s+id="(?P<anchor>[^"]+)")?\s*/>',
            lambda match: (
                '<table class="reading-rule" width="100%" cellspacing="0" '
                'cellpadding="0"><tr><td>'
                + (
                    f'<span id="{match.group("anchor")}">&#8203;</span>'
                    if match.group("anchor")
                    else ""
                )
                + "</td></tr></table>"
            ),
            body,
        )
        body = body.replace(
            '<span class="md-checkbox md-checkbox--unchecked" aria-hidden="true"></span>',
            '<span class="md-checkbox md-checkbox--unchecked" aria-hidden="true">☐</span>&nbsp;',
        ).replace(
            '<span class="md-checkbox md-checkbox--checked" aria-hidden="true"></span>',
            '<span class="md-checkbox md-checkbox--checked" aria-hidden="true">☑</span>&nbsp;',
        )
        self.last_html = f"<html><head><style>{self._css}</style></head><body>{body}</body></html>"
        self.browser._reading_cursor = QTextCursor()
        document = self.browser.document()
        layout_enabled = document.isLayoutEnabled()
        document.setLayoutEnabled(False)
        try:
            self._load_reader_document()
        finally:
            document.setLayoutEnabled(layout_enabled)
        self._fit_images_to_viewport(reset=True)
        # Refit once more after the scrollbar settles against the new layout.
        self._image_resize_timer.start()
        self._requested_source_line = max(1, int(source_line))
        self._handoff_scroll_value = None
        self._handoff_source_line = None
        self.scroll_to_source_line(
            source_line, anchor_global_y=anchor_global_y, remember_handoff=True
        )

    def _load_reader_document(self) -> None:
        """Apply HTML and formatting with layout suspended by the caller."""
        self.browser.setHtml(self.last_html)
        # Qt's HTML importer retains h1..h6 relative size adjustments alongside
        # our explicit point sizes. Those adjustments win during font resolution,
        # so inspecting fontPointSize alone misleadingly reports the right size.
        heading_formats = []
        block = self.browser.document().begin()
        while block.isValid():
            fragment_iterator = block.begin()
            while not fragment_iterator.atEnd():
                fragment = fragment_iterator.fragment()
                fmt = fragment.charFormat()
                if (
                    block.blockFormat().headingLevel() > 0
                    and fmt.hasProperty(QTextFormat.FontSizeAdjustment)
                ):
                    fmt.clearProperty(QTextFormat.FontSizeAdjustment)
                    heading_formats.append((fragment.position(), fragment.length(), fmt))
                fragment_iterator += 1
            block = block.next()
        for position, length, fmt in heading_formats:
            cursor = QTextCursor(self.browser.document())
            cursor.setPosition(position)
            cursor.setPosition(position + length, QTextCursor.KeepAnchor)
            cursor.setCharFormat(fmt)
        self.browser.document().setDocumentMargin(self._editor_document_margin)
        self._index_source_positions()
        self._match_source_spacing()
        self._decorate_reader_document()

    def _decorate_reader_document(self):
        for button, _position, _kind in self._copy_buttons:
            button.hide()
            button.deleteLater()
        self._copy_buttons = []
        document = self.browser.document()
        original_positions = dict((line, position) for position, line in self._source_positions)
        self._source_line_positions = dict(original_positions)
        for source in self._source_blocks:
            anchor = nearest_source_anchor(self.last_markdown, source.line)
            line = int(anchor.rsplit("-", 1)[-1]) if anchor else source.line
            position = original_positions.get(line)
            if position is None:
                continue
            cursor = QTextCursor(document)
            cursor.setPosition(position)
            if source.kind == "table":
                table = cursor.currentTable()
                if table is None:
                    continue
                fmt = table.format()
                fmt.setTopMargin(max(28, fmt.topMargin()))
                table.setFormat(fmt)
                for row in range(table.rows()):
                    source_line = source.line if row == 0 else source.line + row + 1
                    self._source_line_positions[source_line] = table.cellAt(row, 0).firstCursorPosition().position()
                    for column in range(table.columns()):
                        cell = table.cellAt(row, column)
                        cell_position = cell.firstCursorPosition().position()
                        self._source_positions.append((cell_position, source_line))
                        if row and row % 2 == 0 and self.reading_options["stripe_tables"]:
                            color = QColor.fromRgbF(*(
                                base * 0.94 + text * 0.06
                                for base, text in zip(
                                    (self._paper_color.redF(), self._paper_color.greenF(), self._paper_color.blueF()),
                                    (self._text_color.redF(), self._text_color.greenF(), self._text_color.blueF()),
                                )
                            ))
                            cell_format = cell.format().toTableCellFormat()
                            cell_format.setBackground(color)
                            cell.setFormat(cell_format)
            else:
                block = cursor.block()
                if not block.blockFormat().nonBreakableLines():
                    continue
                fmt = block.blockFormat()
                fmt.setTopMargin(max(28, fmt.topMargin()))
                cursor.setBlockFormat(fmt)
                fenced = bool(re.match(r"^ {0,3}(`{3,}|~{3,})", source.markdown))
                source_line = source.line + int(fenced)
                first_block = True
                last_source_line = source.end_line - int(fenced)
                while block.isValid() and block.blockFormat().nonBreakableLines() and source_line <= last_source_line:
                    code_cursor = QTextCursor(block)
                    code_format = block.blockFormat()
                    code_format.setTopMargin(28 if first_block else 0)
                    code_format.setBottomMargin(0)
                    code_format.setLeftMargin(8)
                    code_format.setRightMargin(8)
                    code_format.setLineHeight(100, QTextBlockFormat.ProportionalHeight.value)
                    code_format.setBackground(self._code_background)
                    code_cursor.setBlockFormat(code_format)
                    code_cursor.select(QTextCursor.BlockUnderCursor)
                    char_format = QTextCharFormat()
                    char_format.setFontFamilies([self._code_font_family])
                    char_format.setFontFixedPitch(True)
                    char_format.setFontStyleHint(QFont.Monospace)
                    char_format.setForeground(self._code_text_color)
                    code_cursor.mergeCharFormat(char_format)
                    self._source_line_positions[source_line] = block.position()
                    self._source_positions.append((block.position(), source_line))
                    block = block.next()
                    source_line += 1
                    first_block = False
            button = self._reader_button(self.browser.viewport(), "copy.svg", "Copy Markdown")
            end_line = max((line for line in self._source_line_positions if source.line <= line <= source.end_line), default=source.line)
            button._reading_block_end = self._source_line_positions.get(end_line, position)
            self._style_copy_button(button)
            button.setFocusPolicy(Qt.StrongFocus)
            button.clicked.connect(lambda _checked=False, source=source, button=button: self._copy_source_block(source, button))
            self._copy_buttons.append((button, position, source.kind))
        self._source_positions = sorted(set(self._source_positions))
        headings = []
        for position, line in self._source_positions:
            block = document.findBlock(position)
            level = block.blockFormat().headingLevel()
            if level:
                headings.append({"title": block.text().replace("\u200b", ""), "level": level, "position": line, "line": line})
        self.toc_widget.set_headings(headings)
        palette = self._host_theme_palette
        if palette is not None:
            self.toc_widget.set_host_palette(palette)
            self.toc_widget.set_vault_accent_color(palette.color(QPalette.Highlight).name())
        self._schedule_reader_tools()

    def _copy_source_block(self, source, button):
        QApplication.clipboard().setText(source.markdown)
        self._style_copy_button(button, copied=True)
        button.setText("Copied")
        button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        button.setFixedWidth(86)
        button.setToolTip("Copied Markdown")
        button.setAccessibleName("Copied Markdown")
        self._position_reader_tools()
        effect = button.graphicsEffect()
        if not isinstance(effect, QGraphicsOpacityEffect):
            effect = QGraphicsOpacityEffect(button)
            button.setGraphicsEffect(effect)
        animation = getattr(button, "_copy_feedback_animation", None)
        if animation is not None:
            animation.stop()
            animation.deleteLater()
        animation = QPropertyAnimation(effect, b"opacity", button)
        animation.setDuration(220)
        animation.setStartValue(0.4)
        animation.setEndValue(1.0)
        animation.setEasingCurve(QEasingCurve.OutCubic)
        button._copy_feedback_animation = animation
        animation.start()

        def restore():
            self._style_copy_button(button)
            button.setText("")
            button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.setFixedWidth(24)
            button.setToolTip("Copy Markdown")
            button.setAccessibleName("Copy Markdown")
            self._position_reader_tools()

        previous = getattr(button, "_copy_feedback_timer", None)
        if previous is not None:
            previous.stop()
            previous.deleteLater()
        timer = QTimer(button)
        timer.setSingleShot(True)
        timer.setTimerType(Qt.PreciseTimer)
        timer.timeout.connect(restore)
        button._copy_feedback_timer = timer
        timer.start(1500)

    def _index_source_positions(self) -> None:
        positions: list[tuple[int, int]] = []
        document = self.browser.document()
        block = document.begin()
        while block.isValid():
            iterator = block.begin()
            while not iterator.atEnd():
                fragment = iterator.fragment()
                if fragment.isValid():
                    for name in fragment.charFormat().anchorNames():
                        match = re.fullmatch(r"sp-source-(\d+)", name)
                        if match:
                            item = (fragment.position(), int(match.group(1)))
                            if not positions or positions[-1][1] != item[1]:
                                positions.append(item)
                iterator += 1
            block = block.next()
        self._source_positions = positions

    def source_block_line(self, source_line: int) -> int:
        if self._source_line_positions:
            line = max((line for line in self._source_line_positions if line <= source_line), default=1)
            position = self._source_line_positions.get(line)
            return max((candidate for candidate, value in self._source_line_positions.items() if value == position), default=line)
        anchor = nearest_source_anchor(self.last_markdown, source_line)
        match = re.fullmatch(r"sp-source-(\d+)", anchor)
        return int(match.group(1)) if match else 1

    def current_source_line(self) -> int:
        """Return the Markdown block line nearest the reading rail marker."""
        if self._pending_source_position is not None:
            return self.source_block_line(self._pending_source_position[0])
        if not self._source_positions:
            return 1
        if self.browser._reading_cursor.isNull():
            return self._requested_source_line
        cursor_position = self.browser._reading_cursor.position()
        anchor_position, line = self._source_positions[0]
        next_anchor_position = self.browser.document().characterCount()
        for candidate_position, candidate_line in self._source_positions:
            if candidate_position > cursor_position:
                next_anchor_position = candidate_position
                break
            anchor_position = candidate_position
            line = candidate_line
        cursor_position = min(
            cursor_position, max(anchor_position, next_anchor_position - 1)
        )
        cursor = QTextCursor(self.browser.document())
        cursor.setPosition(anchor_position)
        cursor.setPosition(cursor_position, QTextCursor.KeepAnchor)
        return line + cursor.selectedText().count("\u2028")

    def _cursor_for_source_anchor(self, anchor: str) -> QTextCursor | None:
        match = re.fullmatch(r"sp-source-(\d+)", anchor)
        if not match:
            return None
        line = int(match.group(1))
        if line in self._source_line_positions:
            cursor = QTextCursor(self.browser.document())
            cursor.setPosition(self._source_line_positions[line])
            return self.browser._nearest_content_cursor(cursor)
        for position, source_line in self._source_positions:
            if source_line == line:
                cursor = QTextCursor(self.browser.document())
                cursor.setPosition(position)
                return self.browser._nearest_content_cursor(cursor)
        return None

    def scroll_to_source_line(
        self, source_line: int, *, anchor_global_y: int | None = None,
        remember_handoff: bool = False,
    ) -> None:
        self._scroll_request_id += 1
        request_id = self._scroll_request_id
        self._pending_source_position = (source_line, anchor_global_y)
        anchor = f"sp-source-{self.source_block_line(source_line)}" if self._source_line_positions else nearest_source_anchor(self.last_markdown, source_line)
        if anchor:
            def scroll_and_mark(value=anchor):
                if request_id != self._scroll_request_id:
                    return
                self.browser.scrollToAnchor(value)

                def place_source_cursor():
                    if request_id != self._scroll_request_id:
                        return
                    cursor = self._cursor_for_source_anchor(value)
                    if cursor is None:
                        self._pending_source_position = None
                        self.browser._sync_cursor_to_viewport()
                        return
                    block_line = self.source_block_line(source_line)
                    hard_breaks = max(0, int(source_line) - block_line)
                    if hard_breaks:
                        block_text = cursor.block().text()
                        relative = max(0, cursor.position() - cursor.block().position())
                        for _index in range(hard_breaks):
                            next_break = block_text.find("\u2028", relative)
                            if next_break < 0:
                                break
                            relative = next_break + 1
                        cursor.setPosition(
                            min(
                                cursor.block().position() + relative,
                                cursor.block().position() + len(block_text),
                            )
                        )
                    cursor_rect = self.browser.cursorRect(cursor)
                    scrollbar = self.browser.verticalScrollBar()
                    if anchor_global_y is None:
                        offset = cursor_rect.center().y() - self.browser.viewport().height() // 2
                    else:
                        target_y = self.browser.viewport().mapFromGlobal(QPoint(0, anchor_global_y)).y()
                        offset = cursor_rect.top() - target_y
                    scrollbar.setValue(scrollbar.value() + offset)
                    self.browser._reading_cursor = QTextCursor(cursor)
                    self._pending_source_position = None
                    self.browser.refresh_reading_cursor()
                    if remember_handoff:
                        self._handoff_scroll_value = scrollbar.value()
                        self._handoff_source_line = self.current_source_line()

                place_source_cursor()

            QTimer.singleShot(0, self.browser, scroll_and_mark)
        else:
            self._pending_source_position = None

    def show_find(self) -> None:
        self.find_bar.show()
        self.find_query.setFocus(Qt.ShortcutFocusReason)
        self.find_query.selectAll()

    def hide_find(self) -> None:
        self.find_bar.hide()
        self.browser.setFocus(Qt.ShortcutFocusReason)

    def _find(self, *, backward: bool) -> None:
        query = self.find_query.text()
        if not query:
            return
        flags = QTextDocument.FindBackward if backward else QTextDocument.FindFlag(0)
        if not self.browser.find(query, flags):
            cursor = self.browser.textCursor()
            cursor.movePosition(QTextCursor.End if backward else QTextCursor.Start)
            self.browser.setTextCursor(cursor)
            self.browser.find(query, flags)

    def find_next(self) -> None:
        self._find(backward=False)

    def find_previous(self) -> None:
        self._find(backward=True)

    def focus_reader(self) -> None:
        self.browser.setFocus(Qt.ShortcutFocusReason)

    def zoom_in(self, steps: int = 1) -> None:
        self._set_zoom_steps(self._zoom_steps + max(1, int(steps)))

    def zoom_out(self, steps: int = 1) -> None:
        self._set_zoom_steps(self._zoom_steps - max(1, int(steps)))

    def _set_zoom_steps(self, steps: int) -> None:
        new_steps = max(-8, min(20, int(steps)))
        if new_steps == self._zoom_steps:
            return
        self._zoom_steps = new_steps
        self._zoom_css_dirty = True
        if self.isVisible():
            self._zoom_render_timer.start()

    def _apply_pending_zoom(self) -> None:
        if not self._zoom_css_dirty or not self.isVisible():
            return
        source_line = self.current_source_line()
        self._apply_palette()
        self._zoom_css_dirty = False
        if self.last_markdown:
            self.set_markdown(
                self.last_markdown,
                base_directory=self.base_directory,
                source_line=source_line,
            )
