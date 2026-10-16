"""Token-free, in-process reading view for Markdown editors."""
from __future__ import annotations

import os
import re
import sys
import weakref
from pathlib import Path

from PySide6.QtCore import (
    QEasingCurve,
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
    QImageReader,
    QKeyEvent,
    QKeySequence,
    QMouseEvent,
    QPainter,
    QPalette,
    QResizeEvent,
    QShortcut,
    QTextCursor,
    QTextDocument,
)
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from sp.app import config
from sp.app.ui.theme import theme_value
from sp.markdown_html import nearest_source_anchor, render_markdown_html


class _ReadingBrowser(QTextBrowser):
    """Selectable HTML reader with optional vi-style scrolling."""

    searchRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.vi_enabled = False
        self.vi_cursor_style = "line"
        self._pending_g = False
        self._cursor_sync_pending = False
        self._preserve_cursor_during_animation = False
        self._reading_cursor = QTextCursor()
        self._source_positions: list[tuple[int, int]] = []
        self._requested_source_line = 1
        self._scroll_animation = QPropertyAnimation(
            self.verticalScrollBar(), b"value", self
        )
        self._scroll_animation.setDuration(120)
        self._scroll_animation.setEasingCurve(QEasingCurve.OutCubic)
        self._scroll_animation.finished.connect(self._finish_scroll_animation)
        self.verticalScrollBar().valueChanged.connect(self._schedule_cursor_sync)

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
        self.setCursorWidth(0 if self.vi_enabled else 1)
        normalized = str(cursor_style or "").strip().lower()
        self.vi_cursor_style = normalized if normalized in {"block", "line"} else "line"
        self._sync_cursor_to_viewport()

    def refresh_reading_cursor(self) -> None:
        self.setExtraSelections([])
        if not self.vi_enabled or self.document().isEmpty():
            self.viewport().update()
            return
        cursor = QTextCursor(self._reading_cursor)
        if cursor.isNull():
            cursor = self.cursorForPosition(
                QPoint(max(0, self.viewport().width() // 3), self.viewport().height() // 2)
            )
        cursor = self._nearest_content_cursor(cursor)
        cursor.clearSelection()
        self._reading_cursor = QTextCursor(cursor)
        self.viewport().update()

    def vi_rail_rect(self) -> QRect:
        """Return the compact viewport marker used for the reading position."""
        if not self.vi_enabled or self._reading_cursor.isNull():
            return QRect()
        cursor_rect = self.cursorRect(self._reading_cursor)
        line_height = max(8, self.fontMetrics().height() + 4)
        width = max(18, self.fontMetrics().horizontalAdvance("MMM"))
        return QRect(5, cursor_rect.top(), width, min(line_height, cursor_rect.height()))

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        marker = self.vi_rail_rect()
        if marker.isEmpty() or not marker.intersects(self.viewport().rect()):
            return
        color = QColor(
            str(
                theme_value(
                    "markdown_preview.cursor.rail_bg",
                    theme_value("markdown_editor.vi_block_cursor.bg", "#b259ff"),
                )
            )
        )
        painter = QPainter(self.viewport())
        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(marker, 3, 3)

    @staticmethod
    def _nearest_content_cursor(cursor: QTextCursor) -> QTextCursor:
        """Skip the empty blocks emitted for source-position HTML anchors."""
        origin = cursor.block()
        if origin.isValid() and origin.text().replace("\u200b", "").strip():
            return cursor
        block = origin.next()
        while block.isValid():
            if block.text().replace("\u200b", "").strip():
                return QTextCursor(block)
            block = block.next()
        block = origin.previous()
        while block.isValid():
            if block.text().replace("\u200b", "").strip():
                return QTextCursor(block)
            block = block.previous()
        return cursor

    def _sync_cursor_to_viewport(self) -> None:
        if not self.vi_enabled or self.document().isEmpty():
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
            self.cursorForPosition(QPoint(max(0, self.viewport().width() // 3), y))
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
        if self._reading_cursor.isNull():
            self._sync_cursor_to_viewport()
        cursor = QTextCursor(self._reading_cursor)
        operation = QTextCursor.Down if direction > 0 else QTextCursor.Up
        if not cursor.movePosition(operation):
            cursor.movePosition(QTextCursor.End if direction > 0 else QTextCursor.Start)
        cursor = self._nearest_content_cursor(cursor)
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

    def __init__(self, parent=None):
        super().__init__(parent)
        self.last_markdown = ""
        self.last_html = ""
        self.base_directory: Path | None = None
        self._use_theme_colors = config.load_reading_view_use_theme_colors()
        self._base_font_point_size = 12.0
        self._zoom_steps = 0
        self._zoom_css_dirty = False
        self._zoom_render_timer = QTimer(self)
        self._zoom_render_timer.setSingleShot(True)
        self._zoom_render_timer.setInterval(self.ZOOM_DEBOUNCE_MS)
        self._zoom_render_timer.timeout.connect(self._apply_pending_zoom)
        self._image_layout: dict[int, tuple[int, float, float, float]] = {}
        self._image_resize_timer = QTimer(self)
        self._image_resize_timer.setSingleShot(True)
        self._image_resize_timer.setInterval(self.ZOOM_DEBOUNCE_MS)
        self._image_resize_timer.timeout.connect(self._fit_images_to_viewport)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(self)
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
        self.theme_toggle.setIconSize(QSize(18, 18))
        self.theme_toggle.setAccessibleName("Use theme colors in Reading View")
        self._update_theme_toggle_tooltip()
        header_layout.addWidget(self.theme_toggle)
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
        layout.addWidget(self.find_bar)

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
            lambda url: self.linkActivated.emit(url.toString())
        )
        self.browser.searchRequested.connect(self.show_find)
        layout.addWidget(self.browser, 1)
        self.setFocusProxy(self.browser)
        self.theme_toggle.toggled.connect(self._theme_mode_toggled)
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
            "Use the white Reading View background"
            if self._use_theme_colors
            else "Use theme colors in Reading View"
        )

    def _theme_mode_toggled(self, enabled: bool) -> None:
        config.save_reading_view_use_theme_colors(enabled)
        for view in list(self._instances):
            view._apply_theme_mode(enabled)

    def _apply_theme_mode(self, enabled: bool) -> None:
        source_line = self.current_source_line() if self.last_markdown else 1
        self._use_theme_colors = bool(enabled)
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
            )

    def _apply_palette(self) -> None:
        palette = self.palette()
        scale = 1.1 ** self._zoom_steps

        def points(value: float) -> str:
            return f"{value * scale:.2f}pt"

        body_size = points(self._base_font_point_size)
        if self._use_theme_colors:
            configured_paper = str(
                theme_value("markdown_preview.paper_bg", "#ffffff")
            )
            configured_text = str(theme_value("markdown_preview.text", "#111111"))
            custom_preview_palette = (
                configured_paper.casefold() != "#ffffff"
                or configured_text.casefold() != "#111111"
            )
            if custom_preview_palette:
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
        self._css = f"""
            html, body {{ color: {text}; background: {paper}; }}
            body {{ font-family: sans-serif; font-size: {body_size}; line-height: 1.35;
                    margin: 12px 36px 20px 36px; }}
            h1, h2, h3, h4, h5, h6 {{ color: {text}; margin-top: 6px;
                   margin-bottom: 3px; font-weight: 700; }}
            h1 {{ font-size: {points(self._base_font_point_size * 2.0)}; }}
            h2 {{ font-size: {points(self._base_font_point_size * 1.55)}; }}
            h3 {{ font-size: {points(self._base_font_point_size * 1.3)}; }}
            h4 {{ font-size: {points(self._base_font_point_size * 1.15)}; }}
            h5 {{ font-size: {points(self._base_font_point_size)}; }}
            h6 {{ font-size: {points(self._base_font_point_size)}; color: {muted}; }}
            p, li, th, td, blockquote, pre, code {{ font-size: {body_size}; }}
            p {{ margin-top: 0.25em; margin-bottom: 0.25em; }}
            table {{ border-collapse: collapse; width: 100%; margin-top: 0.55em;
                     margin-bottom: 0.55em; border: 2px solid {border}; }}
            th, td {{ border: 1px solid {border}; padding: 5px 8px; text-align: left; }}
            th {{ background: {table_header}; color: {text}; font-weight: 700; }}
            table.reading-rule {{ width: 100%; border: none;
                                  margin-top: 2px; margin-bottom: 3px; }}
            table.reading-rule td {{ border: none; border-top: 1px solid {border};
                                     padding: 0; height: 1px; font-size: 1px; }}
            blockquote {{ color: {muted}; border-left: 4px solid {border};
                          margin-left: 0; padding-left: 1em; }}
            code {{ background: {code_bg}; font-family: monospace; }}
            pre {{ background: {code_bg}; border: 1px solid {border}; padding: 7px; }}
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
            "}"
        )
        self._update_theme_toggle_icon()
        self.browser.refresh_reading_cursor()

    def set_vi_enabled(self, enabled: bool) -> None:
        self.browser.set_vi_state(enabled, self.browser.vi_cursor_style)

    def set_vi_cursor_style(self, style: str) -> None:
        self.browser.set_vi_state(self.browser.vi_enabled, style)

    def set_base_font_point_size(self, size: float) -> None:
        try:
            value = max(6.0, min(24.0, float(size)))
        except (TypeError, ValueError):
            value = 12.0
        if abs(value - self._base_font_point_size) < 0.01:
            return
        self._base_font_point_size = value
        self._apply_palette()

    def page_scroll(self, direction: int) -> None:
        self.browser.page_scroll(direction)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self.last_html:
            self._image_resize_timer.start()

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
        available_width = max(64.0, float(self.browser.viewport().width() - 80))
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
    ) -> None:
        if self._zoom_css_dirty:
            self._zoom_render_timer.stop()
            self._apply_palette()
            self._zoom_css_dirty = False
        self.last_markdown = markdown_text or ""
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
        self.browser.setHtml(self.last_html)
        self._fit_images_to_viewport(reset=True)
        # A newly visible vertical scrollbar narrows the viewport after the
        # first layout pass; refit once more against that settled width.
        self._image_resize_timer.start()
        self._index_source_positions()
        self._requested_source_line = max(1, int(source_line))
        self.scroll_to_source_line(source_line)

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
        anchor = nearest_source_anchor(self.last_markdown, source_line)
        match = re.fullmatch(r"sp-source-(\d+)", anchor)
        return int(match.group(1)) if match else 1

    def current_source_line(self) -> int:
        """Return the Markdown block line nearest the reading rail marker."""
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
        for position, source_line in self._source_positions:
            if source_line == line:
                cursor = QTextCursor(self.browser.document())
                cursor.setPosition(position)
                return self.browser._nearest_content_cursor(cursor)
        return None

    def scroll_to_source_line(self, source_line: int) -> None:
        anchor = nearest_source_anchor(self.last_markdown, source_line)
        if anchor:
            def scroll_and_mark(value=anchor):
                self.browser.scrollToAnchor(value)

                def place_source_cursor():
                    cursor = self._cursor_for_source_anchor(value)
                    if cursor is None:
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
                    scrollbar.setValue(
                        scrollbar.value()
                        + cursor_rect.center().y()
                        - self.browser.viewport().height() // 2
                    )
                    self.browser._reading_cursor = QTextCursor(cursor)
                    self.browser.refresh_reading_cursor()

                QTimer.singleShot(0, self.browser, place_source_cursor)

            QTimer.singleShot(0, self.browser, scroll_and_mark)

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
